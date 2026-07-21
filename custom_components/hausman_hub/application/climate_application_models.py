from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import re

from ..domain.climate_comparison import ClimateComparisonSnapshot
from ..domain.climate_ha_calls import ClimateHaCallPlanSnapshot, ClimateHaServiceCall
from ..domain.climate_isolation import ClimateIsolationSnapshot


_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ClimateApplicationViolation(ValueError):
    pass


class ClimateApplicationGateStatus(StrEnum):
    READY = "ready"
    ALIGNED = "aligned"
    DENIED = "denied"


class ClimateApplicationDenialReason(StrEnum):
    CONTOUR_NOT_AUTOMATIC = "contour_not_automatic"
    RUNTIME_NOT_MANAGED = "runtime_not_managed"
    ROOM_NOT_IN_CONTOUR = "room_not_in_contour"
    ROOM_NOT_IN_REGISTRY = "room_not_in_registry"
    ACTUATOR_NOT_IN_REGISTRY = "actuator_not_in_registry"
    NO_ACTIVE_ACTUATOR = "no_active_actuator"
    ACTUATOR_NOT_MANAGED = "actuator_not_managed"
    MISSING_CONTROL_ENDPOINT = "missing_control_endpoint"
    ISOLATION_ROOM_MISSING = "isolation_room_missing"
    ROOM_NOT_READY = "room_not_ready"
    COMPARISON_ROOM_MISSING = "comparison_room_missing"
    ROOM_NOT_COMPARABLE = "room_not_comparable"
    TRANSLATION_INCOMPLETE = "translation_incomplete"
    ALREADY_IN_SYNC = "already_in_sync"


_DENIAL_ORDER = tuple(ClimateApplicationDenialReason)


@dataclass(frozen=True, slots=True)
class ClimateDesiredStateChanges:
    temperature: int
    strategy: int
    automatic_mode: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.temperature, self.strategy, self.automatic_mode)
        ):
            raise ClimateApplicationViolation(
                "local desired-state changes must be non-negative integers"
            )


@dataclass(frozen=True, slots=True)
class ClimateApplicationRoomGate:
    room_id: str
    status: ClimateApplicationGateStatus
    reasons: tuple[ClimateApplicationDenialReason, ...]
    strict_calls: tuple[ClimateHaServiceCall, ...]

    def __post_init__(self) -> None:
        _require_stable_id(self.room_id, "application room id")
        _require_reasons(self.reasons)
        if not isinstance(self.status, ClimateApplicationGateStatus):
            raise ClimateApplicationViolation("application gate status is invalid")
        if type(self.strict_calls) is not tuple or any(
            not isinstance(call, ClimateHaServiceCall) for call in self.strict_calls
        ):
            raise ClimateApplicationViolation("application calls must be immutable")
        match self.status:
            case ClimateApplicationGateStatus.READY:
                valid = not self.reasons and bool(self.strict_calls)
            case ClimateApplicationGateStatus.ALIGNED:
                valid = (
                    self.reasons
                    == (ClimateApplicationDenialReason.ALREADY_IN_SYNC,)
                    and not self.strict_calls
                )
            case ClimateApplicationGateStatus.DENIED:
                valid = bool(self.reasons) and not self.strict_calls
            case unreachable:
                raise ClimateApplicationViolation(
                    f"unknown application gate status: {unreachable}"
                )
        if not valid:
            raise ClimateApplicationViolation("application gate shape is invalid")


@dataclass(frozen=True, slots=True)
class ClimateApplicationPlan:
    contour_id: str
    fingerprint: str
    target_room_ids: tuple[str, ...]
    desired_state_changes: ClimateDesiredStateChanges
    isolation: ClimateIsolationSnapshot
    comparison: ClimateComparisonSnapshot
    call_plan: ClimateHaCallPlanSnapshot
    room_gates: tuple[ClimateApplicationRoomGate, ...]
    strict_calls: tuple[ClimateHaServiceCall, ...]
    initially_aligned_room_ids: tuple[str, ...]
    denial_reasons: tuple[ClimateApplicationDenialReason, ...]

    def __post_init__(self) -> None:
        _require_stable_id(self.contour_id, "application contour id")
        _require_room_ids(self.target_room_ids)
        _require_room_ids(self.initially_aligned_room_ids, allow_empty=True)
        _require_reasons(self.denial_reasons)
        if not isinstance(self.fingerprint, str) or _FINGERPRINT.fullmatch(self.fingerprint) is None:
            raise ClimateApplicationViolation("application fingerprint is invalid")
        if not isinstance(self.desired_state_changes, ClimateDesiredStateChanges):
            raise ClimateApplicationViolation("application change counts are invalid")
        if not all(
            isinstance(snapshot, expected)
            for snapshot, expected in (
                (self.isolation, ClimateIsolationSnapshot),
                (self.comparison, ClimateComparisonSnapshot),
                (self.call_plan, ClimateHaCallPlanSnapshot),
            )
        ):
            raise ClimateApplicationViolation("application snapshots are invalid")
        if any(
            snapshot.contour_id != self.contour_id
            for snapshot in (self.isolation, self.comparison, self.call_plan)
        ):
            raise ClimateApplicationViolation("application snapshot contours differ")
        if len({self.isolation.observed_at, self.comparison.observed_at, self.call_plan.observed_at}) != 1:
            raise ClimateApplicationViolation("application snapshots use different observations")
        if type(self.room_gates) is not tuple or any(
            not isinstance(gate, ClimateApplicationRoomGate) for gate in self.room_gates
        ):
            raise ClimateApplicationViolation("application room gates are invalid")
        if tuple(gate.room_id for gate in self.room_gates) != self.target_room_ids:
            raise ClimateApplicationViolation("application gates must match target scope")
        if type(self.strict_calls) is not tuple or any(
            not isinstance(call, ClimateHaServiceCall) for call in self.strict_calls
        ):
            raise ClimateApplicationViolation("application strict calls are invalid")
        denied = _ordered_reasons(
            reason
            for gate in self.room_gates
            if gate.status is ClimateApplicationGateStatus.DENIED
            for reason in gate.reasons
        )
        aligned = tuple(
            gate.room_id
            for gate in self.room_gates
            if gate.status is ClimateApplicationGateStatus.ALIGNED
        )
        executable = tuple(
            call
            for gate in self.room_gates
            if gate.status is ClimateApplicationGateStatus.READY
            for call in gate.strict_calls
        )
        if (
            self.denial_reasons != denied
            or self.initially_aligned_room_ids != aligned
            or self.strict_calls != (() if denied else executable)
        ):
            raise ClimateApplicationViolation("application plan facts are inconsistent")

    @property
    def preflight_permitted(self) -> bool:
        return not self.denial_reasons


def ordered_application_denial_reasons(
    reasons: Iterable[ClimateApplicationDenialReason],
) -> tuple[ClimateApplicationDenialReason, ...]:
    return _ordered_reasons(reasons)


def _ordered_reasons(
    reasons: Iterable[ClimateApplicationDenialReason],
) -> tuple[ClimateApplicationDenialReason, ...]:
    present = frozenset(reasons)
    return tuple(reason for reason in _DENIAL_ORDER if reason in present)


def _require_room_ids(room_ids: tuple[str, ...], *, allow_empty: bool = False) -> None:
    if (
        type(room_ids) is not tuple
        or (not allow_empty and not room_ids)
        or len(room_ids) != len(set(room_ids))
    ):
        raise ClimateApplicationViolation("application room scope is invalid")
    for room_id in room_ids:
        _require_stable_id(room_id, "application room id")


def _require_stable_id(value: str, label: str) -> None:
    if not isinstance(value, str) or _STABLE_ID.fullmatch(value) is None:
        raise ClimateApplicationViolation(f"{label} must be a stable HausmanHub id")


def _require_reasons(reasons: tuple[ClimateApplicationDenialReason, ...]) -> None:
    if type(reasons) is not tuple or any(
        not isinstance(reason, ClimateApplicationDenialReason) for reason in reasons
    ):
        raise ClimateApplicationViolation("application reasons are invalid")
    if len(reasons) != len(set(reasons)) or reasons != _ordered_reasons(reasons):
        raise ClimateApplicationViolation("application reasons must use fixed order")
