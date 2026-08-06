"""Home Assistant Store adapter for durable tablet climate operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantClimateOperationStore:
    """Persist a bounded versioned operation ledger per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.climate_operations.{entry_id}",
        )

    async def async_load(self) -> object | None:
        """Return the exact stored payload for strict application validation."""

        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        """Atomically replace the complete bounded operation ledger."""

        await self._store.async_save(payload)
