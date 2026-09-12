"""Persisted operator gate for the legacy Tambur decision runtime.

The legacy Tambur controller is active by default. An operator may deactivate
it explicitly (for example while the room lighting engine takes over) and may
activate it again later. The gate only decides whether the startup coordinator
starts the runtime; it never edits the Node-RED graph or migration records.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

TAMBUR_LEGACY_RUNTIME_STORAGE_VERSION = 1
TAMBUR_LEGACY_RUNTIME_DEFAULT_ENABLED = True
TAMBUR_LEGACY_RUNTIME_STORE_KEY = "hausman_hub.tambur_legacy_runtime.{entry_id}"


class TamburLegacyRuntimeGate:
    """Remember whether the legacy Tambur runtime may start.

    Missing or damaged storage keeps the historical behaviour: the runtime is
    enabled, so a room is never left without a controller by accident.
    """

    def __init__(
        self,
        store: object | None = None,
        *,
        enabled: bool = TAMBUR_LEGACY_RUNTIME_DEFAULT_ENABLED,
        reason: str = "",
    ) -> None:
        self._store = store
        self._enabled = bool(enabled)
        self._reason = str(reason or "")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def reason(self) -> str:
        return self._reason

    def to_payload(self) -> dict[str, object]:
        return {
            "version": TAMBUR_LEGACY_RUNTIME_STORAGE_VERSION,
            "enabled": self._enabled,
            "reason": self._reason,
        }

    def restore(self, payload: object) -> bool:
        """Apply a stored payload, keeping the enabled default on damage."""

        if not isinstance(payload, Mapping):
            return self._enabled
        enabled = payload.get("enabled")
        if type(enabled) is not bool:
            return self._enabled
        self._enabled = enabled
        reason = payload.get("reason")
        self._reason = reason if isinstance(reason, str) else ""
        return self._enabled

    def set_enabled(self, enabled: bool, reason: str | None = None) -> bool:
        if type(enabled) is not bool:
            raise ValueError("tambur legacy runtime enabled flag must be boolean")
        self._enabled = enabled
        self._reason = str(reason or "")
        return self._enabled

    async def async_load(self) -> bool:
        payload = await self._store.async_load() if self._store is not None else None
        return self.restore(payload)

    async def async_set_enabled(self, enabled: bool, reason: str | None = None) -> bool:
        result = self.set_enabled(enabled, reason)
        if self._store is not None:
            await self._store.async_save(self.to_payload())
        return result


class HomeAssistantTamburLegacyRuntimeStore:
    """Persist the Tambur legacy runtime gate for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store: Store[dict[str, object]] = Store(
            hass,
            TAMBUR_LEGACY_RUNTIME_STORAGE_VERSION,
            TAMBUR_LEGACY_RUNTIME_STORE_KEY.format(entry_id=entry_id),
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)


__all__ = [
    "HomeAssistantTamburLegacyRuntimeStore",
    "TamburLegacyRuntimeGate",
    "TAMBUR_LEGACY_RUNTIME_DEFAULT_ENABLED",
    "TAMBUR_LEGACY_RUNTIME_STORAGE_VERSION",
]
