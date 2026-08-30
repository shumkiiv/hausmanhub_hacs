"""Home Assistant Store adapter for delayed light safety obligations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .application.light_safety_obligations import (
    valid_light_safety_obligation_payload,
)
from .verified_safety_storage import VerifiedSafetyStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantLightSafetyObligationStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        store: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.light_safety_obligations.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            store,
            hass.async_add_executor_job,
            payload_validator=valid_light_safety_obligation_payload,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous
