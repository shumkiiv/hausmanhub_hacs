from __future__ import annotations

from ..domain.climate import (
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..domain.climate_bridge import ClimateBridgeMode
from ..domain.climate_comparison import (
    ClimateComparisonSnapshot,
    ClimateComparisonStatus,
)
from ..domain.climate_ha_calls import ClimateHaCallPlanSnapshot, ClimateHaServiceCall
from ..domain.climate_isolation import ClimateIsolationSnapshot, ClimateRoomIsolationStatus
from ..domain.climate_observation import ClimateObservationSnapshot
from ..domain.contours import ContourDefinition, ContourMode
from .climate_application_models import (
    ClimateApplicationDenialReason,
    ClimateApplicationGateStatus,
    ClimateApplicationPlan,
    ClimateApplicationRoomGate,
    ClimateApplicationViolation,
    ClimateDesiredStateChanges,
    ordered_application_denial_reasons,
)
from .climate_comparison import build_climate_comparison_snapshot
from .climate_ha_adapters import build_climate_ha_call_plan
from .climate_isolation import build_isolated_climate_policy_snapshot


_PASSIVE_KINDS = frozenset(
    {ClimateDeviceKind.TEMPERATURE_SENSOR, ClimateDeviceKind.HUMIDITY_SENSOR}
)
_TRANSLATION_BLOCKERS = frozenset(
    {
        ClimateApplicationDenialReason.ROOM_NOT_IN_CONTOUR,
        ClimateApplicationDenialReason.ROOM_NOT_IN_REGISTRY,
        ClimateApplicationDenialReason.ACTUATOR_NOT_IN_REGISTRY,
        ClimateApplicationDenialReason.NO_ACTIVE_ACTUATOR,
        ClimateApplicationDenialReason.ACTUATOR_NOT_MANAGED,
        ClimateApplicationDenialReason.MISSING_CONTROL_ENDPOINT,
    }
)


def build_climate_application_plan(
    contour: ContourDefinition,
    registry: ClimateRegistry,
    bridge_mode: ClimateBridgeMode,
    observation: ClimateObservationSnapshot,
    *,
    fingerprint: str,
    target_room_ids: tuple[str, ...],
    desired_state_changes: ClimateDesiredStateChanges,
) -> ClimateApplicationPlan:
    if not isinstance(contour, ContourDefinition) or contour.contour_id != "climate":
        raise ClimateApplicationViolation("climate contour is unavailable")
    if not isinstance(registry, ClimateRegistry):
        raise ClimateApplicationViolation("climate registry is unavailable")
    if not isinstance(bridge_mode, ClimateBridgeMode):
        raise ClimateApplicationViolation("climate runtime mode is invalid")
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateApplicationViolation("native climate observation is unavailable")
    if not isinstance(desired_state_changes, ClimateDesiredStateChanges):
        raise ClimateApplicationViolation("local desired-state changes are invalid")
    target_ids = _contour_ordered_target_ids(contour, target_room_ids)
    isolation = build_isolated_climate_policy_snapshot(contour, observation)
    comparison = build_climate_comparison_snapshot(isolation, observation)
    call_plan = build_climate_ha_call_plan(registry, isolation)
    gates = tuple(
        _gate_room(
            room_id,
            contour,
            registry,
            bridge_mode,
            isolation,
            comparison,
            call_plan,
        )
        for room_id in target_ids
    )
    denials = ordered_application_denial_reasons(
        reason
        for gate in gates
        if gate.status is ClimateApplicationGateStatus.DENIED
        for reason in gate.reasons
    )
    return ClimateApplicationPlan(
        contour_id=contour.contour_id,
        fingerprint=fingerprint,
        target_room_ids=target_ids,
        desired_state_changes=desired_state_changes,
        isolation=isolation,
        comparison=comparison,
        call_plan=call_plan,
        room_gates=gates,
        strict_calls=(
            ()
            if denials
            else tuple(
                call
                for gate in gates
                if gate.status is ClimateApplicationGateStatus.READY
                for call in gate.strict_calls
            )
        ),
        initially_aligned_room_ids=tuple(
            gate.room_id
            for gate in gates
            if gate.status is ClimateApplicationGateStatus.ALIGNED
        ),
        denial_reasons=denials,
    )


def _gate_room(
    room_id: str,
    contour: ContourDefinition,
    registry: ClimateRegistry,
    bridge_mode: ClimateBridgeMode,
    isolation: ClimateIsolationSnapshot,
    comparison: ClimateComparisonSnapshot,
    call_plan: ClimateHaCallPlanSnapshot,
) -> ClimateApplicationRoomGate:
    reasons: list[ClimateApplicationDenialReason] = []
    if contour.mode is not ContourMode.AUTOMATIC:
        reasons.append(ClimateApplicationDenialReason.CONTOUR_NOT_AUTOMATIC)
    if bridge_mode is not ClimateBridgeMode.MANAGED:
        reasons.append(ClimateApplicationDenialReason.RUNTIME_NOT_MANAGED)
    assignment = next((room for room in contour.rooms if room.room_id == room_id), None)
    if assignment is None:
        reasons.append(ClimateApplicationDenialReason.ROOM_NOT_IN_CONTOUR)
    elif registry.room(room_id) is None:
        reasons.append(ClimateApplicationDenialReason.ROOM_NOT_IN_REGISTRY)
    actuators = () if assignment is None else _selected_actuators(
        assignment.device_ids,
        room_id,
        registry,
        reasons,
    )
    if assignment is not None and not actuators:
        reasons.append(ClimateApplicationDenialReason.NO_ACTIVE_ACTUATOR)
    if any(device.control_scope is not ClimateControlScope.MANAGED for device in actuators):
        reasons.append(ClimateApplicationDenialReason.ACTUATOR_NOT_MANAGED)
    if any(device.endpoint(ClimateEndpointRole.CONTROL) is None for device in actuators):
        reasons.append(ClimateApplicationDenialReason.MISSING_CONTROL_ENDPOINT)
    isolated = isolation.room(room_id)
    if isolated is None:
        reasons.append(ClimateApplicationDenialReason.ISOLATION_ROOM_MISSING)
    elif isolated.status is not ClimateRoomIsolationStatus.READY:
        reasons.append(ClimateApplicationDenialReason.ROOM_NOT_READY)
    compared = comparison.room(room_id)
    if compared is None:
        reasons.append(ClimateApplicationDenialReason.COMPARISON_ROOM_MISSING)
    elif compared.status is ClimateComparisonStatus.NOT_COMPARABLE:
        reasons.append(ClimateApplicationDenialReason.ROOM_NOT_COMPARABLE)
    strict_calls = _strict_calls_if_complete(
        room_id,
        actuators,
        compared,
        call_plan,
        reasons,
    )
    if reasons:
        return ClimateApplicationRoomGate(
            room_id=room_id,
            status=ClimateApplicationGateStatus.DENIED,
            reasons=ordered_application_denial_reasons(reasons),
            strict_calls=(),
        )
    if compared is not None and compared.status is ClimateComparisonStatus.ALIGNED:
        return ClimateApplicationRoomGate(
            room_id=room_id,
            status=ClimateApplicationGateStatus.ALIGNED,
            reasons=(ClimateApplicationDenialReason.ALREADY_IN_SYNC,),
            strict_calls=(),
        )
    return ClimateApplicationRoomGate(
        room_id=room_id,
        status=ClimateApplicationGateStatus.READY,
        reasons=(),
        strict_calls=strict_calls,
    )


def _selected_actuators(
    device_ids: tuple[str, ...],
    room_id: str,
    registry: ClimateRegistry,
    reasons: list[ClimateApplicationDenialReason],
) -> tuple[ClimateDevice, ...]:
    actuators: list[ClimateDevice] = []
    for device_id in device_ids:
        device = registry.device(device_id)
        if device is None or device.room_id != room_id:
            reasons.append(ClimateApplicationDenialReason.ACTUATOR_NOT_IN_REGISTRY)
        elif device.kind not in _PASSIVE_KINDS:
            actuators.append(device)
    return tuple(actuators)


def _strict_calls_if_complete(
    room_id: str,
    actuators: tuple[ClimateDevice, ...],
    compared,
    call_plan: ClimateHaCallPlanSnapshot,
    reasons: list[ClimateApplicationDenialReason],
) -> tuple[ClimateHaServiceCall, ...]:
    if (
        compared is None
        or compared.status not in {
            ClimateComparisonStatus.ALIGNED,
            ClimateComparisonStatus.DIVERGED,
        }
        or _TRANSLATION_BLOCKERS.intersection(reasons)
    ):
        return ()
    room_plan = call_plan.room(room_id)
    actuator_ids = {device.device_id for device in actuators}
    translated = () if room_plan is None else tuple(
        device for device in room_plan.devices if device.device_id in actuator_ids
    )
    if (
        room_plan is None
        or {device.device_id for device in translated} != actuator_ids
        or any(device.limits or not device.calls for device in translated)
    ):
        reasons.append(ClimateApplicationDenialReason.TRANSLATION_INCOMPLETE)
        return ()
    return (
        tuple(call for device in translated for call in device.calls)
        if compared.status is ClimateComparisonStatus.DIVERGED
        else ()
    )


def _contour_ordered_target_ids(
    contour: ContourDefinition,
    target_room_ids: tuple[str, ...],
) -> tuple[str, ...]:
    requested = set(target_room_ids)
    selected = tuple(room.room_id for room in contour.rooms if room.room_id in requested)
    return selected + tuple(room_id for room_id in target_room_ids if room_id not in selected)
