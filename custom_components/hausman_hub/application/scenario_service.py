"""Application service for HausmanHub scenario CRUD and execution."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..domain.scenarios import (
    Scenario,
    ScenarioCommandMode,
    ScenarioComparison,
    ScenarioDefinition,
    ScenarioExecutionBackend,
    ScenarioExecutionMode,
    ScenarioNodeRedGeneratedBy,
    ScenarioNodeRedSyncStatus,
    ScenarioRegistry,
    ScenarioTriggerType,
    ScenarioViolation,
    _scenario_to_payload,
)
from .operation_journal import scenario_operation_receipt
from .intercom_release_obligation import IntercomReleaseObligation
from .scenario_node_red import (
    NodeRedBackendError,
    NodeRedScenarioBackend,
    NodeRedSourceConflict,
    NodeRedSourceInvalid,
)
from .scenario_metrics import ScenarioPathMetrics
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
from .system_scenario_seeds import async_seed_system_scenarios

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

INTERCOM_RELEASE_SECONDS = 15
CATALOG_WARMUP_DELAYS_SECONDS = (1.0, 3.0, 8.0)
CATALOG_WARMUP_MAX_ATTEMPTS = 1 + len(CATALOG_WARMUP_DELAYS_SECONDS)
SYSTEM_SEED_RETRY_DELAY_SECONDS = 300.0
CATALOG_REFRESH_TIMEOUT_SECONDS = 2.0
_RESTART_ONLY_SYSTEM_SCENARIOS = frozenset(
    {
        "system-shower-comfort-controller",
        "system-tambur-adaptive-controller",
        "system-small-corridor-light-controller",
    }
)

_HEALTH_RECOMMENDATIONS = {
    "missing_device": "restore_device",
    "missing_action": "select_available_action",
    "value_out_of_range": "correct_value",
    "recursive_reference": "break_recursion",
}


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


def _server_owned_node_red_metadata(
    definition: ScenarioDefinition, existing: Scenario | None
) -> ScenarioDefinition:
    """Keep flow identity/evidence server-owned across scenario mutations."""

    if definition.execution_backend is not ScenarioExecutionBackend.NODE_RED:
        return definition
    metadata = definition.node_red
    if metadata is None:
        return definition
    previous = (
        existing.definition.node_red
        if existing is not None
        and existing.definition.execution_backend
        is ScenarioExecutionBackend.NODE_RED
        else None
    )
    if previous is not None:
        return replace(
            definition,
            node_red=replace(
                previous,
                input_target_ids=metadata.input_target_ids,
            ),
        )
    if (
        metadata.flow_id is not None
        or metadata.flow_revision != 0
        or metadata.source_hash is not None
        or metadata.generated_by is not ScenarioNodeRedGeneratedBy.HAUSMAN
        or metadata.sync_status is not ScenarioNodeRedSyncStatus.PENDING
    ):
        raise ScenarioValidationError(
            (
                ScenarioDefinitionViolation(
                    "Node-RED flow metadata is server-owned; create accepts only inputTargetIds.",
                    code="node_red_metadata_server_owned",
                    path="definition.nodeRed",
                ),
            )
        )
    return replace(
        definition,
        node_red=replace(
            metadata,
            flow_id=None,
            flow_revision=0,
            source_hash=None,
            generated_by=ScenarioNodeRedGeneratedBy.HAUSMAN,
            sync_status=ScenarioNodeRedSyncStatus.PENDING,
        ),
    )


def _enforce_system_execution_mode(
    scenario_id: str, definition: ScenarioDefinition
) -> ScenarioDefinition:
    """System safety controllers always replace an obsolete delayed run."""

    if scenario_id in _RESTART_ONLY_SYSTEM_SCENARIOS:
        return replace(definition, execution_mode=ScenarioExecutionMode.RESTART)
    return definition


_COMPARISON_LABELS = {
    "equals": "равно",
    "not_equals": "не равно",
    "above": "выше",
    "below": "ниже",
    "changed": "изменилось",
}


def _display_value(value: object, unit: str | None = None) -> str:
    if value is True:
        rendered = "Да"
    elif value is False:
        rendered = "Нет"
    elif isinstance(value, float):
        rendered = f"{value:g}"
    else:
        rendered = str(value)
    return f"{rendered} {unit}" if unit else rendered


def _public_device_name(device: object | None, fallback: str) -> str:
    if device is None:
        return fallback
    name = getattr(device, "name", None)
    entity_id = getattr(device, "entity_id", None)
    if not isinstance(name, str) or not name.strip() or name == entity_id:
        return fallback
    return name


def _condition_report(
    condition: object,
    catalog: ScenarioCatalog,
    result: Mapping[str, object] | None,
    position: int,
) -> dict[str, object]:
    condition_type = str(getattr(condition, "type", "condition"))
    target_id = getattr(condition, "target_id", None)
    device = catalog.device(target_id) if isinstance(target_id, str) else None
    if condition_type == "device_state":
        property_id = getattr(condition, "property", None)
        prop = device.property(property_id) if device is not None else None
        unit = prop.unit if prop is not None else None
        comparison = _COMPARISON_LABELS.get(
            str(getattr(condition, "comparison", "")),
            "соответствует",
        )
        title = " ".join(
            part
            for part in (
                _public_device_name(device, "Устройство"),
                prop.label if prop is not None else "Состояние",
                comparison,
                _display_value(getattr(condition, "value", ""), unit),
            )
            if part
        )
    elif condition_type == "time_window":
        title = f"Время входит в {getattr(condition, 'value', '')}"
    elif condition_type == "presence":
        title = "Проверить присутствие дома"
    elif condition_type == "weekday":
        title = f"День недели: {getattr(condition, 'value', '')}"
    else:
        title = "Проверить условие"
    passed = result is None or result.get("passed") is True
    return {
        "position": position,
        "title": title[:240],
        "status": "passed" if passed else "failed",
        "reason": None if passed else "Условие сейчас не выполнено",
    }


def _action_report(
    action: object,
    catalog: ScenarioCatalog,
    registry: ScenarioRegistry,
    receipt: Mapping[str, object] | None,
    position: int,
    run_status: str,
) -> dict[str, object]:
    action_type = str(getattr(action, "type", "existing_action"))
    target_name: str | None = None
    room_name: str | None = None
    value_label: str | None = None
    if action_type == "device_action":
        target_id = getattr(action, "target_id", None)
        device = catalog.device(target_id) if isinstance(target_id, str) else None
        allowed = (
            device.action(getattr(action, "action_id", ""))
            if device is not None
            else None
        )
        title = (
            getattr(action, "action_title", None)
            or (allowed.title if allowed is not None else None)
            or "Выполнить действие"
        )
        target_name = _public_device_name(device, "Устройство")
        room_name = device.room_name if device is not None else None
        value = getattr(action, "value", None)
        if value is not None:
            unit = None
            if allowed is not None and allowed.value_policy is not None:
                candidate = allowed.value_policy.get("unit")
                unit = candidate if isinstance(candidate, str) else None
            value_label = _display_value(value, unit)
    elif action_type == "delay":
        delay = getattr(action, "delay_seconds", 0)
        title = f"Подождать {delay} секунд"
        value_label = f"{delay} с"
    elif action_type == "run_scenario":
        scenario_id = getattr(action, "scenario_id", None)
        nested = registry.scenario(scenario_id) if isinstance(scenario_id, str) else None
        title = "Запустить сценарий"
        target_name = nested.title if nested is not None else "Вложенный сценарий"
    elif action_type == "notification":
        title = "Отправить уведомление"
    else:
        title = "Выполнить существующее действие"

    failed = receipt is not None and receipt.get("status") == "failed"
    if failed:
        status = "failed"
    elif run_status in {"skipped", "failed"}:
        status = "skipped"
    else:
        status = "planned"
    return {
        "position": position,
        "type": action_type,
        "title": str(title)[:240],
        "targetName": target_name[:240] if target_name is not None else None,
        "roomName": room_name[:120] if room_name is not None else None,
        "valueLabel": value_label[:120] if value_label is not None else None,
        "status": status,
        "reason": (
            "Шаг не может быть выполнен с текущими данными" if failed else None
        ),
    }


def _dry_run_report(
    definition: ScenarioDefinition,
    catalog: ScenarioCatalog,
    registry: ScenarioRegistry,
    plan: Mapping[str, object] | None,
) -> dict[str, object]:
    raw_status = str(plan.get("status", "failed")) if plan is not None else "failed"
    status = raw_status if raw_status in {"completed", "skipped", "failed"} else "failed"
    raw_condition_results = plan.get("condition_results", []) if plan is not None else []
    condition_results = {
        str(item.get("condition_id")): item
        for item in raw_condition_results
        if isinstance(item, Mapping)
    } if isinstance(raw_condition_results, list) else {}
    raw_receipts = plan.get("receipts", []) if plan is not None else []
    receipts = raw_receipts if isinstance(raw_receipts, list) else []
    conditions = [
        _condition_report(
            condition,
            catalog,
            condition_results.get(condition.id),
            index,
        )
        for index, condition in enumerate(definition.conditions, start=1)
    ]
    steps = [
        _action_report(
            action,
            catalog,
            registry,
            (
                receipts[index - 1]
                if index <= len(receipts)
                and isinstance(receipts[index - 1], Mapping)
                else None
            ),
            index,
            status,
        )
        for index, action in enumerate(definition.actions, start=1)
    ]
    if status == "completed":
        summary = (
            f"Проверка завершена. Будет запланировано {len(steps)} действий, "
            "физические команды не отправлены."
        )
    elif status == "skipped":
        summary = (
            "Условия сейчас не выполнены. Действия пропущены, "
            "физические команды не отправлены."
        )
    else:
        summary = "Проверка нашла невыполнимый шаг. Физические команды не отправлены."
    report: dict[str, object] = {
        "contract": {"name": "hausman-hub-scenario-dry-run", "version": 1},
        "status": status,
        "summary": summary,
        "conditionCount": len(conditions),
        "actionCount": len(steps),
        "commandSent": False,
        "executionBackend": definition.execution_backend.value,
        "conditions": conditions,
        "steps": steps,
    }
    if plan is not None and isinstance(plan.get("node_red"), Mapping):
        report["nodeRed"] = dict(plan["node_red"])
    return report


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


class ScenarioProtectedError(ScenarioServiceError):
    def __init__(self, scenario_id: str):
        super().__init__(
            f"Scenario {scenario_id!r} is protected by system policy",
            status=409,
        )


class ScenarioRevisionConflictError(ScenarioServiceError):
    """An editor tried to overwrite a scenario changed by another client."""

    def __init__(
        self,
        scenario_id: str,
        *,
        expected_revision: int | None,
        current_revision: int | None,
        changed_fields: tuple[str, ...] = (),
        current_room_ids: tuple[str, ...] = (),
        current_action_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            "Scenario changed on another client. Reload it before saving.",
            status=409,
        )
        self.scenario_id = scenario_id
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        self.changed_fields = changed_fields
        self.current_room_ids = current_room_ids
        self.current_action_ids = current_action_ids


class ScenarioCatalogNotReadyError(ScenarioServiceError):
    """Action steps cannot be changed until the live catalog is trustworthy."""

    def __init__(self, readiness: Mapping[str, object]) -> None:
        super().__init__(
            "Action steps cannot be changed while the device catalog is warming up.",
            status=409,
        )
        self.readiness = dict(readiness)


class ScenarioNodeRedSourceConflictError(ScenarioServiceError):
    """The managed function changed after the embedded editor loaded it."""

    def __init__(self, expected_hash: str, current_hash: str) -> None:
        super().__init__(
            "Function changed in another editor. Reload it before saving.",
            status=409,
        )
        self.expected_hash = expected_hash
        self.current_hash = current_hash


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
        ]
        | None = None,
        sun_times_provider: Callable[[], tuple[datetime | None, datetime | None]]
        | None = None,
        now_provider: Callable[[], datetime] | None = None,
        schedule_store: object | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        operation_journal: object | None = None,
        node_red_backend: NodeRedScenarioBackend | None = None,
        intercom_release_publisher: Callable[[dict[str, Any]], None] | None = None,
        scenario_change_publisher: Callable[
            [str, str, int, tuple[str, ...]], None
        ] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        intercom_release_obligation: IntercomReleaseObligation | None = None,
        manual_light_off_protection: object | None = None,
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
        self._sleep = sleep
        self._operation_journal = operation_journal
        self._node_red_backend = node_red_backend
        self._intercom_release_publisher = intercom_release_publisher
        self._scenario_change_publisher = scenario_change_publisher
        self._monotonic = monotonic
        self._intercom_release_obligation = intercom_release_obligation
        self._manual_light_off_protection = manual_light_off_protection
        self._smart_switch_receipt_consumer: object | None = None
        self._path_metrics = ScenarioPathMetrics()
        self._scenario_content_revision_key: tuple[tuple[str, int], ...] | None = None
        self._scenario_content_revision: str | None = None
        self._skipped_runs: set[str] = set()
        self._intercom_release_cancel: Callable[[], None] | None = None
        self._intercom_release_entity: str | None = None
        self._intercom_release_context: dict[str, object] | None = None
        self._registry: ScenarioRegistry | None = None
        self._lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._stopping = False
        self._active_run_calls: set[asyncio.Task[Any]] = set()
        self._run_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._queue_locks: dict[str, asyncio.Lock] = {}
        self._queue_waiters: dict[str, int] = {}
        self._catalog_refresh_lock = asyncio.Lock()
        self._catalog_warmup_task: asyncio.Task[None] | None = None
        # Внутреннее состояние прогрева; публикация в dashboard-снапшот -
        # отдельное изменение контракта, в этот релиз не входит.
        initial_catalog_status = "warming" if catalog_loader is not None else "ready"
        self._catalog_readiness: dict[str, object] = {
            "status": initial_catalog_status,
            "attempt": 1,
            "maxAttempts": CATALOG_WARMUP_MAX_ATTEMPTS,
            "deviceCount": len(catalog.devices),
            "updatedAt": int(time.time() * 1000),
            "reason": "initial_scan" if catalog_loader is not None else "catalog_static",
        }

    async def async_load(self) -> None:
        """Load persisted scenarios; fall back to an empty registry."""

        loaded = await self._store.async_load()
        if isinstance(loaded, ScenarioRegistry):
            registry = loaded
        elif isinstance(loaded, dict):
            registry = ScenarioRegistry.from_storage(loaded)
        else:
            registry = ScenarioRegistry()
        # N-1 storage may contain an unsafe single/queued mode for protected
        # managed controllers. Persist the normalization before any trigger
        # subscription can observe the registry.
        normalized = tuple(
            replace(
                scenario,
                definition=_enforce_system_execution_mode(
                    scenario.id, scenario.definition
                ),
            )
            if scenario.protected and scenario.id in _RESTART_ONLY_SYSTEM_SCENARIOS
            else scenario
            for scenario in registry.scenarios
        )
        if normalized != registry.scenarios:
            next_registry = ScenarioRegistry(scenarios=normalized)
            await self.async_save(next_registry)
            registry = next_registry
        # Do not expose an N-1 unsafe registry if its required migration
        # could not be saved atomically.
        self._registry = registry
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
        if self._hass is None:
            return None, None
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

    def state_trigger_items(
        self,
    ) -> tuple[
        tuple[
            str,
            str,
            str,
            str,
            str,
            ScenarioComparison,
            object | None,
            int,
            int,
            int,
            bool,
        ],
        ...,
    ]:
        """Return enabled device-state triggers resolved to HA entity ids.

        The event adapter deliberately receives only this compact, immutable
        projection. It cannot accidentally run a disabled scenario or address
        an entity that is absent from the allowlisted scenario catalog.
        """

        registry = self._ensure_loaded()
        items: list[
            tuple[
                str,
                str,
                str,
                str,
                str,
                ScenarioComparison,
                object | None,
                int,
                int,
                int,
                bool,
            ]
        ] = []
        for scenario in registry.scenarios:
            if not scenario.enabled:
                continue
            for trigger in scenario.definition.triggers:
                if trigger.type is not ScenarioTriggerType.DEVICE_STATE:
                    continue
                device = self._catalog.device(trigger.target_id or "")
                if device is None or not trigger.property or trigger.comparison is None:
                    continue
                items.append(
                    (
                        scenario.id,
                        trigger.id,
                        device.entity_id,
                        device.target_id,
                        trigger.property,
                        trigger.comparison,
                        trigger.value,
                        trigger.for_seconds,
                        trigger.debounce_seconds,
                        trigger.cooldown_seconds,
                        trigger.ignore_recovery,
                    )
                )
        return tuple(items)

    def event_trigger_items(
        self,
    ) -> tuple[tuple[str, str, str, dict[str, str | float | int | bool]], ...]:
        """Return enabled custom-event triggers without exposing scenario actions.

        Event data is limited by the domain model to a small scalar matcher.
        The Home Assistant adapter never receives arbitrary commands or raw
        scenario definitions from this projection.
        """

        registry = self._ensure_loaded()
        items: list[tuple[str, str, str, dict[str, str | float | int | bool]]] = []
        for scenario in registry.scenarios:
            if not scenario.enabled:
                continue
            for trigger in scenario.definition.triggers:
                if (
                    trigger.type is not ScenarioTriggerType.EVENT
                    or not trigger.event_type
                ):
                    continue
                items.append(
                    (
                        scenario.id,
                        trigger.id,
                        trigger.event_type,
                        dict(trigger.event_data or {}),
                    )
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
        """Persist one registry and restore the last complete value on failure."""

        save = getattr(self._store, "async_save", None)
        if save is None:
            raise ScenarioServiceError("Store does not support save", status=500)
        previous = self._registry
        started = self._monotonic()
        try:
            try:
                await save(registry)
            except Exception as error:
                if previous is not None and previous != registry:
                    try:
                        await save(previous)
                    except Exception as rollback_error:
                        self.cancel_running_scenarios()
                        _LOGGER.error(
                            "scenario registry rollback failed after interrupted write",
                            exc_info=True,
                        )
                        raise ScenarioServiceError(
                            "Scenario storage rollback failed", status=503
                        ) from rollback_error
                raise ScenarioServiceError(
                    "Scenario storage write failed", status=503
                ) from error
        finally:
            self._record_path_latency("storage", started)

    async def async_reset(self) -> None:
        """Remove every user scenario without executing it."""

        self._require_running()
        async with self._lock:
            self._require_running()
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

    def set_smart_switch_receipt_consumer(self, consumer: object) -> None:
        """Wire the sole durable authority for release-owned switch receipts."""

        self._smart_switch_receipt_consumer = consumer

    @property
    def catalog_readiness(self) -> dict[str, object]:
        """Return a redacted snapshot of startup catalog readiness."""

        return dict(self._catalog_readiness)

    def current_catalog(self) -> ScenarioCatalog:
        """Return the live catalog snapshot without triggering a rescan."""

        return self._catalog

    async def async_node_red_status(self) -> dict[str, object]:
        """Return installation and synchronization state for the editor."""

        registry = self._ensure_loaded()
        if self._node_red_backend is None:
            return {
                "contract": {
                    "name": "hausman-hub-scenario-node-red-status",
                    "version": 1,
                },
                "available": False,
                "installed": False,
                "running": False,
                "connected": False,
                "canProvision": False,
                "version": None,
                "message": "Node-RED backend не настроен.",
                "lastCheckedAt": int(time.time() * 1000),
                "flows": [],
            }
        return await self._node_red_backend.async_status(registry.scenarios)

    async def async_node_red_source(self, scenario_id: str) -> dict[str, object]:
        """Return one exact managed function for an embedded editor."""

        registry = self._ensure_loaded()
        scenario = registry.scenario(scenario_id)
        if scenario is None:
            raise ScenarioNotFoundError(scenario_id)
        metadata = scenario.definition.node_red
        if (
            scenario.definition.execution_backend
            is not ScenarioExecutionBackend.NODE_RED
            or metadata is None
            or not metadata.flow_id
        ):
            raise ScenarioServiceError(
                "Scenario does not use a managed Node-RED function.", status=404
            )
        if self._node_red_backend is None:
            raise ScenarioServiceError("Node-RED backend is unavailable.", status=503)
        try:
            source = await self._node_red_backend.async_read_source(
                scenario.id, metadata.flow_id
            )
        except NodeRedBackendError as error:
            raise ScenarioServiceError(str(error), status=503) from error
        actual_hash = str(source["source_hash"])
        sync_status = (
            ScenarioNodeRedSyncStatus.SYNCED.value
            if metadata.source_hash == actual_hash
            else ScenarioNodeRedSyncStatus.CHANGED.value
        )
        return {
            "contract": {
                "name": "hausman-hub-scenario-node-red-source",
                "version": 1,
            },
            "scenarioId": scenario.id,
            "title": scenario.title,
            "flowId": metadata.flow_id,
            "scenarioRevision": scenario.revision,
            "flowRevision": max(1, metadata.flow_revision),
            "sourceHash": actual_hash,
            "syncStatus": sync_status,
            "generatedBy": metadata.generated_by.value,
            "source": source["source"],
            "editable": True,
            "maxSourceBytes": 65_536,
            "updatedAt": scenario.updated_at,
        }

    async def async_update_node_red_source(
        self, scenario_id: str, payload: Mapping[str, object]
    ) -> dict[str, object]:
        """Validate or atomically save one managed function source."""

        self._require_running()
        async with self._lock:
            self._require_running()
            registry = self._ensure_loaded()
            scenario = registry.scenario(scenario_id)
            if scenario is None:
                raise ScenarioNotFoundError(scenario_id)
            metadata = scenario.definition.node_red
            if (
                scenario.definition.execution_backend
                is not ScenarioExecutionBackend.NODE_RED
                or metadata is None
                or not metadata.flow_id
            ):
                raise ScenarioServiceError(
                    "Scenario does not use a managed Node-RED function.",
                    status=404,
                )
            expected_revision = payload.get("expectedScenarioRevision")
            if expected_revision != scenario.revision:
                raise ScenarioRevisionConflictError(
                    scenario.id,
                    expected_revision=(
                        expected_revision if isinstance(expected_revision, int) else None
                    ),
                    current_revision=scenario.revision,
                    changed_fields=("definition.nodeRed",),
                    current_room_ids=scenario.room_ids,
                    current_action_ids=tuple(
                        action.id for action in scenario.definition.actions
                    ),
                )
            expected_source_hash = payload.get("expectedSourceHash")
            source = payload.get("source")
            validate_only = payload.get("validateOnly")
            if (
                not isinstance(expected_source_hash, str)
                or re.fullmatch(r"[a-f0-9]{64}", expected_source_hash) is None
                or not isinstance(source, str)
                or not isinstance(validate_only, bool)
            ):
                raise ScenarioValidationError(
                    (
                        ScenarioDefinitionViolation(
                            "Node-RED source update request is invalid.",
                            code="node_red_source_invalid",
                            path="source",
                        ),
                    )
                )
            if self._node_red_backend is None:
                raise ScenarioServiceError(
                    "Node-RED backend is unavailable.", status=503
                )
            try:
                result = await self._node_red_backend.async_update_source(
                    scenario.id,
                    scenario.definition,
                    metadata.flow_id,
                    source,
                    expected_source_hash,
                    self._catalog,
                    validate_only=validate_only,
                )
            except NodeRedSourceConflict as error:
                raise ScenarioNodeRedSourceConflictError(
                    error.expected_hash, error.current_hash
                ) from error
            except NodeRedSourceInvalid as error:
                raise ScenarioValidationError(
                    (
                        ScenarioDefinitionViolation(
                            str(error),
                            code=error.code,
                            path=(
                                f"source.line.{error.line}"
                                if error.line is not None
                                else "source"
                            ),
                        ),
                    )
                ) from error
            except NodeRedBackendError as error:
                raise ScenarioServiceError(str(error), status=503) from error

            proposed_hash = str(result["proposed_source_hash"])
            flow_changed = bool(result["saved"])
            metadata_changed = (
                metadata.source_hash != proposed_hash
                or metadata.sync_status is not ScenarioNodeRedSyncStatus.SYNCED
                or metadata.generated_by is not ScenarioNodeRedGeneratedBy.USER
            )
            current_scenario = scenario
            if not validate_only and (flow_changed or metadata_changed):
                next_metadata = replace(
                    metadata,
                    flow_revision=metadata.flow_revision + (1 if flow_changed else 0),
                    source_hash=proposed_hash,
                    generated_by=ScenarioNodeRedGeneratedBy.USER,
                    sync_status=ScenarioNodeRedSyncStatus.SYNCED,
                )
                current_scenario = replace(
                    scenario,
                    definition=replace(
                        scenario.definition, node_red=next_metadata
                    ),
                    revision=scenario.revision + 1,
                    updated_at=int(time.time() * 1000),
                )
                scenarios = [
                    item for item in registry.scenarios if item.id != scenario.id
                ]
                scenarios.append(current_scenario)
                new_registry = ScenarioRegistry(scenarios=tuple(scenarios))
                try:
                    await self.async_save(new_registry)
                except ScenarioServiceError:
                    if flow_changed:
                        try:
                            await self._node_red_backend.async_restore_source(
                                scenario.id,
                                metadata.flow_id,
                                str(result["previous_source"]),
                                expected_current_hash=proposed_hash,
                            )
                        except NodeRedBackendError as rollback_error:
                            raise ScenarioServiceError(
                                "Scenario storage and Node-RED rollback failed.",
                                status=503,
                            ) from rollback_error
                    raise
                self._registry = new_registry
                self._scenario_content_revision_key = None
                self._scenario_content_revision = None
                self._publish_scenario_change(
                    "updated",
                    current_scenario.id,
                    current_scenario.revision,
                    ("definition.nodeRed.source",),
                )

            return {
                "contract": {
                    "name": "hausman-hub-scenario-node-red-source-update-receipt",
                    "version": 1,
                },
                "scenarioId": scenario.id,
                "valid": True,
                "saved": bool(
                    not validate_only and (flow_changed or metadata_changed)
                ),
                "commandSent": False,
                "scenarioRevision": current_scenario.revision,
                "flowRevision": max(
                    1,
                    metadata.flow_revision + (1 if flow_changed else 0),
                ),
                "currentSourceHash": (
                    proposed_hash
                    if not validate_only
                    else str(result["current_source_hash"])
                ),
                "proposedSourceHash": proposed_hash,
                "syncStatus": (
                    "validated"
                    if validate_only
                    else ScenarioNodeRedSyncStatus.SYNCED.value
                ),
                "diagnostics": list(result["diagnostics"]),
                "verification": result["verification"],
            }

    async def async_apply_managed_switch_migration(
        self, entries: tuple[object, ...]
    ) -> str:
        """CAS-migrate the three release-owned managed scenarios."""

        self._require_running()
        async with self._lock:
            registry = self._ensure_loaded()
            backend = self._node_red_backend
            if backend is None:
                raise ScenarioServiceError("Node-RED backend is unavailable.", status=503)
            required_ids = {
                "system-shower-comfort-controller",
                "system-small-corridor-light-controller",
                "system-tambur-adaptive-controller",
            }
            if (
                len(entries) != len(required_ids)
                or {str(getattr(item, "scenario_id", "")) for item in entries}
                != required_ids
            ):
                raise ScenarioServiceError("Managed switch migration manifest is invalid.", status=500)

            prepared: list[tuple[object, Scenario, ScenarioNodeRedMetadata, str, bool]] = []
            for item in entries:
                scenario_id = str(getattr(item, "scenario_id"))
                scenario = registry.scenario(scenario_id)
                if scenario is None:
                    raise ScenarioNotFoundError(scenario_id)
                if not scenario.protected:
                    raise ScenarioProtectedError(scenario_id)
                metadata = scenario.definition.node_red
                if (
                    scenario.definition.execution_backend is not ScenarioExecutionBackend.NODE_RED
                    or metadata is None
                    or not metadata.flow_id
                ):
                    raise ScenarioServiceError("Protected scenario topology is unavailable.", status=409)
                legacy_revision = int(getattr(item, "legacy_revision"))
                legacy_hash = str(getattr(item, "legacy_source_hash"))
                new_hash = str(getattr(item, "new_source_hash"))
                legacy_inputs = tuple(getattr(item, "legacy_input_target_ids"))
                new_inputs = tuple(getattr(item, "input_target_ids"))
                for target_id in new_inputs:
                    device = self._catalog.device(target_id)
                    if device is None or getattr(device, "target_id", None) != target_id:
                        raise ScenarioServiceError("Managed switch migration target is missing.", status=409)
                evidence = await backend.async_verify_managed_topology(scenario_id, metadata.flow_id)
                if evidence.get("topology") != getattr(item, "legacy_topology"):
                    raise ScenarioServiceError("Protected scenario topology changed.", status=409)
                deployed_hash = str(evidence.get("source_hash"))
                legacy_registry = (
                    scenario.revision == legacy_revision
                    and metadata.source_hash == legacy_hash
                    and metadata.input_target_ids == legacy_inputs
                )
                migrated_registry = (
                    scenario.revision == legacy_revision + 1
                    and metadata.source_hash == new_hash
                    and metadata.input_target_ids == new_inputs
                    and metadata.generated_by is ScenarioNodeRedGeneratedBy.HAUSMAN
                    and metadata.sync_status is ScenarioNodeRedSyncStatus.SYNCED
                )
                if migrated_registry and deployed_hash == new_hash:
                    prepared.append((item, scenario, metadata, deployed_hash, True))
                elif legacy_registry and deployed_hash in {legacy_hash, new_hash}:
                    prepared.append((item, scenario, metadata, deployed_hash, False))
                else:
                    raise ScenarioRevisionConflictError(
                        scenario_id,
                        expected_revision=legacy_revision,
                        current_revision=scenario.revision,
                        changed_fields=("definition.nodeRed",),
                        current_room_ids=scenario.room_ids,
                        current_action_ids=tuple(action.id for action in scenario.definition.actions),
                    )

            changed_sources: list[tuple[str, str, str, str]] = []
            replacements: dict[str, Scenario] = {}
            last_uses_prepare = False
            try:
                for item, scenario, metadata, deployed_hash, already_done in prepared:
                    if already_done:
                        replacements[scenario.id] = scenario
                        continue
                    new_hash = str(getattr(item, "new_source_hash"))
                    if deployed_hash != new_hash:
                        prepare = getattr(backend, "async_prepare_release_source", None)
                        if callable(prepare):
                            last_uses_prepare = False
                            result = await prepare(
                                scenario.id, scenario.definition, str(metadata.flow_id),
                                str(getattr(item, "source")), deployed_hash, self._catalog,
                            )
                            last_uses_prepare = True
                        else:
                            result = await backend.async_update_source(
                                scenario.id, scenario.definition, str(metadata.flow_id),
                                str(getattr(item, "source")), deployed_hash, self._catalog,
                                validate_only=False,
                            )
                        previous_source = result.get("previous_source")
                        if result.get("saved") is not True or not isinstance(previous_source, str):
                            raise ScenarioServiceError("Managed source update was not confirmed.", status=503)
                        changed_sources.append(
                            (scenario.id, str(metadata.flow_id), previous_source, new_hash)
                        )
                        if result.get("proposed_source_hash") != new_hash:
                            raise ScenarioServiceError("Managed source hash mismatch.", status=503)
                    next_metadata = replace(
                        metadata,
                        flow_revision=metadata.flow_revision + 1,
                        source_hash=new_hash,
                        generated_by=ScenarioNodeRedGeneratedBy.HAUSMAN,
                        sync_status=ScenarioNodeRedSyncStatus.SYNCED,
                        input_target_ids=tuple(getattr(item, "input_target_ids")),
                    )
                    replacements[scenario.id] = replace(
                        scenario,
                        definition=replace(scenario.definition, node_red=next_metadata),
                        revision=scenario.revision + 1,
                        updated_at=int(time.time() * 1000),
                    )
                if any(not item[4] for item in prepared):
                    next_registry = ScenarioRegistry(
                        scenarios=tuple(replacements.get(item.id, item) for item in registry.scenarios)
                    )
                    await self.async_save(next_registry)
                    self._registry = next_registry
                    self._scenario_content_revision_key = None
                    self._scenario_content_revision = None
                    commit = getattr(backend, "async_commit_last_prepare", None)
                    if callable(commit):
                        await commit()
            except Exception:
                try:
                    if last_uses_prepare and changed_sources:
                        compensate = getattr(backend, "async_compensate_last_prepare", None)
                        if callable(compensate):
                            await compensate()
                            changed_sources.pop()
                    for scenario_id, flow_id, previous_source, new_hash in reversed(changed_sources):
                        await backend.async_restore_source(
                            scenario_id, flow_id, previous_source,
                            expected_current_hash=new_hash,
                        )
                except NodeRedBackendError as rollback_error:
                    raise ScenarioServiceError(
                        "Managed migration failed and CAS rollback was rejected.", status=503
                    ) from rollback_error
                raise
            return "completed"

    async def async_verify_managed_switch_migration(
        self, entries: tuple[object, ...]
    ) -> str:
        """Verify all managed definitions and flows in one final CAS revision."""

        self._require_running()
        async with self._lock:
            registry = self._ensure_loaded()
            backend = self._node_red_backend
            if backend is None:
                raise ScenarioServiceError(
                    "Node-RED backend is unavailable.", status=503
                )
            required_ids = {
                "system-shower-comfort-controller",
                "system-small-corridor-light-controller",
                "system-tambur-adaptive-controller",
            }
            if (
                len(entries) != len(required_ids)
                or {str(getattr(item, "scenario_id", "")) for item in entries}
                != required_ids
            ):
                raise ScenarioServiceError(
                    "Managed switch migration manifest is invalid.", status=500
                )

            revisions: set[str] = set()
            for item in entries:
                scenario_id = str(getattr(item, "scenario_id"))
                scenario = registry.scenario(scenario_id)
                if scenario is None:
                    raise ScenarioNotFoundError(scenario_id)
                if not scenario.protected:
                    raise ScenarioProtectedError(scenario_id)
                metadata = scenario.definition.node_red
                expected_revision = int(getattr(item, "legacy_revision")) + 1
                expected_hash = str(getattr(item, "new_source_hash"))
                expected_inputs = tuple(getattr(item, "input_target_ids"))
                if (
                    scenario.revision != expected_revision
                    or scenario.definition.execution_backend
                    is not ScenarioExecutionBackend.NODE_RED
                    or metadata is None
                    or not metadata.flow_id
                    or metadata.source_hash != expected_hash
                    or metadata.input_target_ids != expected_inputs
                    or metadata.generated_by is not ScenarioNodeRedGeneratedBy.HAUSMAN
                    or metadata.sync_status is not ScenarioNodeRedSyncStatus.SYNCED
                ):
                    raise ScenarioRevisionConflictError(
                        scenario_id,
                        expected_revision=expected_revision,
                        current_revision=scenario.revision,
                        changed_fields=("definition.nodeRed",),
                        current_room_ids=scenario.room_ids,
                        current_action_ids=tuple(
                            action.id for action in scenario.definition.actions
                        ),
                    )
                for target_id in expected_inputs:
                    device = self._catalog.device(target_id)
                    if device is None or getattr(device, "target_id", None) != target_id:
                        raise ScenarioServiceError(
                            "Managed switch migration target is missing.", status=409
                        )
                evidence = await backend.async_verify_managed_topology(
                    scenario_id, metadata.flow_id
                )
                revision = evidence.get("revision")
                if (
                    evidence.get("topology")
                    != getattr(item, "legacy_topology")
                    or evidence.get("source_hash") != expected_hash
                    or not isinstance(revision, str)
                    or not revision
                ):
                    raise ScenarioServiceError(
                        "Protected scenario final CAS evidence changed.", status=409
                    )
                revisions.add(revision)
            if len(revisions) != 1:
                raise ScenarioServiceError(
                    "Protected scenario cross-scenario snapshot changed.", status=409
                )
            return next(iter(revisions))

    @property
    def performance_metrics(self) -> dict[str, dict[str, float | int | str]]:
        """Return bounded aggregate timings without payload or entity data."""

        return self._path_metrics.snapshot()

    def _record_path_latency(self, path: str, started: float) -> None:
        duration_ms = max(0.0, (self._monotonic() - started) * 1000)
        self._path_metrics.record(path, duration_ms)

    def _require_catalog_ready_for_actions(self) -> None:
        """Fail closed while late HA integrations can still change selectors."""

        if self._catalog_readiness.get("status") != "ready":
            raise ScenarioCatalogNotReadyError(self.catalog_readiness)

    def start_catalog_warmup(self) -> Callable[[], None]:
        """Start one managed, bounded refresh sequence after HA setup."""

        if self._catalog_warmup_task is None or self._catalog_warmup_task.done():
            # Background-задача, чтобы bootstrap HA не ждал 5-минутную
            # контрольную попытку сидирования внутри прогрева.
            create_task = getattr(
                self._hass, "async_create_background_task", None
            ) or getattr(self._hass, "async_create_task", None)
            coroutine = self._async_catalog_warmup()
            if callable(create_task):
                self._catalog_warmup_task = create_task(
                    coroutine,
                    "HausmanHub device catalog warm-up",
                )
            else:
                self._catalog_warmup_task = asyncio.create_task(coroutine)
        return self.cancel_catalog_warmup

    def cancel_catalog_warmup(self) -> None:
        """Cancel only the pending HausmanHub catalog warm-up task."""

        task = self._catalog_warmup_task
        self._catalog_warmup_task = None
        if task is not None and not task.done():
            task.cancel()

    def cancel_running_scenarios(self) -> None:
        """Stop old-entry runs so reload never replays a physical command."""

        self._stopping = True
        self.cancel_catalog_warmup()
        try:
            current = asyncio.current_task()
        except RuntimeError:  # pragma: no cover - HA invokes callbacks in its loop
            current = None
        tasks = set(self._active_run_calls)
        tasks.update(self._run_tasks.values())
        for task in tasks:
            if task is not current and not task.done():
                task.cancel()

    def _require_running(self) -> None:
        if self._stopping:
            raise ScenarioServiceError("Scenario service is stopping", status=503)

    async def async_run_typed_intent(
        self,
        *,
        binding: str,
        action: str,
        correlation_id: str,
        source: str,
        trigger_id: str,
        intent_receipt_id: str,
        raw_subtype: str,
        dedup_disposition: str,
    ) -> object:
        """Run one release-owned smart-switch intent after strict validation."""
        bindings = {
            "shower-cabinet": {"toggle_b2_down", "on_b2_down"},
            "tambur-light-group": {"on_down", "toggle_down", "off_up"},
        }
        if binding not in bindings or trigger_id not in bindings[binding]:
            raise ValueError("unknown smart-switch binding or trigger")
        if (
            source != "manual"
            or not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or intent_receipt_id != correlation_id
            or raw_subtype != trigger_id
            or dedup_disposition != "accepted"
        ):
            raise ValueError("smart-switch intent must be manual and correlated")
        allowed = {"shower-cabinet": {"toggle"}, "tambur-light-group": {"on", "off", "toggle"}}[binding]
        if action not in allowed:
            raise ValueError("smart-switch action does not match binding")
        consume = getattr(
            self._smart_switch_receipt_consumer,
            "async_consume_intent_receipt",
            None,
        )
        if not callable(consume):
            raise RuntimeError("smart-switch receipt consumer is unavailable")
        consumed = consume(
            binding=binding,
            action=action,
            correlation_id=correlation_id,
            source=source,
            trigger_id=trigger_id,
            intent_receipt_id=intent_receipt_id,
            raw_subtype=raw_subtype,
            dedup_disposition=dedup_disposition,
        )
        if inspect.isawaitable(consumed):
            consumed = await consumed
        if consumed is not True:
            raise ValueError("smart-switch receipt is unknown or already consumed")
        scenario_id = (
            "system-shower-comfort-controller"
            if binding == "shower-cabinet"
            else "system-tambur-adaptive-controller"
        )
        direct_user_intent = action
        if binding == "shower-cabinet":
            cabinet_state = self._trusted_typed_target_state(
                "entity_e7a7c61eec7bdff8"
            )
            if cabinet_state is None:
                return await self._async_record_typed_intent_skip(
                    scenario_id,
                    correlation_id,
                    reason="smart_switch_state_untrusted",
                    binding=binding,
                    action=action,
                    trigger_id=trigger_id,
                    intent_receipt_id=intent_receipt_id,
                    raw_subtype=raw_subtype,
                    dedup_disposition=dedup_disposition,
                )
            direct_user_intent = "off" if cabinet_state == "on" else "on"
        else:
            if self._trusted_typed_target_state("entity_b47991988cc6b9f3") != "on":
                return await self._async_record_typed_intent_skip(
                    scenario_id,
                    correlation_id,
                    reason="smart_switch_power_untrusted",
                    binding=binding,
                    action=action,
                    trigger_id=trigger_id,
                    intent_receipt_id=intent_receipt_id,
                    raw_subtype=raw_subtype,
                    dedup_disposition=dedup_disposition,
                )
            group_states = tuple(
                self._trusted_typed_target_state(target_id)
                for target_id in (
                    "entity_71859313239a14e4",
                    "entity_cd0098e5ff95da46",
                )
            )
            if any(state is None for state in group_states):
                return await self._async_record_typed_intent_skip(
                    scenario_id,
                    correlation_id,
                    reason="smart_switch_state_untrusted",
                    binding=binding,
                    action=action,
                    trigger_id=trigger_id,
                    intent_receipt_id=intent_receipt_id,
                    raw_subtype=raw_subtype,
                    dedup_disposition=dedup_disposition,
                )
            if action == "toggle":
                direct_user_intent = (
                    "off" if "on" in group_states else "on"
                )
            if direct_user_intent == "off":
                protection = self._manual_light_off_protection
                arm = getattr(
                    protection, "async_arm_release_owned_direct_off", None
                )
                if not callable(arm):
                    raise RuntimeError(
                        "release-owned direct-user light protection is unavailable"
                    )
                light_entity_ids = self._typed_entity_ids(
                    (
                        "entity_71859313239a14e4",
                        "entity_cd0098e5ff95da46",
                    )
                )
                sensor_entity_ids = self._typed_entity_ids(
                    (
                        "entity_156050daca86aa6c",
                        "entity_10b78187426f8485",
                    )
                )
                sensor_states = {
                    entity_id: self._hass.states.get(entity_id)
                    for entity_id in sensor_entity_ids
                }
                armed = arm(
                    request_id=intent_receipt_id,
                    light_entity_ids=light_entity_ids,
                    presence_sensor_entity_ids=sensor_entity_ids,
                    sensor_states=sensor_states,
                )
                if inspect.isawaitable(armed):
                    await armed
        trigger_context = {
            "source": source,
            "trigger_id": trigger_id,
            "recovery": False,
            "binding": binding,
            "typed_intent": action,
            "direct_user_intent": direct_user_intent,
            "intent_receipt_id": intent_receipt_id,
            "raw_subtype": raw_subtype,
            "dedup_disposition": dedup_disposition,
            "correlation_id": correlation_id,
        }
        return await self.async_run_scenario(
            scenario_id,
            correlation_id=correlation_id,
            trigger_context=trigger_context,
        )

    async def async_record_typed_intent_disposition(
        self,
        *,
        binding: str,
        correlation_id: str,
        source: str,
        trigger_id: str,
        intent_receipt_id: str,
        raw_subtype: str,
        dedup_disposition: str,
    ) -> dict[str, object]:
        """Journal an ignored or deduplicated release-owned trigger."""

        if (
            binding not in {"shower-cabinet", "tambur-light-group"}
            or source != "manual"
            or intent_receipt_id != correlation_id
            or raw_subtype != trigger_id
            or dedup_disposition not in {"deduplicated", "ignored"}
        ):
            raise ValueError("smart-switch disposition is invalid")
        scenario_id = (
            "system-shower-comfort-controller"
            if binding == "shower-cabinet"
            else "system-tambur-adaptive-controller"
        )
        return await self._async_record_typed_intent_skip(
            scenario_id,
            correlation_id,
            reason=(
                "smart_switch_deduplicated"
                if dedup_disposition == "deduplicated"
                else "smart_switch_release_ignored"
            ),
            binding=binding,
            action=None,
            trigger_id=trigger_id,
            intent_receipt_id=intent_receipt_id,
            raw_subtype=raw_subtype,
            dedup_disposition=dedup_disposition,
        )

    def _typed_entity_ids(self, target_ids: tuple[str, ...]) -> tuple[str, ...]:
        resolved: list[str] = []
        for target_id in target_ids:
            device = self._catalog.device(target_id)
            entity_id = getattr(device, "entity_id", None)
            if not isinstance(entity_id, str) or not entity_id:
                raise RuntimeError("release-owned smart-switch target is unavailable")
            resolved.append(entity_id)
        return tuple(resolved)

    def _trusted_typed_target_state(self, target_id: str) -> str | None:
        try:
            entity_id = self._typed_entity_ids((target_id,))[0]
        except RuntimeError:
            return None
        state = self._hass.states.get(entity_id)
        value = str(getattr(state, "state", "unknown")).strip().casefold()
        attributes = getattr(state, "attributes", {})
        observed = getattr(state, "last_updated", None) or getattr(
            state, "last_changed", None
        )
        if (
            value not in {"on", "off"}
            or not isinstance(observed, datetime)
            or getattr(state, "assumed_state", False) is True
            or getattr(state, "is_assumed_state", False) is True
            or isinstance(attributes, Mapping)
            and (
                attributes.get("assumed_state") is True
                or attributes.get("restored") is True
                or attributes.get("cached") is True
                or attributes.get("cache") is True
                or attributes.get("evidence_source") in {"restore", "cache"}
            )
        ):
            return None
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        return value if 0 <= age <= 300 else None

    async def _async_record_typed_intent_skip(
        self,
        scenario_id: str,
        correlation_id: str,
        *,
        reason: str,
        binding: str,
        action: str | None,
        trigger_id: str,
        intent_receipt_id: str,
        raw_subtype: str,
        dedup_disposition: str,
    ) -> dict[str, object]:
        trigger_context = {
            "source": "manual",
            "trigger_id": trigger_id,
            "recovery": False,
            "binding": binding,
            "typed_intent": (
                action
                if action is not None
                else "release"
                if dedup_disposition == "ignored"
                else "toggle"
            ),
            "direct_user_intent": "none",
            "intent_receipt_id": intent_receipt_id,
            "raw_subtype": raw_subtype,
            "dedup_disposition": dedup_disposition,
            "correlation_id": correlation_id,
        }
        result: dict[str, object] = {
            "scenario_id": scenario_id,
            "run_id": correlation_id,
            "execution_mode": "restart",
            "command_mode": "live",
            "status": "skipped",
            "reason": reason,
            "evidence_revision": None,
            "condition_results": [],
            "receipts": [],
            "accepted": False,
            "confirmed": False,
            "trigger_context": trigger_context,
        }
        await self._async_record_scenario_result(result)
        return result

    async def _async_catalog_warmup(self) -> None:
        """Refresh after late integrations without an unbounded polling loop."""

        for attempt, delay in enumerate(CATALOG_WARMUP_DELAYS_SECONDS, start=2):
            try:
                await self._sleep(delay)
                catalog = await self._async_replace_catalog()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning(
                    "HausmanHub device catalog warm-up attempt %s failed",
                    attempt,
                    exc_info=True,
                )
                self._catalog_readiness = {
                    **self._catalog_readiness,
                    "status": (
                        "degraded"
                        if attempt == CATALOG_WARMUP_MAX_ATTEMPTS
                        else "warming"
                    ),
                    "attempt": attempt,
                    "updatedAt": int(time.time() * 1000),
                    "reason": (
                        "warmup_failed"
                        if attempt == CATALOG_WARMUP_MAX_ATTEMPTS
                        else "initial_scan"
                    ),
                }
                continue
            self._catalog_readiness = {
                "status": (
                    "ready" if attempt == CATALOG_WARMUP_MAX_ATTEMPTS else "warming"
                ),
                "attempt": attempt,
                "maxAttempts": CATALOG_WARMUP_MAX_ATTEMPTS,
                "deviceCount": len(catalog.devices),
                "updatedAt": int(time.time() * 1000),
                "reason": (
                    "warmup_complete"
                    if attempt == CATALOG_WARMUP_MAX_ATTEMPTS
                    else "initial_scan"
                ),
            }
        final_count = len(self._catalog.devices)
        if final_count == 0:
            # После всех попыток каталог пуст: device_state триггеры молчат,
            # как в инциденте 19.08 после рестарта HA. Это не ошибка запроса,
            # поэтому отдельный читаемый warning для журнала.
            _LOGGER.warning(
                "HausmanHub device catalog still empty after warm-up; "
                "device_state triggers inactive"
            )
            return
        # Прогретый каталог есть: досеиваем системные сценарии (перенос
        # остатков Node-RED). Сидирование идемпотентно и не затирает
        # пользовательские правки. Поздние Zigbee2MQTT датчики могут быть
        # недоступны в первые минуты (их свойства тогда не проходят
        # валидацию), поэтому через 5 минут одна контрольная попытка.
        await self._async_seed_system_scenarios_guarded()
        await self._sleep(SYSTEM_SEED_RETRY_DELAY_SECONDS)
        await self._async_seed_system_scenarios_guarded()

    async def _async_seed_system_scenarios_guarded(self) -> None:
        """Seed missing system scenarios, logging instead of failing warm-up."""

        try:
            created = await async_seed_system_scenarios(self)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.warning("HausmanHub system scenario seeding failed", exc_info=True)
        else:
            if created:
                _LOGGER.info(
                    "HausmanHub system scenarios seeded: %s",
                    ", ".join(created),
                )

    async def _async_replace_catalog(self) -> ScenarioCatalog:
        """Run one serialized scan and atomically replace all consumers."""

        if self._catalog_loader is None:
            return self._catalog
        async with self._catalog_refresh_lock:
            catalog = await self._catalog_loader()
            if not isinstance(catalog, ScenarioCatalog):
                raise ScenarioServiceError(
                    "Catalog refresh returned invalid data", status=500
                )
            self._catalog = catalog
            replace_catalog = getattr(self._executor, "replace_catalog", None)
            if callable(replace_catalog):
                replace_catalog(catalog)
            return catalog

    async def async_refresh_catalog(self) -> ScenarioCatalog:
        """Refresh controllable HA entities without reloading the integration."""

        started = self._monotonic()
        try:
            catalog = await asyncio.wait_for(
                self._async_replace_catalog(),
                timeout=CATALOG_REFRESH_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            self._catalog_readiness.update(
                {
                    "status": "degraded",
                    "updatedAt": int(time.time() * 1000),
                    "reason": "warmup_failed",
                }
            )
            raise ScenarioServiceError("Catalog refresh timed out", status=503) from None
        finally:
            self._record_path_latency("catalog", started)
        self._catalog_readiness["deviceCount"] = len(catalog.devices)
        self._catalog_readiness["updatedAt"] = int(time.time() * 1000)
        return catalog

    async def async_list_scenarios(self) -> tuple[Scenario, ...]:
        """Return all stored scenarios ordered by title."""

        registry = self._ensure_loaded()
        return tuple(sorted(registry.scenarios, key=lambda scenario: scenario.title))

    async def async_scenario_health(self) -> dict[str, object]:
        """Inspect saved definitions against the live catalog without mutating them."""

        registry = self._ensure_loaded()
        await self.async_refresh_catalog()
        catalog = self._validation_catalog(registry)
        violations: list[dict[str, str]] = []
        for scenario in registry.scenarios:
            try:
                validate_scenario_definition(
                    scenario.definition,
                    catalog=catalog,
                    existing_scenario_id=scenario.id,
                )
            except ScenarioDefinitionViolation as error:
                code = error.code
                if code not in _HEALTH_RECOMMENDATIONS:
                    code = "invalid_definition"
                path = error.path if isinstance(error.path, str) else "definition"
                violations.append(
                    {
                        "scenarioId": scenario.id,
                        "path": path,
                        "code": code,
                        "recommendedAction": _HEALTH_RECOMMENDATIONS.get(
                            code, "review_step"
                        ),
                    }
                )
        return {
            "contract": {"name": "hausman-hub-scenario-health", "version": 1},
            "generatedAt": max(0, int(time.time() * 1000)),
            "status": "healthy" if not violations else "degraded",
            "violations": violations,
        }

    async def async_scenario_content_revision(self) -> str:
        """Return the revision for the stable scenario definition content."""

        scenarios = await self.async_list_scenarios()
        return self._scenario_content_revision_for(scenarios)

    def _scenario_content_revision_for(
        self, scenarios: tuple[Scenario, ...]
    ) -> str:
        """Hash definitions only after a scenario revision has changed."""

        cache_key = tuple(
            sorted((scenario.id, scenario.revision) for scenario in scenarios)
        )
        if (
            cache_key == self._scenario_content_revision_key
            and self._scenario_content_revision is not None
        ):
            return self._scenario_content_revision
        content = [
            _scenario_to_payload(scenario)
            for scenario in sorted(scenarios, key=lambda item: item.id)
        ]
        self._scenario_content_revision = hashlib.sha256(
            json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:32]
        self._scenario_content_revision_key = cache_key
        return self._scenario_content_revision

    async def async_scenario_list_payload(self) -> dict[str, object]:
        """Return the classified list with schedule and durable run evidence."""

        list_started = self._monotonic()
        scenarios = await self.async_list_scenarios()
        now = self._now_local()
        next_sunrise, next_sunset = self._sun_times()
        all_runs = compute_upcoming_runs(
            scenarios,
            now,
            next_sunrise,
            next_sunset,
            frozenset(),
        )
        active_by_scenario: dict[str, ScheduledRun] = {}
        skipped_by_scenario: dict[str, ScheduledRun] = {}
        for run in all_runs:
            destination = (
                skipped_by_scenario
                if run.skip_key in self._skipped_runs
                else active_by_scenario
            )
            destination.setdefault(run.scenario_id, run)

        last_results: dict[str, dict[str, object]] = {}
        snapshot = getattr(self._operation_journal, "snapshot", None)
        if callable(snapshot):
            result_started = self._monotonic()
            journal = snapshot(limit=512, source="scenario")
            records = journal.get("records", ()) if isinstance(journal, dict) else ()
            if isinstance(records, list):
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    trace = record.get("scenario")
                    if not isinstance(trace, dict):
                        continue
                    scenario_id = trace.get("scenario_id")
                    if not isinstance(scenario_id, str) or scenario_id in last_results:
                        continue
                    outcome = trace.get("outcome")
                    occurred_at = record.get("occurred_at")
                    correlation_id = record.get("correlation_id")
                    command_mode = trace.get("command_mode", "live")
                    if (
                        outcome
                        not in {"completed", "skipped", "cancelled", "partial", "failed"}
                        or type(occurred_at) is not int
                        or not isinstance(correlation_id, str)
                        or command_mode not in {"live", "shadow"}
                    ):
                        continue
                    last_results[scenario_id] = {
                        "outcome": outcome,
                        "occurredAt": occurred_at,
                        "correlationId": correlation_id,
                        "commandMode": command_mode,
                    }
            self._record_path_latency("last_result", result_started)

        payloads: list[dict[str, object]] = []
        for scenario in scenarios:
            payload = _scenario_to_payload(scenario)
            active = active_by_scenario.get(scenario.id)
            skipped = skipped_by_scenario.get(scenario.id)
            payload.update(
                {
                    "nextRun": active.run_at.isoformat() if active is not None else None,
                    "lastResult": last_results.get(scenario.id),
                    "temporaryException": (
                        {
                            "kind": "skip_once",
                            "triggerId": skipped.trigger_id,
                            "runAt": skipped.run_at.isoformat(),
                        }
                        if skipped is not None
                        else None
                    ),
                }
            )
            payloads.append(payload)
        content_revision = self._scenario_content_revision_for(scenarios)
        result = {
            "contract": {"name": "hausman-hub-scenario-list", "version": 1},
            "generatedAt": max(0, int(now.timestamp() * 1000)),
            "contentRevision": content_revision,
            "scenarios": payloads,
        }
        self._record_path_latency("list", list_started)
        return result

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

    async def async_update_scenario(self, payload: dict[str, Any]) -> Scenario:
        """Create or replace a scenario atomically."""

        self._require_running()
        if "revision" in payload:
            raise ScenarioValidationError(
                (ScenarioDefinitionViolation(
                    "revision is server-owned; send expectedRevision instead",
                    path="revision",
                ),)
            )
        async with self._lock:
            self._require_running()
            registry = self._ensure_loaded()
            raw_definition = payload.get("definition")
            if not isinstance(raw_definition, dict):
                raise ScenarioValidationError(
                    (
                        ScenarioDefinitionViolation(
                            "definition object is required",
                            path="definition",
                        ),
                    )
                )
            try:
                definition = ScenarioDefinition.from_payload(raw_definition)
            except ScenarioViolation as error:
                raise ScenarioValidationError(
                    (
                        ScenarioDefinitionViolation(
                            str(error),
                            path="definition",
                        ),
                    )
                ) from error
            raw_id = payload.get("id") or payload.get("scenarioId")
            raw_title = payload.get("title")
            if not isinstance(raw_id, str) or not raw_id:
                raise ScenarioValidationError(
                    (
                        ScenarioDefinitionViolation(
                            "scenario id is required",
                            path="id",
                        ),
                    )
                )
            if not isinstance(raw_title, str) or not raw_title.strip():
                raise ScenarioValidationError(
                    (
                        ScenarioDefinitionViolation(
                            "scenario title is required",
                            path="title",
                        ),
                    )
                )
            existing = registry.scenario(raw_id)
            definition = _server_owned_node_red_metadata(definition, existing)
            definition = _enforce_system_execution_mode(raw_id, definition)
            room_ids = self._room_ids_from_payload(payload)
            room_assignment_changed = (
                existing is None or room_ids != existing.room_ids
            )
            action_steps_changed = (
                existing is None or definition.actions != existing.definition.actions
            )
            definition_unchanged = (
                existing is not None and definition == existing.definition
            )
            if action_steps_changed or (room_assignment_changed and room_ids):
                self._require_catalog_ready_for_actions()
            if not definition_unchanged or room_assignment_changed:
                await self.async_refresh_catalog()
            if room_assignment_changed:
                available_room_ids = {
                    device.room_id
                    for device in self._catalog.devices.values()
                    if device.room_id
                }
                missing_room_ids = [
                    room_id for room_id in room_ids if room_id not in available_room_ids
                ]
                if missing_room_ids:
                    raise ScenarioValidationError(
                        tuple(
                            ScenarioDefinitionViolation(
                                "Выбранная комната больше не доступна. Обновите каталог и выберите комнату снова.",
                                code="missing_room",
                                path=f"roomIds.{index}",
                            )
                            for index, room_id in enumerate(room_ids)
                            if room_id in missing_room_ids
                        )
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
            expected_is_present = "expectedRevision" in payload
            expected_revision = payload.get("expectedRevision")
            if expected_is_present:
                if expected_revision is not None and (
                    isinstance(expected_revision, bool)
                    or not isinstance(expected_revision, int)
                    or expected_revision < 0
                ):
                    raise ScenarioValidationError(
                        (
                            ScenarioDefinitionViolation(
                                "expectedRevision must be a non-negative integer or null",
                                path="expectedRevision",
                            ),
                        )
                    )
                current_revision = existing.revision if existing is not None else None
                if (
                    (existing is None and expected_revision is not None)
                    or (
                        existing is not None
                        and expected_revision != current_revision
                    )
                ):
                    raise ScenarioRevisionConflictError(
                        raw_id,
                        expected_revision=expected_revision,
                        current_revision=current_revision,
                        changed_fields=self._conflict_changed_fields(payload, existing),
                        current_room_ids=existing.room_ids if existing is not None else (),
                        current_action_ids=(
                            tuple(action.id for action in existing.definition.actions)
                            if existing is not None
                            else ()
                        ),
                    )
            node_red_prepared = False
            if definition.execution_backend is ScenarioExecutionBackend.NODE_RED:
                if self._node_red_backend is None:
                    raise ScenarioValidationError(
                        (
                            ScenarioDefinitionViolation(
                                "Node-RED недоступен. Установите и запустите дополнение либо выберите выполнение в Hausman.",
                                code="node_red_unavailable",
                                path="definition.executionBackend",
                            ),
                        )
                    )
                try:
                    definition = await self._node_red_backend.async_prepare(
                        raw_id,
                        raw_title.strip(),
                        definition,
                        previous=(
                            existing.definition.node_red
                            if existing is not None
                            and existing.definition.execution_backend
                            is ScenarioExecutionBackend.NODE_RED
                            else None
                        ),
                    )
                    node_red_prepared = True
                except NodeRedBackendError as error:
                    raise ScenarioValidationError(
                        (
                            ScenarioDefinitionViolation(
                                str(error),
                                code="node_red_sync_failed",
                                path="definition.executionBackend",
                            ),
                        )
                    ) from error

            async def compensate_node_red_prepare() -> None:
                if not node_red_prepared:
                    return
                compensate = getattr(
                    self._node_red_backend, "async_compensate_last_prepare", None
                )
                if not callable(compensate):
                    return
                try:
                    await compensate()
                except NodeRedBackendError as error:
                    raise ScenarioServiceError(
                        "Node-RED flow was created but safe compensation failed.",
                        status=503,
                    ) from error

            group = _str_or_default(payload, "group", "custom")
            if existing is not None and existing.protected:
                group = existing.group
            try:
                new_scenario = Scenario.from_definition(
                    scenario_id=raw_id,
                    title=raw_title.strip(),
                    definition=definition,
                    enabled=enabled,
                    group=group,
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
                    action_description=_str_or_default(
                        payload, "actionDescription", ""
                    ),
                    updated_at=int(time.time() * 1000),
                    room_ids=room_ids,
                    protected=(existing.protected if existing is not None else None),
                    revision=(existing.revision + 1) if existing is not None else 0,
                )
            except ScenarioViolation as error:
                await compensate_node_red_prepare()
                raise ScenarioValidationError(
                    (ScenarioDefinitionViolation(str(error), path="scenario"),)
                ) from error
            scenarios = [s for s in registry.scenarios if s.id != new_scenario.id]
            scenarios.append(new_scenario)
            try:
                new_registry = ScenarioRegistry(scenarios=tuple(scenarios))
            except ScenarioViolation as error:
                await compensate_node_red_prepare()
                raise ScenarioValidationError(
                    (ScenarioDefinitionViolation(str(error), path="scenarios"),)
                ) from error
            self._require_running()
            try:
                await self.async_save(new_registry)
            except ScenarioServiceError:
                await compensate_node_red_prepare()
                raise
            if node_red_prepared:
                commit_prepare = getattr(
                    self._node_red_backend, "async_commit_last_prepare", None
                )
                if callable(commit_prepare):
                    await commit_prepare()
            self._registry = new_registry
            self._scenario_content_revision_key = None
            self._scenario_content_revision = None
            change = (
                "created"
                if existing is None
                else (
                    "enabled"
                    if not existing.enabled and new_scenario.enabled
                    else (
                        "disabled"
                        if existing.enabled and not new_scenario.enabled
                        else "updated"
                    )
                )
            )
            changed_fields = self._changed_fields(existing, new_scenario)
            self._publish_scenario_change(
                change,
                new_scenario.id,
                new_scenario.revision,
                changed_fields,
            )
            return new_scenario

    @staticmethod
    def _room_ids_from_payload(payload: Mapping[str, object]) -> tuple[str, ...]:
        """Read the additive multi-room field with legacy roomId fallback."""

        if "roomIds" in payload:
            raw = payload.get("roomIds")
            if not isinstance(raw, list):
                raise ScenarioValidationError(
                    (ScenarioDefinitionViolation(
                        "scenario roomIds must be an array",
                        path="roomIds",
                    ),)
                )
            if len(raw) > 32 or any(not isinstance(item, str) for item in raw):
                raise ScenarioValidationError(
                    (ScenarioDefinitionViolation(
                        "scenario roomIds must contain at most 32 room IDs",
                        path="roomIds",
                    ),)
                )
            result = tuple(raw)
            if len(result) != len(set(result)):
                raise ScenarioValidationError(
                    (ScenarioDefinitionViolation(
                        "scenario roomIds must be unique",
                        path="roomIds",
                    ),)
                )
            if "roomId" in payload:
                legacy = payload.get("roomId")
                if legacy is not None and not isinstance(legacy, str):
                    raise ScenarioValidationError(
                        (ScenarioDefinitionViolation(
                            "scenario roomId must be a string or null",
                            path="roomId",
                        ),)
                    )
                if legacy != (result[0] if result else None):
                    raise ScenarioValidationError(
                        (ScenarioDefinitionViolation(
                            "scenario roomId must mirror the first roomIds item",
                            path="roomId",
                        ),)
                    )
            return result
        legacy = payload.get("roomId")
        if legacy is None:
            return ()
        if not isinstance(legacy, str):
            raise ScenarioValidationError(
                (ScenarioDefinitionViolation(
                    "scenario roomId must be a string or null",
                    path="roomId",
                ),)
            )
        return (legacy,)

    @staticmethod
    def _changed_fields(existing: Scenario | None, current: Scenario) -> tuple[str, ...]:
        if existing is None:
            return ("created", "roomIds", "actions")
        fields: list[str] = []
        if existing.room_ids != current.room_ids:
            fields.append("roomIds")
        if existing.definition.actions != current.definition.actions:
            fields.append("actions")
        if existing.enabled != current.enabled:
            fields.append("enabled")
        if existing.title != current.title:
            fields.append("title")
        if existing.definition.triggers != current.definition.triggers:
            fields.append("triggers")
        if existing.definition.conditions != current.definition.conditions:
            fields.append("conditions")
        return tuple(fields or ("metadata",))

    @classmethod
    def _conflict_changed_fields(
        cls, payload: Mapping[str, object], current: Scenario | None
    ) -> tuple[str, ...]:
        if current is None:
            return ("deleted",)
        fields: list[str] = []
        try:
            if cls._room_ids_from_payload(payload) != current.room_ids:
                fields.append("roomIds")
        except ScenarioValidationError:
            fields.append("roomIds")
        raw_definition = payload.get("definition")
        if isinstance(raw_definition, dict):
            try:
                proposed = ScenarioDefinition.from_payload(raw_definition)
                if proposed.actions != current.definition.actions:
                    fields.append("actions")
            except ScenarioViolation:
                fields.append("actions")
        return tuple(fields or ("metadata",))

    async def async_test_scenario(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a scenario definition and return a dry-run trace."""

        started = self._monotonic()
        self._require_catalog_ready_for_actions()
        await self.async_refresh_catalog()
        registry = self._ensure_loaded()
        raw_definition = payload.get("definition", payload)
        try:
            definition = ScenarioDefinition.from_payload(raw_definition)
        except ScenarioViolation as error:
            raise ScenarioValidationError(
                (
                    ScenarioDefinitionViolation(
                        str(error),
                        path="definition",
                    ),
                )
            ) from error
        try:
            validate_scenario_definition(
                definition,
                catalog=self._validation_catalog(registry),
                existing_scenario_id=payload.get("id") or payload.get("scenarioId"),
            )
        except ScenarioDefinitionViolation as error:
            raise ScenarioValidationError((error,)) from error

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
                trigger_context=(
                    payload.get("triggerContext")
                    if isinstance(payload.get("triggerContext"), Mapping)
                    else None
                ),
                scenario_title=str(payload.get("title") or ""),
                scenario_description=str(payload.get("description") or ""),
                scenario_action_description=str(
                    payload.get("actionDescription") or ""
                ),
                scenario_icon=str(payload.get("icon") or ""),
            )
        result = {
            "valid": True,
            "action_count": action_count,
            "referenced_scenario_ids": sorted(referenced),
            "plan": plan,
            "report": _dry_run_report(definition, self._catalog, registry, plan),
        }
        self._record_path_latency("dry_run", started)
        return result

    async def async_delete_scenario(self, scenario_id: str) -> None:
        """Delete a scenario unless it is referenced by others."""

        self._require_running()
        async with self._lock:
            self._require_running()
            registry = self._ensure_loaded()
            if not any(s.id == scenario_id for s in registry.scenarios):
                raise ScenarioNotFoundError(scenario_id)

            target = registry.scenario(scenario_id)
            if target is not None and target.protected and target.enabled:
                raise ScenarioProtectedError(scenario_id)

            for scenario in registry.scenarios:
                for action in scenario.definition.actions:
                    if (
                        action.type == "run_scenario"
                        and action.scenario_id == scenario_id
                    ):
                        raise ScenarioReferencedError(scenario_id)

            scenarios = tuple(s for s in registry.scenarios if s.id != scenario_id)
            new_registry = ScenarioRegistry(scenarios=scenarios)
            self._require_running()
            await self.async_save(new_registry)
            self._registry = new_registry
            self._scenario_content_revision_key = None
            self._scenario_content_revision = None
            assert target is not None
            self._publish_scenario_change("deleted", target.id, target.revision + 1)

    def _publish_scenario_change(
        self,
        change: str,
        scenario_id: str,
        revision: int,
        changed_fields: tuple[str, ...] = (),
    ) -> None:
        """Notify connected editors after the durable registry write succeeds."""

        if self._scenario_change_publisher is None:
            return
        try:
            self._scenario_change_publisher(
                change, scenario_id, revision, changed_fields
            )
        except Exception:  # pragma: no cover - live fan-out must not undo storage
            _LOGGER.exception("Failed to publish scenario change invalidation")

    async def async_run_scenario(
        self,
        scenario_id: str,
        visited: frozenset[str] | None = None,
        *,
        correlation_id: str | None = None,
        trigger_context: Mapping[str, object] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute a scenario via the configured executor."""

        self._require_running()
        caller = asyncio.current_task()
        registered = False
        if caller is not None and caller not in self._active_run_calls:
            self._active_run_calls.add(caller)
            registered = True
        try:
            return await self._async_run_scenario(
                scenario_id,
                visited,
                correlation_id=correlation_id,
                trigger_context=trigger_context,
                dry_run=dry_run,
            )
        finally:
            if registered and caller is not None:
                self._active_run_calls.discard(caller)

    async def _async_run_scenario(
        self,
        scenario_id: str,
        visited: frozenset[str] | None = None,
        *,
        correlation_id: str | None = None,
        trigger_context: Mapping[str, object] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute one already registered run call."""

        await self.async_refresh_catalog()
        scenario = await self.async_get_scenario(scenario_id)
        if not scenario.enabled:
            raise ScenarioServiceError(
                f"Scenario {scenario_id!r} is disabled", status=409
            )
        if self._executor is None:
            raise ScenarioServiceError("Executor not configured", status=500)
        run_id = correlation_id or self._executor.new_run_id()
        resolved_trigger_context = (
            dict(trigger_context)
            if trigger_context is not None
            else {"source": "manual", "trigger_id": None, "recovery": False}
        )

        async def execute() -> dict[str, Any]:
            result = await self._executor.async_execute(
                scenario.definition,
                run_id,
                scenario_id=scenario.id,
                visited_scenarios=visited,
                dry_run=(
                    dry_run
                    or scenario.definition.command_mode is ScenarioCommandMode.SHADOW
                ),
                trigger_context=resolved_trigger_context,
                scenario_title=scenario.title,
                scenario_description=scenario.description,
                scenario_action_description=scenario.action_description,
                scenario_icon=scenario.icon,
            )
            result.setdefault("scenario_id", scenario.id)
            result.setdefault("run_id", run_id)
            result.setdefault(
                "execution_mode", scenario.definition.execution_mode.value
            )
            result.setdefault("command_mode", scenario.definition.command_mode.value)
            result.setdefault("evidence_revision", None)
            result.setdefault(
                "trigger_context",
                resolved_trigger_context,
            )
            result.setdefault("accepted", result.get("status") == "completed")
            result.setdefault("confirmed", False)
            await self._async_record_scenario_result(result)
            return result

        if scenario.definition.execution_mode is ScenarioExecutionMode.QUEUED:
            queue_lock = self._queue_locks.setdefault(scenario.id, asyncio.Lock())
            queued = False
            async with self._run_lock:
                if queue_lock.locked():
                    waiting = self._queue_waiters.get(scenario.id, 0)
                    if waiting >= scenario.definition.queue_limit:
                        result = {
                            "scenario_id": scenario.id,
                            "run_id": run_id,
                            "execution_mode": scenario.definition.execution_mode.value,
                            "command_mode": scenario.definition.command_mode.value,
                            "status": "skipped",
                            "reason": "scenario_queue_full",
                            "receipts": [],
                            "accepted": False,
                            "confirmed": False,
                            "trigger_context": resolved_trigger_context,
                        }
                        await self._async_record_scenario_result(result)
                        return result
                    self._queue_waiters[scenario.id] = waiting + 1
                    queued = True
            try:
                async with queue_lock:
                    return await execute()
            finally:
                if queued:
                    async with self._run_lock:
                        waiting = self._queue_waiters.get(scenario.id, 0) - 1
                        if waiting > 0:
                            self._queue_waiters[scenario.id] = waiting
                        else:
                            self._queue_waiters.pop(scenario.id, None)

        async with self._run_lock:
            previous = self._run_tasks.get(scenario.id)
            if previous is not None and not previous.done():
                if scenario.definition.execution_mode is ScenarioExecutionMode.SINGLE:
                    result = {
                        "scenario_id": scenario.id,
                        "run_id": run_id,
                        "execution_mode": scenario.definition.execution_mode.value,
                        "command_mode": scenario.definition.command_mode.value,
                        "status": "skipped",
                        "reason": "scenario_already_running",
                        "evidence_revision": None,
                        "receipts": [],
                        "accepted": False,
                        "confirmed": False,
                        "trigger_context": resolved_trigger_context,
                    }
                    await self._async_record_scenario_result(result)
                    return result
                previous.cancel()
            task = asyncio.create_task(execute())
            self._run_tasks[scenario.id] = task

        try:
            return await task
        except asyncio.CancelledError:
            async with self._run_lock:
                replaced = self._run_tasks.get(scenario.id) is not task
            if not replaced:
                raise
            result = {
                "scenario_id": scenario.id,
                "run_id": run_id,
                "execution_mode": scenario.definition.execution_mode.value,
                "command_mode": scenario.definition.command_mode.value,
                "status": "cancelled",
                "reason": "restarted_by_new_trigger",
                "evidence_revision": None,
                "receipts": [],
                "accepted": False,
                "confirmed": False,
                "trigger_context": resolved_trigger_context,
            }
            return result
        finally:
            async with self._run_lock:
                if self._run_tasks.get(scenario.id) is task:
                    self._run_tasks.pop(scenario.id, None)

    async def _async_record_scenario_result(self, result: dict[str, Any]) -> None:
        """Persist every manual, scheduled and nested run without raw HA ids."""

        append = getattr(self._operation_journal, "async_append", None)
        if not callable(append):
            return
        try:
            await append(scenario_operation_receipt(result))
        except Exception:
            _LOGGER.warning("scenario run journal append failed", exc_info=True)

    async def async_execute_device_action(
        self,
        target_id: str,
        action_id: str,
        value: object | None = None,
        *,
        correlation_id: str | None = None,
        dry_run: bool = False,
        dangerous_authorized: bool = False,
        force_new_readback: bool = False,
        automatic_reassert: bool = False,
        reassert_claim_id: str | None = None,
        request_id: str | None = None,
        expected_evidence_revision: str | None = None,
        expected_evidence_sequence: int | None = None,
        expected_entity_id: str | None = None,
        expected_domain: str | None = None,
        expected_service: str | None = None,
        intercom_release_required: bool = False,
    ) -> dict[str, Any]:
        """Execute one catalog action through the shared strict executor."""

        await self.async_refresh_catalog()
        if self._executor is None:
            raise ScenarioServiceError("Executor not configured", status=500)
        options: dict[str, object] = {}
        if correlation_id is not None:
            options["correlation_id"] = correlation_id
        if dry_run:
            options["dry_run"] = True
        if dangerous_authorized:
            options["dangerous_authorized"] = True
        if force_new_readback:
            options["force_new_readback"] = True
        if automatic_reassert:
            options["automatic_reassert"] = True
        if reassert_claim_id is not None:
            options["reassert_claim_id"] = reassert_claim_id
        if request_id is not None:
            options["request_id"] = request_id
        if expected_evidence_revision is not None:
            options["expected_evidence_revision"] = expected_evidence_revision
        if expected_evidence_sequence is not None:
            options["expected_evidence_sequence"] = expected_evidence_sequence
        device = self._catalog.device(target_id)
        action = device.action(action_id) if device is not None else None
        if (
            expected_entity_id is not None
            and getattr(device, "entity_id", None) != expected_entity_id
        ) or (
            expected_domain is not None
            and getattr(action, "domain", None) != expected_domain
        ) or (
            expected_service is not None
            and getattr(action, "service", None) != expected_service
        ):
            raise ScenarioServiceError(
                "Device action dispatch descriptor changed", status=409
            )
        if expected_entity_id is not None:
            options["expected_entity_id"] = expected_entity_id
        if expected_domain is not None:
            options["expected_domain"] = expected_domain
        if expected_service is not None:
            options["expected_service"] = expected_service
        current_intercom_action = self._is_intercom_action(target_id, action_id)
        current_contextual_action = self.is_contextually_dangerous_action(
            target_id, action_id
        )
        if intercom_release_required and (
            not dangerous_authorized
            or not current_intercom_action
            or request_id is None
            or expected_entity_id is None
        ):
            raise ScenarioServiceError(
                "Intercom release dispatch descriptor changed", status=409
            )
        if dangerous_authorized and current_contextual_action:
            options["contextually_dangerous"] = True

        if intercom_release_required or (
            dangerous_authorized and current_intercom_action
        ):

            async def arm_intercom_release() -> None:
                try:
                    if not self._is_intercom_action(target_id, action_id):
                        raise RuntimeError(
                            "intercom release dispatch descriptor changed"
                        )
                    await self.async_arm_intercom_release(
                        target_id,
                        expected_entity_id=expected_entity_id,
                        expected_request_id=(
                            f"{request_id}.release"
                            if request_id is not None
                            else None
                        ),
                    )
                except Exception:
                    await self.async_cancel_intercom_release(
                        target_id,
                        expected_entity_id=expected_entity_id,
                        expected_request_id=(
                            f"{request_id}.release"
                            if request_id is not None
                            else None
                        ),
                    )
                    raise

            options["before_dispatch"] = (
                arm_intercom_release
            )
        try:
            return await self._executor.async_execute_device_action(
                target_id,
                action_id,
                value,
                **options,
            )
        except Exception:
            if intercom_release_required:
                await self.async_cancel_intercom_release(
                    target_id,
                    expected_entity_id=expected_entity_id,
                    expected_request_id=(
                        f"{request_id}.release" if request_id is not None else None
                    ),
                )
            raise

    async def async_execute_device_action_batch(
        self,
        actions: list[Mapping[str, object]],
        *,
        correlation_id: str,
        dangerous_authorized: frozenset[tuple[str, str]] = frozenset(),
        intercom_release_required: frozenset[tuple[str, str]] = frozenset(),
        request_ids: tuple[str, ...] | None = None,
        dispatch_contexts: (
            tuple[tuple[str, str, tuple[str, ...], str] | None, ...] | None
        ) = None,
    ) -> list[dict[str, Any]]:
        """Run one bounded ordered batch and preserve every target receipt."""

        if not 1 <= len(actions) <= 64:
            raise ScenarioServiceError("Action batch must contain 1 to 64 items")
        if request_ids is not None and (
            len(request_ids) != len(actions)
            or len(set(request_ids)) != len(request_ids)
            or not all(isinstance(item, str) and item for item in request_ids)
        ):
            raise ScenarioServiceError("Action batch request ids are invalid")
        action_keys: set[tuple[str, str]] = set()
        normalized_actions: list[
            tuple[
                str,
                str,
                object | None,
                bool,
                str | None,
                str | None,
                int | None,
            ]
        ] = []
        for item in actions:
            target_id = item.get("targetId")
            action_id = item.get("actionId")
            if not isinstance(target_id, str) or not isinstance(action_id, str):
                raise ScenarioServiceError("Action batch item is invalid")
            action_key = (target_id, action_id)
            if action_key in action_keys:
                raise ScenarioServiceError(
                    "Action batch contains a duplicate target and action"
                )
            action_keys.add(action_key)
            dry_run = item.get("dryRun", False)
            if type(dry_run) is not bool:
                raise ScenarioServiceError("Action batch item dryRun is invalid")
            normalized_actions.append(
                (
                    target_id,
                    action_id,
                    item.get("value"),
                    dry_run,
                    (
                        str(item["reassertKey"])
                        if isinstance(item.get("reassertKey"), str)
                        else None
                    ),
                    (
                        str(item["expectedEvidenceRevision"])
                        if isinstance(item.get("expectedEvidenceRevision"), str)
                        else None
                    ),
                    (
                        int(item["expectedEvidenceSequence"])
                        if type(item.get("expectedEvidenceSequence")) is int
                        else None
                    ),
                )
            )

        await self.async_refresh_catalog()
        if self._executor is None:
            raise ScenarioServiceError("Executor not configured", status=500)
        receipts: list[dict[str, Any]] = []
        for index, (
            target_id,
            action_id,
            value,
            dry_run,
            reassert_key,
            expected_revision,
            expected_sequence,
        ) in enumerate(normalized_actions):
            options: dict[str, object] = {"correlation_id": correlation_id}
            context = (
                dispatch_contexts[index]
                if dispatch_contexts is not None and index < len(dispatch_contexts)
                else None
            )
            if context is not None:
                options["expected_entity_id"] = context[0]
                options["expected_domain"] = context[1]
                options["expected_service"] = context[3]
            if request_ids is not None:
                options["request_id"] = request_ids[index]
            if dry_run:
                options["dry_run"] = True
            release_required = (
                target_id, action_id
            ) in intercom_release_required
            if release_required and (
                target_id, action_id
            ) not in dangerous_authorized:
                raise ScenarioServiceError(
                    "Intercom release authorization is missing", status=409
                )
            if (target_id, action_id) in dangerous_authorized:
                options["dangerous_authorized"] = True
                current_intercom_action = self._is_intercom_action(
                    target_id, action_id
                )
                current_contextual_action = self.is_contextually_dangerous_action(
                    target_id, action_id
                )
                if release_required and (
                    not current_intercom_action
                    or request_ids is None
                    or context is None
                ):
                    raise ScenarioServiceError(
                        "Intercom release dispatch descriptor changed", status=409
                    )
                if current_contextual_action:
                    options["contextually_dangerous"] = True
                if release_required or current_intercom_action:
                    expected_entity_id = context[0] if context is not None else None
                    expected_request_id = (
                        f"{request_ids[index]}.release"
                        if request_ids is not None
                        else None
                    )
                    async def arm_intercom_release(
                        target_id: str = target_id,
                        action_id: str = action_id,
                        expected_entity_id: str | None = expected_entity_id,
                        expected_request_id: str | None = expected_request_id,
                    ) -> None:
                        if not self._is_intercom_action(
                            target_id, action_id
                        ):
                            raise RuntimeError(
                                "intercom release dispatch descriptor changed"
                            )
                        await self.async_arm_intercom_release(
                            target_id,
                            expected_entity_id=expected_entity_id,
                            expected_request_id=expected_request_id,
                        )

                    options["before_dispatch"] = arm_intercom_release
            if reassert_key is not None:
                options["force_new_readback"] = True
                options["automatic_reassert"] = True
                options["reassert_claim_id"] = reassert_key
                options["expected_evidence_revision"] = expected_revision
                options["expected_evidence_sequence"] = expected_sequence
            receipts.append(
                await self._executor.async_execute_device_action(
                    target_id,
                    action_id,
                    value,
                    **options,
                )
            )
        return receipts

    async def async_resolve_device_action(
        self,
        target_id: str,
        action_id: str,
    ) -> tuple[str, str] | None:
        """Resolve an approved catalog action without executing it."""

        catalog = await self.async_refresh_catalog()
        device = catalog.device(target_id)
        action = device.action(action_id) if device is not None else None
        if device is None or action is None:
            return None
        return device.entity_id, action.domain

    async def async_resolve_device_action_context(
        self,
        target_id: str,
        action_id: str,
    ) -> tuple[str, str, tuple[str, ...], str] | None:
        """Resolve registry-owned identity and current allowlisted actions."""

        catalog = await self.async_refresh_catalog()
        device = catalog.device(target_id)
        action = device.action(action_id) if device is not None else None
        if device is None or action is None:
            return None
        return (
            device.entity_id,
            action.domain,
            tuple(item.action_id for item in device.actions),
            action.service,
        )

    async def async_schedule_intercom_release(
        self,
        target_id: str,
        action_id: str,
        *,
        correlation_id: str | None = None,
        request_id: str | None = None,
    ) -> int | None:
        """Hold the intercom relay open, then always return it to off.

        The door strike must be energised only for a short pulse.
        Returns the hold length in seconds when a release was scheduled.
        """

        if (
            action_id not in {"turn_on", "toggle"}
            or self._intercom_entity_resolver is None
        ):
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
        if self._intercom_release_obligation is not None:
            return INTERCOM_RELEASE_SECONDS
        if self._intercom_release_cancel is not None:
            return None
        self._intercom_release_entity = entity_id
        self._intercom_release_context = {
            "correlationId": correlation_id or request_id or "intercom-release",
            "requestId": f"{request_id or 'intercom'}.release",
            "targetId": target_id,
        }

        async def _async_release(_now: Any) -> None:
            self._intercom_release_cancel = None
            self._intercom_release_entity = None
            context = self._intercom_release_context
            self._intercom_release_context = None
            release = self._intercom_release_callable()
            if release is None:
                _LOGGER.warning(
                    "intercom release for %s skipped: executor is not configured",
                    entity_id,
                )
                self._publish_intercom_release(
                    context, confirmed=False, reason="release_executor_unavailable"
                )
                return
            try:
                released = await release(entity_id)
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "intercom release turn_off failed for %s", entity_id, exc_info=True
                )
                self._publish_intercom_release(
                    context, confirmed=False, reason="release_failed"
                )
            else:
                self._publish_intercom_release(
                    context,
                    confirmed=released is True,
                    reason=(
                        "relay_released"
                        if released is True
                        else "release_not_confirmed"
                    ),
                )

        self._intercom_release_cancel = self._call_later(
            self._hass, INTERCOM_RELEASE_SECONDS, _async_release
        )
        return INTERCOM_RELEASE_SECONDS

    async def async_prepare_intercom_release(
        self,
        target_id: str,
        action_id: str,
        *,
        correlation_id: str,
        request_id: str,
        expected_entity_id: str | None = None,
    ) -> int | None:
        """Persist the relay-off deadline before a dangerous turn-on dispatch."""

        obligation = self._intercom_release_obligation
        if obligation is None or not self._is_intercom_action(target_id, action_id):
            return None
        device = self._catalog.device(target_id)
        entity_id = getattr(device, "entity_id", None)
        if not isinstance(entity_id, str) or not entity_id.startswith("switch."):
            return None
        if expected_entity_id is not None and entity_id != expected_entity_id:
            return None
        return await obligation.async_prepare(
            target_id=target_id,
            entity_id=entity_id,
            correlation_id=correlation_id,
            request_id=request_id,
        )

    async def async_arm_intercom_release(
        self,
        target_id: str,
        *,
        expected_entity_id: str | None = None,
        expected_request_id: str | None = None,
    ) -> None:
        """Verify a fresh persisted off deadline at the dispatch boundary."""

        obligation = self._intercom_release_obligation
        if obligation is None:
            raise RuntimeError("intercom release obligation is unavailable")
        await obligation.async_arm(
            target_id,
            expected_entity_id=expected_entity_id,
            expected_request_id=expected_request_id,
        )

    async def async_cancel_intercom_release(
        self,
        target_id: str,
        *,
        expected_entity_id: str | None = None,
        expected_request_id: str | None = None,
    ) -> bool:
        """Clear only an unarmed release prepared for a failed dispatch."""

        obligation = self._intercom_release_obligation
        if obligation is None:
            return False
        return await obligation.async_cancel(
            target_id,
            expected_entity_id=expected_entity_id,
            expected_request_id=expected_request_id,
            unarmed_only=True,
        )

    async def async_reconcile_intercom_release(
        self, record: Mapping[str, object]
    ) -> bool:
        """Execute and publish one restored or due relay-off obligation."""

        entity_id = record.get("entityId")
        if not isinstance(entity_id, str):
            return False
        release = self._intercom_release_callable()
        released = False
        if release is not None:
            try:
                released = await release(entity_id)
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "durable intercom release failed for %s",
                    entity_id,
                    exc_info=True,
                )
        self._publish_intercom_release(
            {
                "correlationId": str(record.get("correlationId")),
                "requestId": str(record.get("requestId")),
                "targetId": str(record.get("targetId")),
            },
            confirmed=released,
            reason="relay_released" if released else "release_not_confirmed",
        )
        return released

    async def async_is_intercom_action(
        self, target_id: str, action_id: str
    ) -> bool:
        """Classify only the explicitly configured intercom target."""

        await self.async_refresh_catalog()
        return self._is_intercom_action(target_id, action_id)

    def _is_intercom_action(self, target_id: str, action_id: str) -> bool:
        """Classify the configured relay without including other hazards."""

        if self._intercom_entity_resolver is None:
            return False
        configured = self._intercom_entity_resolver()
        device = self._catalog.device(target_id)
        entity_id = getattr(device, "entity_id", None)
        action = device.action(action_id) if device is not None else None
        return bool(
            configured
            and action_id in {"turn_on", "toggle"}
            and isinstance(entity_id, str)
            and configured in {entity_id, target_id}
            and action is not None
        )

    def is_external_cover_action(self, target_id: str, action_id: str) -> bool:
        """Recognize movement of gates and external covers, not room curtains."""

        if action_id not in {"open_cover", "close_cover", "set_position"}:
            return False
        device = self._catalog.device(target_id)
        action = device.action(action_id) if device is not None else None
        if device is None or action is None or action.domain != "cover":
            return False
        device_type = str(getattr(device, "device_type", "") or "").casefold()
        if device_type in {"garage", "garage_door", "gate"}:
            return True
        # ``capability_name`` is the generic catalog label "Шторы и ворота"
        # for every Home Assistant cover.  It is not device identity evidence.
        identity = " ".join(
            str(value or "").casefold()
            for value in (
                getattr(device, "entity_id", ""),
                getattr(device, "name", ""),
                getattr(device, "physical_name", ""),
            )
        )
        return any(
            marker in identity
            for marker in (
                "garage",
                "gate",
                "external",
                "exterior",
                "ворот",
                "гараж",
                "калитк",
                "шлагбаум",
                "наружн",
                "въезд",
            )
        )

    def is_contextually_dangerous_action(
        self, target_id: str, action_id: str
    ) -> bool:
        """Classify device-specific hazards without causing catalog I/O."""

        return self._is_intercom_action(
            target_id, action_id
        ) or self.is_external_cover_action(
            target_id, action_id
        )

    def publish_intercom_dry_run(
        self, *, target_id: str, correlation_id: str, request_id: str
    ) -> None:
        """Publish a redacted command-free safety receipt."""

        self._publish_intercom_release(
            {
                "correlationId": correlation_id,
                "requestId": f"{request_id}.release",
                "targetId": target_id,
            },
            confirmed=False,
            reason="dry_run",
            outcome="dry_run",
        )

    def cancel_intercom_release(self, *, turn_off_now: bool = False) -> None:
        """Cancel a pending intercom release, optionally switching off now."""

        entity_id = self._intercom_release_entity
        context = self._intercom_release_context
        self._cancel_intercom_release()
        release = self._intercom_release_callable()
        if turn_off_now and entity_id is not None and release is not None:
            async def _release_now() -> None:
                try:
                    released = await release(entity_id)
                except Exception:  # noqa: BLE001
                    self._publish_intercom_release(
                        context, confirmed=False, reason="release_failed"
                    )
                else:
                    self._publish_intercom_release(
                        context,
                        confirmed=released is True,
                        reason=(
                            "relay_released"
                            if released is True
                            else "release_not_confirmed"
                        ),
                    )

            self._hass.async_create_task(_release_now())

    def _intercom_release_callable(self) -> Callable[[str], Awaitable[bool]] | None:
        release = getattr(self._executor, "async_release_intercom_switch", None)
        return release if callable(release) else None

    def _cancel_intercom_release(self) -> None:
        cancel = self._intercom_release_cancel
        self._intercom_release_cancel = None
        self._intercom_release_entity = None
        self._intercom_release_context = None
        if cancel is not None:
            cancel()

    def _publish_intercom_release(
        self,
        context: dict[str, object] | None,
        *,
        confirmed: bool,
        reason: str,
        outcome: str | None = None,
    ) -> None:
        if context is None or self._intercom_release_publisher is None:
            return
        self._intercom_release_publisher(
            {
                "contract": {
                    "name": "hausman-hub-intercom-release-receipt",
                    "version": 1,
                },
                **context,
                "accepted": outcome != "release_failed",
                "confirmed": confirmed,
                "outcome": outcome or ("released" if confirmed else "release_failed"),
                "holdSeconds": INTERCOM_RELEASE_SECONDS,
                "occurredAt": int(time.time() * 1000),
                "reason": reason,
            }
        )
