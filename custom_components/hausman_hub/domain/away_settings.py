"""Validated configuration for the configurable "away from home" automations.

The document is additive: an empty document keeps the legacy behaviour and the
runtime stays inactive. Only an explicitly saved configuration activates the
Hausman-owned away/return engine.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

MAX_AWAY_TRIGGERS = 8
MAX_AWAY_ACTIONS = 64

AWAY_ACTIONS = frozenset(
    {
        "turn_on",
        "turn_off",
        "set_brightness_percent",
        "set_color_temperature",
    }
)
TRIGGER_ACTIVE_STATES = frozenset(
    {
        "on",
        "off",
        "locked",
        "unlocked",
        "open",
        "closed",
        "home",
        "not_home",
        "detected",
        "clear",
    }
)
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
_LABEL = re.compile(r"^[^\x00-\x1f]{0,80}$")


class AwaySettingsViolation(ValueError):
    """An away-mode document is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class AwayTrigger:
    """One entity whose state indicates that the home is away."""

    entity_id: str
    active_state: str
    for_seconds: int = 0
    label: str = ""

    def __post_init__(self) -> None:
        _entity_id(self.entity_id, "trigger entity")
        if not isinstance(self.active_state, str) or self.active_state not in TRIGGER_ACTIVE_STATES:
            raise AwaySettingsViolation("away trigger active state is invalid")
        if (
            type(self.for_seconds) is not int
            or not 0 <= self.for_seconds <= 3600
        ):
            raise AwaySettingsViolation("away trigger delay is invalid")
        _label(self.label)


@dataclass(frozen=True, slots=True)
class AwayAction:
    """One bounded device action applied on away or on return."""

    target_id: str
    action_id: str
    value: int | None = None
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not self.target_id or len(self.target_id) > 128:
            raise AwaySettingsViolation("away action target is invalid")
        if not isinstance(self.action_id, str) or self.action_id not in AWAY_ACTIONS:
            raise AwaySettingsViolation("away action id is invalid")
        if self.action_id in {"turn_on", "turn_off"}:
            if self.value is not None:
                raise AwaySettingsViolation("turn actions must not carry a value")
        elif self.action_id == "set_brightness_percent":
            if type(self.value) is not int or not 0 <= self.value <= 100:
                raise AwaySettingsViolation("brightness value is invalid")
        elif self.action_id == "set_color_temperature":
            if type(self.value) is not int or not 1500 <= self.value <= 10000:
                raise AwaySettingsViolation("color temperature value is invalid")
        _label(self.label)


@dataclass(frozen=True, slots=True)
class AwaySettings:
    """Complete away-mode configuration."""

    triggers: tuple[AwayTrigger, ...] = ()
    away_actions: tuple[AwayAction, ...] = ()
    return_actions: tuple[AwayAction, ...] = ()

    @property
    def active(self) -> bool:
        """Whether the configurable engine should be armed."""

        return bool(self.triggers)

    def trigger_entity_ids(self) -> frozenset[str]:
        return frozenset(item.entity_id for item in self.triggers)


def validate_away_settings(value: object) -> AwaySettings:
    """Validate one bounded public away-settings payload."""

    if not isinstance(value, Mapping):
        raise AwaySettingsViolation("away settings document is invalid")
    fields = set(value)
    if fields != {"triggers", "awayActions", "returnActions"}:
        raise AwaySettingsViolation("away settings fields are invalid")
    triggers = _validate_triggers(value["triggers"])
    away_actions = _validate_actions(value["awayActions"], "awayActions")
    return_actions = _validate_actions(value["returnActions"], "returnActions")
    return AwaySettings(
        triggers=triggers,
        away_actions=away_actions,
        return_actions=return_actions,
    )


def away_settings_to_payload(settings: AwaySettings) -> dict[str, object]:
    """Encode canonical contract field names."""

    return {
        "triggers": [
            {
                "entityId": item.entity_id,
                "activeState": item.active_state,
                **({"forSeconds": item.for_seconds} if item.for_seconds else {}),
                **({"label": item.label} if item.label else {}),
            }
            for item in settings.triggers
        ],
        "awayActions": [_action_to_payload(item) for item in settings.away_actions],
        "returnActions": [
            _action_to_payload(item) for item in settings.return_actions
        ],
    }


def _action_to_payload(action: AwayAction) -> dict[str, object]:
    return {
        "targetId": action.target_id,
        "actionId": action.action_id,
        **({"value": action.value} if action.value is not None else {}),
        **({"label": action.label} if action.label else {}),
    }


def _validate_triggers(value: object) -> tuple[AwayTrigger, ...]:
    if not isinstance(value, list) or len(value) > MAX_AWAY_TRIGGERS:
        raise AwaySettingsViolation("away triggers are invalid")
    triggers: list[AwayTrigger] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise AwaySettingsViolation("away trigger fields are invalid")
        fields = set(item)
        required = {"entityId", "activeState"}
        allowed = required | {"forSeconds", "label"}
        if not required.issubset(fields) or not fields.issubset(allowed):
            raise AwaySettingsViolation("away trigger fields are invalid")
        triggers.append(
            AwayTrigger(
                entity_id=item["entityId"],
                active_state=item["activeState"],
                for_seconds=item.get("forSeconds", 0),
                label=item.get("label", ""),
            )
        )
    entity_ids = [item.entity_id for item in triggers]
    if len(entity_ids) != len(set(entity_ids)):
        raise AwaySettingsViolation("away trigger entities must be unique")
    return tuple(triggers)


def _validate_actions(value: object, name: str) -> tuple[AwayAction, ...]:
    if not isinstance(value, list) or len(value) > MAX_AWAY_ACTIONS:
        raise AwaySettingsViolation(f"{name} are invalid")
    actions: list[AwayAction] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise AwaySettingsViolation(f"{name} fields are invalid")
        fields = set(item)
        required = {"targetId", "actionId"}
        allowed = required | {"value", "label"}
        if not required.issubset(fields) or not fields.issubset(allowed):
            raise AwaySettingsViolation(f"{name} fields are invalid")
        actions.append(
            AwayAction(
                target_id=item["targetId"],
                action_id=item["actionId"],
                value=item.get("value"),
                label=item.get("label", ""),
            )
        )
    target_ids = [item.target_id for item in actions]
    if len(target_ids) != len(set(target_ids)):
        raise AwaySettingsViolation(f"{name} target devices must be unique")
    return tuple(actions)


def _entity_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _ENTITY_ID.fullmatch(value):
        raise AwaySettingsViolation(f"{label} is invalid")


def _label(value: object) -> None:
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise AwaySettingsViolation("away label is invalid")
