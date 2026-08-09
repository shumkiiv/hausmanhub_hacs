"""Home Assistant storage adapter for cancelled scheduled scenario runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

SCHEDULE_STORAGE_VERSION = 1


class HomeAssistantScenarioScheduleStore:
    """Persist skip-once marks for scheduled scenario runs per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = Store(
            hass,
            SCHEDULE_STORAGE_VERSION,
            f"hausman_hub.scenario_schedule.{entry_id}",
        )

    async def async_load(self) -> dict[str, object]:
        payload = await self._store.async_load()
        return payload if isinstance(payload, dict) else {}

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)
