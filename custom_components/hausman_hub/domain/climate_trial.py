"""Describe the one-room climate trial decision and its redacted receipt.

The trial is the first place where a strict HausmanHub plan may physically
execute.  The decision names only stable HausmanHub identifiers and approved
codes; the receipt never retains entity ids, payloads, or backend responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from .climate_ha_calls import ClimateHaServiceCall


CLIMATE_TRIAL_MODEL_VERSION = 1
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ClimateTrialViolation(ValueError):
    """A trial decision or receipt is forged, mixed, or contradictory."""


class ClimateTrialStatus(StrEnum):
    """The only outcomes of one trial evaluation."""

    APPLIED = "applied"
    UP_TO_DATE = "up_to_date"
    DENIED = "denied"
    FAILED = "failed"


class ClimateTrialReason(StrEnum):
    """Bounded explanation of one trial outcome."""

    NO_TRIAL_ROOM = "no_trial_room"
    NOT_TRIAL_MODE = "not_trial_mode"
    CONTOUR_NOT_AUTOMATIC = "contour_not_automatic"
    OBSERVATION_STALE = "observation_stale"
    ROOM_NOT_READY = "room_not_ready"
    ROOM_UNCERTAIN = "room_uncertain"
    UP_TO_DATE = "up_to_date"
    DEVICE_NOT_TRIAL_SCOPE = "device_not_trial_scope"
    TRANSLATION_INCOMPLETE = "translation_incomplete"
    NOTHING_TO_APPLY = "nothing_to_apply"
    EXECUTOR_UNAVAILABLE = "executor_unavailable"
    SERVICE_ERROR = "service_error"


_REASON_ORDER = tuple(ClimateTrialReason)
_UP_TO_DATE_REASONS = frozenset({ClimateTrialReason.UP_TO_DATE})
_FAILURE_REASONS = frozenset(
    {
        ClimateTrialReason.EXECUTOR_UNAVAILABLE,
        ClimateTrialReason.SERVICE_ERROR,
    }
)


@dataclass(frozen=True, slots=True)
class ClimateTrialDecision:
    """One gated trial decision: permitted calls or bounded deny reasons."""

    room_id: str
    observed_at: int
    permitted: bool
    reasons: tuple[ClimateTrialReason, ...]
    calls: tuple[ClimateHaServiceCall, ...]

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "trial room id")
        _timestamp(self.observed_at, "trial observation time")
        if type(self.permitted) is not bool:
            raise ClimateTrialViolation("trial permission must be boolean")
        _reasons(self.reasons)
        if type(self.calls) is not tuple or any(
            not isinstance(call, ClimateHaServiceCall) for call in self.calls
        ):
            raise ClimateTrialViolation("trial calls must be strict and immutable")
        if self.permitted:
            if self.reasons or not self.calls:
                raise ClimateTrialViolation(
                    "permitted trial requires calls and no reasons"
                )
        elif not self.reasons or self.calls:
            raise ClimateTrialViolation(
                "denied trial requires reasons and no calls"
            )


@dataclass(frozen=True, slots=True)
class ClimateTrialReceipt:
    """One redacted trial outcome without entity ids or backend payloads."""

    room_id: str
    observed_at: int
    status: ClimateTrialStatus
    reasons: tuple[ClimateTrialReason, ...]
    call_count: int
    executed_count: int
    version: int = CLIMATE_TRIAL_MODEL_VERSION

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "trial room id")
        _timestamp(self.observed_at, "trial observation time")
        if not isinstance(self.status, ClimateTrialStatus):
            raise ClimateTrialViolation("trial status must be approved")
        _reasons(self.reasons)
        for value, label in (
            (self.call_count, "trial call count"),
            (self.executed_count, "trial executed count"),
        ):
            if type(value) is not int or value < 0:
                raise ClimateTrialViolation(f"{label} must be non-negative")
        if self.executed_count > self.call_count:
            raise ClimateTrialViolation("trial cannot execute more than planned")
        if (
            type(self.version) is not int
            or self.version != CLIMATE_TRIAL_MODEL_VERSION
        ):
            raise ClimateTrialViolation("climate trial model is unsupported")
        if self.status is ClimateTrialStatus.APPLIED:
            if self.reasons or not self.call_count or self.executed_count != self.call_count:
                raise ClimateTrialViolation(
                    "applied trial requires all planned calls executed"
                )
        elif self.status is ClimateTrialStatus.UP_TO_DATE:
            if self.reasons != (ClimateTrialReason.UP_TO_DATE,) or self.call_count:
                raise ClimateTrialViolation(
                    "up-to-date trial retains only the matching reason"
                )
        elif self.status is ClimateTrialStatus.DENIED:
            if (
                not self.reasons
                or set(self.reasons) & _FAILURE_REASONS
                or set(self.reasons) & _UP_TO_DATE_REASONS
                or self.call_count
                or self.executed_count
            ):
                raise ClimateTrialViolation(
                    "denied trial requires only bounded deny reasons"
                )
        else:
            if (
                not set(self.reasons) & _FAILURE_REASONS
                or not self.call_count
                or self.executed_count >= self.call_count
            ):
                raise ClimateTrialViolation(
                    "failed trial requires a failure reason and a partial run"
                )


def _stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ClimateTrialViolation(f"{label} must be a stable HausmanHub id")


def _timestamp(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ClimateTrialViolation(f"{label} must be a non-negative integer")


def _reasons(value: object) -> None:
    if type(value) is not tuple or any(
        not isinstance(reason, ClimateTrialReason) for reason in value
    ):
        raise ClimateTrialViolation("trial reasons must be immutable and typed")
    if len(value) != len(set(value)):
        raise ClimateTrialViolation("trial reasons must be unique")
    if value != tuple(reason for reason in _REASON_ORDER if reason in value):
        raise ClimateTrialViolation("trial reasons must use the fixed order")
