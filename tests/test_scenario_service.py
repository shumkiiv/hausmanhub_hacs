"""Tests for the HausmanHub scenario application service."""

from __future__ import annotations

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
        self._counter = 0

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


if __name__ == "__main__":
    unittest.main()
