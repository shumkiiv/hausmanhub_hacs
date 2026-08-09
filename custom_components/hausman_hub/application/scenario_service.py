"""Application service for HausmanHub scenario CRUD and execution."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .scenario_schedule import (
    ScheduledRun,
    compute_upcoming_runs,
    prune_skip_keys,
    skip_key_for,
)
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

_LOGGER = logging.getLogger(__name__)

INTERCOM_RELEASE_SECONDS = 15


def _default_call_later(
    hass: HomeAssistant, delay: float, callback: Callable[[Any], Awaitable[None]]
) -> Callable[[], None]:
    from homeassistant.helpers.event import async_call_later  # noqa: PLC0415

    return async_call_later(hass, delay, callback)


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
        intercom_entity_resolver: Callable[[], str | None] | None = None,
        call_later: Callable[
            [HomeAssistant, float, Callable[[Any], Awaitable[None]]],
            Callable[[], None],
        ] | None = None,
        sun_times_provider: Callable[[], tuple[datetime | None, datetime | None]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        schedule_store: object | None = None,
    ):
        self._hass = hass
        self._store = store
        self._catalog = catalog
        self._executor = executor
        self._catalog_loader = catalog_loader
        self._intercom_entity_resolver = intercom_entity_resolver
        self._call_later = call_later or _default_call_later
        self._sun_times_provider = sun_times_provider
        self._now_provider = now_provider
        self._schedule_store = schedule_store
        self._skipped_runs: set[str] = set()
        self._intercom_release_cancel: Callable[[], None] | None = None
        self._intercom_release_entity: str | None = None
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
        if self._schedule_store is not None:
            payload = await self._schedule_store.async_load()
            raw = payload.get("skips", ()) if isinstance(payload, dict) else ()
            self._skipped_runs = prune_skip_keys(
                (key for key in raw if isinstance(key, str)),
                self._now_local().date().isoformat(),
            )

    def _now_local(self) -> datetime:
        if self._now_provider is not None:
            return self._now_provider()
        from homeassistant.util import dt as dt_util  # noqa: PLC0415

        return dt_util.now()

    def _sun_times(self) -> tuple[datetime | None, datetime | None]:
        if self._sun_times_provider is not None:
            return self._sun_times_provider()
        state = self._hass.states.get("sun.sun")
        attributes = getattr(state, "attributes", {}) if state is not None else {}
        from homeassistant.util import dt as dt_util  # noqa: PLC0415

        return (
            dt_util.parse_datetime(str(attributes.get("next_rising") or "")),
            dt_util.parse_datetime(str(attributes.get("next_setting") or "")),
        )

    def scheduled_trigger_items(self) -> tuple[tuple[str, str, str, object], ...]:
        """(scenario_id, trigger_id, trigger type, value) for armed triggers."""

        registry = self._ensure_loaded()
        items: list[tuple[str, str, str, object]] = []
        for scenario in registry.scenarios:
            if not scenario.enabled:
                continue
            for trigger in scenario.definition.triggers:
                if trigger.type.value in ("time", "sunrise", "sunset"):
                    items.append(
                        (scenario.id, trigger.id, trigger.type.value, trigger.value)
                    )
        return tuple(items)

    def _upcoming_runs(self) -> list[ScheduledRun]:
        registry = self._ensure_loaded()
        now = self._now_local()
        next_sunrise, next_sunset = self._sun_times()
        return compute_upcoming_runs(
            registry.scenarios, now, next_sunrise, next_sunset, self._skipped_runs
        )

    async def async_list_upcoming_events(self) -> dict[str, Any]:
        """Public payload: upcoming scheduled runs, next run per trigger."""

        runs = self._upcoming_runs()
        return {
            "generatedAt": self._now_local().astimezone(timezone.utc).isoformat(),
            "events": [
                {
                    "scenarioId": run.scenario_id,
                    "scenarioTitle": run.scenario_title,
                    "triggerId": run.trigger_id,
                    "triggerType": run.trigger_type,
                    "runAt": run.run_at.isoformat(),
                    "cancellable": True,
                }
                for run in runs
            ],
        }

    async def async_cancel_upcoming(
        self,
        scenario_id: str,
        trigger_id: str,
        run_at: str,
    ) -> dict[str, Any]:
        """Skip one concrete scheduled occurrence (the one the client saw)."""

        try:
            requested = datetime.fromisoformat(run_at)
        except (TypeError, ValueError) as error:
            raise ScenarioServiceError(
                "runAt must be an ISO datetime string", status=400
            ) from error
        match = next(
            (
                run
                for run in self._upcoming_runs()
                if run.scenario_id == scenario_id
                and run.trigger_id == trigger_id
                and run.run_at == requested
            ),
            None,
        )
        if match is None:
            raise ScenarioServiceError(
                "Scheduled run not found or already cancelled", status=404
            )
        self._skipped_runs.add(match.skip_key)
        await self._async_persist_skips()
        return {
            "cancelled": True,
            "scenarioId": match.scenario_id,
            "triggerId": match.trigger_id,
            "runAt": match.run_at.isoformat(),
        }

    async def async_consume_skip(
        self, scenario_id: str, trigger_id: str, day: str
    ) -> bool:
        """At fire time: True means this occurrence was cancelled by the user."""

        key = skip_key_for(scenario_id, trigger_id, day)
        if key not in self._skipped_runs:
            return False
        self._skipped_runs.discard(key)
        await self._async_persist_skips()
        return True

    async def _async_persist_skips(self) -> None:
        if self._schedule_store is None:
            return
        today = self._now_local().date().isoformat()
        self._skipped_runs = prune_skip_keys(self._skipped_runs, today)
        save = getattr(self._schedule_store, "async_save", None)
        if save is not None:
            await save({"version": 1, "skips": sorted(self._skipped_runs)})

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

    async def async_schedule_intercom_release(
        self,
        target_id: str,
        action_id: str,
    ) -> int | None:
        """Hold the intercom relay open, then always return it to off.

        The door strike must be energised only for a short pulse. A repeated
        press extends the hold, exactly like the retired Node-RED flow did.
        Returns the hold length in seconds when a release was scheduled.
        """

        if action_id != "turn_on" or self._intercom_entity_resolver is None:
            return None
        configured = self._intercom_entity_resolver()
        if not configured:
            return None
        device = self._catalog.device(target_id)
        entity_id = getattr(device, "entity_id", None)
        if not entity_id or not entity_id.startswith("switch."):
            return None
        if configured not in {entity_id, target_id}:
            return None
        self._cancel_intercom_release()
        self._intercom_release_entity = entity_id

        async def _async_release(_now: Any) -> None:
            self._intercom_release_cancel = None
            self._intercom_release_entity = None
            release = self._intercom_release_callable()
            if release is None:
                _LOGGER.warning(
                    "intercom release for %s skipped: executor is not configured",
                    entity_id,
                )
                return
            try:
                await release(entity_id)
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "intercom release turn_off failed for %s", entity_id, exc_info=True
                )

        self._intercom_release_cancel = self._call_later(
            self._hass, INTERCOM_RELEASE_SECONDS, _async_release
        )
        return INTERCOM_RELEASE_SECONDS

    def cancel_intercom_release(self, *, turn_off_now: bool = False) -> None:
        """Cancel a pending intercom release, optionally switching off now."""

        entity_id = self._intercom_release_entity
        self._cancel_intercom_release()
        release = self._intercom_release_callable()
        if turn_off_now and entity_id is not None and release is not None:
            self._hass.async_create_task(release(entity_id))

    def _intercom_release_callable(self) -> Callable[[str], Awaitable[None]] | None:
        release = getattr(self._executor, "async_release_intercom_switch", None)
        return release if callable(release) else None

    def _cancel_intercom_release(self) -> None:
        cancel = self._intercom_release_cancel
        self._intercom_release_cancel = None
        self._intercom_release_entity = None
        if cancel is not None:
            cancel()
