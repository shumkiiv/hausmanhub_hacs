"""Pure tests for HausmanHub scenario definitions."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.domain.scenarios import (
    MAX_ACTIONS,
    MAX_CONDITIONS,
    MAX_DELAY_SECONDS,
    MAX_TRIGGERS,
    Scenario,
    ScenarioAction,
    ScenarioActionType,
    ScenarioComparison,
    ScenarioCondition,
    ScenarioConditionType,
    ScenarioDefinition,
    ScenarioDeviceCommand,
    ScenarioExecutionMode,
    ScenarioRegistry,
    ScenarioTrigger,
    ScenarioTriggerType,
    ScenarioViolation,
    scenario_registry_from_payload,
    scenario_registry_to_payload,
)


def valid_definition(**changes: object) -> ScenarioDefinition:
    """Build one minimal valid scenario definition."""

    values: dict[str, object] = {
        "version": 1,
        "execution_mode": ScenarioExecutionMode.RESTART,
        "triggers": (ScenarioTrigger(id="t1", type=ScenarioTriggerType.MANUAL),),
        "conditions": (),
        "actions": (
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.NOTIFICATION,
                message="Test",
            ),
        ),
    }
    values.update(changes)
    return ScenarioDefinition(**values)  # type: ignore[arg-type]


def valid_scenario(**changes: object) -> Scenario:
    """Build one minimal valid scenario."""

    values: dict[str, object] = {
        "id": "test_scenario",
        "title": "Test scenario",
        "group": "Tests",
        "description": "A test scenario",
        "icon": "home",
        "enabled": True,
        "favorite": False,
        "danger": False,
        "requires_confirmation": False,
        "trigger_description": "Manual",
        "condition_description": "Always",
        "action_description": "Notify",
        "updated_at": 1,
        "definition": valid_definition(),
    }
    values.update(changes)
    return Scenario(**values)  # type: ignore[arg-type]


class ScenarioDomainTest(unittest.TestCase):
    """Keep scenario definitions strictly typed and validated."""

    def test_minimal_valid_scenario(self) -> None:
        scenario = valid_scenario()
        self.assertEqual(scenario.id, "test_scenario")
        self.assertTrue(scenario.enabled)

    def test_round_trip_payload(self) -> None:
        registry = ScenarioRegistry(scenarios=(valid_scenario(),))
        payload = scenario_registry_to_payload(registry)
        restored = scenario_registry_from_payload(payload)
        self.assertEqual(len(restored.scenarios), 1)
        self.assertEqual(restored.scenarios[0].id, "test_scenario")

    def test_empty_registry(self) -> None:
        registry = ScenarioRegistry()
        self.assertEqual(len(registry.scenarios), 0)
        self.assertEqual(registry.version, 1)

    def test_scenario_ids_must_be_unique(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioRegistry(
                scenarios=(valid_scenario(id="same"), valid_scenario(id="same"))
            )

    def test_danger_requires_confirmation(self) -> None:
        with self.assertRaises(ScenarioViolation):
            valid_scenario(danger=True, requires_confirmation=False)

    def test_empty_triggers_rejected(self) -> None:
        with self.assertRaises(ScenarioViolation):
            valid_definition(triggers=())

    def test_empty_actions_rejected(self) -> None:
        with self.assertRaises(ScenarioViolation):
            valid_definition(actions=())

    def test_too_many_triggers(self) -> None:
        triggers = tuple(
            ScenarioTrigger(id=f"t{i}", type=ScenarioTriggerType.MANUAL)
            for i in range(MAX_TRIGGERS + 1)
        )
        with self.assertRaises(ScenarioViolation):
            valid_definition(triggers=triggers)

    def test_too_many_conditions(self) -> None:
        conditions = tuple(
            ScenarioCondition(id=f"c{i}", type=ScenarioConditionType.PRESENCE, value="home")
            for i in range(MAX_CONDITIONS + 1)
        )
        with self.assertRaises(ScenarioViolation):
            valid_definition(conditions=conditions)

    def test_too_many_actions(self) -> None:
        actions = tuple(
            ScenarioAction(
                id=f"a{i}", type=ScenarioActionType.NOTIFICATION, message="Test"
            )
            for i in range(MAX_ACTIONS + 1)
        )
        with self.assertRaises(ScenarioViolation):
            valid_definition(actions=actions)

    def test_duplicate_trigger_ids(self) -> None:
        triggers = (
            ScenarioTrigger(id="same", type=ScenarioTriggerType.MANUAL),
            ScenarioTrigger(id="same", type=ScenarioTriggerType.MANUAL),
        )
        with self.assertRaises(ScenarioViolation):
            valid_definition(triggers=triggers)

    def test_time_trigger_requires_clock_time(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioTrigger(id="t1", type=ScenarioTriggerType.TIME, value="25:00")

    def test_device_state_trigger_requires_value_except_changed(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioTrigger(
                id="t1",
                type=ScenarioTriggerType.DEVICE_STATE,
                target_id="dev1",
                property="state",
                comparison=ScenarioComparison.EQUALS,
                value=None,
            )

    def test_changed_trigger_forbids_value(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioTrigger(
                id="t1",
                type=ScenarioTriggerType.DEVICE_STATE,
                target_id="dev1",
                property="state",
                comparison=ScenarioComparison.CHANGED,
                value="on",
            )

    def test_condition_changed_comparison_forbidden(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioCondition(
                id="c1",
                type=ScenarioConditionType.DEVICE_STATE,
                target_id="dev1",
                property="state",
                comparison=ScenarioComparison.CHANGED,
                value="on",
            )

    def test_time_window_condition_format(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioCondition(
                id="c1",
                type=ScenarioConditionType.TIME_WINDOW,
                value="09:00",
            )

    def test_weekday_condition_format(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioCondition(
                id="c1",
                type=ScenarioConditionType.WEEKDAY,
                value="mon, tue",
            )

    def test_presence_value_must_be_home_or_away(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioCondition(
                id="c1",
                type=ScenarioConditionType.PRESENCE,
                value="unknown",
            )

    def test_delay_bounds(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioAction(
                id="a1", type=ScenarioActionType.DELAY, delay_seconds=0
            )
        with self.assertRaises(ScenarioViolation):
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DELAY,
                delay_seconds=MAX_DELAY_SECONDS + 1,
            )

    def test_device_action_command_snapshot_optional(self) -> None:
        action = ScenarioAction(
            id="a1",
            type=ScenarioActionType.DEVICE_ACTION,
            target_id="dev1",
            action_id="turn_on",
        )
        self.assertIsNone(action.command)

    def test_device_action_command_validated_when_present(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="dev1",
                action_id="turn_on",
                command=ScenarioDeviceCommand(
                    domain="", service="turn_on", entity_id="light.living"
                ),
            )

    def test_run_scenario_requires_scenario_id(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioAction(id="a1", type=ScenarioActionType.RUN_SCENARIO)

    def test_notification_requires_message(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioAction(id="a1", type=ScenarioActionType.NOTIFICATION)

    def test_definition_version_must_be_one(self) -> None:
        with self.assertRaises(ScenarioViolation):
            valid_definition(version=2)

    def test_stable_id_validation(self) -> None:
        with self.assertRaises(ScenarioViolation):
            valid_scenario(id="Bad id")

    def test_scenario_title_stripped(self) -> None:
        with self.assertRaises(ScenarioViolation):
            valid_scenario(title="  ")

    def test_payload_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ScenarioViolation):
            scenario_registry_from_payload(
                {
                    "version": 1,
                    "scenarios": [],
                    "extra": True,
                }
            )

    def test_payload_rejects_missing_definition_field(self) -> None:
        payload = scenario_registry_to_payload(ScenarioRegistry(scenarios=(valid_scenario(),)))
        del payload["scenarios"][0]["definition"]["executionMode"]
        with self.assertRaises(ScenarioViolation):
            scenario_registry_from_payload(payload)

    def test_device_state_trigger_full(self) -> None:
        trigger = ScenarioTrigger(
            id="t1",
            type=ScenarioTriggerType.DEVICE_STATE,
            target_id="motion_living",
            target_name="Motion",
            property="state",
            comparison=ScenarioComparison.EQUALS,
            value="on",
        )
        self.assertEqual(trigger.target_id, "motion_living")

    def test_sunrise_offset_optional(self) -> None:
        trigger = ScenarioTrigger(id="t1", type=ScenarioTriggerType.SUNRISE)
        self.assertIsNone(trigger.value)
        trigger_with_offset = ScenarioTrigger(
            id="t1", type=ScenarioTriggerType.SUNRISE, value=-30
        )
        self.assertEqual(trigger_with_offset.value, -30)

    def test_sunrise_offset_accepts_numeric_string(self) -> None:
        trigger = ScenarioTrigger(
            id="t1", type=ScenarioTriggerType.SUNSET, value="15"
        )
        self.assertEqual(trigger.value, "15")

    def test_android_manual_trigger_with_ignored_comparison(self) -> None:
        trigger = ScenarioTrigger(
            id="t1",
            type=ScenarioTriggerType.MANUAL,
            comparison=ScenarioComparison.EQUALS,
        )
        self.assertEqual(trigger.type, ScenarioTriggerType.MANUAL)

    def test_time_window_accepts_en_dash(self) -> None:
        condition = ScenarioCondition(
            id="c1",
            type=ScenarioConditionType.TIME_WINDOW,
            value="09:00–18:00",
        )
        self.assertEqual(condition.value, "09:00–18:00")

    def test_existing_action_requires_target_id(self) -> None:
        with self.assertRaises(ScenarioViolation):
            ScenarioAction(id="a1", type=ScenarioActionType.EXISTING_ACTION)


if __name__ == "__main__":
    unittest.main()
