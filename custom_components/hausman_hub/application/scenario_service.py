"""Application service for HausmanHub scenario CRUD and execution."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .scenarios import (
    ScenarioCatalog,
    ScenarioDefinitionViolation,
    validate_scenario_definition,
)
from ..domain.scenarios import (
    Scenario,
    ScenarioDefinition,
    ScenarioRegistry,
    ScenarioViolation,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _str_or_default(payload: dict[str, Any], key: str, default: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else default


def _bool_or_default(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return default


class ScenarioServiceError(Exception):
    """Base error for scenario service operations."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class ScenarioNotFoundError(ScenarioServiceError):
    def __init__(self, scenario_id: str):
        super().__init__(f"Scenario {scenario_id!r} not found", status=404)


class ScenarioReferencedError(ScenarioServiceError):
    def __init__(self, scenario_id: str):
        super().__init__(
            f"Scenario {scenario_id!r} is referenced by other scenarios",
            status=409,
        )


class ScenarioValidationError(ScenarioServiceError):
    def __init__(self, violations: tuple[ScenarioDefinitionViolation, ...]):
        super().__init__("Scenario validation failed", status=400)
        self.violations = violations


class ScenarioService:
    """Coordinate scenario persistence, validation and execution."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: object,
        catalog: ScenarioCatalog,
        executor: object | None = None,
        catalog_loader: Callable[[], Awaitable[ScenarioCatalog]] | None = None,
    ):
        self._hass = hass
        self._store = store
        self._catalog = catalog
        self._executor = executor
        self._catalog_loader = catalog_loader
        self._registry: ScenarioRegistry | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load persisted scenarios; fall back to an empty registry."""

        loaded = await self._store.async_load()
        if isinstance(loaded, ScenarioRegistry):
            self._registry = loaded
        elif isinstance(loaded, dict):
            self._registry = ScenarioRegistry.from_storage(loaded)
        else:
            self._registry = ScenarioRegistry()

    async def async_save(self, registry: ScenarioRegistry) -> None:
        """Persist the provided registry."""

        save = getattr(self._store, "async_save", None)
        if save is None:
            raise ScenarioServiceError("Store does not support save", status=500)
        await save(registry)

    async def async_reset(self) -> None:
        """Remove every user scenario without executing it."""

        async with self._lock:
            registry = ScenarioRegistry()
            await self.async_save(registry)
            self._registry = registry

    def _ensure_loaded(self) -> ScenarioRegistry:
        if self._registry is None:
            raise ScenarioServiceError("Service not loaded", status=500)
        return self._registry

    def set_executor(self, executor: object) -> None:
        """Wire the executor after both objects are created."""

        self._executor = executor

    async def async_refresh_catalog(self) -> ScenarioCatalog:
        """Refresh controllable HA entities without reloading the integration."""

        if self._catalog_loader is None:
            return self._catalog
        catalog = await self._catalog_loader()
        if not isinstance(catalog, ScenarioCatalog):
            raise ScenarioServiceError("Catalog refresh returned invalid data", status=500)
        self._catalog = catalog
        replace_catalog = getattr(self._executor, "replace_catalog", None)
        if callable(replace_catalog):
            replace_catalog(catalog)
        return catalog

    async def async_list_scenarios(self) -> tuple[Scenario, ...]:
        """Return all stored scenarios ordered by title."""

        registry = self._ensure_loaded()
        return tuple(
            sorted(registry.scenarios, key=lambda scenario: scenario.title)
        )

    async def async_get_scenario(self, scenario_id: str) -> Scenario:
        """Return one scenario or raise 404."""

        registry = self._ensure_loaded()
        for scenario in registry.scenarios:
            if scenario.id == scenario_id:
                return scenario
        raise ScenarioNotFoundError(scenario_id)

    def _validation_catalog(self, registry: ScenarioRegistry) -> ScenarioCatalog:
        """Merge live devices with the current registry for validation."""

        scenario_defs = {
            scenario.id: scenario.definition for scenario in registry.scenarios
        }
        return ScenarioCatalog(
            devices=self._catalog.devices,
            scenarios={**self._catalog.scenarios, **scenario_defs},
            scenario_definitions={
                **self._catalog.scenario_definitions,
                **scenario_defs,
            },
        )

    async def async_update_scenario(
        self, payload: dict[str, Any]
    ) -> Scenario:
        """Create or replace a scenario atomically."""

        await self.async_refresh_catalog()
        async with self._lock:
            registry = self._ensure_loaded()
            raw_definition = payload.get("definition")
            if not isinstance(raw_definition, dict):
                raise ScenarioValidationError(
                    tuple([
                        ScenarioDefinitionViolation(
                            "definition object is required",
                            path="definition",
                        )
                    ])
                )
            try:
                definition = ScenarioDefinition.from_payload(raw_definition)
            except ScenarioViolation as error:
                raise ScenarioValidationError(
                    tuple([
                        ScenarioDefinitionViolation(
                            str(error),
                            path="definition",
                        )
                    ])
                ) from error
            raw_id = payload.get("id") or payload.get("scenarioId")
            raw_title = payload.get("title")
            if not isinstance(raw_id, str) or not raw_id:
                raise ScenarioValidationError(
                    tuple([
                        ScenarioDefinitionViolation(
                            "scenario id is required",
                            path="id",
                        )
                    ])
                )
            if not isinstance(raw_title, str) or not raw_title.strip():
                raise ScenarioValidationError(
                    tuple([
                        ScenarioDefinitionViolation(
                            "scenario title is required",
                            path="title",
                        )
                    ])
                )
            validate_scenario_definition(
                definition,
                catalog=self._validation_catalog(registry),
                existing_scenario_id=raw_id,
            )

            raw_enabled = payload.get("enabled", True)
            enabled = raw_enabled is True or (
                isinstance(raw_enabled, str) and raw_enabled.lower() == "true"
            )
            new_scenario = Scenario.from_definition(
                scenario_id=raw_id,
                title=raw_title.strip(),
                definition=definition,
                enabled=enabled,
                group=_str_or_default(payload, "group", "custom"),
                description=_str_or_default(payload, "description", ""),
                icon=_str_or_default(payload, "icon", "mdi:script"),
                favorite=_bool_or_default(payload, "favorite", False),
                danger=_bool_or_default(payload, "danger", False),
                requires_confirmation=_bool_or_default(
                    payload, "requiresConfirmation", False
                ),
                trigger_description=_str_or_default(
                    payload, "triggerDescription", ""
                ),
                condition_description=_str_or_default(
                    payload, "conditionDescription", ""
                ),
                action_description=_str_or_default(payload, "actionDescription", ""),
                updated_at=int(time.time() * 1000),
            )
            scenarios = [
                s
                for s in registry.scenarios
                if s.id != new_scenario.id
            ]
            scenarios.append(new_scenario)
            new_registry = ScenarioRegistry(scenarios=tuple(scenarios))
            await self.async_save(new_registry)
            self._registry = new_registry
            return new_scenario

    async def async_test_scenario(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate a scenario definition and return a dry-run trace."""

        await self.async_refresh_catalog()
        registry = self._ensure_loaded()
        raw_definition = payload.get("definition", payload)
        try:
            definition = ScenarioDefinition.from_payload(raw_definition)
        except ScenarioViolation as error:
            raise ScenarioValidationError(
                tuple([
                    ScenarioDefinitionViolation(
                        str(error),
                        path="definition",
                    )
                ])
            ) from error
        validate_scenario_definition(
            definition,
            catalog=self._validation_catalog(registry),
            existing_scenario_id=payload.get("id") or payload.get("scenarioId"),
        )

        action_count = len(definition.actions)
        referenced = {
            action.scenario_id
            for action in definition.actions
            if action.type == "run_scenario"
        }
        plan: dict[str, Any] | None = None
        if self._executor is not None:
            run_id = self._executor.new_run_id()
            plan = await self._executor.async_execute(
                definition,
                run_id,
                scenario_id=payload.get("id") or payload.get("scenarioId") or "",
                dry_run=True,
            )
        return {
            "valid": True,
            "action_count": action_count,
            "referenced_scenario_ids": sorted(referenced),
            "plan": plan,
        }

    async def async_delete_scenario(self, scenario_id: str) -> None:
        """Delete a scenario unless it is referenced by others."""

        async with self._lock:
            registry = self._ensure_loaded()
            if not any(s.id == scenario_id for s in registry.scenarios):
                raise ScenarioNotFoundError(scenario_id)

            for scenario in registry.scenarios:
                for action in scenario.definition.actions:
                    if action.type == "run_scenario" and action.scenario_id == scenario_id:
                        raise ScenarioReferencedError(scenario_id)

            scenarios = tuple(
                s for s in registry.scenarios if s.id != scenario_id
            )
            new_registry = ScenarioRegistry(scenarios=scenarios)
            await self.async_save(new_registry)
            self._registry = new_registry

    async def async_run_scenario(
        self,
        scenario_id: str,
        visited: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Execute a scenario via the configured executor."""

        await self.async_refresh_catalog()
        scenario = await self.async_get_scenario(scenario_id)
        if not scenario.enabled:
            raise ScenarioServiceError(
                f"Scenario {scenario_id!r} is disabled", status=409
            )
        if self._executor is None:
            raise ScenarioServiceError("Executor not configured", status=500)
        run_id = self._executor.new_run_id()
        result = await self._executor.async_execute(
            scenario.definition,
            run_id,
            scenario_id=scenario.id,
            visited_scenarios=visited,
        )
        return result

    async def async_execute_device_action(
        self,
        target_id: str,
        action_id: str,
        value: object | None = None,
    ) -> dict[str, Any]:
        """Execute one catalog action through the shared strict executor."""

        await self.async_refresh_catalog()
        if self._executor is None:
            raise ScenarioServiceError("Executor not configured", status=500)
        return await self._executor.async_execute_device_action(
            target_id,
            action_id,
            value,
        )
