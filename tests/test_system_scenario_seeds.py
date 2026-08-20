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
