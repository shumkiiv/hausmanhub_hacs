from __future__ import annotations

import asyncio
import json
import unittest

from custom_components.hausman_hub.application.ai_assistant import AiProviderCompletion
from custom_components.hausman_hub.application.scenario_ai import (
    ScenarioAiDraftService,
    ScenarioAiOutputInvalid,
    ScenarioAiRequestError,
)
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
    ScenarioDeviceProperty,
    ScenarioPropertyOption,
)


def _catalog() -> ScenarioCatalog:
    motion = ScenarioDeviceEntry(
        target_id="motion_hall",
        name="Датчик движения",
        entity_id="binary_sensor.private_motion",
        room_id="hall",
        room_name="Коридор",
        properties=(
            ScenarioDeviceProperty(
                property_id="state",
                label="Движение",
                value_type="enum",
                comparisons=("equals", "not_equals", "changed"),
                options=(
                    ScenarioPropertyOption("on", "Есть движение"),
                    ScenarioPropertyOption("off", "Нет движения"),
                ),
            ),
        ),
        actions=(),
    )
    light = ScenarioDeviceEntry(
        target_id="hall_light",
        name="Свет в коридоре",
        entity_id="light.private_hall",
        room_id="hall",
        room_name="Коридор",
        actions=(
            ScenarioDeviceAction("turn_on", "Включить", "light", "turn_on", frozenset()),
            ScenarioDeviceAction("unlock", "Разблокировать", "lock", "unlock", frozenset()),
        ),
    )
    return ScenarioCatalog(
        devices={motion.target_id: motion, light.target_id: light},
        scenarios={},
    )


class FakeAssistant:
    scenario_generation_available = True

    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.payload = None
        self.prompt = None

    async def async_complete_json_task(self, *, system_prompt, payload):
        self.prompt = system_prompt
        self.payload = payload
        return AiProviderCompletion(json.dumps(self.response, ensure_ascii=False), 20, 10)


class FakeScenarios:
    def __init__(self) -> None:
        self.catalog = _catalog()
        self.tested = []

    def current_catalog(self):
        return self.catalog

    async def async_test_scenario(self, payload):
        self.tested.append(payload)
        return {"valid": True}


def _request() -> dict[str, object]:
    return {
        "contract": {"name": "hausman-hub-scenario-ai-draft-request", "version": 1},
        "text": "Когда @Датчик движения заметит движение, включи @Свет в коридоре.",
        "locale": "ru-RU",
        "mentions": [
            {"id": "mention_1", "token": "@Датчик движения", "label": "Датчик движения", "targetId": "motion_hall"},
            {"id": "mention_2", "token": "@Свет в коридоре", "label": "Свет в коридоре", "targetId": "hall_light"},
        ],
    }


def _ready(action_id: str = "turn_on") -> dict[str, object]:
    return {
        "status": "ready",
        "summary": "Свет включится при движении.",
        "draft": {
            "title": "Свет по движению",
            "group": "Освещение",
            "description": "Включает свет при движении.",
            "icon": "mdi:lightbulb-auto",
            "favorite": False,
            "definition": {
                "version": 1,
                "executionMode": "restart",
                "triggers": [
                    {"id": "trigger_1", "type": "device_state", "targetId": "motion_hall", "property": "state", "comparison": "equals", "value": "on"}
                ],
                "conditions": [],
                "actions": [
                    {"id": "action_1", "type": "device_action", "targetId": "hall_light", "actionId": action_id}
                ],
            },
        },
    }


class ScenarioAiDraftServiceTest(unittest.TestCase):
    def test_ready_draft_is_disabled_validated_and_contains_no_entity_ids(self) -> None:
        assistant = FakeAssistant(_ready())
        scenarios = FakeScenarios()
        service = ScenarioAiDraftService(
            assistant, scenarios, id_factory=lambda: "custom_ai_test"
        )

        result = asyncio.run(service.async_generate(_request()))

        self.assertEqual("ready", result["status"])
        draft = result["draft"]
        self.assertFalse(draft["enabled"])
        self.assertFalse(result["saved"])
        self.assertFalse(result["commandSent"])
        self.assertEqual(["hall"], draft["roomIds"])
        self.assertEqual("Датчик движения", draft["definition"]["triggers"][0]["targetName"])
        self.assertEqual("Включить", draft["definition"]["actions"][0]["actionTitle"])
        self.assertEqual(1, len(scenarios.tested))
        provider_json = json.dumps(assistant.payload, ensure_ascii=False)
        self.assertNotIn("binary_sensor.private_motion", provider_json)
        self.assertNotIn("light.private_hall", provider_json)

    def test_dangerous_action_is_forced_to_confirmation(self) -> None:
        service = ScenarioAiDraftService(
            FakeAssistant(_ready("unlock")), FakeScenarios(), id_factory=lambda: "custom_ai_test"
        )

        result = asyncio.run(service.async_generate(_request()))

        self.assertTrue(result["draft"]["danger"])
        self.assertTrue(result["draft"]["requiresConfirmation"])
        self.assertIn("dangerous_action_requires_confirmation", result["warnings"])

    def test_unknown_mention_is_rejected_before_provider_call(self) -> None:
        request = _request()
        request["mentions"][0]["targetId"] = "missing_device"
        service = ScenarioAiDraftService(FakeAssistant(_ready()), FakeScenarios())

        with self.assertRaises(ScenarioAiRequestError):
            asyncio.run(service.async_generate(request))

    def test_clarification_never_returns_a_draft(self) -> None:
        service = ScenarioAiDraftService(
            FakeAssistant({"status": "needs_clarification", "summary": "Нужно уточнение", "questions": ["Во сколько включать свет?"]}),
            FakeScenarios(),
        )

        result = asyncio.run(service.async_generate(_request()))

        self.assertEqual("needs_clarification", result["status"])
        self.assertNotIn("draft", result)
        self.assertEqual(["Во сколько включать свет?"], result["clarifyingQuestions"])

    def test_provider_cannot_inject_raw_or_existing_commands(self) -> None:
        output = _ready()
        output["draft"]["definition"]["actions"][0] = {
            "id": "action_1",
            "type": "existing_action",
            "command": "light.turn_on",
        }
        scenarios = FakeScenarios()
        service = ScenarioAiDraftService(FakeAssistant(output), scenarios)

        with self.assertRaises(ScenarioAiOutputInvalid):
            asyncio.run(service.async_generate(_request()))

        self.assertEqual([], scenarios.tested)

    def test_provider_output_cannot_disclose_entity_id(self) -> None:
        output = _ready()
        output["summary"] = "Использую light.private_hall"
        service = ScenarioAiDraftService(FakeAssistant(output), FakeScenarios())

        with self.assertRaises(ScenarioAiOutputInvalid):
            asyncio.run(service.async_generate(_request()))


if __name__ == "__main__":
    unittest.main()
