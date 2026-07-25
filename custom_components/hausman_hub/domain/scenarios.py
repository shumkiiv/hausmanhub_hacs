"""Versioned scenario definition domain for HausmanHub.

The model follows the HausmanHub Scenario Editor API Contract v1:
https://github.com/shumkiiv/hausmanhub_hacs/blob/main/docs/SCENARIO_EDITOR_API_CONTRACT.md
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import re


SCENARIO_REGISTRY_VERSION = 1
MAX_TRIGGERS = 16
MAX_CONDITIONS = 32
MAX_ACTIONS = 64
MAX_SCENARIO_ID_LENGTH = 64
MAX_TITLE_LENGTH = 120
MAX_GROUP_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 500
MAX_MESSAGE_LENGTH = 500
MIN_DELAY_SECONDS = 1
MAX_DELAY_SECONDS = 86400

_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CLOCK_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_TIME_WINDOW = re.compile(
    r"^(?:[01][0-9]|2[0-3]):[0-5][0-9][\-\u2013](?:[01][0-9]|2[0-3]):[0-5][0-9]$"
)
_WEEKDAY = re.compile(r"^(?:пн|вт|ср|чт|пт|сб|вс)(?:, (?:пн|вт|ср|чт|пт|сб|вс))*$")


class ScenarioViolation(ValueError):
    """A scenario definition is incomplete or internally inconsistent."""


class ScenarioExecutionMode(StrEnum):
    """How repeated scenario runs interact."""

    SINGLE = "single"
    RESTART = "restart"
    QUEUED = "queued"


class ScenarioTriggerType(StrEnum):
    """Supported scenario trigger kinds."""

    MANUAL = "manual"
    TIME = "time"
    DEVICE_STATE = "device_state"
    SUNRISE = "sunrise"
    SUNSET = "sunset"
    PRESENCE = "presence"


class ScenarioConditionType(StrEnum):
    """Supported scenario condition kinds."""

    DEVICE_STATE = "device_state"
    TIME_WINDOW = "time_window"
    PRESENCE = "presence"
    WEEKDAY = "weekday"


class ScenarioActionType(StrEnum):
    """Supported scenario action kinds."""

    DEVICE_ACTION = "device_action"
    DELAY = "delay"
    RUN_SCENARIO = "run_scenario"
    NOTIFICATION = "notification"
    EXISTING_ACTION = "existing_action"


class ScenarioComparison(StrEnum):
    """How a device-state value is compared."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    ABOVE = "above"
    BELOW = "below"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class ScenarioTrigger:
    """One event that can start a scenario run."""

    id: str
    type: ScenarioTriggerType
    target_id: str | None = None
    target_name: str | None = None
    property: str | None = None
    comparison: ScenarioComparison | None = None
    value: str | float | int | None = None

    def __post_init__(self) -> None:
        _stable_id(self.id, "trigger id")
        if not isinstance(self.type, ScenarioTriggerType):
            raise ScenarioViolation("trigger type must be approved")
        # Android always emits `comparison`; ignore it for non-device_state triggers.
        if self.comparison is not None and self.comparison not in (
            ScenarioComparison.EQUALS,
            ScenarioComparison.NOT_EQUALS,
            ScenarioComparison.ABOVE,
            ScenarioComparison.BELOW,
            ScenarioComparison.CHANGED,
        ):
            raise ScenarioViolation("trigger comparison must be approved")
        if self.type is ScenarioTriggerType.MANUAL:
            _none(self.target_id, "trigger target_id", "manual trigger")
            _none(self.property, "trigger property", "manual trigger")
            _none(self.value, "trigger value", "manual trigger")
        elif self.type is ScenarioTriggerType.TIME:
            _none(self.target_id, "trigger target_id", "time trigger")
            _none(self.property, "trigger property", "time trigger")
            _clock_time(self.value, "time trigger value")
        elif self.type in (
            ScenarioTriggerType.SUNRISE,
            ScenarioTriggerType.SUNSET,
        ):
            _none(self.target_id, "trigger target_id", f"{self.type.value} trigger")
            _none(self.property, "trigger property", f"{self.type.value} trigger")
            if self.value is not None:
                _offset_minutes(self.value, f"{self.type.value} trigger offset")
        elif self.type is ScenarioTriggerType.DEVICE_STATE:
            _required(self.target_id, "trigger target_id", "device_state trigger")
            _required(self.property, "trigger property", "device_state trigger")
            _required(self.comparison, "trigger comparison", "device_state trigger")
            if not isinstance(self.comparison, ScenarioComparison):
                raise ScenarioViolation("trigger comparison must be approved")
            if self.comparison is ScenarioComparison.CHANGED:
                _none(self.value, "trigger value", "changed comparison")
            else:
                _required(self.value, "trigger value", "device_state trigger")
        elif self.type is ScenarioTriggerType.PRESENCE:
            _none(self.target_id, "trigger target_id", "presence trigger")
            _none(self.property, "trigger property", "presence trigger")
            _presence_value(self.value, "presence trigger")


@dataclass(frozen=True, slots=True)
class ScenarioCondition:
    """One guard that must be true when a scenario starts."""

    id: str
    type: ScenarioConditionType
    target_id: str | None = None
    target_name: str | None = None
    property: str | None = None
    comparison: ScenarioComparison | None = None
    value: str | float | int | None = None

    def __post_init__(self) -> None:
        _stable_id(self.id, "condition id")
        if not isinstance(self.type, ScenarioConditionType):
            raise ScenarioViolation("condition type must be approved")
        # Android always emits `comparison`; ignore it for non-device_state conditions.
        if self.comparison is not None and self.comparison not in (
            ScenarioComparison.EQUALS,
            ScenarioComparison.NOT_EQUALS,
            ScenarioComparison.ABOVE,
            ScenarioComparison.BELOW,
        ):
            raise ScenarioViolation("condition comparison must be approved")
        if self.type is ScenarioConditionType.DEVICE_STATE:
            _required(self.target_id, "condition target_id", "device_state condition")
            _required(self.property, "condition property", "device_state condition")
            _required(
                self.comparison, "condition comparison", "device_state condition"
            )
            if not isinstance(self.comparison, ScenarioComparison):
                raise ScenarioViolation("condition comparison must be approved")
            if self.comparison is ScenarioComparison.CHANGED:
                raise ScenarioViolation(
                    "changed comparison is not allowed in conditions"
                )
            _required(self.value, "condition value", "device_state condition")
        elif self.type is ScenarioConditionType.TIME_WINDOW:
            _none(self.target_id, "condition target_id", "time_window condition")
            _none(self.property, "condition property", "time_window condition")
            _time_window(self.value, "time_window condition")
        elif self.type is ScenarioConditionType.PRESENCE:
            _none(self.target_id, "condition target_id", "presence condition")
            _none(self.property, "condition property", "presence condition")
            _presence_value(self.value, "presence condition")
        elif self.type is ScenarioConditionType.WEEKDAY:
            _none(self.target_id, "condition target_id", "weekday condition")
            _none(self.property, "condition property", "weekday condition")
            _weekday(self.value, "weekday condition")


@dataclass(frozen=True, slots=True)
class ScenarioDeviceCommand:
    """Snapshot of a resolved device command for diagnostics only."""

    domain: str
    service: str
    entity_id: str

    def __post_init__(self) -> None:
        _non_empty_string(self.domain, "command domain")
        _non_empty_string(self.service, "command service")
        _non_empty_string(self.entity_id, "command entity_id")


@dataclass(frozen=True, slots=True)
class ScenarioAction:
    """One step executed sequentially inside a scenario run."""

    id: str
    type: ScenarioActionType
    target_id: str | None = None
    target_name: str | None = None
    action_id: str | None = None
    action_title: str | None = None
    value: str | float | int | None = None
    command: ScenarioDeviceCommand | None = None
    delay_seconds: int | None = None
    scenario_id: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        _stable_id(self.id, "action id")
        if not isinstance(self.type, ScenarioActionType):
            raise ScenarioViolation("action type must be approved")
        if self.type is ScenarioActionType.DEVICE_ACTION:
            _required(self.target_id, "action target_id", "device_action")
            _required(self.action_id, "action action_id", "device_action")
            if self.command is not None and not isinstance(
                self.command, ScenarioDeviceCommand
            ):
                raise ScenarioViolation("action command must be a command object")
        elif self.type is ScenarioActionType.DELAY:
            _required(self.delay_seconds, "action delay_seconds", "delay")
            if type(self.delay_seconds) is not int:
                raise ScenarioViolation("delay must be an integer")
            if self.delay_seconds < MIN_DELAY_SECONDS:
                raise ScenarioViolation("delay must be at least 1 second")
            if self.delay_seconds > MAX_DELAY_SECONDS:
                raise ScenarioViolation("delay must be at most 86400 seconds")
        elif self.type is ScenarioActionType.RUN_SCENARIO:
            _required(self.scenario_id, "action scenario_id", "run_scenario")
        elif self.type is ScenarioActionType.NOTIFICATION:
            _required(self.message, "action message", "notification")
            if (
                not isinstance(self.message, str)
                or not self.message.strip()
                or len(self.message) > MAX_MESSAGE_LENGTH
            ):
                raise ScenarioViolation("notification message is invalid")
        elif self.type is ScenarioActionType.EXISTING_ACTION:
            _required(self.target_id, "action target_id", "existing_action")


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """Structured logic of one scenario."""

    version: int
    execution_mode: ScenarioExecutionMode
    triggers: tuple[ScenarioTrigger, ...]
    conditions: tuple[ScenarioCondition, ...]
    actions: tuple[ScenarioAction, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "triggers", tuple(self.triggers))
        object.__setattr__(self, "conditions", tuple(self.conditions))
        object.__setattr__(self, "actions", tuple(self.actions))
        if self.version != 1:
            raise ScenarioViolation("unsupported scenario definition version")
        if not isinstance(self.execution_mode, ScenarioExecutionMode):
            raise ScenarioViolation("execution mode must be approved")
        if len(self.triggers) > MAX_TRIGGERS:
            raise ScenarioViolation("too many triggers")
        if len(self.conditions) > MAX_CONDITIONS:
            raise ScenarioViolation("too many conditions")
        if len(self.actions) > MAX_ACTIONS:
            raise ScenarioViolation("too many actions")
        if not self.triggers:
            raise ScenarioViolation("scenario needs at least one trigger")
        if not self.actions:
            raise ScenarioViolation("scenario needs at least one action")
        if any(not isinstance(item, ScenarioTrigger) for item in self.triggers):
            raise ScenarioViolation("trigger must be validated")
        if any(not isinstance(item, ScenarioCondition) for item in self.conditions):
            raise ScenarioViolation("condition must be validated")
        if any(not isinstance(item, ScenarioAction) for item in self.actions):
            raise ScenarioViolation("action must be validated")
        _unique(
            (item.id for item in (*self.triggers, *self.conditions, *self.actions)),
            "rule ids",
        )

    @classmethod
    def from_payload(cls, payload: object) -> "ScenarioDefinition":
        """Decode a scenario definition from the wire/persistence format."""

        return _definition_from_payload(payload, "definition")

    def to_payload(self) -> dict[str, object]:
        """Encode the definition to the wire/persistence format."""

        return _definition_to_payload(self)


@dataclass(frozen=True, slots=True)
class Scenario:
    """One user-defined or built-in scenario exposed to clients."""

    id: str
    title: str
    group: str
    description: str
    icon: str
    enabled: bool
    favorite: bool
    danger: bool
    requires_confirmation: bool
    trigger_description: str
    condition_description: str
    action_description: str
    updated_at: int
    definition: ScenarioDefinition

    def __post_init__(self) -> None:
        _stable_id(self.id, "scenario id")
        _title(self.title, "scenario title")
        _non_empty_string(self.group, "scenario group")
        if len(self.group) > MAX_GROUP_LENGTH:
            raise ScenarioViolation("scenario group is too long")
        if not isinstance(self.description, str):
            raise ScenarioViolation("scenario description must be a string")
        if len(self.description) > MAX_DESCRIPTION_LENGTH:
            raise ScenarioViolation("scenario description is too long")
        if not isinstance(self.icon, str):
            raise ScenarioViolation("scenario icon must be a string")
        if type(self.enabled) is not bool:
            raise ScenarioViolation("scenario enabled must be boolean")
        if type(self.favorite) is not bool:
            raise ScenarioViolation("scenario favorite must be boolean")
        if type(self.danger) is not bool:
            raise ScenarioViolation("scenario danger must be boolean")
        if type(self.requires_confirmation) is not bool:
            raise ScenarioViolation("scenario requires_confirmation must be boolean")
        if self.danger and not self.requires_confirmation:
            raise ScenarioViolation("dangerous scenarios require confirmation")
        if not isinstance(self.trigger_description, str):
            raise ScenarioViolation("trigger description must be a string")
        if len(self.trigger_description) > MAX_DESCRIPTION_LENGTH:
            raise ScenarioViolation("trigger description is too long")
        if not isinstance(self.condition_description, str):
            raise ScenarioViolation("condition description must be a string")
        if len(self.condition_description) > MAX_DESCRIPTION_LENGTH:
            raise ScenarioViolation("condition description is too long")
        if not isinstance(self.action_description, str):
            raise ScenarioViolation("action description must be a string")
        if len(self.action_description) > MAX_DESCRIPTION_LENGTH:
            raise ScenarioViolation("action description is too long")
        if type(self.updated_at) is not int or self.updated_at < 0:
            raise ScenarioViolation("scenario updated_at must be a non-negative integer")
        if not isinstance(self.definition, ScenarioDefinition):
            raise ScenarioViolation("scenario definition must be validated")

    @classmethod
    def from_definition(
        cls,
        scenario_id: str,
        title: str,
        definition: ScenarioDefinition,
        *,
        enabled: bool = True,
        group: str = "custom",
        description: str = "No description",
        icon: str = "mdi:script",
        favorite: bool = False,
        danger: bool = False,
        requires_confirmation: bool = False,
        trigger_description: str = "None",
        condition_description: str = "None",
        action_description: str = "None",
        updated_at: int | None = None,
    ) -> "Scenario":
        """Build a full persisted scenario from a validated definition."""

        if updated_at is None:
            updated_at = int(__import__("time").time())
        return cls(
            id=scenario_id,
            title=title,
            group=group,
            description=description,
            icon=icon,
            enabled=enabled,
            favorite=favorite,
            danger=danger,
            requires_confirmation=requires_confirmation,
            trigger_description=trigger_description,
            condition_description=condition_description,
            action_description=action_description,
            updated_at=updated_at,
            definition=definition,
        )


@dataclass(frozen=True, slots=True)
class ScenarioRegistry:
    """Complete versioned collection of HausmanHub-owned scenarios."""

    scenarios: tuple[Scenario, ...] = ()
    version: int = SCENARIO_REGISTRY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenarios", tuple(self.scenarios))
        if self.version != SCENARIO_REGISTRY_VERSION:
            raise ScenarioViolation("unsupported scenario registry version")
        if any(not isinstance(item, Scenario) for item in self.scenarios):
            raise ScenarioViolation("scenario must be validated")
        _unique((item.id for item in self.scenarios), "scenario ids")

    def scenario(self, scenario_id: str) -> Scenario | None:
        """Return one scenario by its stable public identifier."""

        return next(
            (item for item in self.scenarios if item.id == scenario_id),
            None,
        )

    @classmethod
    def from_storage(cls, payload: object) -> "ScenarioRegistry":
        """Decode one persisted scenario registry."""

        return scenario_registry_from_payload(payload)

    def to_storage(self) -> dict[str, object]:
        """Encode the registry for persistence."""

        return scenario_registry_to_payload(self)


# --- Helpers ---


def _non_empty_string(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioViolation(f"{label} must be a non-empty string")


def _title(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > MAX_TITLE_LENGTH
    ):
        raise ScenarioViolation(f"{label} must be non-empty and at most 120 characters")


def _stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ScenarioViolation(f"{label} must be a stable lowercase id")


def _unique(values: object, label: str) -> None:
    items = tuple(values)  # type: ignore[arg-type]
    if len(items) != len(set(items)):
        raise ScenarioViolation(f"{label} must be unique")


def _required(value: object, label: str, context: str) -> None:
    if value is None:
        raise ScenarioViolation(f"{label} is required for {context}")


def _none(value: object, label: str, context: str) -> None:
    if value is not None:
        raise ScenarioViolation(f"{label} must not be set for {context}")


def _clock_time(value: object, label: str) -> None:
    if not isinstance(value, str) or _CLOCK_TIME.fullmatch(value) is None:
        raise ScenarioViolation(f"{label} must use HH:mm")


def _time_window(value: object, label: str) -> None:
    if not isinstance(value, str) or _TIME_WINDOW.fullmatch(value) is None:
        raise ScenarioViolation(f"{label} must use HH:mm-HH:mm")


def _weekday(value: object, label: str) -> None:
    if not isinstance(value, str) or _WEEKDAY.fullmatch(value) is None:
        raise ScenarioViolation(f"{label} must be a comma-separated list of пн..вс")


def _presence_value(value: object, label: str) -> None:
    if value not in ("home", "away"):
        raise ScenarioViolation(f"{label} must be home or away")


def _integer_minutes(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioViolation(f"{label} must be an integer")
    return value


def _offset_minutes(value: object, label: str) -> int:
    """Accept integer offset or numeric string for Android compatibility."""

    if isinstance(value, bool):
        raise ScenarioViolation(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as error:
            raise ScenarioViolation(f"{label} must be an integer") from error
    raise ScenarioViolation(f"{label} must be an integer")


# --- Payload conversion ---


def scenario_registry_from_payload(payload: object) -> ScenarioRegistry:
    """Decode one exact persisted scenario registry."""

    root = _mapping(payload, "scenario registry")
    _exact_keys(root, {"version", "scenarios"}, "scenario registry")
    if root.get("version") != SCENARIO_REGISTRY_VERSION:
        raise ScenarioViolation("unsupported scenario registry version")
    raw_scenarios = _list(root.get("scenarios"), "scenarios")
    scenarios: list[Scenario] = []
    for index, raw in enumerate(raw_scenarios):
        scenarios.append(_scenario_from_payload(raw, f"scenario {index}"))
    return ScenarioRegistry(scenarios=tuple(scenarios))


def scenario_registry_to_payload(registry: ScenarioRegistry) -> dict[str, object]:
    """Encode one scenario registry to a persistable payload."""

    return {
        "version": registry.version,
        "scenarios": [_scenario_to_payload(item) for item in registry.scenarios],
    }


def _scenario_from_payload(payload: object, label: str) -> Scenario:
    root = _mapping(payload, label)
    _exact_keys(
        root,
        {
            "id",
            "title",
            "group",
            "description",
            "icon",
            "enabled",
            "favorite",
            "danger",
            "requiresConfirmation",
            "triggerDescription",
            "conditionDescription",
            "actionDescription",
            "updatedAt",
            "definition",
        },
        label,
    )
    return Scenario(
        id=_str(root.get("id"), f"{label} id"),
        title=_str(root.get("title"), f"{label} title"),
        group=_str(root.get("group"), f"{label} group"),
        description=_str(root.get("description"), f"{label} description"),
        icon=_str(root.get("icon"), f"{label} icon"),
        enabled=_bool(root.get("enabled"), f"{label} enabled"),
        favorite=_bool(root.get("favorite"), f"{label} favorite"),
        danger=_bool(root.get("danger"), f"{label} danger"),
        requires_confirmation=_bool(
            root.get("requiresConfirmation"), f"{label} requiresConfirmation"
        ),
        trigger_description=_str(
            root.get("triggerDescription"), f"{label} triggerDescription"
        ),
        condition_description=_str(
            root.get("conditionDescription"), f"{label} conditionDescription"
        ),
        action_description=_str(
            root.get("actionDescription"), f"{label} actionDescription"
        ),
        updated_at=_int(root.get("updatedAt"), f"{label} updatedAt"),
        definition=_definition_from_payload(root.get("definition"), f"{label} definition"),
    )


def _scenario_to_payload(scenario: Scenario) -> dict[str, object]:
    return {
        "id": scenario.id,
        "title": scenario.title,
        "group": scenario.group,
        "description": scenario.description,
        "icon": scenario.icon,
        "enabled": scenario.enabled,
        "favorite": scenario.favorite,
        "danger": scenario.danger,
        "requiresConfirmation": scenario.requires_confirmation,
        "triggerDescription": scenario.trigger_description,
        "conditionDescription": scenario.condition_description,
        "actionDescription": scenario.action_description,
        "updatedAt": scenario.updated_at,
        "definition": _definition_to_payload(scenario.definition),
    }


def _definition_from_payload(payload: object, label: str) -> ScenarioDefinition:
    root = _mapping(payload, label)
    _exact_keys(root, {"version", "executionMode", "triggers", "conditions", "actions"}, label)
    return ScenarioDefinition(
        version=_int(root.get("version"), f"{label} version"),
        execution_mode=_enum(
            root.get("executionMode"),
            ScenarioExecutionMode,
            f"{label} executionMode",
        ),
        triggers=tuple(
            _trigger_from_payload(item, f"{label} trigger {index}")
            for index, item in enumerate(_list(root.get("triggers"), f"{label} triggers"))
        ),
        conditions=tuple(
            _condition_from_payload(item, f"{label} condition {index}")
            for index, item in enumerate(
                _list(root.get("conditions"), f"{label} conditions")
            )
        ),
        actions=tuple(
            _action_from_payload(item, f"{label} action {index}")
            for index, item in enumerate(_list(root.get("actions"), f"{label} actions"))
        ),
    )


def _definition_to_payload(definition: ScenarioDefinition) -> dict[str, object]:
    return {
        "version": definition.version,
        "executionMode": definition.execution_mode.value,
        "triggers": [_trigger_to_payload(item) for item in definition.triggers],
        "conditions": [_condition_to_payload(item) for item in definition.conditions],
        "actions": [_action_to_payload(item) for item in definition.actions],
    }


def _trigger_from_payload(payload: object, label: str) -> ScenarioTrigger:
    root = _mapping(payload, label)
    return ScenarioTrigger(
        id=_str(root.get("id"), f"{label} id"),
        type=_enum(root.get("type"), ScenarioTriggerType, f"{label} type"),
        target_id=_optional_str(root.get("targetId")),
        target_name=_optional_str(root.get("targetName")),
        property=_optional_str(root.get("property")),
        comparison=_optional_enum(root.get("comparison"), ScenarioComparison),
        value=root.get("value"),
    )


def _trigger_to_payload(trigger: ScenarioTrigger) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": trigger.id,
        "type": trigger.type.value,
    }
    if trigger.target_id is not None:
        payload["targetId"] = trigger.target_id
    if trigger.target_name is not None:
        payload["targetName"] = trigger.target_name
    if trigger.property is not None:
        payload["property"] = trigger.property
    if trigger.comparison is not None:
        payload["comparison"] = trigger.comparison.value
    if trigger.value is not None:
        payload["value"] = trigger.value
    return payload


def _condition_from_payload(payload: object, label: str) -> ScenarioCondition:
    root = _mapping(payload, label)
    return ScenarioCondition(
        id=_str(root.get("id"), f"{label} id"),
        type=_enum(root.get("type"), ScenarioConditionType, f"{label} type"),
        target_id=_optional_str(root.get("targetId")),
        target_name=_optional_str(root.get("targetName")),
        property=_optional_str(root.get("property")),
        comparison=_optional_enum(root.get("comparison"), ScenarioComparison),
        value=root.get("value"),
    )


def _condition_to_payload(condition: ScenarioCondition) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": condition.id,
        "type": condition.type.value,
    }
    if condition.target_id is not None:
        payload["targetId"] = condition.target_id
    if condition.target_name is not None:
        payload["targetName"] = condition.target_name
    if condition.property is not None:
        payload["property"] = condition.property
    if condition.comparison is not None:
        payload["comparison"] = condition.comparison.value
    if condition.value is not None:
        payload["value"] = condition.value
    return payload


def _action_from_payload(payload: object, label: str) -> ScenarioAction:
    root = _mapping(payload, label)
    return ScenarioAction(
        id=_str(root.get("id"), f"{label} id"),
        type=_enum(root.get("type"), ScenarioActionType, f"{label} type"),
        target_id=_optional_str(root.get("targetId")),
        target_name=_optional_str(root.get("targetName")),
        action_id=_optional_str(root.get("actionId")),
        action_title=_optional_str(root.get("actionTitle")),
        value=root.get("value"),
        command=_optional_command(root.get("command")),
        delay_seconds=_optional_int(root.get("delaySeconds")),
        scenario_id=_optional_str(root.get("scenarioId")),
        message=_optional_str(root.get("message")),
    )


def _action_to_payload(action: ScenarioAction) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": action.id,
        "type": action.type.value,
    }
    if action.target_id is not None:
        payload["targetId"] = action.target_id
    if action.target_name is not None:
        payload["targetName"] = action.target_name
    if action.action_id is not None:
        payload["actionId"] = action.action_id
    if action.action_title is not None:
        payload["actionTitle"] = action.action_title
    if action.value is not None:
        payload["value"] = action.value
    if action.command is not None:
        payload["command"] = {
            "domain": action.command.domain,
            "service": action.command.service,
            "entity_id": action.command.entity_id,
        }
    if action.delay_seconds is not None:
        payload["delaySeconds"] = action.delay_seconds
    if action.scenario_id is not None:
        payload["scenarioId"] = action.scenario_id
    if action.message is not None:
        payload["message"] = action.message
    return payload


def _optional_command(payload: object) -> ScenarioDeviceCommand | None:
    if payload is None:
        return None
    root = _mapping(payload, "command")
    _exact_keys(root, {"domain", "service", "entity_id"}, "command")
    return ScenarioDeviceCommand(
        domain=_str(root.get("domain"), "command domain"),
        service=_str(root.get("service"), "command service"),
        entity_id=_str(root.get("entity_id"), "command entity_id"),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScenarioViolation("expected string or null")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioViolation("expected integer or null")
    return value


def _optional_enum(value: object, enum_cls: type[StrEnum]) -> StrEnum | None:
    if value is None:
        return None
    return _enum(value, enum_cls, "optional enum")


def _str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ScenarioViolation(f"{label} must be a string")
    return value


def _bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ScenarioViolation(f"{label} must be a boolean")
    return value


def _int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioViolation(f"{label} must be an integer")
    return value


def _enum(value: object, enum_cls: type[StrEnum], label: str) -> StrEnum:
    if not isinstance(value, str):
        raise ScenarioViolation(f"{label} must be a string enum")
    try:
        return enum_cls(value)
    except ValueError as error:
        raise ScenarioViolation(f"{label} has unsupported value") from error


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ScenarioViolation(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ScenarioViolation(f"{label} must be a list")
    return value


def _exact_keys(root: Mapping[str, object], keys: set[str], label: str) -> None:
    if set(root) != keys:
        raise ScenarioViolation(f"{label} has unexpected fields")
