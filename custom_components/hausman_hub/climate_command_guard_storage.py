"""Versioned Home Assistant storage for climate command suppression."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .application.climate_command_guard import (
    climate_command_guard_from_payload,
    climate_command_guard_to_payload,
)
from .domain.climate_command_guard import (
    CLIMATE_COMMAND_GUARD_VERSION,
    ClimateCommandGuardMemory,
    ClimateCommandGuardViolation,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class ClimateCommandGuardStorageError(RuntimeError):
    """Stored climate command guard is damaged or unavailable."""


class HomeAssistantClimateCommandGuardStore:
    """Persist desired-state fingerprints without entity IDs or payloads."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = Store(
            hass,
            CLIMATE_COMMAND_GUARD_VERSION,
            f"hausman_hub.climate_command_guard.{entry_id}",
        )

    async def async_load(self) -> ClimateCommandGuardMemory | None:
        """Return no memory only before the first guarded command."""

        payload = await self._store.async_load()
        if payload is None:
            return None
        try:
            return climate_command_guard_from_payload(payload)
        except ClimateCommandGuardViolation as error:
            raise ClimateCommandGuardStorageError(
                "stored climate command guard is invalid"
            ) from error

    async def async_save(self, memory: ClimateCommandGuardMemory) -> None:
        """Persist one exact validated command guard snapshot."""

        await self._store.async_save(climate_command_guard_to_payload(memory))
