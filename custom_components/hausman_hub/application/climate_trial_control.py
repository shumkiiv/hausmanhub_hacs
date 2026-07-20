"""Gate the one-room internal climate trial and build redacted receipts."""

from __future__ import annotations

from ..domain.climate import ClimateControlScope, ClimateRegistry
from ..domain.climate_bridge import ClimateBridgeMode
from ..domain.climate_comparison import (
    ClimateComparisonReason,
    ClimateComparisonSnapshot,
    ClimateComparisonStatus,
)
from ..domain.climate_ha_calls import ClimateHaCallPlanSnapshot
from ..domain.climate_isolation import ClimateIsolationSnapshot, ClimateRoomIsolationStatus
from ..domain.climate_trial import (
    ClimateTrialDecision,
    ClimateTrialReceipt,
    ClimateTrialReason,
    ClimateTrialStatus,
    ClimateTrialViolation,
)
from ..domain.contours import ContourMode


def plan_climate_trial(
    trial_room_id: str,
    *,
    bridge_mode: ClimateBridgeMode,
    contour_mode: ContourMode,
    isolation: ClimateIsolationSnapshot,
    comparison: ClimateComparisonSnapshot,
    call_plan: ClimateHaCallPlanSnapshot,
    registry: ClimateRegistry,
    required_scope: ClimateControlScope = ClimateControlScope.CANARY,
    allowed_bridge_modes: frozenset[ClimateBridgeMode] = frozenset(
        {ClimateBridgeMode.CANARY}
    ),
) -> ClimateTrialDecision:
    """Evaluate every gate for one internally controlled room."""

    if not isinstance(trial_room_id, str) or not trial_room_id:
        raise ClimateTrialViolation("trial room id is required")
    for value, model in (
        (isolation, ClimateIsolationSnapshot),
        (comparison, ClimateComparisonSnapshot),
        (call_plan, ClimateHaCallPlanSnapshot),
        (registry, ClimateRegistry),
    ):
        if not isinstance(value, model):
            raise ClimateTrialViolation("trial requires validated inputs")
    observed_at = isolation.observed_at
    if (
        comparison.observed_at != observed_at
        or call_plan.observed_at != observed_at
    ):
        raise ClimateTrialViolation("trial inputs must share one observation")
    if comparison.contour_mode is not contour_mode or (
        call_plan.contour_mode is not contour_mode
    ):
        raise ClimateTrialViolation("trial inputs must share one contour mode")

    def deny(reason: ClimateTrialReason) -> ClimateTrialDecision:
        return ClimateTrialDecision(
            room_id=trial_room_id,
            observed_at=observed_at,
            permitted=False,
            reasons=(reason,),
            calls=(),
        )

    isolated = isolation.room(trial_room_id)
    if isolated is None:
        return deny(ClimateTrialReason.NO_TRIAL_ROOM)
    if bridge_mode not in allowed_bridge_modes:
        return deny(ClimateTrialReason.NOT_TRIAL_MODE)
    if contour_mode is not ContourMode.AUTOMATIC:
        return deny(ClimateTrialReason.CONTOUR_NOT_AUTOMATIC)
    compared = comparison.room(trial_room_id)
    if compared is not None and (
        ClimateComparisonReason.OBSERVATION_STALE in compared.reasons
    ):
        return deny(ClimateTrialReason.OBSERVATION_STALE)
    if isolated.status is not ClimateRoomIsolationStatus.READY:
        return deny(ClimateTrialReason.ROOM_NOT_READY)
    if compared is None or compared.status is ClimateComparisonStatus.NOT_COMPARABLE:
        return deny(ClimateTrialReason.ROOM_UNCERTAIN)
    if compared.status is ClimateComparisonStatus.ALIGNED:
        return deny(ClimateTrialReason.UP_TO_DATE)
    room_plan = call_plan.room(trial_room_id)
    if room_plan is None or not room_plan.devices:
        return deny(ClimateTrialReason.NOTHING_TO_APPLY)
    for device in room_plan.devices:
        registered = next(
            (item for item in registry.devices if item.device_id == device.device_id),
            None,
        )
        if (
            registered is None
            or registered.control_scope is not required_scope
        ):
            return deny(ClimateTrialReason.DEVICE_NOT_TRIAL_SCOPE)
    if any(device.limits for device in room_plan.devices):
        return deny(ClimateTrialReason.TRANSLATION_INCOMPLETE)
    calls = tuple(call for device in room_plan.devices for call in device.calls)
    if not calls:
        return deny(ClimateTrialReason.NOTHING_TO_APPLY)
    return ClimateTrialDecision(
        room_id=trial_room_id,
        observed_at=observed_at,
        permitted=True,
        reasons=(),
        calls=calls,
    )


def climate_trial_skip_receipt(decision: ClimateTrialDecision) -> ClimateTrialReceipt:
    """Return the redacted receipt of one not-permitted trial decision."""

    if not isinstance(decision, ClimateTrialDecision) or decision.permitted:
        raise ClimateTrialViolation("skip receipt requires a denied decision")
    if decision.reasons == (ClimateTrialReason.UP_TO_DATE,):
        return ClimateTrialReceipt(
            room_id=decision.room_id,
            observed_at=decision.observed_at,
            status=ClimateTrialStatus.UP_TO_DATE,
            reasons=decision.reasons,
            call_count=0,
            executed_count=0,
        )
    return ClimateTrialReceipt(
        room_id=decision.room_id,
        observed_at=decision.observed_at,
        status=ClimateTrialStatus.DENIED,
        reasons=decision.reasons,
        call_count=0,
        executed_count=0,
    )


def climate_trial_applied_receipt(decision: ClimateTrialDecision) -> ClimateTrialReceipt:
    """Return the redacted receipt of one fully executed trial decision."""

    if not isinstance(decision, ClimateTrialDecision) or not decision.permitted:
        raise ClimateTrialViolation("applied receipt requires a permitted decision")
    return ClimateTrialReceipt(
        room_id=decision.room_id,
        observed_at=decision.observed_at,
        status=ClimateTrialStatus.APPLIED,
        reasons=(),
        call_count=len(decision.calls),
        executed_count=len(decision.calls),
    )


def climate_trial_failure_receipt(
    decision: ClimateTrialDecision,
    *,
    reason: ClimateTrialReason,
    executed_count: int,
) -> ClimateTrialReceipt:
    """Return the redacted receipt of one interrupted trial execution."""

    if not isinstance(decision, ClimateTrialDecision) or not decision.permitted:
        raise ClimateTrialViolation("failure receipt requires a permitted decision")
    if reason not in {
        ClimateTrialReason.EXECUTOR_UNAVAILABLE,
        ClimateTrialReason.SERVICE_ERROR,
    }:
        raise ClimateTrialViolation("trial failure reason must be bounded")
    return ClimateTrialReceipt(
        room_id=decision.room_id,
        observed_at=decision.observed_at,
        status=ClimateTrialStatus.FAILED,
        reasons=(reason,),
        call_count=len(decision.calls),
        executed_count=executed_count,
    )
