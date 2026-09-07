"""Verified Home Assistant storage for per-cover manual-open protection."""

from __future__ import annotations

from homeassistant.helpers.storage import Store

from .application.curtain_protection import valid_curtain_protection_payload
from .verified_safety_storage import VerifiedSafetyStore


class HomeAssistantCurtainProtectionStore:
    def __init__(self, hass: object, entry_id: str) -> None:
        backend = Store(
            hass,
            1,
            f"hausman_hub.curtain_protection.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=valid_curtain_protection_payload,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous
