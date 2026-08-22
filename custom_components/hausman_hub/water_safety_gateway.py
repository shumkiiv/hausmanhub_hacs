"""Strict Home Assistant service boundary for water safety commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantWaterSafetyGateway:
    """Execute only a prevalidated close or notification call."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def has_service(self, target: str) -> bool:
        if "." not in target:
            return False
        domain, service = target.split(".", 1)
        return self._hass.services.has_service(domain, service)

    async def async_close(self, entity_id: str, close_action: str) -> None:
        domain = entity_id.split(".", 1)[0]
        await self._hass.services.async_call(
            domain,
            close_action,
            {"entity_id": entity_id},
            blocking=True,
        )

    async def async_notify(self, target: str, message: str) -> None:
        domain, service = target.split(".", 1)
        await self._hass.services.async_call(
            domain,
            service,
            {"message": message},
            blocking=True,
        )
