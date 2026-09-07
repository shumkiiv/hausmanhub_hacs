"""Verified Home Assistant storage for bounded safe device-command lifecycles."""

from __future__ import annotations

from homeassistant.helpers.storage import Store

from .application.safe_device_command_lifecycle import (
    valid_safe_device_command_payload,
)
from .verified_safety_storage import VerifiedSafetyStore


class HomeAssistantSafeDeviceCommandStore:
    """Persist one config entry's safe climate/humidifier operation journal."""

    def __init__(self, hass: object, entry_id: str) -> None:
        self._entry_id = entry_id
        backend = Store(
            hass,
            1,
            f"hausman_hub.safe_device_commands.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=valid_safe_device_command_payload,
        )

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous

    @property
    def coordination_key(self) -> str:
        """Stable same-process identity shared by reload instances."""

        return f"hausman_hub.safe_device_commands.{self._entry_id}"

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)
