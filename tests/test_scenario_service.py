"""Tests for the HausmanHub scenario application service."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.scenario_service import (
    ScenarioNotFoundError,
    ScenarioProtectedError,
    ScenarioReferencedError,
    ScenarioService,
    ScenarioServiceError,
    ScenarioValidationError,
    _public_device_name,
)
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
    ScenarioDeviceProperty,
    ScenarioPropertyOption,
)
from custom_components.hausman_hub.domain.scenarios import ScenarioRegistry


SCENARIO_LIST_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "custom_components/hausman_hub/contracts/v1/scenario-list.schema.json"
)
SCENARIO_DRY_RUN_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "custom_components/hausman_hub/contracts/v1/scenario-dry-run-result.schema.json"
)


class _FakeStore:
    def __init__(self) -> None:
        self._data: ScenarioRegistry | None = None

    async def async_load(self) -> ScenarioRegistry | None:
        return self._data

    async def async_save(self, registry: ScenarioRegistry) -> None:
        self._data = registry


class _FakeExecutor:
    def __init__(self) -> None:
        self.runs: list[tuple[Any, str]] = []
        self.catalogs: list[ScenarioCatalog] = []
        self.device_actions: list[tuple[str, str, object]] = []
        self.correlated_device_actions: list[tuple[str, str, object, str]] = []
        self.dry_run_device_actions: list[tuple[str, str, object]] = []
        self._counter = 0

    def replace_catalog(self, catalog: ScenarioCatalog) -> None:
        self.catalogs.append(catalog)

    async def async_execute_device_action(
        self,
        target_id: str,
        action_id: str,
        value: object | None = None,
        *,
        correlation_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self.device_actions.append((target_id, action_id, value))
        if correlation_id is not None:
            self.correlated_device_actions.append(
                (target_id, action_id, value, correlation_id)
            )
        if dry_run:
            self.dry_run_device_actions.append((target_id, action_id, value))
        return {
            "accepted": True,
            "confirmed": not dry_run,
            "status": "accepted" if dry_run else "confirmed",
            "dryRun": dry_run,
        }

    def new_run_id(self) -> str:
        self._counter += 1
        return f"run-{self._counter}"

    async def async_execute(
        self,
        definition: Any,
        run_id: str,
        *,
        scenario_id: str = "",
        visited_scenarios: frozenset[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self.runs.append((definition, run_id, dry_run))
        return {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "status": "completed",
            "receipts": [],
        }


class _RestartExecutor(_FakeExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = asyncio.Event()
        self.first_cancelled = asyncio.Event()

    async def async_execute(
        self,
        definition: Any,
        run_id: str,
        *,
        scenario_id: str = "",
        visited_scenarios: frozenset[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self.runs.append((definition, run_id, dry_run))
        if len(self.runs) == 1:
            self.first_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.first_cancelled.set()
                raise
        return {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "status": "completed",
            "receipts": [],
        }


class _QueueExecutor(_FakeExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def async_execute(
        self,
        definition: Any,
        run_id: str,
        *,
        scenario_id: str = "",
        visited_scenarios: frozenset[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self.runs.append((definition, run_id, dry_run))
        if len(self.runs) == 1:
            self.first_started.set()
            await self.release_first.wait()
        return {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "status": "completed",
            "receipts": [],
        }


class _FakeJournal:
    def __init__(self, records: list[dict[str, object]] | None = None) -> None:
        self.receipts: list[dict[str, object]] = []
        self.records = records or []

    async def async_append(self, receipt: dict[str, object]) -> None:
        self.receipts.append(receipt)

    def snapshot(self, **_: object) -> dict[str, object]:
        return {"records": self.records}


def _catalog() -> ScenarioCatalog:
    entry = ScenarioDeviceEntry(
        target_id="device_abc",
        name="Light",
        entity_id="light.living_room",
        actions=(
            ScenarioDeviceAction(
                action_id="turn_on",
                title="On",
                domain="light",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
        ),
    )
    return ScenarioCatalog(devices={"device_abc": entry}, scenarios={})


def _valid_definition(scenario_id: str = "scenario_1") -> dict[str, Any]:
    return {
        "version": 1,
        "executionMode": "single",
        "triggers": [{"id": "t1", "type": "time", "value": "08:00"}],
        "conditions": [],
        "actions": [
            {
                "id": "a1",
                "type": "device_action",
                "targetId": "device_abc",
                "actionId": "turn_on",
                "command": {
                    "domain": "light",
                    "service": "turn_on",
                    "entity_id": "light.living_room",
                },
            }
        ],
    }


def _valid_payload(scenario_id: str = "scenario_1") -> dict[str, Any]:
    return {
        "id": scenario_id,
        "title": f"Scenario {scenario_id}",
        "definition": _valid_definition(scenario_id),
        "enabled": True,
    }


class ScenarioServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = _FakeStore()
        self.catalog = _catalog()
        self.executor = _FakeExecutor()
        self.service = ScenarioService(None, self.store, self.catalog, self.executor)
        await self.service.async_load()

    async def test_load_empty_registry(self) -> None:
        scenarios = await self.service.async_list_scenarios()
        self.assertEqual(scenarios, ())

    async def test_update_and_list(self) -> None:
        scenario = await self.service.async_update_scenario(_valid_payload())
        self.assertEqual(scenario.id, "scenario_1")
        self.assertEqual(scenario.title, "Scenario scenario_1")

        listed = await self.service.async_list_scenarios()
        self.assertEqual(len(listed), 1)
        self.assertTrue(self.store._data)
        self.assertEqual(self.store._data.version, 1)

    async def test_classified_list_exposes_room_next_run_and_no_false_result(self) -> None:
        now = datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)
        service = ScenarioService(
            None,
            self.store,
            self.catalog,
            self.executor,
            now_provider=lambda: now,
            sun_times_provider=lambda: (None, None),
            operation_journal=_FakeJournal(),
        )
        await service.async_load()
        payload = _valid_payload("morning_light")
        payload["roomId"] = "living"
        await service.async_update_scenario(payload)

        result = await service.async_scenario_list_payload()
        item = result["scenarios"][0]

        Draft202012Validator(
            json.loads(SCENARIO_LIST_SCHEMA.read_text(encoding="utf-8"))
        ).validate(result)

        self.assertEqual(
            {"name": "hausman-hub-scenario-list", "version": 1},
            result["contract"],
        )
        self.assertEqual("automatic", item["activationKind"])
        self.assertEqual("living", item["roomId"])
        self.assertEqual("2026-08-22T08:00:00+00:00", item["nextRun"])
        self.assertIsNone(item["lastResult"])
        self.assertIsNone(item["temporaryException"])

    async def test_classified_list_keeps_full_ordered_definition_for_tablets(self) -> None:
        service = ScenarioService(
            None,
            self.store,
            self.catalog,
            self.executor,
            now_provider=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
        await service.async_load()
        payload = _valid_payload("tablet_editor")
        payload["enabled"] = False
        payload["definition"]["actions"] = [
            {
                "id": "turn-on",
                "type": "device_action",
                "targetId": "device_abc",
                "actionId": "turn_on",
                "command": {
                    "domain": "light",
                    "service": "turn_on",
                    "entity_id": "light.living_room",
                },
            },
            {"id": "pause", "type": "delay", "delaySeconds": 30},
            {
                "id": "notice",
                "type": "notification",
                "message": "Свет включён",
            },
        ]
        await service.async_update_scenario(payload)

        result = await service.async_scenario_list_payload()
        item = result["scenarios"][0]

        Draft202012Validator(
            json.loads(SCENARIO_LIST_SCHEMA.read_text(encoding="utf-8"))
        ).validate(result)
        self.assertFalse(item["enabled"])
        self.assertEqual(
            ["turn-on", "pause", "notice"],
            [action["id"] for action in item["definition"]["actions"]],
        )

    async def test_classified_list_exposes_durable_result_and_skip_once(self) -> None:
        now = datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)
        journal = _FakeJournal(
            [
                {
                    "correlation_id": "scenario.morning-light.1",
                    "occurred_at": 1787381000000,
                    "scenario": {
                        "scenario_id": "morning_light",
                        "outcome": "completed",
                        "command_mode": "shadow",
                    },
                }
            ]
        )
        service = ScenarioService(
            None,
            self.store,
            self.catalog,
            self.executor,
            now_provider=lambda: now,
            sun_times_provider=lambda: (None, None),
            operation_journal=journal,
        )
        await service.async_load()
        await service.async_update_scenario(_valid_payload("morning_light"))
        cancelled = await service.async_cancel_upcoming(
            "morning_light",
            "t1",
            "2026-08-22T08:00:00+00:00",
        )

        result = await service.async_scenario_list_payload()
        item = result["scenarios"][0]

        self.assertTrue(cancelled["cancelled"])
        self.assertIsNone(item["nextRun"])
        self.assertEqual(
            {
                "kind": "skip_once",
                "triggerId": "t1",
                "runAt": "2026-08-22T08:00:00+00:00",
            },
            item["temporaryException"],
        )
        self.assertEqual(
            {
                "outcome": "completed",
                "occurredAt": 1787381000000,
                "correlationId": "scenario.morning-light.1",
                "commandMode": "shadow",
            },
            item["lastResult"],
        )

    async def test_event_trigger_projection_contains_only_enabled_filter(self) -> None:
        payload = _valid_payload("event_button")
        payload["definition"]["triggers"] = [
            {
                "id": "event-1",
                "type": "event",
                "eventType": "zha_event",
                "eventData": {"device_id": "kids-button", "command": "single"},
            }
        ]
        await self.service.async_update_scenario(payload)
        self.assertEqual(
            self.service.event_trigger_items(),
            (
                (
                    "event_button",
                    "event-1",
                    "zha_event",
                    {"device_id": "kids-button", "command": "single"},
                ),
            ),
        )

    async def test_update_overwrites_existing(self) -> None:
        await self.service.async_update_scenario(_valid_payload())
        payload = _valid_payload()
        payload["title"] = "Updated"
        scenario = await self.service.async_update_scenario(payload)
        self.assertEqual(scenario.title, "Updated")
        self.assertEqual(len(await self.service.async_list_scenarios()), 1)

    async def test_update_validation_error(self) -> None:
        payload = _valid_payload()
        payload["definition"]["actions"] = []
        with self.assertRaises(ScenarioValidationError) as ctx:
            await self.service.async_update_scenario(payload)
        self.assertTrue(ctx.exception.violations)

    async def test_test_scenario_returns_dry_run(self) -> None:
        result = await self.service.async_test_scenario(_valid_payload())
        self.assertTrue(result["valid"])
        self.assertEqual(result["action_count"], 1)
        Draft202012Validator(
            json.loads(SCENARIO_DRY_RUN_SCHEMA.read_text(encoding="utf-8"))
        ).validate(result)
        self.assertFalse(result["report"]["commandSent"])
        self.assertEqual("Light", result["report"]["steps"][0]["targetName"])
        self.assertNotIn("entity", json.dumps(result["report"]).casefold())
        technical = ScenarioDeviceEntry(
            "technical", "light.raw_entity", "light.raw_entity", ()
        )
        self.assertEqual("Устройство", _public_device_name(technical, "Устройство"))

    async def test_editor_golden_journeys_dry_run_without_technical_ids(self) -> None:
        state_property = ScenarioDeviceProperty(
            property_id="state",
            label="Состояние",
            value_type="enum",
            comparisons=("equals", "not_equals", "changed"),
            options=(
                ScenarioPropertyOption("on", "Включено"),
                ScenarioPropertyOption("off", "Выключено"),
            ),
        )
        humidity_property = ScenarioDeviceProperty(
            property_id="state",
            label="Влажность",
            value_type="number",
            comparisons=("equals", "not_equals", "above", "below", "changed"),
            unit="%",
        )
        devices = {
            "motion": ScenarioDeviceEntry(
                "motion", "Датчик движения", "binary_sensor.motion", (),
                room_name="Коридор", properties=(state_property,),
            ),
            "light": ScenarioDeviceEntry(
                "light", "Дополнительный свет", "light.corridor",
                (ScenarioDeviceAction("turn_on", "Включить", "light", "turn_on", frozenset()),),
                room_name="Коридор", properties=(state_property,),
            ),
            "curtain": ScenarioDeviceEntry(
                "curtain", "Шторы", "cover.living",
                (
                    ScenarioDeviceAction(
                        "close_cover", "Закрыть", "cover", "close_cover", frozenset()
                    ),
                ),
                room_name="Гостиная", properties=(state_property,),
            ),
            "humidity": ScenarioDeviceEntry(
                "humidity", "Датчик влажности", "sensor.bathroom_humidity", (),
                room_name="Ванная", properties=(humidity_property,),
            ),
            "fan": ScenarioDeviceEntry(
                "fan", "Вытяжка", "fan.bathroom",
                (ScenarioDeviceAction("turn_on", "Включить", "fan", "turn_on", frozenset()),),
                room_name="Ванная", properties=(state_property,),
            ),
        }
        service = ScenarioService(
            None,
            _FakeStore(),
            ScenarioCatalog(devices=devices, scenarios={}),
            _FakeExecutor(),
        )
        await service.async_load()
        definitions = (
            {
                "version": 1, "executionMode": "single",
                "triggers": [{
                    "id": "motion_on", "type": "device_state",
                    "targetId": "motion", "property": "state",
                    "comparison": "equals", "value": "on",
                }],
                "conditions": [],
                "actions": [{
                    "id": "light_on", "type": "device_action",
                    "targetId": "light", "actionId": "turn_on",
                }],
            },
            {
                "version": 1, "executionMode": "single",
                "triggers": [{"id": "sunset", "type": "sunset"}],
                "conditions": [],
                "actions": [{
                    "id": "curtains_close", "type": "device_action",
                    "targetId": "curtain", "actionId": "close_cover",
                }],
            },
            {
                "version": 1, "executionMode": "single",
                "triggers": [{
                    "id": "humid", "type": "device_state",
                    "targetId": "humidity", "property": "state",
                    "comparison": "above", "value": 65,
                }],
                "conditions": [],
                "actions": [{
                    "id": "fan_on", "type": "device_action",
                    "targetId": "fan", "actionId": "turn_on",
                }],
            },
        )
        for definition in definitions:
            result = await service.async_test_scenario({"definition": definition})
            with self.subTest(definition=definition["triggers"][0]["type"]):
                self.assertFalse(result["report"]["commandSent"])
                self.assertEqual("planned", result["report"]["steps"][0]["status"])
                serialized = json.dumps(result["report"]).casefold()
                self.assertNotIn("entity_id", serialized)
                self.assertNotIn("binary_sensor.", serialized)

    async def test_test_scenario_wraps_stale_catalog_target(self) -> None:
        payload = _valid_payload()
        payload["definition"]["actions"][0]["targetId"] = "missing_device"

        with self.assertRaises(ScenarioValidationError) as ctx:
            await self.service.async_test_scenario(payload)

        self.assertEqual(400, ctx.exception.status)
        self.assertEqual(
            "definition.actions[0].targetId",
            ctx.exception.violations[0].path,
        )

    async def test_device_action_refreshes_catalog_before_execution(self) -> None:
        refreshed = ScenarioCatalog(
            devices={
                "late_light": ScenarioDeviceEntry(
                    target_id="late_light",
                    name="Late Zigbee light",
                    entity_id="light.late",
                    actions=(
                        ScenarioDeviceAction(
                            action_id="turn_on",
                            title="On",
                            domain="light",
                            service="turn_on",
                            allowed_fields=frozenset(),
                        ),
                    ),
                )
            },
            scenarios={},
        )
        refreshes = 0

        async def load_catalog() -> ScenarioCatalog:
            nonlocal refreshes
            refreshes += 1
            return refreshed

        service = ScenarioService(
            None,
            self.store,
            self.catalog,
            self.executor,
            catalog_loader=load_catalog,
        )
        await service.async_load()

        receipt = await service.async_execute_device_action(
            "late_light",
            "turn_on",
        )

        self.assertEqual(1, refreshes)
        self.assertIs(refreshed, service._catalog)
        self.assertEqual([refreshed], self.executor.catalogs)
        self.assertEqual(
            [("late_light", "turn_on", None)], self.executor.device_actions
        )
        self.assertTrue(receipt["confirmed"])

    async def test_device_action_forwards_dry_run_to_executor(self) -> None:
        receipt = await self.service.async_execute_device_action(
            "device_1",
            "turn_on",
            correlation_id="intercom-dry-run",
            dry_run=True,
        )

        self.assertTrue(receipt["dryRun"])
        self.assertFalse(receipt["confirmed"])
        self.assertEqual(
            [("device_1", "turn_on", None)],
            self.executor.dry_run_device_actions,
        )

    async def test_device_action_batch_preserves_each_target_receipt(self) -> None:
        receipts = await self.service.async_execute_device_action_batch(
            [
                {"targetId": "device_1", "actionId": "turn_on"},
                {"targetId": "device_1", "actionId": "turn_off"},
            ],
            correlation_id="room-off-1",
        )

        self.assertEqual(2, len(receipts))
        self.assertEqual(
            [
                ("device_1", "turn_on", None, "room-off-1"),
                ("device_1", "turn_off", None, "room-off-1"),
            ],
            self.executor.correlated_device_actions,
        )

    async def test_device_action_batch_rejects_duplicate_target_action(self) -> None:
        with self.assertRaisesRegex(
            ScenarioServiceError,
            "duplicate target and action",
        ):
            await self.service.async_execute_device_action_batch(
                [
                    {"targetId": "device_1", "actionId": "turn_off"},
                    {"targetId": "device_1", "actionId": "turn_off"},
                ],
                correlation_id="room-off-duplicate",
            )

        self.assertEqual([], self.executor.correlated_device_actions)

    async def test_catalog_warmup_is_bounded_and_publishes_ready(self) -> None:
        refreshes = 0
        delays: list[float] = []

        async def skip_delay(delay: float) -> None:
            delays.append(delay)

        async def load_catalog() -> ScenarioCatalog:
            nonlocal refreshes
            refreshes += 1
            return self.catalog

        service = ScenarioService(
            None,
            self.store,
            ScenarioCatalog(devices={}, scenarios={}),
            self.executor,
            catalog_loader=load_catalog,
            sleep=skip_delay,
        )

        await service._async_catalog_warmup()

        self.assertEqual([1.0, 3.0, 8.0, 300.0], delays)
        self.assertEqual(3, refreshes)
        self.assertEqual(
            {
                "status": "ready",
                "attempt": 4,
                "maxAttempts": 4,
                "deviceCount": 1,
                "reason": "warmup_complete",
            },
            {
                key: value
                for key, value in service.catalog_readiness.items()
                if key != "updatedAt"
            },
        )

    async def test_catalog_warmup_exhaustion_is_degraded(self) -> None:
        refreshes = 0

        async def skip_delay(_: float) -> None:
            return None

        async def load_catalog() -> ScenarioCatalog:
            nonlocal refreshes
            refreshes += 1
            raise RuntimeError("late integration unavailable")

        service = ScenarioService(
            None,
            self.store,
            self.catalog,
            self.executor,
            catalog_loader=load_catalog,
            sleep=skip_delay,
        )

        await service._async_catalog_warmup()

        self.assertEqual(3, refreshes)
        self.assertEqual("degraded", service.catalog_readiness["status"])
        self.assertEqual(4, service.catalog_readiness["attempt"])
        self.assertEqual("warmup_failed", service.catalog_readiness["reason"])
        self.assertEqual(1, service.catalog_readiness["deviceCount"])

    async def test_catalog_warmup_cancel_stops_pending_refreshes(self) -> None:
        refreshes = 0

        async def load_catalog() -> ScenarioCatalog:
            nonlocal refreshes
            refreshes += 1
            return self.catalog

        service = ScenarioService(
            None,
            self.store,
            self.catalog,
            self.executor,
            catalog_loader=load_catalog,
        )
        cancel = service.start_catalog_warmup()
        task = service._catalog_warmup_task
        self.assertIsNotNone(task)

        cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(0, refreshes)
        self.assertIsNone(service._catalog_warmup_task)

    async def test_catalog_warmup_does_not_start_twice(self) -> None:
        async def load_catalog() -> ScenarioCatalog:
            return self.catalog

        service = ScenarioService(
            None,
            self.store,
            self.catalog,
            self.executor,
            catalog_loader=load_catalog,
        )
        service.start_catalog_warmup()
        first = service._catalog_warmup_task

        service.start_catalog_warmup()

        self.assertIs(first, service._catalog_warmup_task)
        service.cancel_catalog_warmup()
        with self.assertRaises(asyncio.CancelledError):
            await first

    async def test_catalog_warmup_warns_when_catalog_stays_empty(self) -> None:
        async def skip_delay(_: float) -> None:
            return None

        async def load_catalog() -> ScenarioCatalog:
            return ScenarioCatalog(devices={}, scenarios={})

        service = ScenarioService(
            None,
            self.store,
            ScenarioCatalog(devices={}, scenarios={}),
            self.executor,
            catalog_loader=load_catalog,
            sleep=skip_delay,
        )

        with self.assertLogs(
            "custom_components.hausman_hub.application.scenario_service",
            level="WARNING",
        ) as logs:
            await service._async_catalog_warmup()

        self.assertTrue(
            any(
                "device catalog still empty after warm-up" in line
                for line in logs.output
            ),
            logs.output,
        )
        self.assertEqual("ready", service.catalog_readiness["status"])
        self.assertEqual(0, service.catalog_readiness["deviceCount"])

    async def test_device_action_can_be_resolved_without_execution(self) -> None:
        resolved = await self.service.async_resolve_device_action(
            "device_abc", "turn_on"
        )
        missing = await self.service.async_resolve_device_action(
            "device_abc", "turn_off"
        )

        self.assertEqual(("light.living_room", "light"), resolved)
        self.assertIsNone(missing)
        self.assertEqual([], self.executor.device_actions)

    async def test_get_scenario_found(self) -> None:
        await self.service.async_update_scenario(_valid_payload())
        scenario = await self.service.async_get_scenario("scenario_1")
        self.assertEqual(scenario.id, "scenario_1")

    async def test_get_scenario_not_found(self) -> None:
        with self.assertRaises(ScenarioNotFoundError):
            await self.service.async_get_scenario("missing")

    async def test_delete_scenario(self) -> None:
        await self.service.async_update_scenario(_valid_payload())
        await self.service.async_delete_scenario("scenario_1")
        self.assertEqual(len(await self.service.async_list_scenarios()), 0)

    async def test_delete_protected_system_scenario_raises_409(self) -> None:
        payload = _valid_payload("system_safety")
        payload["group"] = "system"
        scenario = await self.service.async_update_scenario(payload)
        self.assertTrue(scenario.protected)

        with self.assertRaises(ScenarioProtectedError) as ctx:
            await self.service.async_delete_scenario("system_safety")

        self.assertEqual(409, ctx.exception.status)

    async def test_delete_missing_raises_404(self) -> None:
        with self.assertRaises(ScenarioNotFoundError):
            await self.service.async_delete_scenario("missing")

    async def test_delete_referenced_scenario_raises_409(self) -> None:
        await self.service.async_update_scenario(_valid_payload("base"))
        payload = _valid_payload("runner")
        payload["definition"]["actions"] = [
            {
                "id": "a1",
                "type": "run_scenario",
                "scenarioId": "base",
            }
        ]
        await self.service.async_update_scenario(payload)
        with self.assertRaises(ScenarioReferencedError):
            await self.service.async_delete_scenario("base")

    async def test_run_scenario_calls_executor(self) -> None:
        await self.service.async_update_scenario(_valid_payload())
        result = await self.service.async_run_scenario("scenario_1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.executor.runs), 1)

    async def test_run_scenario_preserves_supplied_correlation_id(self) -> None:
        await self.service.async_update_scenario(_valid_payload())

        result = await self.service.async_run_scenario(
            "scenario_1",
            correlation_id="corr.scenario.tablet-1",
        )

        self.assertEqual("corr.scenario.tablet-1", result["run_id"])
        self.assertEqual("corr.scenario.tablet-1", self.executor.runs[0][1])

    async def test_shadow_scenario_uses_dry_run_and_journal_marker(self) -> None:
        journal = _FakeJournal()
        service = ScenarioService(
            None,
            self.store,
            self.catalog,
            self.executor,
            operation_journal=journal,
        )
        await service.async_load()
        payload = _valid_payload("shadow_scenario")
        payload["definition"]["commandMode"] = "shadow"
        await service.async_update_scenario(payload)

        result = await service.async_run_scenario("shadow_scenario")

        self.assertTrue(self.executor.runs[0][2])
        self.assertEqual("shadow", result["command_mode"])
        self.assertEqual("shadow", journal.receipts[0]["scenario"]["command_mode"])
        self.assertFalse(journal.receipts[0]["confirmed"])

    async def test_restart_mode_restarts_the_five_minute_timer(self) -> None:
        executor = _RestartExecutor()
        journal = _FakeJournal()
        service = ScenarioService(
            None, self.store, self.catalog, executor, operation_journal=journal
        )
        await service.async_load()
        payload = _valid_payload()
        payload["definition"]["executionMode"] = "restart"
        await service.async_update_scenario(payload)

        first = asyncio.create_task(service.async_run_scenario("scenario_1"))
        await asyncio.wait_for(executor.first_started.wait(), timeout=0.1)
        second_result = await service.async_run_scenario("scenario_1")
        first_result = await asyncio.wait_for(first, timeout=0.1)

        self.assertTrue(executor.first_cancelled.is_set())
        self.assertEqual("cancelled", first_result["status"])
        self.assertEqual("completed", second_result["status"])
        self.assertEqual(2, len(executor.runs))
        self.assertEqual(1, len(journal.receipts))
        self.assertEqual("completed", journal.receipts[0]["scenario"]["outcome"])

    async def test_single_mode_skips_parallel_duplicate(self) -> None:
        executor = _RestartExecutor()
        service = ScenarioService(None, self.store, self.catalog, executor)
        await service.async_load()
        await service.async_update_scenario(_valid_payload())

        first = asyncio.create_task(service.async_run_scenario("scenario_1"))
        await asyncio.wait_for(executor.first_started.wait(), timeout=0.1)
        second_result = await service.async_run_scenario("scenario_1")

        self.assertEqual("skipped", second_result["status"])
        self.assertEqual("scenario_already_running", second_result["reason"])
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first

    async def test_queued_mode_waits_for_the_previous_run(self) -> None:
        executor = _QueueExecutor()
        service = ScenarioService(None, self.store, self.catalog, executor)
        await service.async_load()
        payload = _valid_payload()
        payload["definition"]["executionMode"] = "queued"
        await service.async_update_scenario(payload)

        first = asyncio.create_task(service.async_run_scenario("scenario_1"))
        await asyncio.wait_for(executor.first_started.wait(), timeout=0.1)
        second = asyncio.create_task(service.async_run_scenario("scenario_1"))
        await asyncio.sleep(0)
        self.assertEqual(1, len(executor.runs))
        executor.release_first.set()

        first_result, second_result = await asyncio.gather(first, second)
        self.assertEqual("completed", first_result["status"])
        self.assertEqual("completed", second_result["status"])
        self.assertEqual(2, len(executor.runs))

    async def test_queued_mode_rejects_when_bounded_queue_is_full(self) -> None:
        executor = _QueueExecutor()
        journal = _FakeJournal()
        service = ScenarioService(
            None,
            self.store,
            self.catalog,
            executor,
            operation_journal=journal,
        )
        await service.async_load()
        payload = _valid_payload()
        payload["definition"]["executionMode"] = "queued"
        payload["definition"]["queueLimit"] = 1
        await service.async_update_scenario(payload)

        first = asyncio.create_task(service.async_run_scenario("scenario_1"))
        await asyncio.wait_for(executor.first_started.wait(), timeout=0.1)
        queued = asyncio.create_task(service.async_run_scenario("scenario_1"))
        await asyncio.sleep(0)
        rejected = await service.async_run_scenario("scenario_1")

        self.assertEqual("skipped", rejected["status"])
        self.assertEqual("scenario_queue_full", rejected["reason"])
        self.assertEqual(1, len(executor.runs))
        executor.release_first.set()
        await asyncio.gather(first, queued)
        self.assertEqual(2, len(executor.runs))
        outcomes = [item["scenario"]["outcome"] for item in journal.receipts]
        self.assertIn("skipped", outcomes)
        self.assertEqual(2, outcomes.count("completed"))

    async def test_run_without_executor_raises(self) -> None:
        await self.service.async_update_scenario(_valid_payload())
        self.service.set_executor(None)
        with self.assertRaises(ScenarioServiceError) as ctx:
            await self.service.async_run_scenario("scenario_1")
        self.assertEqual(ctx.exception.status, 500)


class _FakeHass:
    def __init__(self) -> None:
        self.created_tasks: list[asyncio.Task[Any]] = []

    def async_create_task(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self.created_tasks.append(task)
        return task


class _FakeReleaseExecutor:
    def __init__(self) -> None:
        self.releases: list[str] = []

    async def async_release_intercom_switch(self, entity_id: str) -> bool:
        self.releases.append(entity_id)
        return True


class _FakeCallLater:
    def __init__(self) -> None:
        self.calls: list[tuple[float, Any]] = []
        self.cancelled = 0

    def __call__(self, hass: Any, delay: float, callback: Any) -> Any:
        self.calls.append((delay, callback))

        def cancel() -> None:
            self.cancelled += 1

        return cancel


def _intercom_catalog() -> ScenarioCatalog:
    entry = ScenarioDeviceEntry(
        target_id="intercom_target",
        name="Домофон",
        entity_id="switch.prikhozhaia_domofon_2",
        actions=(
            ScenarioDeviceAction(
                action_id="turn_on",
                title="Open",
                domain="switch",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
        ),
    )
    return ScenarioCatalog(devices={"intercom_target": entry}, scenarios={})


class ScenarioServiceIntercomReleaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.hass = _FakeHass()
        self.call_later = _FakeCallLater()
        self.executor = _FakeReleaseExecutor()
        self.release_receipts: list[dict[str, object]] = []
        self.service = ScenarioService(
            self.hass,
            _FakeStore(),
            _intercom_catalog(),
            self.executor,
            intercom_entity_resolver=lambda: "switch.prikhozhaia_domofon_2",
            call_later=self.call_later,
            intercom_release_publisher=self.release_receipts.append,
        )

    async def test_release_scheduled_and_fires_turn_off(self) -> None:
        seconds = await self.service.async_schedule_intercom_release(
            "intercom_target", "turn_on"
        )
        self.assertEqual(seconds, 15)
        self.assertEqual(len(self.call_later.calls), 1)
        delay, callback = self.call_later.calls[0]
        self.assertEqual(delay, 15)
        await callback(None)
        self.assertEqual(self.executor.releases, ["switch.prikhozhaia_domofon_2"])
        self.assertEqual("released", self.release_receipts[0]["outcome"])
        self.assertEqual("intercom_target", self.release_receipts[0]["targetId"])
        self.assertNotIn("entityId", self.release_receipts[0])

    async def test_dry_run_receipt_does_not_touch_relay(self) -> None:
        self.service.publish_intercom_dry_run(
            target_id="intercom_target",
            correlation_id="corr.intercom-dry-run",
            request_id="request-dry-run",
        )
        self.assertEqual("dry_run", self.release_receipts[0]["outcome"])
        self.assertFalse(self.release_receipts[0]["confirmed"])
        self.assertEqual([], self.executor.releases)

    async def test_unconfirmed_off_read_back_publishes_release_failure(self) -> None:
        self.executor.async_release_intercom_switch = AsyncMock(return_value=False)
        await self.service.async_schedule_intercom_release(
            "intercom_target",
            "turn_on",
            correlation_id="corr.release-failed",
            request_id="request-release-failed",
        )
        _, callback = self.call_later.calls[0]
        await callback(None)
        self.assertEqual("release_failed", self.release_receipts[0]["outcome"])
        self.assertFalse(self.release_receipts[0]["confirmed"])
        self.assertEqual("release_not_confirmed", self.release_receipts[0]["reason"])

    async def test_configured_intercom_is_classified_for_confirmation(self) -> None:
        self.assertTrue(
            await self.service.async_is_intercom_action(
                "intercom_target", "turn_on"
            )
        )
        self.assertFalse(
            await self.service.async_is_intercom_action("device_abc", "turn_on")
        )

    async def test_release_skips_unrelated_target(self) -> None:
        self.assertIsNone(
            await self.service.async_schedule_intercom_release("device_abc", "turn_on")
        )
        self.assertEqual(self.call_later.calls, [])

    async def test_release_skips_non_turn_on_action(self) -> None:
        self.assertIsNone(
            await self.service.async_schedule_intercom_release(
                "intercom_target", "turn_off"
            )
        )
        self.assertEqual(self.call_later.calls, [])

    async def test_release_matches_configured_target_id(self) -> None:
        service = ScenarioService(
            self.hass,
            _FakeStore(),
            _intercom_catalog(),
            self.executor,
            intercom_entity_resolver=lambda: "intercom_target",
            call_later=self.call_later,
        )
        seconds = await service.async_schedule_intercom_release(
            "intercom_target", "turn_on"
        )
        self.assertEqual(seconds, 15)

    async def test_repeat_press_extends_hold(self) -> None:
        await self.service.async_schedule_intercom_release("intercom_target", "turn_on")
        await self.service.async_schedule_intercom_release("intercom_target", "turn_on")
        self.assertEqual(self.call_later.cancelled, 1)
        self.assertEqual(len(self.call_later.calls), 2)

    async def test_cancel_release_turns_off_now(self) -> None:
        await self.service.async_schedule_intercom_release("intercom_target", "turn_on")
        self.service.cancel_intercom_release(turn_off_now=True)
        self.assertEqual(self.call_later.cancelled, 1)
        await asyncio.gather(*self.hass.created_tasks)
        self.assertEqual(self.executor.releases, ["switch.prikhozhaia_domofon_2"])

    async def test_cancel_without_turn_off_keeps_executor_quiet(self) -> None:
        await self.service.async_schedule_intercom_release("intercom_target", "turn_on")
        self.service.cancel_intercom_release()
        self.assertEqual(self.call_later.cancelled, 1)
        self.assertEqual(self.executor.releases, [])

    async def test_no_resolver_no_release(self) -> None:
        service = ScenarioService(self.hass, _FakeStore(), _intercom_catalog())
        self.assertIsNone(
            await service.async_schedule_intercom_release("intercom_target", "turn_on")
        )

    async def test_release_without_executor_is_skipped_safely(self) -> None:
        service = ScenarioService(
            self.hass,
            _FakeStore(),
            _intercom_catalog(),
            intercom_entity_resolver=lambda: "switch.prikhozhaia_domofon_2",
            call_later=self.call_later,
        )
        seconds = await service.async_schedule_intercom_release(
            "intercom_target", "turn_on"
        )
        self.assertEqual(seconds, 15)
        _, callback = self.call_later.calls[0]
        await callback(None)
        self.assertEqual(self.executor.releases, [])


class _FakeScheduleStore:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data
        self.saves: list[dict[str, Any]] = []

    async def async_load(self) -> dict[str, Any] | None:
        return self._data

    async def async_save(self, payload: dict[str, Any]) -> None:
        self.saves.append(payload)
        self._data = payload


_SCHEDULE_NOW = datetime(2026, 8, 9, 10, 0, 0, tzinfo=timezone(timedelta(hours=6)))


class ScenarioUpcomingServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = _FakeStore()
        self.schedule_store = _FakeScheduleStore()
        self.executor = _FakeExecutor()
        self.service = ScenarioService(
            None,
            self.store,
            _catalog(),
            self.executor,
            sun_times_provider=lambda: (None, None),
            now_provider=lambda: _SCHEDULE_NOW,
            schedule_store=self.schedule_store,
        )
        await self.service.async_load()

    async def _add_time_scenario(self, value: str = "12:00") -> None:
        payload = _valid_payload()
        payload["definition"]["triggers"] = [
            {"id": "t1", "type": "time", "value": value}
        ]
        await self.service.async_update_scenario(payload)

    async def test_upcoming_events_payload(self) -> None:
        await self._add_time_scenario()
        result = await self.service.async_list_upcoming_events()
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["scenarioId"], "scenario_1")
        self.assertEqual(event["scenarioTitle"], "Scenario scenario_1")
        self.assertEqual(event["triggerId"], "t1")
        self.assertEqual(event["triggerType"], "time")
        self.assertEqual(
            event["runAt"],
            datetime(
                2026, 8, 9, 12, 0, tzinfo=timezone(timedelta(hours=6))
            ).isoformat(),
        )
        self.assertTrue(event["cancellable"])
        generated = datetime.fromisoformat(result["generatedAt"])
        self.assertEqual(generated.utcoffset(), timedelta(0))

    async def test_upcoming_empty_without_scheduled_triggers(self) -> None:
        payload = _valid_payload()
        payload["definition"]["triggers"] = [{"id": "t1", "type": "manual"}]
        await self.service.async_update_scenario(payload)
        result = await self.service.async_list_upcoming_events()
        self.assertEqual(result["events"], [])

    async def test_scheduled_trigger_items(self) -> None:
        await self._add_time_scenario()
        self.assertEqual(
            self.service.scheduled_trigger_items(),
            (("scenario_1", "t1", "time", "12:00"),),
        )

    async def test_cancel_upcoming_success(self) -> None:
        await self._add_time_scenario()
        run_at = (await self.service.async_list_upcoming_events())["events"][0]["runAt"]
        receipt = await self.service.async_cancel_upcoming("scenario_1", "t1", run_at)
        self.assertTrue(receipt["cancelled"])
        self.assertEqual(receipt["scenarioId"], "scenario_1")
        self.assertEqual(receipt["triggerId"], "t1")
        self.assertEqual(receipt["runAt"], run_at)
        self.assertEqual(
            self.schedule_store.saves[-1],
            {"version": 1, "skips": ["scenario_1|t1|2026-08-09"]},
        )
        remaining = await self.service.async_list_upcoming_events()
        self.assertEqual(remaining["events"], [])

    async def test_cancel_upcoming_not_found(self) -> None:
        await self._add_time_scenario()
        run_at = (await self.service.async_list_upcoming_events())["events"][0]["runAt"]
        with self.assertRaises(ScenarioServiceError) as ctx:
            await self.service.async_cancel_upcoming("scenario_1", "missing", run_at)
        self.assertEqual(ctx.exception.status, 404)

    async def test_cancel_upcoming_bad_run_at(self) -> None:
        await self._add_time_scenario()
        with self.assertRaises(ScenarioServiceError) as ctx:
            await self.service.async_cancel_upcoming("scenario_1", "t1", "not-a-date")
        self.assertEqual(ctx.exception.status, 400)

    async def test_cancel_upcoming_twice_is_not_found(self) -> None:
        await self._add_time_scenario()
        run_at = (await self.service.async_list_upcoming_events())["events"][0]["runAt"]
        await self.service.async_cancel_upcoming("scenario_1", "t1", run_at)
        with self.assertRaises(ScenarioServiceError) as ctx:
            await self.service.async_cancel_upcoming("scenario_1", "t1", run_at)
        self.assertEqual(ctx.exception.status, 404)

    async def test_consume_skip(self) -> None:
        await self._add_time_scenario()
        run_at = (await self.service.async_list_upcoming_events())["events"][0]["runAt"]
        await self.service.async_cancel_upcoming("scenario_1", "t1", run_at)
        self.assertTrue(
            await self.service.async_consume_skip("scenario_1", "t1", "2026-08-09")
        )
        self.assertFalse(
            await self.service.async_consume_skip("scenario_1", "t1", "2026-08-09")
        )

    async def test_consume_skip_unknown(self) -> None:
        self.assertFalse(
            await self.service.async_consume_skip("scenario_1", "t1", "2026-08-09")
        )

    async def test_skips_survive_reload(self) -> None:
        await self._add_time_scenario()
        run_at = (await self.service.async_list_upcoming_events())["events"][0]["runAt"]
        await self.service.async_cancel_upcoming("scenario_1", "t1", run_at)

        reloaded = ScenarioService(
            None,
            self.store,
            _catalog(),
            self.executor,
            sun_times_provider=lambda: (None, None),
            now_provider=lambda: _SCHEDULE_NOW,
            schedule_store=self.schedule_store,
        )
        await reloaded.async_load()
        remaining = await reloaded.async_list_upcoming_events()
        self.assertEqual(remaining["events"], [])

    async def test_load_prunes_past_skips(self) -> None:
        self.schedule_store._data = {
            "version": 1,
            "skips": ["scenario_1|t1|2026-08-08", "scenario_1|t1|2026-08-09"],
        }
        await self.service.async_load()
        self.assertEqual(self.service._skipped_runs, {"scenario_1|t1|2026-08-09"})


if __name__ == "__main__":
    unittest.main()
