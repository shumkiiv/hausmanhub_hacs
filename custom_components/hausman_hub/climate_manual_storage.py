"""Versioned Home Assistant storage for climate manual ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .application.climate_manual import (
    climate_manual_from_payload,
    climate_manual_to_payload,
)
from .domain.climate_manual import (
    CLIMATE_MANUAL_MEMORY_VERSION,
    ClimateManualMemory,
    ClimateManualViolation,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class ClimateManualStorageError(RuntimeError):
    """Stored manual-control data is damaged or unavailable."""


class HomeAssistantClimateManualStore:
    """Persist direct Wi-Fi observations and room ownership per entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = Store(
            hass,
            CLIMATE_MANUAL_MEMORY_VERSION,
            f"hausman_hub.climate_manual.{entry_id}",
        )

    async def async_load(self) -> ClimateManualMemory | None:
        """Return no memory only before the first direct Wi-Fi observation."""

        payload = await self._store.async_load()
        if payload is None:
            return None
        try:
            return climate_manual_from_payload(payload)
        except ClimateManualViolation as error:
            raise ClimateManualStorageError(
                "stored manual-control memory is invalid"
            ) from error

    async def async_save(self, memory: ClimateManualMemory) -> None:
        """Save only the exact private-binding-free manual payload."""

        await self._store.async_save(climate_manual_to_payload(memory))
