"""Home Assistant Store adapter for dangerous device-action idempotency."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .verified_safety_storage import VerifiedSafetyStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantDeviceActionIdempotencyStore:
    """Persist the global dangerous-command journal per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        store: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.device_action_idempotency.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(store, hass.async_add_executor_job)

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)
