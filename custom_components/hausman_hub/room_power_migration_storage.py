"""Home Assistant storage for protected room power migration claims."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.room_power_migration import ROOM_POWER_MIGRATION_CLAIM_VERSION

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantRoomPowerMigrationManifestStore:
    """Persist per-install exact-power claims outside the public package."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store: Store[dict[str, object]] = Store(
            hass,
            ROOM_POWER_MIGRATION_CLAIM_VERSION,
            f"hausman_hub.room_power_migration.{entry_id}",
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> bool | None:
        return await self._store.async_save(payload)


__all__ = ["HomeAssistantRoomPowerMigrationManifestStore"]
