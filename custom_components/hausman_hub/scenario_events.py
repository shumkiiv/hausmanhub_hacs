"""Home Assistant event adapter for scenario device-state triggers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import time
from typing import TYPE_CHECKING, Any

from .domain.scenarios import ScenarioComparison

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.scenario_service import ScenarioService
    from .application.scenario_command_context import ScenarioCommandContextRegistry


_LOGGER = logging.getLogger(__name__)
_EVENT_STATE_CHANGED = "state_changed"
_UNAVAILABLE_STATE_VALUES = frozenset({"unknown", "unavailable"})
_MANUAL_STATE_TRIGGERS = frozenset(
    {
        ("system-tambur-adaptive-controller", "manual_chandelier_on"),
        ("system-small-corridor-light-controller", "manual_chandelier_on"),
    }
)


def _state_value(state: object | None, property_name: str) -> object | None:
    if state is None:
        return None
    if property_name in {"state", "Состояние"}:
        return getattr(state, "state", None)
    attributes = getattr(state, "attributes", {})
    return attributes.get(property_name) if isinstance(attributes, dict) else None


def state_trigger_matches(
    old_state: object | None,
    new_state: object | None,
    property_name: str,
    comparison: ScenarioComparison,
    expected: object | None,
    *,
    ignore_recovery: bool = True,
) -> bool:
    """Return whether an actual state transition satisfies one trigger.

    Numeric thresholds fire only when crossed. Equality triggers likewise need
    a transition, so irrelevant attribute updates do not repeatedly launch a
    scenario while a sensor remains in the same alarming state.
    """

    old = _state_value(old_state, property_name)
    new = _state_value(new_state, property_name)
    if old is None or new is None:
        return False
    if ignore_recovery and str(old).lower() in _UNAVAILABLE_STATE_VALUES:
        return False
    if str(new).lower() in _UNAVAILABLE_STATE_VALUES:
        return False
    if comparison is ScenarioComparison.CHANGED:
        return old != new
    if comparison is ScenarioComparison.EQUALS:
        return str(new) == str(expected) and str(old) != str(expected)
    if comparison is ScenarioComparison.NOT_EQUALS:
        return str(new) != str(expected) and str(old) == str(expected)
    if comparison in (ScenarioComparison.ABOVE, ScenarioComparison.BELOW):
        try:
            old_number = float(old)
            new_number = float(new)
            threshold = float(expected)
        except (TypeError, ValueError):
            return False
        if comparison is ScenarioComparison.ABOVE:
            return old_number <= threshold < new_number
        return old_number >= threshold > new_number
    return False


def state_level_matches(
    state: object | None,
    property_name: str,
    comparison: ScenarioComparison,
    expected: object | None,
) -> bool:
    """Recheck that a delayed state trigger still holds without a transition."""

    actual = _state_value(state, property_name)
    if actual is None or str(actual).lower() in _UNAVAILABLE_STATE_VALUES:
        return False
    if comparison is ScenarioComparison.CHANGED:
        return True
    if comparison is ScenarioComparison.EQUALS:
        return str(actual) == str(expected)
    if comparison is ScenarioComparison.NOT_EQUALS:
        return str(actual) != str(expected)
    try:
        actual_number = float(actual)
        threshold = float(expected)
    except (TypeError, ValueError):
        return False
    if comparison is ScenarioComparison.ABOVE:
        return actual_number > threshold
    if comparison is ScenarioComparison.BELOW:
        return actual_number < threshold
    return False


class _StateTriggerCoordinator:
    """Apply per-trigger debounce, hold duration and cooldown bounds."""

    def __init__(
        self,
        hass: HomeAssistant,
        service: ScenarioService,
        command_contexts: ScenarioCommandContextRegistry | None = None,
    ) -> None:
        self._hass = hass
        self._service = service
        self._command_contexts = command_contexts
        self._pending: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._cooldown_until: dict[tuple[str, str], float] = {}

    async def async_handle(
        self,
        item: tuple[
            str,
            str,
            str,
            str,
            str,
            ScenarioComparison,
            object | None,
            int,
            int,
            int,
            bool,
        ],
        old_state: object | None,
        new_state: object | None,
        event_context: object | None = None,
    ) -> None:
        (
            scenario_id,
            trigger_id,
            entity_id,
            target_id,
            property_name,
            comparison,
            expected,
            for_seconds,
            debounce_seconds,
            cooldown_seconds,
            ignore_recovery,
        ) = item
        key = (scenario_id, trigger_id)
        if time.monotonic() < self._cooldown_until.get(key, 0.0):
            return
        matched = state_trigger_matches(
            old_state,
            new_state,
            property_name,
            comparison,
            expected,
            ignore_recovery=ignore_recovery,
        )
        existing = self._pending.get(key)
        if not matched:
            if existing is not None and not state_level_matches(
                new_state, property_name, comparison, expected
            ):
                existing.cancel()
                self._pending.pop(key, None)
            return
        if (
            (scenario_id, trigger_id) in _MANUAL_STATE_TRIGGERS
            and self._command_contexts is not None
            and self._command_contexts.match(
                event_context,
                entity_id,
                _state_value(new_state, property_name),
            ) is not None
        ):
            return
        if existing is not None:
            existing.cancel()
        old_value = _state_value(old_state, property_name)
        new_value = _state_value(new_state, property_name)
        trigger_context = {
            "source": (
                "manual"
                if (scenario_id, trigger_id) in _MANUAL_STATE_TRIGGERS
                else "device_state"
            ),
            "trigger_id": trigger_id,
            "target_id": target_id,
            "old_value": old_value,
            "new_value": new_value,
            "recovery": (
                str(old_value).casefold() in _UNAVAILABLE_STATE_VALUES
                and str(new_value).casefold() not in _UNAVAILABLE_STATE_VALUES
            ),
        }
        delay = max(for_seconds, debounce_seconds)
        if delay <= 0:
            await self._async_run(
                key,
                scenario_id,
                cooldown_seconds,
                trigger_context,
            )
            return

        async def _async_delayed() -> None:
            try:
                await asyncio.sleep(delay)
                current = self._hass.states.get(entity_id)
                if state_level_matches(
                    current, property_name, comparison, expected
                ):
                    await self._async_run(
                        key,
                        scenario_id,
                        cooldown_seconds,
                        trigger_context,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "delayed state trigger %s of scenario %s failed",
                    trigger_id,
                    scenario_id,
                    exc_info=True,
                )
            finally:
                if self._pending.get(key) is asyncio.current_task():
                    self._pending.pop(key, None)

        create_task = getattr(self._hass, "async_create_task", None)
        coroutine = _async_delayed()
        task = (
            create_task(coroutine)
            if callable(create_task)
            else asyncio.create_task(coroutine)
        )
        self._pending[key] = task

    async def _async_run(
        self,
        key: tuple[str, str],
        scenario_id: str,
        cooldown_seconds: int,
        trigger_context: Mapping[str, object],
    ) -> None:
        if cooldown_seconds:
            self._cooldown_until[key] = time.monotonic() + cooldown_seconds
        await self._service.async_run_scenario(
            scenario_id,
            trigger_context=trigger_context,
        )

    def cancel(self) -> None:
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()


def event_trigger_matches(
    actual: object,
    expected: Mapping[str, str | float | int | bool],
) -> bool:
    """Match only a complete scalar event-data filter.

    Missing, nested or type-coerced fields never count as a match. This keeps
    custom HA events from accidentally starting an automation because a
    similarly named payload field happened to be present.
    """

    if not isinstance(actual, Mapping):
        return False
    for key, expected_value in expected.items():
        value = actual.get(key)
        if type(value) is not type(expected_value) or value != expected_value:
            return False
    return True


async def async_start_scenario_events(
    hass: HomeAssistant,
    entry: ConfigEntry,
    service: ScenarioService,
    command_contexts: ScenarioCommandContextRegistry | None = None,
) -> None:
    """Subscribe enabled device-state scenario triggers to HA state events."""

    coordinator = _StateTriggerCoordinator(hass, service, command_contexts)
    entry.async_on_unload(coordinator.cancel)

    async def _async_handle(event: Any) -> None:
        data = getattr(event, "data", {})
        entity_id = data.get("entity_id") if isinstance(data, dict) else None
        if not isinstance(entity_id, str):
            return
        old_state = data.get("old_state")
        new_state = data.get("new_state")
        for item in service.state_trigger_items():
            scenario_id, trigger_id, target_entity_id = item[:3]
            if entity_id != target_entity_id:
                continue
            try:
                await coordinator.async_handle(
                    item,
                    old_state,
                    new_state,
                    getattr(event, "context", None),
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "state trigger %s of scenario %s failed",
                    trigger_id,
                    scenario_id,
                    exc_info=True,
                )

    async def _async_handle_custom_event(event: Any) -> None:
        event_type = getattr(event, "event_type", None)
        data = getattr(event, "data", {})
        if not isinstance(event_type, str) or event_type == _EVENT_STATE_CHANGED:
            return
        for scenario_id, trigger_id, expected_type, expected_data in service.event_trigger_items():
            if event_type != expected_type or not event_trigger_matches(data, expected_data):
                continue
            try:
                await service.async_run_scenario(
                    scenario_id,
                    trigger_context={
                        "source": "custom_event",
                        "trigger_id": trigger_id,
                        "recovery": False,
                    },
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "event trigger %s of scenario %s failed",
                    trigger_id,
                    scenario_id,
                    exc_info=True,
                )

    bus = getattr(hass, "bus", None)
    if bus is None:
        _LOGGER.debug("Home Assistant event bus is unavailable; scenario events are not started")
        return
    unsubscribe = bus.async_listen(_EVENT_STATE_CHANGED, _async_handle)
    entry.async_on_unload(unsubscribe)
    from homeassistant.const import MATCH_ALL  # noqa: PLC0415

    unsubscribe_all = bus.async_listen(MATCH_ALL, _async_handle_custom_event)
    entry.async_on_unload(unsubscribe_all)
