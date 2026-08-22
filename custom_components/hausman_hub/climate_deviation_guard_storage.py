"""Home Assistant Store adapter for the climate deviation guard."""

from __future__ import annotations

from homeassistant.helpers.storage import Store


class HomeAssistantClimateDeviationGuardStore:
    """Persist one guard document per HausmanHub config entry."""

    def __init__(self, hass, entry_id: str) -> None:
        self._store = Store(
            hass,
            1,
            f"hausman_hub.climate_deviation_guard.{entry_id}",
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)
