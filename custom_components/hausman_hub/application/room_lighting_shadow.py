"""Shadow-mode room lighting: compute and journal, never execute.

The service builds a decision from the deterministic engine, appends a bounded
journal entry and persists it. It advertises ``commands_enabled=False`` and
never calls a physical command executor, so shadow evaluation is safe by
construction.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from ..domain.room_lighting import RoomLightingConfig
from ..domain.room_lighting_engine import (
    RoomLightingContext,
    RoomLightingDecision,
    evaluate_room_lighting,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

ROOM_LIGHTING_SHADOW_VERSION = 1
MAX_JOURNAL_ENTRIES = 200


class RoomLightingShadowStore(Protocol):
    """Persistence boundary for bounded shadow journal entries."""

    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


class RoomLightingShadowService:
    """Compute shadow decisions and journal them without any command path."""

    commands_enabled = False

    def __init__(
        self,
        store: RoomLightingShadowStore,
        *,
        executor: object | None = None,
    ) -> None:
        self._store = store
        # Kept only so callers can pass the future executor; it is never invoked.
        self._executor = executor
        self._entries: list[dict[str, object]] = []
        self._loaded = False

    @property
    def commands_enabled_flag(self) -> bool:
        return False

    @property
    def executor(self) -> object | None:
        return self._executor

    async def async_load(self) -> None:
        payload = await self._store.async_load()
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, list):
            self._entries = [
                entry for entry in entries if isinstance(entry, dict)
            ][-MAX_JOURNAL_ENTRIES:]
        else:
            if payload is not None:
                _LOGGER.warning("room lighting shadow journal is damaged; starting empty")
            self._entries = []
        self._loaded = True

    async def async_evaluate(
        self,
        config: RoomLightingConfig,
        context: RoomLightingContext,
    ) -> RoomLightingDecision:
        """Compute one shadow decision, journal it and return it."""

        decision = evaluate_room_lighting(config, context)
        if not self._loaded:
            await self.async_load()
        self._entries.append(
            {
                "at": decision.evaluated_at,
                "roomId": decision.room_id,
                "mode": decision.mode,
                "commandsEnabled": False,
                "commands": [
                    {
                        "targetId": command.target_id,
                        "action": command.action.value,
                        "brightness": command.brightness,
                        "colorTemperature": command.color_temperature,
                        "reason": command.reason.value,
                    }
                    for command in decision.commands
                ],
                "skips": [
                    {
                        "targetId": skip.target_id,
                        "reason": skip.reason.value,
                        "detail": skip.detail,
                    }
                    for skip in decision.skips
                ],
            }
        )
        self._entries = self._entries[-MAX_JOURNAL_ENTRIES:]
        await self._store.async_save(
            {
                "version": ROOM_LIGHTING_SHADOW_VERSION,
                "mode": "shadow",
                "commandsEnabled": False,
                "entries": self._entries,
            }
        )
        return decision

    def journal_payload(self) -> dict[str, object]:
        return {
            "version": ROOM_LIGHTING_SHADOW_VERSION,
            "mode": "shadow",
            "commandsEnabled": False,
            "entries": list(self._entries),
        }


class HomeAssistantRoomLightingShadowStore:
    """Persist bounded shadow journal entries for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store: Store[dict[str, object]] = Store(
            hass,
            ROOM_LIGHTING_SHADOW_VERSION,
            f"hausman_hub.room_lighting_shadow.{entry_id}",
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)
