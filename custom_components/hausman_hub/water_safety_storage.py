"""Home Assistant Store adapter for durable water safety state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantWaterSafetyStore:
    """Persist one water safety policy per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.water_safety.{entry_id}",
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)
