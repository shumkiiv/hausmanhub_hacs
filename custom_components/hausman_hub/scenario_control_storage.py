"""Verified Home Assistant storage for consolidated-controller policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .domain.scenario_controls import valid_scenario_control_document_payload
from .verified_safety_storage import VerifiedSafetyStore

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantScenarioControlPolicyStore:
    """Persist the exact policy document per Hausman Hub config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        backend: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.scenario_control_policy.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=valid_scenario_control_document_payload,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous
