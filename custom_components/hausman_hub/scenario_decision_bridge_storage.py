"""Verified Home Assistant storage for the inactive Tambur decision bridge."""

from __future__ import annotations

from homeassistant.helpers.storage import Store

from .application.scenario_decision_bridge import (
    valid_scenario_decision_bridge_payload,
)
from .verified_safety_storage import VerifiedSafetyStore


class HomeAssistantScenarioDecisionBridgeStore:
    """Persist one config entry's bounded Tambur decision ledger."""

    def __init__(self, hass: object, entry_id: str) -> None:
        backend: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.scenario_decision_bridge.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=valid_scenario_decision_bridge_payload,
        )

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)
