"""Home Assistant state view for the native climate observation adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.climate_ha_observations import (
    MAX_STATE_LENGTH,
    ClimateHaEntityState,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_OBSERVED_ATTRIBUTES = frozenset(
    {
        "hvac_action",
        "temperature",
        "current_temperature",
        "fan_mode",
        "humidity",
    }
)


class HomeAssistantClimateStateView:
    """Expose bounded immutable entity states to the native adapter."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def entity_state(self, entity_id: str) -> ClimateHaEntityState | None:
        """Return one current bounded state, or None when it cannot be used."""

        state = self._hass.states.get(entity_id)
        if state is None or len(state.state) > MAX_STATE_LENGTH:
            return None
        attributes = {
            key: value
            for key, value in state.attributes.items()
            if key in _OBSERVED_ATTRIBUTES
            and type(value) in {str, int, float, bool}
        }
        return ClimateHaEntityState(
            entity_id=entity_id,
            state=state.state,
            attributes=attributes,
            last_updated_ms=int(state.last_updated.timestamp() * 1000),
        )
