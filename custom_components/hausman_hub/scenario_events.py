"""Home Assistant event adapter for scenario device-state triggers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .domain.scenarios import ScenarioComparison

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.scenario_service import ScenarioService


_LOGGER = logging.getLogger(__name__)


def _state_value(state: object | None, property_name: str) -> object | None:
    if state is None:
        return None
    if property_name == "state":
        return getattr(state, "state", None)
    attributes = getattr(state, "attributes", {})
    return attributes.get(property_name) if isinstance(attributes, dict) else None


def state_trigger_matches(
    old_state: object | None,
    new_state: object | None,
    property_name: str,
    comparison: ScenarioComparison,
    expected: object | None,
) -> bool:
    """Return whether an actual state transition satisfies one trigger.

    Numeric thresholds fire only when crossed. Equality triggers likewise need
    a transition, so irrelevant attribute updates do not repeatedly launch a
    scenario while a sensor remains in the same alarming state.
    """

    old = _state_value(old_state, property_name)
    new = _state_value(new_state, property_name)
    if new is None:
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


async def async_start_scenario_events(
    hass: HomeAssistant,
    entry: ConfigEntry,
    service: ScenarioService,
) -> None:
    """Subscribe enabled device-state scenario triggers to HA state events."""

    from homeassistant.const import EVENT_STATE_CHANGED  # noqa: PLC0415

    async def _async_handle(event: Any) -> None:
        data = getattr(event, "data", {})
        entity_id = data.get("entity_id") if isinstance(data, dict) else None
        if not isinstance(entity_id, str):
            return
        old_state = data.get("old_state")
        new_state = data.get("new_state")
        for scenario_id, trigger_id, target_entity_id, property_name, comparison, expected in (
            service.state_trigger_items()
        ):
            if entity_id != target_entity_id:
                continue
            if not state_trigger_matches(
                old_state, new_state, property_name, comparison, expected
            ):
                continue
            try:
                await service.async_run_scenario(scenario_id)
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "state trigger %s of scenario %s failed",
                    trigger_id,
                    scenario_id,
                    exc_info=True,
                )

    unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, _async_handle)
    entry.async_on_unload(unsubscribe)
