"""Home Assistant state view for the native climate observation adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.climate_ha_observations import (
    MAX_STATE_LENGTH,
    ClimateHaEntityState,
)
from .application.climate_native_setup import (
    CLIMATE_HA_CATALOG_DOMAINS,
    CLIMATE_HA_SENSOR_DEVICE_CLASSES,
    ClimateHaCatalogEntry,
    ClimateHaEntityCatalog,
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

    def entity_catalog(self) -> ClimateHaEntityCatalog:
        """Enumerate climate-relevant entities for native setup discovery."""

        entries: list[ClimateHaCatalogEntry] = []
        for state in self._hass.states.async_all():
            domain = state.entity_id.split(".", 1)[0]
            if domain not in CLIMATE_HA_CATALOG_DOMAINS:
                continue
            device_class = state.attributes.get("device_class")
            if (
                domain == "sensor"
                and device_class not in CLIMATE_HA_SENSOR_DEVICE_CLASSES
            ):
                continue
            if len(state.state) > MAX_STATE_LENGTH:
                continue
            supported_features = state.attributes.get("supported_features")
            friendly_name = state.attributes.get("friendly_name")
            entries.append(
                ClimateHaCatalogEntry(
                    entity_id=state.entity_id,
                    domain=domain,
                    state=state.state,
                    device_class=(
                        device_class if isinstance(device_class, str) else None
                    ),
                    supported_features=(
                        supported_features
                        if type(supported_features) is int
                        and supported_features >= 0
                        else 0
                    ),
                    friendly_name=(
                        friendly_name if isinstance(friendly_name, str) else None
                    ),
                    available=state.state not in {"", "unavailable", "unknown"},
                    last_updated_ms=int(state.last_updated.timestamp() * 1000),
                )
            )
        return ClimateHaEntityCatalog(
            entries=tuple(
                sorted(entries, key=lambda entry: entry.entity_id)
            )
        )

    def signal_entity_catalog(
        self,
        allowed_domains: frozenset[str],
    ) -> ClimateHaEntityCatalog:
        """Enumerate only entities usable for one signal binding selection."""

        entries: list[ClimateHaCatalogEntry] = []
        for state in self._hass.states.async_all():
            domain = state.entity_id.split(".", 1)[0]
            if domain not in allowed_domains:
                continue
            if len(state.state) > MAX_STATE_LENGTH:
                continue
            friendly_name = state.attributes.get("friendly_name")
            entries.append(
                ClimateHaCatalogEntry(
                    entity_id=state.entity_id,
                    domain=domain,
                    state=state.state,
                    device_class=None,
                    supported_features=0,
                    friendly_name=(
                        friendly_name if isinstance(friendly_name, str) else None
                    ),
                    available=state.state not in {"", "unavailable", "unknown"},
                    last_updated_ms=int(state.last_updated.timestamp() * 1000),
                )
            )
        return ClimateHaEntityCatalog(
            entries=tuple(
                sorted(entries, key=lambda entry: entry.entity_id)
            )
        )
