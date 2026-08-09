"""Tests for the HausmanHub scenario application service."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from custom_components.hausman_hub.application.scenario_service import (
    ScenarioNotFoundError,
    ScenarioReferencedError,
    ScenarioService,
    ScenarioServiceError,
    ScenarioValidationError,
)
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.domain.scenarios import ScenarioRegistry


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
        self._counter = 0

    def replace_catalog(self, catalog: ScenarioCatalog) -> None:
        self.catalogs.append(catalog)

    async def async_execute_device_action(
        self,
        target_id: str,
        action_id: str,
        value: object | None = None,
    ) -> dict[str, Any]:
        self.device_actions.append((target_id, action_id, value))
        return {"accepted": True, "confirmed": True, "status": "confirmed"}

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
        "triggers": [
            {"id": "t1", "type": "time", "value": "08:00"}
        ],
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
        self.assertEqual([("late_light", "turn_on", None)], self.executor.device_actions)
        self.assertTrue(receipt["confirmed"])

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

    async def async_release_intercom_switch(self, entity_id: str) -> None:
        self.releases.append(entity_id)


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
        self.service = ScenarioService(
            self.hass,
            _FakeStore(),
            _intercom_catalog(),
            self.executor,
            intercom_entity_resolver=lambda: "switch.prikhozhaia_domofon_2",
            call_later=self.call_later,
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


if __name__ == "__main__":
    unittest.main()
