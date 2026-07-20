"""Versioned Home Assistant storage for climate restart protection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .application.climate_protection import (
    climate_protection_from_payload,
    climate_protection_to_payload,
)
from .domain.climate_protection import (
    CLIMATE_PROTECTION_MEMORY_VERSION,
    ClimateProtectionMemory,
    ClimateProtectionViolation,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class ClimateProtectionStorageError(RuntimeError):
    """Stored climate protection data is damaged or unavailable."""


class HomeAssistantClimateProtectionStore:
    """Persist bounded transition facts for one HausmanHub config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = Store(
            hass,
            CLIMATE_PROTECTION_MEMORY_VERSION,
            f"hausman_hub.climate_protection.{entry_id}",
        )

    async def async_load(self) -> ClimateProtectionMemory | None:
        """Return no memory only before the first protected observation."""

        payload = await self._store.async_load()
        if payload is None:
            return None
        try:
            return climate_protection_from_payload(payload)
        except ClimateProtectionViolation as error:
            raise ClimateProtectionStorageError(
                "stored climate protection memory is invalid"
            ) from error

    async def async_save(self, memory: ClimateProtectionMemory) -> None:
        """Save only the exact private-binding-free protection payload."""

        await self._store.async_save(climate_protection_to_payload(memory))
