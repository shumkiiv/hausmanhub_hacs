"""Runtime that applies the configured away/return device actions.

The engine is intentionally small and fail-closed:

- it stays inactive while no triggers are configured;
- at startup it only observes the current state and never sends commands;
- it reacts only on real transitions between "away" and "home";
- an unknown, unavailable, restored or cached trigger never counts as active
  and never fabricates a return.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging

from ..domain.away_settings import AwayAction, AwaySettings, AwayTrigger

_LOGGER = logging.getLogger(__name__)

_UNTRUSTED_STATES = frozenset({"unknown", "unavailable"})


StateProvider = Callable[[str], object | None]
ActionRunner = Callable[[tuple[AwayAction, ...], str], Awaitable[object]]


class AwayRuntime:
    """Observe configured trigger entities and run bounded device batches."""

    def __init__(
        self,
        *,
        settings_provider: Callable[[], AwaySettings],
        state_provider: StateProvider,
        action_runner: ActionRunner,
        now_ms: Callable[[], int],
        hass: object | None = None,
        track_state_changes: Callable[
            [tuple[str, ...], Callable[[object], None]], Callable[[], None]
        ]
        | None = None,
    ) -> None:
        if not all(
            callable(candidate)
            for candidate in (settings_provider, state_provider, action_runner, now_ms)
        ):
            raise TypeError("away runtime providers are invalid")
        self._settings_provider = settings_provider
        self._state_provider = state_provider
        self._action_runner = action_runner
        self._now_ms = now_ms
        self._hass = hass
        self._track = track_state_changes
        self._unsubscribers: list[Callable[[], None]] = []
        self._tasks: set[asyncio.Task[object]] = set()
        self._running = False
        self._away_active = False
        self._last_transition_ms: int | None = None
        self._last_reason = "inactive"
        self._execution_lock = asyncio.Lock()

    @property
    def status(self) -> dict[str, object]:
        settings = self._settings_provider()
        return {
            "active": settings.active,
            "awayActive": self._away_active,
            "triggerCount": len(settings.triggers),
            "awayActionCount": len(settings.away_actions),
            "returnActionCount": len(settings.return_actions),
            "lastTransitionAtMs": self._last_transition_ms,
            "reason": self._last_reason,
        }

    async def async_start(self) -> None:
        """Subscribe and remember the initial state without sending commands."""

        if self._running:
            raise RuntimeError("away runtime is already active")
        self._running = True
        settings = self._settings_provider()
        if not settings.active:
            self._last_reason = "not_configured"
            return
        entity_ids = tuple(sorted(settings.trigger_entity_ids()))
        self._away_active = self._compute_away_active(settings)
        self._last_reason = "observed_initial"
        self._subscribe(entity_ids)

    def _subscribe(self, entity_ids: tuple[str, ...]) -> None:
        if self._track is not None:
            self._unsubscribers.append(self._track(entity_ids, self._handle_event))
            return
        if self._hass is None:
            raise RuntimeError("away runtime requires Home Assistant")
        from homeassistant.helpers.event import async_track_state_change_event

        self._unsubscribers.append(
            async_track_state_change_event(self._hass, entity_ids, self._handle_event)
        )

    async def async_refresh(self) -> None:
        """Re-arm after the settings document changed without a restart."""

        if not self._running:
            return
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        settings = self._settings_provider()
        if not settings.active:
            self._away_active = False
            self._last_reason = "not_configured"
            return
        self._away_active = self._compute_away_active(settings)
        self._last_reason = "observed_initial"
        self._subscribe(tuple(sorted(settings.trigger_entity_ids())))

    async def async_stop(self) -> None:
        self.stop()

    def stop(self) -> None:
        """Release subscriptions and cancel pending work without a new loop."""

        self._running = False
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        for task in tuple(self._tasks):
            task.cancel()
        self._tasks.clear()

    def _handle_event(self, event: object) -> None:
        if not self._running:
            return
        settings = self._settings_provider()
        if not settings.active:
            return
        data = getattr(event, "data", None)
        entity_id = data.get("entity_id") if isinstance(data, Mapping) else None
        if not any(item.entity_id == entity_id for item in settings.triggers):
            return
        self._evaluate(settings)

    def _evaluate(self, settings: AwaySettings) -> None:
        if not all(self._is_trigger_reliable(item) for item in settings.triggers):
            self._last_reason = "trigger_unreliable"
            return
        active = [self._is_trigger_active(item) for item in settings.triggers]
        if not all(active):
            if self._away_active:
                self._away_active = False
                self._last_reason = "trigger_released"
                self._schedule(settings.return_actions, "return")
            return
        if self._away_active:
            return
        delay = max(item.for_seconds for item in settings.triggers)
        if delay > 0:
            self._last_reason = "waiting_delay"
            self._schedule_delayed(settings, delay)
            return
        self._away_active = True
        self._last_reason = "trigger_active"
        self._schedule(settings.away_actions, "away")

    def _schedule_delayed(self, settings: AwaySettings, delay: int) -> None:
        async def runner() -> None:
            await asyncio.sleep(delay)
            if not self._running or self._away_active:
                return
            if self._compute_away_active(settings):
                self._away_active = True
                self._last_reason = "delay_elapsed"
                await self._run(settings.away_actions, "away")

        self._spawn(runner())

    def _schedule(self, actions: tuple[AwayAction, ...], kind: str) -> None:
        if not actions:
            self._last_reason = f"{kind}_no_actions"
            return

        async def runner() -> None:
            await self._run(actions, kind)

        self._spawn(runner())

    def _spawn(self, coro: Awaitable[object]) -> None:
        task = asyncio.get_event_loop().create_task(coro)
        self._tasks.add(task)  # type: ignore[arg-type]
        task.add_done_callback(self._tasks.discard)  # type: ignore[union-attr]

    async def _run(self, actions: tuple[AwayAction, ...], kind: str) -> None:
        async with self._execution_lock:
            now = self._now_ms()
            correlation = f"away.{kind}.{now}"
            try:
                await self._action_runner(actions, correlation)
            except Exception:  # noqa: BLE001 - engine must never crash the loop
                _LOGGER.exception("away %s action batch failed", kind)
                self._last_reason = f"{kind}_failed"
            else:
                self._last_transition_ms = now
                self._last_reason = f"{kind}_applied"

    def _compute_away_active(self, settings: AwaySettings) -> bool:
        if not settings.triggers:
            return False
        return all(
            self._is_trigger_reliable(item) and self._is_trigger_active(item)
            for item in settings.triggers
        )

    def _trigger_value(self, trigger: AwayTrigger) -> tuple[bool, str]:
        state = self._state_provider(trigger.entity_id)
        value = str(getattr(state, "state", "unknown")).strip().casefold()
        if value in _UNTRUSTED_STATES:
            return False, value
        attributes = getattr(state, "attributes", None)
        if isinstance(attributes, Mapping) and (
            attributes.get("restored") is True
            or attributes.get("cached") is True
            or attributes.get("assumed_state") is True
        ):
            return False, value
        return True, value

    def _is_trigger_reliable(self, trigger: AwayTrigger) -> bool:
        return self._trigger_value(trigger)[0]

    def _is_trigger_active(self, trigger: AwayTrigger) -> bool:
        reliable, value = self._trigger_value(trigger)
        return reliable and value == trigger.active_state
