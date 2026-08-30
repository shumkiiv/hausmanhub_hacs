"""Home Assistant Store adapter for the intercom release deadline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .application.intercom_release_obligation import valid_intercom_release_payload
from .verified_safety_storage import VerifiedSafetyStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantIntercomReleaseObligationStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        store: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.intercom_release_obligation.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            store,
            hass.async_add_executor_job,
            payload_validator=valid_intercom_release_payload,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous
