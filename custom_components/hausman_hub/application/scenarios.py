"""Scenario validation and catalog resolution for HausmanHub."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus

from ..domain.scenarios import (
    MAX_ACTIONS,
    MAX_CONDITIONS,
    MAX_TRIGGERS,
    ScenarioAction,
    ScenarioActionType,
    ScenarioComparison,
    ScenarioCondition,
    ScenarioConditionType,
    ScenarioDefinition,
    ScenarioDeviceCommand,
    ScenarioExecutionMode,
    ScenarioTrigger,
    ScenarioTriggerType,
    ScenarioViolation,
)


class ScenarioDefinitionViolation(ScenarioViolation):
    """A scenario definition fails semantic validation against the live catalog."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "scenario_validation_failed",
        status: int = HTTPStatus.BAD_REQUEST,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.path = path


@dataclass(frozen=True, slots=True)
class ScenarioDeviceAction:
    """One resolved action that a physical device can perform."""

    action_id: str
    title: str
    domain: str
    service: str
    allowed_fields: frozenset[str]


@dataclass(frozen=True, slots=True)
class ScenarioDeviceEntry:
    """One device known to the scenario catalog."""

    target_id: str
    name: str
    entity_id: str
    actions: tuple[ScenarioDeviceAction, ...]

    def action(self, action_id: str) -> ScenarioDeviceAction | None:
        """Return one allowed action by id."""

        return next(
            (action for action in self.actions if action.action_id == action_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class ScenarioCatalog:
    """Live snapshot of devices and scenarios available to the validator."""

    devices: Mapping[str, ScenarioDeviceEntry]
    scenarios: Mapping[str, object]
    scenario_definitions: Mapping[str, ScenarioDefinition] = field(
        default_factory=dict
    )

    def device(self, target_id: str) -> ScenarioDeviceEntry | None:
        return self.devices.get(target_id)

    def has_scenario(self, scenario_id: str) -> bool:
        return scenario_id in self.scenarios

    def scenario_definition(self, scenario_id: str) -> ScenarioDefinition | None:
        return self.scenario_definitions.get(scenario_id)


def validate_scenario_definition(
    definition: ScenarioDefinition,
    catalog: ScenarioCatalog,
    *,
    existing_scenario_id: str | None = None,
) -> None:
    """Validate one scenario definition against the live catalog.

    Raises ScenarioDefinitionViolation with a ``path`` field matching the
    SCENARIO_EDITOR_API_CONTRACT.md error shape.
    """

    # Structural bounds are already enforced by the domain model, but this
    # function is the entry point for external payloads, so repeat the cheap
    # checks explicitly and with paths.
    if len(definition.triggers) > MAX_TRIGGERS:
        raise ScenarioDefinitionViolation(
            "too many triggers",
            path="definition.triggers",
        )
    if len(definition.conditions) > MAX_CONDITIONS:
        raise ScenarioDefinitionViolation(
            "too many conditions",
            path="definition.conditions",
        )
    if len(definition.actions) > MAX_ACTIONS:
        raise ScenarioDefinitionViolation(
            "too many actions",
            path="definition.actions",
        )
    if not definition.triggers:
        raise ScenarioDefinitionViolation(
            "scenario needs at least one trigger",
            path="definition.triggers",
        )
    if not definition.actions:
        raise ScenarioDefinitionViolation(
            "scenario needs at least one action",
            path="definition.actions",
        )
    if definition.execution_mode not in (
        ScenarioExecutionMode.SINGLE,
        ScenarioExecutionMode.RESTART,
        ScenarioExecutionMode.QUEUED,
    ):
        raise ScenarioDefinitionViolation(
            "unsupported execution mode",
            path="definition.executionMode",
        )

    _validate_unique_ids(definition)
    _validate_trigger_semantics(definition, catalog)
    _validate_condition_semantics(definition, catalog)
    _validate_action_semantics(definition, catalog, existing_scenario_id)
    _validate_no_cycles(definition, catalog, existing_scenario_id)


def _validate_unique_ids(definition: ScenarioDefinition) -> None:
    ids: set[str] = set()
    for index, trigger in enumerate(definition.triggers):
        if trigger.id in ids:
            raise ScenarioDefinitionViolation(
                f"duplicate rule id {trigger.id}",
                path=f"definition.triggers[{index}].id",
            )
        ids.add(trigger.id)
    for index, condition in enumerate(definition.conditions):
        if condition.id in ids:
            raise ScenarioDefinitionViolation(
                f"duplicate rule id {condition.id}",
                path=f"definition.conditions[{index}].id",
            )
        ids.add(condition.id)
    for index, action in enumerate(definition.actions):
        if action.id in ids:
            raise ScenarioDefinitionViolation(
                f"duplicate rule id {action.id}",
                path=f"definition.actions[{index}].id",
            )
        ids.add(action.id)


def _validate_trigger_semantics(
    definition: ScenarioDefinition, catalog: ScenarioCatalog
) -> None:
    for index, trigger in enumerate(definition.triggers):
        path = f"definition.triggers[{index}]"
        if trigger.type is ScenarioTriggerType.DEVICE_STATE:
            _require_device(
                catalog,
                trigger.target_id,
                f"{path}.targetId",
            )
        elif trigger.type is ScenarioTriggerType.PRESENCE:
            _require_presence_value(trigger.value, f"{path}.value")
        elif trigger.type is ScenarioTriggerType.TIME:
            _require_clock_time(trigger.value, f"{path}.value")
        elif trigger.type is ScenarioTriggerType.EVENT:
            if not trigger.event_type:
                raise ScenarioDefinitionViolation(
                    "event trigger needs eventType",
                    path=f"{path}.eventType",
                )


def _validate_condition_semantics(
    definition: ScenarioDefinition, catalog: ScenarioCatalog
) -> None:
    for index, condition in enumerate(definition.conditions):
        path = f"definition.conditions[{index}]"
        if condition.type is ScenarioConditionType.DEVICE_STATE:
            _require_device(
                catalog,
                condition.target_id,
                f"{path}.targetId",
            )
            if condition.comparison is ScenarioComparison.CHANGED:
                raise ScenarioDefinitionViolation(
                    "changed comparison is not allowed in conditions",
                    path=f"{path}.comparison",
                )
            if condition.value is None:
                raise ScenarioDefinitionViolation(
                    "device_state condition needs a value",
                    path=f"{path}.value",
                )
        elif condition.type is ScenarioConditionType.PRESENCE:
            _require_presence_value(condition.value, f"{path}.value")
        elif condition.type is ScenarioConditionType.TIME_WINDOW:
            _require_time_window(condition.value, f"{path}.value")
        elif condition.type is ScenarioConditionType.WEEKDAY:
            _require_weekday(condition.value, f"{path}.value")


def _validate_action_semantics(
    definition: ScenarioDefinition,
    catalog: ScenarioCatalog,
    existing_scenario_id: str | None,
) -> None:
    for index, action in enumerate(definition.actions):
        path = f"definition.actions[{index}]"
        if action.type is ScenarioActionType.DEVICE_ACTION:
            _validate_device_action(action, catalog, path)
        elif action.type is ScenarioActionType.DELAY:
            if action.delay_seconds is None or not (
                1 <= action.delay_seconds <= 86400
            ):
                raise ScenarioDefinitionViolation(
                    "delay must be between 1 and 86400 seconds",
                    path=f"{path}.delaySeconds",
                )
        elif action.type is ScenarioActionType.RUN_SCENARIO:
            _validate_run_scenario_action(
                action, catalog, existing_scenario_id, path
            )
        elif action.type is ScenarioActionType.NOTIFICATION:
            if not action.message:
                raise ScenarioDefinitionViolation(
                    "notification message is required",
                    path=f"{path}.message",
                )


def _validate_device_action(
    action: ScenarioAction,
    catalog: ScenarioCatalog,
    path: str,
) -> None:
    if not action.target_id:
        raise ScenarioDefinitionViolation(
            "device_action needs targetId",
            path=f"{path}.targetId",
        )
    if not action.action_id:
        raise ScenarioDefinitionViolation(
            "device_action needs actionId",
            path=f"{path}.actionId",
        )
    device = catalog.device(action.target_id)
    if device is None:
        raise ScenarioDefinitionViolation(
            f"device {action.target_id} is not available",
            path=f"{path}.targetId",
            status=HTTPStatus.NOT_FOUND,
        )
    allowed = device.action(action.action_id)
    if allowed is None:
        raise ScenarioDefinitionViolation(
            f"action {action.action_id} is not available for device {action.target_id}",
            path=f"{path}.actionId",
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
        )
    if action.value is not None and "value" not in allowed.allowed_fields:
        raise ScenarioDefinitionViolation(
            f"action {action.action_id} does not accept a value",
            path=f"{path}.value",
        )
    if action.value is None and "value" in allowed.allowed_fields:
        raise ScenarioDefinitionViolation(
            f"action {action.action_id} requires a value",
            path=f"{path}.value",
        )
    if action.command is not None:
        if action.command.domain != allowed.domain:
            raise ScenarioDefinitionViolation(
                "client command domain does not match the resolved action",
                path=f"{path}.command.domain",
            )
        if action.command.service != allowed.service:
            raise ScenarioDefinitionViolation(
                "client command service does not match the resolved action",
                path=f"{path}.command.service",
            )
        if action.command.entity_id != device.entity_id:
            raise ScenarioDefinitionViolation(
                "client command entity_id does not match the resolved device",
                path=f"{path}.command.entity_id",
            )


def _validate_run_scenario_action(
    action: ScenarioAction,
    catalog: ScenarioCatalog,
    existing_scenario_id: str | None,
    path: str,
) -> None:
    if not action.scenario_id:
        raise ScenarioDefinitionViolation(
            "run_scenario action needs scenarioId",
            path=f"{path}.scenarioId",
        )
    if action.scenario_id == existing_scenario_id:
        raise ScenarioDefinitionViolation(
            "scenario cannot call itself",
            path=f"{path}.scenarioId",
            status=HTTPStatus.CONFLICT,
        )
    if not catalog.has_scenario(action.scenario_id):
        raise ScenarioDefinitionViolation(
            f"scenario {action.scenario_id} does not exist",
            path=f"{path}.scenarioId",
            status=HTTPStatus.NOT_FOUND,
        )


def _validate_no_cycles(
    definition: ScenarioDefinition,
    catalog: ScenarioCatalog,
    existing_scenario_id: str | None,
) -> None:
    """Detect direct or indirect recursion through run_scenario actions."""

    if existing_scenario_id is None:
        return

    def visit(scenario_id: str, path: str, visiting: set[str]) -> None:
        if scenario_id in visiting:
            raise ScenarioDefinitionViolation(
                "recursive scenario call detected",
                path=path,
                status=HTTPStatus.CONFLICT,
            )
        target = catalog.scenario_definition(scenario_id)
        if target is None:
            return
        visiting = visiting | {scenario_id}
        for index, action in enumerate(target.actions):
            if action.type is ScenarioActionType.RUN_SCENARIO and action.scenario_id:
                visit(
                    action.scenario_id,
                    f"definition.actions[{index}].scenarioId",
                    visiting,
                )

    for index, action in enumerate(definition.actions):
        if action.type is ScenarioActionType.RUN_SCENARIO and action.scenario_id:
            visit(
                action.scenario_id,
                f"definition.actions[{index}].scenarioId",
                {existing_scenario_id},
            )


def _require_device(
    catalog: ScenarioCatalog,
    target_id: str | None,
    path: str,
) -> None:
    if not target_id:
        raise ScenarioDefinitionViolation(
            "targetId is required",
            path=path,
        )
    if catalog.device(target_id) is None:
        raise ScenarioDefinitionViolation(
            f"device {target_id} is not available",
            path=path,
            status=HTTPStatus.NOT_FOUND,
        )


def _require_presence_value(value: object, path: str) -> None:
    if value not in ("home", "away"):
        raise ScenarioDefinitionViolation(
            "presence value must be home or away",
            path=path,
        )


def _require_clock_time(value: object, path: str) -> None:
    import re

    if not isinstance(value, str) or not re.match(
        r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$", value
    ):
        raise ScenarioDefinitionViolation(
            "time value must use HH:mm",
            path=path,
        )


def _require_time_window(value: object, path: str) -> None:
    import re

    if not isinstance(value, str) or not re.match(
        r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]-(?:[01][0-9]|2[0-3]):[0-5][0-9]$",
        value,
    ):
        raise ScenarioDefinitionViolation(
            "time window must use HH:mm-HH:mm",
            path=path,
        )


def _require_weekday(value: object, path: str) -> None:
    import re

    if not isinstance(value, str) or not re.match(
        r"^(?:пн|вт|ср|чт|пт|сб|вс)(?:, (?:пн|вт|ср|чт|пт|сб|вс))*$",
        value,
    ):
        raise ScenarioDefinitionViolation(
            "weekday must be a comma-separated list of пн..вс",
            path=path,
        )
