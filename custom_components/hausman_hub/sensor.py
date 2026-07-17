"""Read-only Home Assistant display for the approved HASC aggregate summary.

The coordinator keeps only the fixed nine-number payload in memory. It reads
local registries on a timer and does not call services, change state, connect
outward, or retain any source identifier, name, reading, or command.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .application.configuration import ConfigurationViolation, effective_configuration
from .application.local_summary import HOME_SUMMARY_COUNT_KEYS, home_summary_payload
from .const import DOMAIN
from .home_observation import collect_home_summary


_LOGGER: Final = logging.getLogger(__name__)
SUMMARY_UPDATE_INTERVALS: Final[dict[str, timedelta]] = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
}
SENSOR_ENTITY_ID_PREFIX: Final = f"sensor.{DOMAIN}_hasc"
SUMMARY_SENSOR_ICONS: Final[dict[str, str]] = {
    "areas_count": "mdi:floor-plan",
    "devices_count": "mdi:devices",
    "entities_count": "mdi:shape",
    "sensors_count": "mdi:eye-outline",
    "available_entities_count": "mdi:check-circle-outline",
    "unavailable_entities_count": "mdi:alert-circle-outline",
    "unknown_entities_count": "mdi:help-circle-outline",
    "not_reported_entities_count": "mdi:minus-circle-outline",
    "disabled_entities_count": "mdi:pause-circle-outline",
}


class HomeSummaryCoordinator(DataUpdateCoordinator[dict[str, int]]):
    """Refresh one redacted aggregate snapshot for all nine display sensors."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        update_interval: timedelta,
    ) -> None:
        """Pass the saved entry and its approved fixed refresh interval."""

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=update_interval,
            always_update=False,
        )

    async def _async_update_data(self) -> dict[str, int]:
        """Fail closed before reading the home when saved settings are damaged."""

        entry = self.config_entry
        if entry is None:
            raise UpdateFailed("HASC saved configuration is unavailable")
        try:
            effective_configuration(entry.data, entry.options)
        except ConfigurationViolation as error:
            raise UpdateFailed("HASC saved configuration is unsafe") from error

        return home_summary_payload(
            collect_home_summary(self.hass, entry.entry_id)
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add exactly nine diagnostic number sensors and nothing controllable."""

    configuration = effective_configuration(entry.data, entry.options)
    coordinator = HomeSummaryCoordinator(
        hass,
        entry,
        SUMMARY_UPDATE_INTERVALS[configuration.summary_update_interval],
    )
    await coordinator.async_config_entry_first_refresh()
    async_add_entities(
        HomeSummaryCountSensor(coordinator, entry.entry_id, key)
        for key in HOME_SUMMARY_COUNT_KEYS
    )


class HomeSummaryCountSensor(CoordinatorEntity[HomeSummaryCoordinator], SensorEntity):
    """Expose one allowed aggregate count without attributes or actions."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: HomeSummaryCoordinator,
        entry_id: str,
        summary_key: str,
    ) -> None:
        """Create a HASC-owned sensor with a static safe translation key."""

        super().__init__(coordinator)
        self._summary_key = summary_key
        self._attr_translation_key = summary_key
        self._attr_unique_id = f"{entry_id}_{summary_key}"
        self._attr_icon = SUMMARY_SENSOR_ICONS[summary_key]
        # Keep new installations away from generic names such as ``sensor.areas``.
        # Home Assistant preserves the existing registry name for current users.
        self.entity_id = f"{SENSOR_ENTITY_ID_PREFIX}_{summary_key}"

    @property
    def native_value(self) -> int:
        """Return only the one count selected from the fixed redacted payload."""

        return self.coordinator.data[self._summary_key]
