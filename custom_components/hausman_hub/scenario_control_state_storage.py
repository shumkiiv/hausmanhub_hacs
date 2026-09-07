"""Verified Home Assistant storage for managed-controller generations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .application.scenario_control_coordinator import (
    valid_scenario_control_state_payload,
)
from .verified_safety_storage import VerifiedSafetyStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantScenarioControlStateStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        backend: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.scenario_control_state.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=valid_scenario_control_state_payload,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous
