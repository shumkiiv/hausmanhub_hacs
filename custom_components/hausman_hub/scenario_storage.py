"""Versioned Home Assistant storage adapter for HausmanHub scenario definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .domain.scenarios import (
    SCENARIO_REGISTRY_VERSION,
    ScenarioRegistry,
    ScenarioViolation,
    scenario_registry_from_payload,
    scenario_registry_to_payload,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class ScenarioStorageError(RuntimeError):
    """Persisted scenario data is damaged or unavailable."""


class _MigratingScenarioStore(Store[dict[str, object]]):
    """Let Home Assistant rewrite the exact legacy scenario payload once."""

    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: object,
    ) -> dict[str, object]:
        del old_major_version
        del old_minor_version
        # v1 is the first persisted version. Anything older starts empty.
        return {"version": SCENARIO_REGISTRY_VERSION, "scenarios": []}


class HomeAssistantScenarioStore:
    """Persist one complete scenario registry per HausmanHub config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = _MigratingScenarioStore(
            hass,
            SCENARIO_REGISTRY_VERSION,
            f"hausman_hub.scenarios.{entry_id}",
            max_readable_version=SCENARIO_REGISTRY_VERSION,
        )

    async def async_load(self) -> ScenarioRegistry:
        """Return an empty registry only before the first scenario is saved."""

        payload = await self._store.async_load()
        if payload is None:
            return ScenarioRegistry()
        try:
            return scenario_registry_from_payload(payload)
        except ScenarioViolation as error:
            raise ScenarioStorageError("stored scenario registry is invalid") from error

    async def async_save(self, registry: ScenarioRegistry) -> None:
        """Save only the exact validated scenario payload."""

        await self._store.async_save(scenario_registry_to_payload(registry))
