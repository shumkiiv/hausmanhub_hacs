"""Application service for HausmanHub scenario CRUD and execution."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..domain.scenarios import (
    Scenario,
    ScenarioCommandMode,
    ScenarioComparison,
    ScenarioDefinition,
    ScenarioExecutionMode,
    ScenarioRegistry,
    ScenarioTriggerType,
    ScenarioViolation,
    _scenario_to_payload,
)
from .operation_journal import scenario_operation_receipt
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
    return {
        "contract": {"name": "hausman-hub-scenario-dry-run", "version": 1},
        "status": status,
        "summary": summary,
        "conditionCount": len(conditions),
        "actionCount": len(steps),
        "commandSent": False,
        "conditions": conditions,
        "steps": steps,
    }


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
    ) -> None:
        super().__init__(
            "Scenario changed on another client. Reload it before saving.",
            status=409,
        )
        self.scenario_id = scenario_id
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class ScenarioCatalogNotReadyError(ScenarioServiceError):
    """Action steps cannot be changed until the live catalog is trustworthy."""

    def __init__(self, readiness: Mapping[str, object]) -> None:
        super().__init__(
            "Action steps cannot be changed while the device catalog is warming up.",
            status=409,
        )
        self.readiness = dict(readiness)


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
        intercom_release_publisher: Callable[[dict[str, Any]], None] | None = None,
        scenario_change_publisher: Callable[[str, str, int], None] | None = None,
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
        self._intercom_release_publisher = intercom_release_publisher
        self._scenario_change_publisher = scenario_change_publisher
        self._skipped_runs: set[str] = set()
        self._intercom_release_cancel: Callable[[], None] | None = None
        self._intercom_release_entity: str | None = None
        self._intercom_release_context: dict[str, object] | None = None
        self._registry: ScenarioRegistry | None = None
        self._lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
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

    @property
    def catalog_readiness(self) -> dict[str, object]:
        """Return a redacted snapshot of startup catalog readiness."""

        return dict(self._catalog_readiness)

    def current_catalog(self) -> ScenarioCatalog:
        """Return the live catalog snapshot without triggering a rescan."""

        return self._catalog

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

        catalog = await self._async_replace_catalog()
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

    async def async_scenario_list_payload(self) -> dict[str, object]:
        """Return the classified list with schedule and durable run evidence."""

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
        return {
            "contract": {"name": "hausman-hub-scenario-list", "version": 1},
            "generatedAt": max(0, int(now.timestamp() * 1000)),
            "scenarios": payloads,
        }

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

        async with self._lock:
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
            action_steps_changed = (
                existing is None or definition.actions != existing.definition.actions
            )
            definition_unchanged = (
                existing is not None and definition == existing.definition
            )
            if action_steps_changed:
                self._require_catalog_ready_for_actions()
            if not definition_unchanged:
                await self.async_refresh_catalog()
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
                    )
            group = _str_or_default(payload, "group", "custom")
            if existing is not None and existing.protected:
                group = existing.group
            room_id = payload.get("roomId")
            if room_id is not None and not isinstance(room_id, str):
                raise ScenarioValidationError(
                    (
                        ScenarioDefinitionViolation(
                            "scenario roomId must be a string or null",
                            path="roomId",
                        ),
                    )
                )
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
                trigger_description=_str_or_default(payload, "triggerDescription", ""),
                condition_description=_str_or_default(
                    payload, "conditionDescription", ""
                ),
                action_description=_str_or_default(payload, "actionDescription", ""),
                updated_at=int(time.time() * 1000),
                room_id=room_id,
                protected=(existing.protected if existing is not None else None),
                revision=(existing.revision + 1) if existing is not None else 0,
            )
            scenarios = [s for s in registry.scenarios if s.id != new_scenario.id]
            scenarios.append(new_scenario)
            new_registry = ScenarioRegistry(scenarios=tuple(scenarios))
            await self.async_save(new_registry)
            self._registry = new_registry
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
            self._publish_scenario_change(change, new_scenario.id, new_scenario.revision)
            return new_scenario

    async def async_test_scenario(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a scenario definition and return a dry-run trace."""

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
            )
        return {
            "valid": True,
            "action_count": action_count,
            "referenced_scenario_ids": sorted(referenced),
            "plan": plan,
            "report": _dry_run_report(definition, self._catalog, registry, plan),
        }

    async def async_delete_scenario(self, scenario_id: str) -> None:
        """Delete a scenario unless it is referenced by others."""

        async with self._lock:
            registry = self._ensure_loaded()
            if not any(s.id == scenario_id for s in registry.scenarios):
                raise ScenarioNotFoundError(scenario_id)

            target = registry.scenario(scenario_id)
            if target is not None and target.protected:
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
            await self.async_save(new_registry)
            self._registry = new_registry
            assert target is not None
            self._publish_scenario_change("deleted", target.id, target.revision + 1)

    def _publish_scenario_change(
        self, change: str, scenario_id: str, revision: int
    ) -> None:
        """Notify connected editors after the durable registry write succeeds."""

        if self._scenario_change_publisher is None:
            return
        try:
            self._scenario_change_publisher(change, scenario_id, revision)
        except Exception:  # pragma: no cover - live fan-out must not undo storage
            _LOGGER.exception("Failed to publish scenario change invalidation")

    async def async_run_scenario(
        self,
        scenario_id: str,
        visited: frozenset[str] | None = None,
        *,
        correlation_id: str | None = None,
        trigger_context: Mapping[str, object] | None = None,
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
                    scenario.definition.command_mode is ScenarioCommandMode.SHADOW
                ),
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
        return await self._executor.async_execute_device_action(
            target_id,
            action_id,
            value,
            **options,
        )

    async def async_execute_device_action_batch(
        self,
        actions: list[Mapping[str, object]],
        *,
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        """Run one bounded ordered batch and preserve every target receipt."""

        if not 1 <= len(actions) <= 64:
            raise ScenarioServiceError("Action batch must contain 1 to 64 items")
        action_keys: set[tuple[str, str]] = set()
        normalized_actions: list[tuple[str, str, object | None]] = []
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
            normalized_actions.append((target_id, action_id, item.get("value")))

        await self.async_refresh_catalog()
        if self._executor is None:
            raise ScenarioServiceError("Executor not configured", status=500)
        receipts: list[dict[str, Any]] = []
        for target_id, action_id, value in normalized_actions:
            receipts.append(
                await self._executor.async_execute_device_action(
                    target_id,
                    action_id,
                    value,
                    correlation_id=correlation_id,
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

    async def async_schedule_intercom_release(
        self,
        target_id: str,
        action_id: str,
        *,
        correlation_id: str | None = None,
        request_id: str | None = None,
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

    async def async_is_intercom_action(
        self, target_id: str, action_id: str
    ) -> bool:
        """Classify only the explicitly configured intercom target."""

        await self.async_refresh_catalog()
        if self._intercom_entity_resolver is None:
            return False
        configured = self._intercom_entity_resolver()
        device = self._catalog.device(target_id)
        entity_id = getattr(device, "entity_id", None)
        action = device.action(action_id) if device is not None else None
        return bool(
            configured
            and isinstance(entity_id, str)
            and configured in {entity_id, target_id}
            and action is not None
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
