"""Room-scoped Home Assistant driver for the Tambur decision bridge."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import timedelta
import logging
import time

from .scenario_node_red_decision import TAMBUR_DECISION_SCENARIO_ID


_LOGGER = logging.getLogger(__name__)
_MAX_RECEIPT_CHAIN = 8


class TamburDecisionRuntime:
    """Subscribe only Tambur evidence and execute strict Node-RED decisions."""

    def __init__(
        self,
        hass: object,
        backend: object,
        bridge: object,
        observations: object,
        *,
        presence_entities: Mapping[str, str],
        now_ms: Callable[[], int] | None = None,
        track_state_changes: Callable[..., Callable[[], None]] | None = None,
        track_interval: Callable[..., Callable[[], None]] | None = None,
        call_later: Callable[..., Callable[[], None]] | None = None,
        recovered_callback: Callable[[int], None] | None = None,
    ) -> None:
        self._hass = hass
        self._backend = backend
        self._bridge = bridge
        self._observations = observations
        self._presence_entities = dict(presence_entities)
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._track_state_changes = track_state_changes
        self._track_interval = track_interval
        self._call_later = call_later
        self._recovered_callback = recovered_callback
        self._unsubscribers: list[Callable[[], None]] = []
        self._wakeups: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._running = False
        self._activated = False
        self._sequence = 0

    async def async_start(self) -> Callable[[], None]:
        if self._running:
            raise RuntimeError("Tambur decision runtime is already active")
        if self._track_state_changes is None:
            from homeassistant.helpers.event import (  # noqa: PLC0415
                async_call_later,
                async_track_state_change_event,
                async_track_time_interval,
            )

            self._track_state_changes = async_track_state_change_event
            self._track_interval = async_track_time_interval
            self._call_later = async_call_later
        recover = getattr(self._bridge, "async_recover", None)
        calculate = getattr(self._backend, "async_calculate_tambur_decision", None)
        start_observations = getattr(self._observations, "start", None)
        if not all(callable(item) for item in (recover, calculate, start_observations)):
            raise RuntimeError("Tambur decision runtime boundary is incomplete")
        recovered = await recover()
        observation_epoch = recovered.get("observationEpoch") if isinstance(recovered, Mapping) else None
        if type(observation_epoch) is not int:
            raise RuntimeError("Tambur decision recovery is invalid")
        if self._recovered_callback is not None:
            self._recovered_callback(observation_epoch)
        stop_observations = start_observations()
        if not callable(stop_observations):
            raise RuntimeError("Tambur observation cleanup is invalid")
        self._unsubscribers.append(stop_observations)
        self._running = True
        entities = tuple(self._presence_entities)
        self._unsubscribers.append(
            self._track_state_changes(
                self._hass, entities, self._state_event
            )
        )
        if not callable(self._track_interval) or not callable(self._call_later):
            self.cancel()
            raise RuntimeError("Tambur timer boundary is incomplete")
        self._unsubscribers.append(
            self._track_interval(
                self._hass, self._clock_event, timedelta(minutes=1)
            )
        )
        try:
            recovery_event = {
                "id": f"recovery.{observation_epoch}",
                "kind": "recovery",
                "observedAtMs": self._now_ms(),
            }
            request = await self._bridge.async_snapshot(
                TAMBUR_DECISION_SCENARIO_ID, recovery_event
            )
            await self._backend.async_calculate_tambur_decision(request)
        except Exception:  # noqa: BLE001 - no legacy fallback is permitted
            self.cancel()
            raise
        return self.cancel

    def activate(self) -> None:
        """Open callbacks only after the enclosing room activation commit."""

        if not self._running or self._activated:
            return
        self._activated = True
        self._sequence += 1
        self._create_task(
            self._async_process(
                {
                    "id": f"recovery.active.{self._sequence}",
                    "kind": "recovery",
                    "observedAtMs": self._now_ms(),
                }
            )
        )

    def _state_event(self, event: object) -> None:
        if not self._running or not self._activated:
            return
        data = getattr(event, "data", None)
        if not isinstance(data, Mapping):
            return
        entity_id = data.get("entity_id")
        old_state = data.get("old_state")
        new_state = data.get("new_state")
        target_id = self._presence_entities.get(str(entity_id))
        if (
            target_id is None
            or old_state is None
            or new_state is None
            or str(getattr(old_state, "state", "unknown"))
            == str(getattr(new_state, "state", "unknown"))
        ):
            return
        self._sequence += 1
        self._create_task(
            self._async_process(
                {
                    "id": f"sensor.{self._sequence}",
                    "kind": "sensor",
                    "observedAtMs": self._now_ms(),
                    "targetId": target_id,
                }
            )
        )

    async def _clock_event(self, _now: object) -> None:
        if not self._running or not self._activated:
            return
        self._sequence += 1
        await self._async_process(
            {
                "id": f"clock.{self._sequence}",
                "kind": "clock",
                "observedAtMs": self._now_ms(),
            }
        )

    def _create_task(self, coroutine: object) -> None:
        create = getattr(self._hass, "async_create_task", None)
        if callable(create):
            create(coroutine)
        else:
            asyncio.create_task(coroutine)

    async def _async_process(self, event: Mapping[str, object]) -> None:
        async with self._lock:
            if not self._running or not self._activated:
                return
            next_event: Mapping[str, object] | None = dict(event)
            last_decision: Mapping[str, object] | None = None
            for _ in range(_MAX_RECEIPT_CHAIN):
                if next_event is None or not self._running:
                    break
                request = await self._bridge.async_snapshot(
                    TAMBUR_DECISION_SCENARIO_ID, next_event
                )
                decision = await self._backend.async_calculate_tambur_decision(request)
                result = await self._bridge.async_execute_decision(decision)
                last_decision = decision
                candidate = result.get("event") if isinstance(result, Mapping) else None
                next_event = candidate if isinstance(candidate, Mapping) else None
            else:
                raise RuntimeError("Tambur decision receipt chain exceeded its bound")
            if last_decision is not None:
                self._replace_wakeups(last_decision.get("wakeups"))

    def _replace_wakeups(self, value: object) -> None:
        for cancel in self._wakeups:
            cancel()
        self._wakeups.clear()
        if not isinstance(value, list):
            return
        now = self._now_ms()
        for item in value:
            if not isinstance(item, Mapping):
                continue
            wakeup_id = item.get("id")
            due = item.get("dueAtMs")
            if not isinstance(wakeup_id, str) or type(due) is not int:
                continue
            delay = max(0.0, (due - now) / 1000)

            async def fire(_now: object, ident: str = wakeup_id) -> None:
                if not self._running or not self._activated:
                    return
                self._sequence += 1
                await self._async_process(
                    {
                        "id": f"wakeup.{self._sequence}",
                        "kind": "wakeup",
                        "observedAtMs": self._now_ms(),
                        "wakeupId": ident,
                    }
                )

            self._wakeups.append(self._call_later(self._hass, delay, fire))

    def cancel(self) -> None:
        if not self._running and not self._unsubscribers:
            return
        self._running = False
        self._activated = False
        for cancel in self._wakeups:
            cancel()
        self._wakeups.clear()
        for unsubscribe in reversed(self._unsubscribers):
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001
                _LOGGER.error("Tambur runtime cleanup failed")
        self._unsubscribers.clear()
