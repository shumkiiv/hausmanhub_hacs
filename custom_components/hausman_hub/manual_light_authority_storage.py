"""Verified storage adapter for deny-only manual light authority."""
from __future__ import annotations
from typing import TYPE_CHECKING
from homeassistant.helpers.storage import Store
from .verified_safety_storage import VerifiedSafetyStore
if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

def valid_manual_light_authority_payload(value: object) -> bool:
    return isinstance(value, dict) and value.get("version") == 1 and isinstance(value.get("records"), list)

class HomeAssistantManualLightAuthorityStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = VerifiedSafetyStore(Store(hass, 1, f"hausman_hub.manual_light_authority.{entry_id}", atomic_writes=True), hass.async_add_executor_job, payload_validator=valid_manual_light_authority_payload)
    async def async_load(self) -> object | None: return await self._store.async_load()
    async def async_save(self, payload: dict[str, object]) -> None: await self._store.async_save(payload)
    @property
    def recovered_previous(self) -> bool: return self._store.recovered_previous
