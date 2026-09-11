"""Shadow-mode room lighting: compute and journal, never execute.

The service builds a decision from the deterministic engine, appends a bounded
journal entry and persists it. It advertises ``commands_enabled=False`` and
never calls a physical command executor, so shadow evaluation is safe by
construction.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import time
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
# State events can arrive in bursts; coalesce storage writes instead of
# rewriting the whole bounded journal on every evaluation.
DEFAULT_SAVE_INTERVAL_SECONDS = 1.0


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
        save_interval_seconds: float = DEFAULT_SAVE_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        # Kept only so callers can pass the future executor; it is never invoked.
        self._executor = executor
        self._entries: list[dict[str, object]] = []
        self._loaded = False
        self._save_interval = max(0.0, float(save_interval_seconds))
        self._clock = clock
        self._last_save_at: float | None = None

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
        self._last_save_at = None

    async def async_evaluate(
        self,
        config: RoomLightingConfig,
        context: RoomLightingContext,
    ) -> RoomLightingDecision:
        """Compute one shadow decision, journal it and return it."""

        decision = evaluate_room_lighting(config, context)
        await self.async_record(decision, commands_enabled=False)
        return decision

    async def async_record(
        self,
        decision: RoomLightingDecision,
        *,
        commands_enabled: bool = False,
    ) -> None:
        """Journal an already computed decision with the honest command flag.

        The shadow service still never executes anything; the flag only
        describes whether the enclosing runtime was allowed to dispatch.
        """

        enabled = bool(commands_enabled)
        mode = "live" if enabled else "shadow"
        if not self._loaded:
            await self.async_load()
        self._entries.append(
            {
                "at": decision.evaluated_at,
                "roomId": decision.room_id,
                "mode": mode,
                "commandsEnabled": enabled,
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
        await self._save_if_due(enabled)

    async def async_flush(self) -> None:
        """Force a write, for example before shutdown."""

        if not self._loaded:
            await self.async_load()
        await self._store.async_save(self._document())
        self._last_save_at = self._clock()

    async def _save_if_due(self, enabled: bool) -> None:
        now = self._clock()
        if (
            self._last_save_at is not None
            and now - self._last_save_at < self._save_interval
        ):
            return
        await self._store.async_save(self._document(enabled))
        self._last_save_at = now

    def _document(self, commands_enabled: bool | None = None) -> dict[str, object]:
        if commands_enabled is None:
            entry = self._entries[-1] if self._entries else None
            enabled = bool(entry.get("commandsEnabled")) if entry else False
        else:
            enabled = bool(commands_enabled)
        return {
            "version": ROOM_LIGHTING_SHADOW_VERSION,
            "mode": "live" if enabled else "shadow",
            "commandsEnabled": enabled,
            "entries": self._entries,
        }

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
