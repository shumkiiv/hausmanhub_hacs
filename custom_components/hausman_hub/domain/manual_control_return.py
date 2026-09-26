"""Pure policy for the bounded return after manual light control.

The policy answers whether a *proposed* automatic action is inside the safe
scope. It neither stores state nor calls Home Assistant, so a later runtime
adapter remains responsible for durable attribution and dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


_ENTITY_ID = re.compile(r"^(?:light|switch)\.[A-Za-z0-9_]+$")
MIN_PRIORITY_SECONDS = 15
MAX_PRIORITY_SECONDS = 86400
DEFAULT_PRIORITY_SECONDS = 3600


class ManualControlReturnViolation(ValueError):
    """A policy input cannot safely describe a proposed automatic action."""


class ManualAction(StrEnum):
    ON = "on"
    OFF = "off"


class AutomaticLightAction(StrEnum):
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    SET_BRIGHTNESS = "set_brightness"


class AutomaticScope(StrEnum):
    BLOCKED = "blocked"
    SAME_SOURCE_BRIGHTNESS_DOWN = "same_source_brightness_down"
    NORMAL = "normal"


@dataclass(frozen=True, slots=True)
class ManualControlReturnIntent:
    """One attributed manual transition, expressed in monotonic virtual time."""

    action: ManualAction
    entity_id: str
    occurred_at_seconds: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", ManualAction(self.action))
        _entity_id(self.entity_id, "manual entity id")
        _timestamp(self.occurred_at_seconds, "manual occurrence time")


@dataclass(frozen=True, slots=True)
class AutomaticLightIntent:
    """A non-dispatch description of the automatic action under review."""

    action: AutomaticLightAction
    entity_id: str
    source_is_on: bool = False
    lowers_brightness: bool = False
    confirmed_absence: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", AutomaticLightAction(self.action))
        _entity_id(self.entity_id, "automatic entity id")
        for name in ("source_is_on", "lowers_brightness", "confirmed_absence"):
            if type(getattr(self, name)) is not bool:
                raise ManualControlReturnViolation(f"{name} must be boolean")


@dataclass(frozen=True, slots=True)
class ManualControlReturnInput:
    """All facts needed to make a fail-closed decision.

    ``legacy``, ``restart_uncertain``, ``manual_only`` and ``unknown`` are
    explicit because absent lineage must never be silently treated as safe.
    """

    now_seconds: int
    proposed: AutomaticLightIntent
    manual: ManualControlReturnIntent | None = None
    legacy: bool = False
    restart_uncertain: bool = False
    manual_only: bool = False
    unknown: bool = False

    def __post_init__(self) -> None:
        _timestamp(self.now_seconds, "decision time")
        if not isinstance(self.proposed, AutomaticLightIntent):
            raise ManualControlReturnViolation("proposed automatic action is invalid")
        if self.manual is not None and not isinstance(self.manual, ManualControlReturnIntent):
            raise ManualControlReturnViolation("manual intent is invalid")
        for name in ("legacy", "restart_uncertain", "manual_only", "unknown"):
            if type(getattr(self, name)) is not bool:
                raise ManualControlReturnViolation(f"{name} must be boolean")


@dataclass(frozen=True, slots=True)
class ManualControlReturnDecision:
    """Safe result for a caller that has not yet dispatched anything."""

    allowed: bool
    scope: AutomaticScope
    reason: str


@dataclass(frozen=True, slots=True)
class ManualControlReturnPolicy:
    priority_seconds: int = DEFAULT_PRIORITY_SECONDS

    def __post_init__(self) -> None:
        if type(self.priority_seconds) is not int or not (
            MIN_PRIORITY_SECONDS <= self.priority_seconds <= MAX_PRIORITY_SECONDS
        ):
            raise ManualControlReturnViolation(
                f"priority seconds must be within {MIN_PRIORITY_SECONDS}..{MAX_PRIORITY_SECONDS}"
            )

    def decide(self, value: ManualControlReturnInput) -> ManualControlReturnDecision:
        """Return the permitted automatic scope without invoking an executor."""

        if not isinstance(value, ManualControlReturnInput):
            raise ManualControlReturnViolation("manual return input is invalid")
        if value.legacy:
            return _blocked("legacy_lineage")
        if value.restart_uncertain:
            return _blocked("restart_uncertain")
        if value.manual_only:
            return _blocked("manual_only_target")
        if value.unknown:
            return _blocked("unknown_lineage")
        if value.manual is None:
            return ManualControlReturnDecision(True, AutomaticScope.NORMAL, "no_manual_control")
        if value.now_seconds < value.manual.occurred_at_seconds:
            return _blocked("clock_uncertain")
        if value.manual.action is ManualAction.OFF:
            return _blocked("manual_off_separate_protection")
        elapsed = value.now_seconds - value.manual.occurred_at_seconds
        if elapsed < self.priority_seconds:
            return _blocked("manual_on_wait")
        proposed = value.proposed
        if (
            proposed.action is AutomaticLightAction.SET_BRIGHTNESS
            and proposed.entity_id == value.manual.entity_id
            and proposed.source_is_on
            and proposed.lowers_brightness
            and proposed.confirmed_absence
        ):
            return ManualControlReturnDecision(
                True,
                AutomaticScope.SAME_SOURCE_BRIGHTNESS_DOWN,
                "manual_on_limited",
            )
        return _blocked("manual_on_limited")


def _blocked(reason: str) -> ManualControlReturnDecision:
    return ManualControlReturnDecision(False, AutomaticScope.BLOCKED, reason)


def _timestamp(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ManualControlReturnViolation(f"{label} must be a non-negative integer")


def _entity_id(value: object, label: str) -> None:
    if not isinstance(value, str) or _ENTITY_ID.fullmatch(value) is None:
        raise ManualControlReturnViolation(f"{label} is invalid")
