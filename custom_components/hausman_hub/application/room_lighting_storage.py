"""Versioned Home Assistant storage for room lighting configurations.

The adapter only reads and writes configuration documents. It never calls a
Home Assistant service, a device or any command executor, and a damaged payload
always fails closed to an empty result instead of raising into the caller.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..domain.room_lighting import (
    ROOM_LIGHTING_STORAGE_VERSION,
    RoomLightingConfig,
    RoomLightingViolation,
    config_from_payload,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class HomeAssistantRoomLightingStore:
    """Persist every room lighting configuration for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        class _MigratingRoomLightingStore(Store[dict[str, object]]):  # type: ignore[type-arg]
            async def _async_migrate_func(
                self,
                old_major_version: int,
                old_minor_version: int,
                old_data: object,
            ) -> dict[str, object]:
                del old_major_version
                del old_minor_version
                del old_data
                # v1 is the first persisted version. Anything older starts empty.
                return {
                    "version": ROOM_LIGHTING_STORAGE_VERSION,
                    "rooms": [],
                }

        self._store: Store[dict[str, object]] = _MigratingRoomLightingStore(
            hass,
            ROOM_LIGHTING_STORAGE_VERSION,
            f"hausman_hub.room_lighting.{entry_id}",
            max_readable_version=ROOM_LIGHTING_STORAGE_VERSION,
        )

    async def async_load_all(self) -> tuple[RoomLightingConfig, ...]:
        """Return every valid configuration, or an empty result on damage."""

        payload = await self._store.async_load()
        if payload is None:
            return ()
        rooms = payload.get("rooms") if isinstance(payload, dict) else None
        if not isinstance(rooms, list):
            _LOGGER.warning("room lighting store payload is damaged; ignoring it")
            return ()
        configs: list[RoomLightingConfig] = []
        for raw in rooms:
            try:
                configs.append(config_from_payload(raw))
            except RoomLightingViolation:
                _LOGGER.warning("room lighting store skipped an invalid room document")
        configs.sort(key=lambda item: item.room_id)
        return tuple(configs)

    async def async_get(self, room_id: str) -> RoomLightingConfig | None:
        """Return one configuration or None without raising on damage."""

        for config in await self.async_load_all():
            if config.room_id == room_id:
                return config
        return None

    async def async_upsert(self, config: RoomLightingConfig) -> None:
        """Insert or replace one validated configuration atomically."""

        if not isinstance(config, RoomLightingConfig):
            raise RoomLightingViolation("room lighting config is required")
        existing = await self.async_load_all()
        merged = [item for item in existing if item.room_id != config.room_id]
        merged.append(config)
        merged.sort(key=lambda item: item.room_id)
        await self._save(merged)

    async def async_delete(self, room_id: str) -> bool:
        """Delete one configuration and report whether it existed."""

        existing = await self.async_load_all()
        merged = [item for item in existing if item.room_id != room_id]
        if len(merged) == len(existing):
            return False
        await self._save(merged)
        return True

    async def _save(self, configs: list[RoomLightingConfig]) -> None:
        await self._store.async_save(
            {
                "version": ROOM_LIGHTING_STORAGE_VERSION,
                "rooms": [config.to_dict() for config in configs],
            }
        )
