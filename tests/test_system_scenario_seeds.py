"""Tests for built-in system scenario seeds (Node-RED leftovers port)."""

from __future__ import annotations

import unittest
from typing import Any

from custom_components.hausman_hub.application.scenario_catalog import (
    _stable_target_id_from_entity,
)
from custom_components.hausman_hub.application.scenario_service import (
    ScenarioService,
)
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.application.system_scenario_seeds import (
    AWAY_OFF_ENTITIES,
    SYSTEM_SCENARIO_SEEDS,
    async_seed_system_scenarios,
)
from custom_components.hausman_hub.domain.scenarios import ScenarioRegistry


class _FakeStore:
    def __init__(self) -> None:
        self._data: ScenarioRegistry | None = None

    async def async_load(self) -> ScenarioRegistry | None:
        return self._data

    async def async_save(self, registry: ScenarioRegistry) -> None:
        self._data = registry


def _on_off_actions(domain: str) -> tuple[ScenarioDeviceAction, ...]:
    return (
        ScenarioDeviceAction(
            action_id="turn_on",
            title="Включить",
            domain=domain,
            service="turn_on",
            allowed_fields=frozenset(),
        ),
        ScenarioDeviceAction(
            action_id="turn_off",
            title="Выключить",
            domain=domain,
            service="turn_off",
            allowed_fields=frozenset(),
        ),
    )


def _cover_actions() -> tuple[ScenarioDeviceAction, ...]:
    return (
        ScenarioDeviceAction(
            action_id="close_cover",
            title="Закрыть",
            domain="cover",
            service="close_cover",
            allowed_fields=frozenset(),
        ),
        ScenarioDeviceAction(
            action_id="open_cover",
            title="Открыть",
            domain="cover",
            service="open_cover",
            allowed_fields=frozenset(),
        ),
        ScenarioDeviceAction(
            action_id="set_position",
            title="Позиция",
            domain="cover",
            service="set_cover_position",
            allowed_fields=frozenset({"value"}),
        ),
    )


def _entry(entity_id: str) -> ScenarioDeviceEntry:
    domain = entity_id.split(".", 1)[0]
    if domain == "cover":
        actions = _cover_actions()
    elif domain in ("switch", "light", "climate"):
        actions = _on_off_actions(domain)
    else:
        actions = ()
    return ScenarioDeviceEntry(
        target_id=_stable_target_id_from_entity(entity_id),
        name=entity_id,
        entity_id=entity_id,
        actions=actions,
    )


def _all_seed_entities() -> tuple[str, ...]:
    entities: set[str] = set()
    for seed in SYSTEM_SCENARIO_SEEDS:
        entities.update(seed.required_entities)
        entities.update(entity_id for entity_id, _ in seed.optional_actions)
    return tuple(sorted(entities))


def _catalog(entities: tuple[str, ...]) -> ScenarioCatalog:
    entries = {e: _entry(e) for e in entities}
    return ScenarioCatalog(
        devices={entry.target_id: entry for entry in entries.values()},
        scenarios={},
    )


class SystemScenarioSeedsTest(unittest.IsolatedAsyncioTestCase):
    async def _make_service(self, catalog: ScenarioCatalog) -> ScenarioService:
        service = ScenarioService(None, _FakeStore(), catalog, None)
        await service.async_load()
        return service

    async def test_seed_creates_everything_when_catalog_complete(self) -> None:
        service = await self._make_service(_catalog(_all_seed_entities()))
        created = await async_seed_system_scenarios(service)
        self.assertEqual(len(created), len(SYSTEM_SCENARIO_SEEDS))
        scenarios = await service.async_list_scenarios()
        self.assertEqual(len(scenarios), len(SYSTEM_SCENARIO_SEEDS))
        for scenario in scenarios:
            self.assertEqual(scenario.group, "system")
            self.assertTrue(scenario.enabled)
            self.assertTrue(scenario.id.startswith("system-"))

    async def test_seed_is_idempotent(self) -> None:
        service = await self._make_service(_catalog(_all_seed_entities()))
        first = await async_seed_system_scenarios(service)
        second = await async_seed_system_scenarios(service)
        self.assertEqual(second, ())
        scenarios = await service.async_list_scenarios()
        self.assertEqual(len(scenarios), len(first))

    async def test_seed_fills_target_names_from_live_catalog(self) -> None:
        # Решение владельца 2026-08-20: шаги системных сценариев несут имена
        # устройств, чтобы лента и редактор не показывали безликое «Устройство».
        catalog = _catalog(_all_seed_entities())
        service = await self._make_service(catalog)
        await async_seed_system_scenarios(service)
        scenarios = await service.async_list_scenarios()
        named = 0
        for scenario in scenarios:
            steps = (
                *scenario.definition.triggers,
                *scenario.definition.conditions,
                *scenario.definition.actions,
            )
            for step in steps:
                target_id = getattr(step, "target_id", None)
                if target_id is None:
                    continue
                device = catalog.devices.get(target_id)
                self.assertIsNotNone(device)
                self.assertEqual(device.name, getattr(step, "target_name", None))
                named += 1
        self.assertGreater(named, 0)

    async def test_seed_skips_missing_required_entities(self) -> None:
        entities = tuple(
            e for e in _all_seed_entities() if not e.startswith("cover.")
        )
        service = await self._make_service(_catalog(entities))
        created = await async_seed_system_scenarios(service)
        self.assertNotIn("system-twilight-curtains-close", created)
        self.assertNotIn("system-kitchen-curtains-open-weekday", created)
        self.assertNotIn("system-kitchen-curtains-open-weekend", created)
        self.assertIn("system-leak-toilet-alert", created)

    async def test_seed_away_filters_absent_entities(self) -> None:
        keep = ("climate.detskaia_konditsioner", "light.nochnik_u_dveri")
        service = await self._make_service(_catalog(keep))
        created = await async_seed_system_scenarios(service)
        self.assertIn("system-away-turn-off", created)
        scenario = await service.async_get_scenario("system-away-turn-off")
        targets = {
            action.target_id
            for action in scenario.definition.actions
            if action.type.value == "device_action"
        }
        self.assertEqual(
            targets,
            {_stable_target_id_from_entity(e) for e in keep},
        )
        self.assertTrue(
            any(action.type.value == "notification" for action in scenario.definition.actions)
        )

    async def test_seed_never_overwrites_existing_scenario(self) -> None:
        service = await self._make_service(_catalog(_all_seed_entities()))
        custom: dict[str, Any] = {
            "id": "system-leak-toilet-alert",
            "title": "Моя протечка",
            "definition": {
                "version": 1,
                "executionMode": "single",
                "triggers": [{"id": "t1", "type": "time", "value": "08:00"}],
                "conditions": [],
                "actions": [
                    {"id": "a1", "type": "notification", "message": "проверка"}
                ],
            },
            "enabled": True,
        }
        await service.async_update_scenario(custom)
        created = await async_seed_system_scenarios(service)
        self.assertNotIn("system-leak-toilet-alert", created)
        scenario = await service.async_get_scenario("system-leak-toilet-alert")
        self.assertEqual(scenario.title, "Моя протечка")

    def test_toilet_light_seeds_split_channels_by_time_and_sun(self) -> None:
        seeds = {
            seed.scenario_id: seed
            for seed in SYSTEM_SCENARIO_SEEDS
            if seed.scenario_id.startswith("system-toilet-light-motion")
        }
        self.assertEqual(
            set(seeds),
            {
                "system-toilet-light-motion",
                "system-toilet-light-motion-evening",
                "system-toilet-light-motion-night",
            },
        )

        expected_turn_on = {
            "system-toilet-light-motion": "switch.0xacbac0fffebde2d3_2",
            "system-toilet-light-motion-evening": "switch.0xacbac0fffebde2d3_2",
            "system-toilet-light-motion-night": "switch.0xacbac0fffebde2d3_1",
        }
        expected_windows = {
            "system-toilet-light-motion": None,
            "system-toilet-light-motion-evening": "12:00-22:59",
            "system-toilet-light-motion-night": "23:00-12:00",
        }
        expected_sun = {
            "system-toilet-light-motion": "above_horizon",
            "system-toilet-light-motion-evening": "below_horizon",
            "system-toilet-light-motion-night": "below_horizon",
        }

        for scenario_id, seed in seeds.items():
            turn_on = [
                action
                for action in seed.actions
                if action.get("actionId") == "turn_on"
            ]
            self.assertEqual(len(turn_on), 1)
            self.assertEqual(
                turn_on[0]["targetId"],
                _stable_target_id_from_entity(expected_turn_on[scenario_id]),
            )
            time_windows = [
                condition.get("value")
                for condition in seed.conditions
                if condition.get("type") == "time_window"
            ]
            self.assertEqual(
                time_windows,
                []
                if expected_windows[scenario_id] is None
                else [expected_windows[scenario_id]],
            )
            sun_conditions = [
                condition
                for condition in seed.conditions
                if condition.get("targetId")
                == _stable_target_id_from_entity("sun.sun")
            ]
            self.assertEqual(len(sun_conditions), 1)
            self.assertEqual(sun_conditions[0]["value"], expected_sun[scenario_id])
            away_conditions = [
                condition
                for condition in seed.conditions
                if condition.get("targetId")
                == _stable_target_id_from_entity(
                    "binary_sensor.a100_away_zaniatost"
                )
            ]
            self.assertEqual(len(away_conditions), 1)
            self.assertEqual(away_conditions[0]["value"], "off")
            self.assertEqual(seed.execution_mode, "restart")

    async def test_seed_empty_catalog_creates_nothing(self) -> None:
        service = await self._make_service(_catalog(()))
        created = await async_seed_system_scenarios(service)
        self.assertEqual(created, ())
        self.assertEqual(await service.async_list_scenarios(), ())

    def test_seed_ids_are_unique_and_stable(self) -> None:
        ids = [seed.scenario_id for seed in SYSTEM_SCENARIO_SEEDS]
        self.assertEqual(len(ids), len(set(ids)))
        for seed in SYSTEM_SCENARIO_SEEDS:
            self.assertTrue(seed.title.strip())
            self.assertTrue(seed.description.strip())
            self.assertTrue(seed.triggers)
        # Список выключения «не дома» не должен быть пустым.
        self.assertGreaterEqual(len(AWAY_OFF_ENTITIES), 10)


if __name__ == "__main__":
    unittest.main()
