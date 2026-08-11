"""Home Assistant Store adapter for the durable energy meter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantEnergyMeterStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.energy_meter.{entry_id}",
        )

    async def async_load(self) -> dict[str, object] | None:
        return await self._store.async_load()

    async def async_save(self, value: dict[str, object]) -> None:
        await self._store.async_save(value)
