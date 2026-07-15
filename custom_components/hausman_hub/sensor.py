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
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .application.local_summary import HOME_SUMMARY_COUNT_KEYS, home_summary_payload
from .const import DOMAIN
from .home_observation import collect_home_summary


_LOGGER: Final = logging.getLogger(__name__)
SUMMARY_UPDATE_INTERVAL: Final = timedelta(minutes=5)
SENSOR_ENTITY_ID_PREFIX: Final = f"sensor.{DOMAIN}_hasc"


class HomeSummaryCoordinator(DataUpdateCoordinator[dict[str, int]]):
    """Refresh one redacted aggregate snapshot for all nine display sensors."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Keep one entry identifier only to exclude HASC's own sensors."""

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SUMMARY_UPDATE_INTERVAL,
            always_update=False,
        )
        self._entry_id = entry.entry_id

    async def _async_update_data(self) -> dict[str, int]:
        """Project local registry facts immediately to the approved nine counts."""

        return home_summary_payload(
            collect_home_summary(self.hass, self._entry_id)
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add exactly nine diagnostic number sensors and nothing controllable."""

    coordinator = HomeSummaryCoordinator(hass, entry)
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
        # Keep new installations away from generic names such as ``sensor.areas``.
        # Home Assistant preserves the existing registry name for current users.
        self.entity_id = f"{SENSOR_ENTITY_ID_PREFIX}_{summary_key}"

    @property
    def native_value(self) -> int:
        """Return only the one count selected from the fixed redacted payload."""

        return self.coordinator.data[self._summary_key]
