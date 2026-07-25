"""Storage adapter tests for HausmanHub scenario definitions."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

try:
    from homeassistant.helpers.storage import Store

    HAS_HA = True
except Exception:  # pragma: no cover
    HAS_HA = False

from custom_components.hausman_hub.domain.scenarios import (
    Scenario,
    ScenarioAction,
    ScenarioActionType,
    ScenarioDefinition,
    ScenarioExecutionMode,
    ScenarioRegistry,
    ScenarioTrigger,
    ScenarioTriggerType,
)

if HAS_HA:
    from custom_components.hausman_hub.scenario_storage import (
        HomeAssistantScenarioStore,
        ScenarioStorageError,
    )


def _scenario() -> Scenario:
    return Scenario(
        id="movie_mode",
        title="Кино в гостиной",
        group="Медиа",
        description="Подготовить гостиную к просмотру",
        icon="movie",
        enabled=True,
        favorite=True,
        danger=False,
        requires_confirmation=False,
        trigger_description="Ручной запуск",
        condition_description="Кто-то дома",
        action_description="Свет 20% · шторы закрыть · телевизор включить",
        updated_at=1784700000000,
        definition=ScenarioDefinition(
            version=1,
            execution_mode=ScenarioExecutionMode.RESTART,
            triggers=(ScenarioTrigger(id="t1", type=ScenarioTriggerType.MANUAL),),
            conditions=(),
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.NOTIFICATION,
                    message="Гостиная готова",
                ),
            ),
        ),
    )


@unittest.skipUnless(HAS_HA, "homeassistant is not available")
class ScenarioStorageTest(unittest.IsolatedAsyncioTestCase):
    """Keep scenario persistence safe and versioned."""

    async def test_load_returns_empty_registry_when_store_is_none(self) -> None:
        hass = MagicMock()
        store = HomeAssistantScenarioStore(hass, "entry_1")
        store._store.async_load = AsyncMock(return_value=None)
        registry = await store.async_load()
        self.assertEqual(registry, ScenarioRegistry())

    async def test_load_restores_saved_payload(self) -> None:
        hass = MagicMock()
        store = HomeAssistantScenarioStore(hass, "entry_1")
        payload = {
            "version": 1,
            "scenarios": [
                {
                    "id": "movie_mode",
                    "title": "Кино в гостиной",
                    "group": "Медиа",
                    "description": "Подготовить гостиную к просмотру",
                    "icon": "movie",
                    "enabled": True,
                    "favorite": True,
                    "danger": False,
                    "requires_confirmation": False,
                    "trigger_description": "Ручной запуск",
                    "condition_description": "Кто-то дома",
                    "action_description": "Свет 20%",
                    "updated_at": 1784700000000,
                    "definition": {
                        "version": 1,
                        "executionMode": "restart",
                        "triggers": [{"id": "t1", "type": "manual"}],
                        "conditions": [],
                        "actions": [
                            {"id": "a1", "type": "notification", "message": "Готово"}
                        ],
                    },
                }
            ],
        }
        store._store.async_load = AsyncMock(return_value=payload)
        registry = await store.async_load()
        self.assertEqual(len(registry.scenarios), 1)
        self.assertEqual(registry.scenarios[0].id, "movie_mode")

    async def test_load_raises_on_invalid_payload(self) -> None:
        hass = MagicMock()
        store = HomeAssistantScenarioStore(hass, "entry_1")
        store._store.async_load = AsyncMock(
            return_value={"version": 1, "scenarios": [{"bad": True}]}
        )
        with self.assertRaises(ScenarioStorageError):
            await store.async_load()

    async def test_save_writes_exact_payload(self) -> None:
        hass = MagicMock()
        store = HomeAssistantScenarioStore(hass, "entry_1")
        registry = ScenarioRegistry(scenarios=(_scenario(),))
        saved: dict[str, object] | None = None

        async def capture(payload: dict[str, object]) -> None:
            nonlocal saved
            saved = payload

        store._store.async_save = capture
        await store.async_save(registry)

        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["version"], 1)
        self.assertEqual(len(saved["scenarios"]), 1)
        self.assertEqual(saved["scenarios"][0]["id"], "movie_mode")


if __name__ == "__main__":
    unittest.main()
