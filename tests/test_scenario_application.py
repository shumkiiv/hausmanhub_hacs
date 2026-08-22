"""Application-level scenario validation tests."""

from __future__ import annotations

import unittest
from http import HTTPStatus

from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDefinitionViolation,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
    ScenarioDeviceProperty,
    ScenarioPropertyOption,
    validate_scenario_definition,
)
from custom_components.hausman_hub.domain.scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioComparison,
    ScenarioCondition,
    ScenarioConditionType,
    ScenarioDefinition,
    ScenarioDeviceCommand,
    ScenarioExecutionMode,
    ScenarioSafetyPolicy,
    ScenarioTrigger,
    ScenarioTriggerType,
)


def _catalog(
    *,
    devices: dict[str, ScenarioDeviceEntry] | None = None,
    scenarios: dict[str, object] | None = None,
    scenario_definitions: dict[str, ScenarioDefinition] | None = None,
) -> ScenarioCatalog:
    return ScenarioCatalog(
        devices=devices or {},
        scenarios=scenarios or {},
        scenario_definitions=scenario_definitions or {},
    )


def _valid_definition(**changes: object) -> ScenarioDefinition:
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


class ScenarioApplicationValidationTest(unittest.TestCase):
    """Validate scenario definitions against a live catalog."""

    def test_valid_definition_passes(self) -> None:
        catalog = _catalog()
        definition = _valid_definition()
        validate_scenario_definition(definition, catalog)

    def test_motion_trigger_rejects_state_from_another_device_kind(self) -> None:
        catalog = _catalog(
            devices={
                "motion": ScenarioDeviceEntry(
                    target_id="motion",
                    name="Датчик движения",
                    entity_id="binary_sensor.motion",
                    actions=(),
                    properties=(
                        ScenarioDeviceProperty(
                            property_id="state",
                            label="Движение",
                            value_type="enum",
                            comparisons=("equals", "not_equals", "changed"),
                            options=(
                                ScenarioPropertyOption("on", "Движение"),
                                ScenarioPropertyOption("off", "Нет движения"),
                            ),
                        ),
                    ),
                )
            }
        )
        definition = _valid_definition(
            triggers=(
                ScenarioTrigger(
                    id="t1",
                    type=ScenarioTriggerType.DEVICE_STATE,
                    target_id="motion",
                    property="state",
                    comparison=ScenarioComparison.EQUALS,
                    value="locked",
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog)
        self.assertEqual(raised.exception.path, "definition.triggers[0].value")

    def test_legacy_russian_state_property_remains_compatible(self) -> None:
        catalog = _catalog(
            devices={
                "motion": ScenarioDeviceEntry(
                    target_id="motion",
                    name="Датчик движения",
                    entity_id="binary_sensor.motion",
                    actions=(),
                    properties=(
                        ScenarioDeviceProperty(
                            property_id="state",
                            label="Движение",
                            value_type="enum",
                            comparisons=("equals", "not_equals", "changed"),
                            options=(ScenarioPropertyOption("on", "Движение"),),
                        ),
                    ),
                )
            }
        )
        definition = _valid_definition(
            triggers=(
                ScenarioTrigger(
                    id="t1",
                    type=ScenarioTriggerType.DEVICE_STATE,
                    target_id="motion",
                    property="Состояние",
                    comparison=ScenarioComparison.EQUALS,
                    value="on",
                ),
            )
        )
        validate_scenario_definition(definition, catalog)

    def test_device_action_resolved_against_catalog(self) -> None:
        catalog = _catalog(
            devices={
                "light_living": ScenarioDeviceEntry(
                    target_id="light_living",
                    name="Living light",
                    entity_id="light.living",
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
            }
        )
        definition = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="light_living",
                    action_id="turn_on",
                ),
            )
        )
        validate_scenario_definition(definition, catalog)

    def test_device_action_unknown_device_returns_404(self) -> None:
        catalog = _catalog()
        definition = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="missing",
                    action_id="turn_on",
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog)
        self.assertEqual(raised.exception.status, HTTPStatus.NOT_FOUND)
        self.assertEqual(raised.exception.path, "definition.actions[0].targetId")

    def test_device_action_unknown_action_returns_422(self) -> None:
        catalog = _catalog(
            devices={
                "light_living": ScenarioDeviceEntry(
                    target_id="light_living",
                    name="Living light",
                    entity_id="light.living",
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
            }
        )
        definition = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="light_living",
                    action_id="turn_off",
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog)
        self.assertEqual(raised.exception.status, HTTPStatus.UNPROCESSABLE_ENTITY)
        self.assertEqual(raised.exception.path, "definition.actions[0].actionId")

    def test_command_snapshot_must_match_resolved_action(self) -> None:
        catalog = _catalog(
            devices={
                "light_living": ScenarioDeviceEntry(
                    target_id="light_living",
                    name="Living light",
                    entity_id="light.living",
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
            }
        )
        definition = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="light_living",
                    action_id="turn_on",
                    command=ScenarioDeviceCommand(
                        domain="switch",
                        service="turn_on",
                        entity_id="light.living",
                    ),
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog)
        self.assertEqual(raised.exception.path, "definition.actions[0].command.domain")

    def test_run_scenario_references_existing_scenario(self) -> None:
        catalog = _catalog(scenarios={"other": object()})
        definition = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="other",
                ),
            )
        )
        validate_scenario_definition(definition, catalog)

    def test_run_scenario_unknown_returns_404(self) -> None:
        catalog = _catalog()
        definition = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="missing",
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog)
        self.assertEqual(raised.exception.status, HTTPStatus.NOT_FOUND)

    def test_run_scenario_self_reference_returns_409(self) -> None:
        catalog = _catalog(scenarios={"self": object()})
        definition = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="self",
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog, existing_scenario_id="self")
        self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)

    def test_device_state_trigger_needs_known_device(self) -> None:
        catalog = _catalog()
        definition = _valid_definition(
            triggers=(
                ScenarioTrigger(
                    id="t1",
                    type=ScenarioTriggerType.DEVICE_STATE,
                    target_id="missing",
                    property="state",
                    comparison=ScenarioComparison.EQUALS,
                    value="on",
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog)
        self.assertEqual(raised.exception.path, "definition.triggers[0].targetId")

    def test_device_state_condition_needs_known_device(self) -> None:
        catalog = _catalog()
        definition = _valid_definition(
            conditions=(
                ScenarioCondition(
                    id="c1",
                    type=ScenarioConditionType.DEVICE_STATE,
                    target_id="missing",
                    property="state",
                    comparison=ScenarioComparison.EQUALS,
                    value="on",
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog)
        self.assertEqual(raised.exception.path, "definition.conditions[0].targetId")

    def test_duplicate_rule_ids_across_types_rejected(self) -> None:
        from custom_components.hausman_hub.domain.scenarios import ScenarioViolation

        with self.assertRaises(ScenarioViolation):
            _valid_definition(
                triggers=(ScenarioTrigger(id="same", type=ScenarioTriggerType.MANUAL),),
                conditions=(
                    ScenarioCondition(
                        id="same",
                        type=ScenarioConditionType.PRESENCE,
                        value="home",
                    ),
                ),
            )

    def test_action_value_allowlist_enforced(self) -> None:
        catalog = _catalog(
            devices={
                "light_living": ScenarioDeviceEntry(
                    target_id="light_living",
                    name="Living light",
                    entity_id="light.living",
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
            }
        )
        definition = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="light_living",
                    action_id="turn_on",
                    value="75%",
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog)
        self.assertEqual(raised.exception.path, "definition.actions[0].value")

    def test_action_value_required_when_allowlist_has_value(self) -> None:
        catalog = _catalog(
            devices={
                "light_living": ScenarioDeviceEntry(
                    target_id="light_living",
                    name="Living light",
                    entity_id="light.living",
                    actions=(
                        ScenarioDeviceAction(
                            action_id="set_brightness",
                            title="Brightness",
                            domain="light",
                            service="turn_on",
                            allowed_fields=frozenset({"value"}),
                        ),
                    ),
                )
            }
        )
        definition = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="light_living",
                    action_id="set_brightness",
                ),
            )
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(definition, catalog)
        self.assertEqual(raised.exception.path, "definition.actions[0].value")

    def test_indirect_recursion_detected(self) -> None:
        inner = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="self",
                ),
            )
        )
        outer = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="inner",
                ),
            )
        )
        catalog = _catalog(
            scenarios={"inner": object(), "self": object()},
            scenario_definitions={"inner": inner},
        )
        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(outer, catalog, existing_scenario_id="self")
        self.assertEqual(raised.exception.status, HTTPStatus.CONFLICT)

    def test_nested_depth_limit_is_rejected_before_save(self) -> None:
        leaf = _valid_definition()
        inner = _valid_definition(
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="leaf",
                ),
            )
        )
        outer = _valid_definition(
            safety_policy=ScenarioSafetyPolicy(nested_depth_limit=1),
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="inner",
                ),
            ),
        )
        catalog = _catalog(
            scenarios={"inner": object(), "leaf": object()},
            scenario_definitions={"inner": inner, "leaf": leaf},
        )

        with self.assertRaises(ScenarioDefinitionViolation) as raised:
            validate_scenario_definition(outer, catalog, existing_scenario_id="outer")

        self.assertEqual(HTTPStatus.CONFLICT, raised.exception.status)
        self.assertIn("depth limit", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
