"""Narrow Home Assistant command boundary for physical device identification."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantDeviceIdentifier:
    """Press only an explicitly discovered Identify/Locate button entity."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_identify(self, entity_id: str) -> None:
        await self._hass.services.async_call(
            "button",
            "press",
            {"entity_id": entity_id},
            blocking=True,
        )
