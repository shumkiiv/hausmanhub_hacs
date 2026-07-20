"""Build the command-free decision comparison against the working module."""

from __future__ import annotations

from ..domain.climate_comparison import (
    ClimateComparisonReason,
    ClimateComparisonSnapshot,
    ClimateComparisonStatus,
    ClimateComparisonViolation,
    ClimateDeviceComparison,
    ClimateRoomComparison,
)
from ..domain.climate_isolation import (
    ClimateIsolatedRoomResult,
    ClimateIsolationSnapshot,
    ClimateRoomIsolationStatus,
)
from ..domain.climate_observation import (
    ClimateDataStatus,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateObservationSnapshot,
    ClimateRoomMode,
)
from ..domain.climate_policy import (
    ClimateFinalDeviceAction,
    ClimateFinalDevicePlan,
    ClimatePolicyAction,
    ClimatePolicyReason,
)
from ..domain.contours import ContourMode
from .climate_observations import (
    climate_reference_module_observation,
    climate_reference_observation,
)
from .climate_policy import climate_reference_policy
from .contours import CLIMATE_CONTOUR_ID


_ACTIVE = frozenset(
    {
        ClimateDeviceActivity.RUNNING,
        ClimateDeviceActivity.COOLING,
        ClimateDeviceActivity.HEATING,
        ClimateDeviceActivity.HUMIDIFYING,
    }
)
_INACTIVE = frozenset(
    {
        ClimateDeviceActivity.STOPPED,
        ClimateDeviceActivity.IDLE,
    }
)
_EXPECTED_ACTIVE = frozenset(
    {
        ClimateFinalDeviceAction.COOL,
        ClimateFinalDeviceAction.HEAT,
        ClimateFinalDeviceAction.HUMIDIFY,
        ClimateFinalDeviceAction.SET_TEMPERATURE,
    }
)
_EXPECTED_INACTIVE = frozenset(
    {
        ClimateFinalDeviceAction.OFF,
        ClimateFinalDeviceAction.SAFE_OFF,
        ClimateFinalDeviceAction.HOLD,
    }
)
_SETTING_ACTIONS = frozenset(
    {
        ClimateFinalDeviceAction.COOL,
        ClimateFinalDeviceAction.MAINTAIN,
        ClimateFinalDeviceAction.SET_TEMPERATURE,
    }
)
_OBSERVE_ACTIONS = frozenset(
    {
        ClimateFinalDeviceAction.OBSERVE,
        ClimateFinalDeviceAction.UNAVAILABLE,
    }
)
_MISMATCH_REASONS = frozenset(
    {
        ClimateComparisonReason.DEVICE_ACTIVITY_MISMATCH,
        ClimateComparisonReason.DEVICE_SETTING_MISMATCH,
    }
)


def build_climate_comparison_snapshot(
    isolation: ClimateIsolationSnapshot,
    observation: ClimateObservationSnapshot,
) -> ClimateComparisonSnapshot:
    """Compare strict native decisions with one observed module state."""

    if not isinstance(isolation, ClimateIsolationSnapshot):
        raise ClimateComparisonViolation("validated isolation snapshot is required")
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateComparisonViolation("validated observation snapshot is required")
    if isolation.observed_at != observation.observed_at:
        raise ClimateComparisonViolation(
            "comparison requires one shared observation time"
        )
    rooms = tuple(
        _compare_room(result, observation) for result in isolation.rooms
    )
    return ClimateComparisonSnapshot(
        contour_id=isolation.contour_id,
        contour_mode=isolation.contour_mode,
        observed_at=isolation.observed_at,
        rooms=rooms,
    )


def climate_reference_comparison(case_id: str) -> ClimateComparisonSnapshot:
    """Compare the native plan with the frozen module decision of one case."""

    observation = climate_reference_observation(case_id)
    module_observation = climate_reference_module_observation(case_id)
    isolation = ClimateIsolationSnapshot(
        contour_id=CLIMATE_CONTOUR_ID,
        contour_mode=ContourMode.AUTOMATIC,
        observed_at=observation.observed_at,
        rooms=(
            ClimateIsolatedRoomResult(
                room_id=observation.rooms[0].room_id,
                status=ClimateRoomIsolationStatus.READY,
                reasons=(),
                failed_device_ids=(),
                policy=climate_reference_policy(case_id),
            ),
        ),
    )
    return build_climate_comparison_snapshot(isolation, module_observation)


def _compare_room(
    result: ClimateIsolatedRoomResult,
    observation: ClimateObservationSnapshot,
) -> ClimateRoomComparison:
    policy = result.policy
    observed_room = observation.room(result.room_id)
    observed_mode = (
        observed_room.mode if observed_room is not None else ClimateRoomMode.UNKNOWN
    )
    planned_policy = policy.policy if policy is not None else None
    planned_action = policy.action if policy is not None else None
    if observation.data_status is not ClimateDataStatus.FRESH:
        return ClimateRoomComparison(
            room_id=result.room_id,
            status=ClimateComparisonStatus.NOT_COMPARABLE,
            reasons=(ClimateComparisonReason.OBSERVATION_STALE,),
            planned_policy=planned_policy,
            planned_action=planned_action,
            observed_mode=observed_mode,
            devices=(),
        )
    if policy is None:
        return ClimateRoomComparison(
            room_id=result.room_id,
            status=ClimateComparisonStatus.NOT_COMPARABLE,
            reasons=(ClimateComparisonReason.ROOM_POLICY_MISSING,),
            planned_policy=None,
            planned_action=None,
            observed_mode=observed_mode,
            devices=(),
        )
    if result.status is ClimateRoomIsolationStatus.UNAVAILABLE:
        return ClimateRoomComparison(
            room_id=result.room_id,
            status=ClimateComparisonStatus.NOT_COMPARABLE,
            reasons=(ClimateComparisonReason.ROOM_DATA_UNAVAILABLE,),
            planned_policy=planned_policy,
            planned_action=planned_action,
            observed_mode=observed_mode,
            devices=(),
        )
    if (
        policy.action is ClimatePolicyAction.OBSERVE
        and policy.reason is not ClimatePolicyReason.AUTOMATIC
    ):
        reason = (
            ClimateComparisonReason.MANUAL_OBSERVE
            if policy.reason is ClimatePolicyReason.MANUAL_OBSERVE
            else ClimateComparisonReason.PLANNED_OBSERVE
        )
        return ClimateRoomComparison(
            room_id=result.room_id,
            status=ClimateComparisonStatus.NOT_COMPARABLE,
            reasons=(reason,),
            planned_policy=planned_policy,
            planned_action=planned_action,
            observed_mode=observed_mode,
            devices=(),
        )

    reasons: set[ClimateComparisonReason] = set()
    devices = tuple(
        _compare_device(plan, observation) for plan in policy.devices
    )
    for device in devices:
        reasons.update(device.reasons)
    ordered = tuple(
        reason for reason in ClimateComparisonReason if reason in reasons
    )
    if any(reason in _MISMATCH_REASONS for reason in ordered):
        status = ClimateComparisonStatus.DIVERGED
    elif ordered:
        status = ClimateComparisonStatus.NOT_COMPARABLE
    else:
        status = ClimateComparisonStatus.ALIGNED
    return ClimateRoomComparison(
        room_id=result.room_id,
        status=status,
        reasons=ordered,
        planned_policy=planned_policy,
        planned_action=planned_action,
        observed_mode=observed_mode,
        devices=devices,
    )


def _compare_device(
    plan: ClimateFinalDevicePlan,
    observation: ClimateObservationSnapshot,
) -> ClimateDeviceComparison:
    observed = observation.device(plan.device_id)
    if observed is None:
        return ClimateDeviceComparison(
            device_id=plan.device_id,
            room_id=plan.room_id,
            kind=plan.kind,
            status=ClimateComparisonStatus.NOT_COMPARABLE,
            reasons=(ClimateComparisonReason.DEVICE_UNOBSERVED,),
            planned_action=plan.action,
            observed_activity=ClimateDeviceActivity.UNKNOWN,
        )
    reasons: list[ClimateComparisonReason] = []
    if observed.availability is not ClimateDeviceAvailability.AVAILABLE:
        reasons.append(ClimateComparisonReason.DEVICE_UNAVAILABLE)
    elif observed.activity is ClimateDeviceActivity.UNKNOWN:
        reasons.append(ClimateComparisonReason.DEVICE_ACTIVITY_UNKNOWN)
    elif plan.action in _OBSERVE_ACTIONS:
        reasons.append(ClimateComparisonReason.PLANNED_OBSERVE)
    else:
        _compare_activity(plan, observed, reasons)
        if plan.action in _SETTING_ACTIONS:
            _compare_settings(plan, observed, reasons)
    ordered = tuple(
        reason for reason in ClimateComparisonReason if reason in reasons
    )
    if any(reason in _MISMATCH_REASONS for reason in ordered):
        status = ClimateComparisonStatus.DIVERGED
    elif ordered:
        status = ClimateComparisonStatus.NOT_COMPARABLE
    else:
        status = ClimateComparisonStatus.ALIGNED
    return ClimateDeviceComparison(
        device_id=plan.device_id,
        room_id=plan.room_id,
        kind=plan.kind,
        status=status,
        reasons=ordered,
        planned_action=plan.action,
        observed_activity=observed.activity,
    )


def _compare_activity(
    plan: ClimateFinalDevicePlan,
    observed: ClimateDeviceObservation,
    reasons: list[ClimateComparisonReason],
) -> None:
    if plan.action in _EXPECTED_ACTIVE and observed.activity not in _ACTIVE:
        reasons.append(ClimateComparisonReason.DEVICE_ACTIVITY_MISMATCH)
    elif plan.action in _EXPECTED_INACTIVE and observed.activity not in _INACTIVE:
        reasons.append(ClimateComparisonReason.DEVICE_ACTIVITY_MISMATCH)


def _compare_settings(
    plan: ClimateFinalDevicePlan,
    observed: ClimateDeviceObservation,
    reasons: list[ClimateComparisonReason],
) -> None:
    pairs = (
        (plan.target_temperature, observed.current_target_temperature),
        (plan.fan_mode, observed.fan_mode),
        (plan.quiet, observed.quiet),
    )
    for planned, current in pairs:
        if planned is None:
            continue
        if current is None:
            reasons.append(ClimateComparisonReason.DEVICE_SETTING_UNOBSERVED)
        elif current != planned:
            reasons.append(ClimateComparisonReason.DEVICE_SETTING_MISMATCH)
