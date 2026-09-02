from __future__ import annotations

from collections.abc import Mapping

from ..domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..domain.climate_bridge import ClimateControlMode
from ..domain.climate_comparison import (
    ClimateComparisonSnapshot,
    ClimateComparisonStatus,
)
from ..domain.climate_ha_calls import (
    ClimateHaCallPlanSnapshot,
    ClimateHaService,
    ClimateHaServiceCall,
)
from ..domain.climate_isolation import ClimateIsolationSnapshot, ClimateRoomIsolationStatus
from ..domain.climate_observation import ClimateObservationSnapshot
from ..domain.contours import ContourDefinition, ContourMode
from .climate_application_models import (
    ClimateApplicationDenialReason,
    ClimateApplicationGateStatus,
    ClimateApplicationPlan,
    ClimateApplicationDeviceGate,
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
    bridge_mode: ClimateControlMode,
    observation: ClimateObservationSnapshot,
    *,
    fingerprint: str,
    target_room_ids: tuple[str, ...],
    desired_state_changes: ClimateDesiredStateChanges,
    ir_code_service: object | None = None,
    explicit_temperature_targets: Mapping[str, float] | None = None,
    explicit_humidity_targets: Mapping[str, int] | None = None,
) -> ClimateApplicationPlan:
    if not isinstance(contour, ContourDefinition) or contour.contour_id != "climate":
        raise ClimateApplicationViolation("climate contour is unavailable")
    if not isinstance(registry, ClimateRegistry):
        raise ClimateApplicationViolation("climate registry is unavailable")
    if not isinstance(bridge_mode, ClimateControlMode):
        raise ClimateApplicationViolation("climate runtime mode is invalid")
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateApplicationViolation("native climate observation is unavailable")
    if not isinstance(desired_state_changes, ClimateDesiredStateChanges):
        raise ClimateApplicationViolation("local desired-state changes are invalid")
    target_ids = _contour_ordered_target_ids(contour, target_room_ids)
    isolation = build_isolated_climate_policy_snapshot(contour, observation)
    comparison = build_climate_comparison_snapshot(isolation, observation)
    call_plan = build_climate_ha_call_plan(
        registry,
        isolation,
        ir_code_service=ir_code_service,
    )
    gates = tuple(
        _gate_room(
            room_id,
            contour,
            registry,
            bridge_mode,
            isolation,
            comparison,
            call_plan,
            observation,
            None if explicit_temperature_targets is None else explicit_temperature_targets.get(room_id),
            None if explicit_humidity_targets is None else explicit_humidity_targets.get(room_id),
        )
        for room_id in target_ids
    )
    explicit = explicit_temperature_targets is not None or explicit_humidity_targets is not None
    device_gates = (
        tuple(
            _gate_explicit_device(
                room_id, device, registry, observation,
                None if explicit_temperature_targets is None else explicit_temperature_targets.get(room_id),
                None if explicit_humidity_targets is None else explicit_humidity_targets.get(room_id),
            )
            for room_id in target_ids
            for device in _explicit_selected_devices(
                contour, registry, room_id,
                None if explicit_temperature_targets is None else explicit_temperature_targets.get(room_id),
                None if explicit_humidity_targets is None else explicit_humidity_targets.get(room_id),
            )
        ) if explicit else ()
    )
    if explicit:
        gates = _aggregate_explicit_room_gates(target_ids, device_gates)
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
        device_gates=device_gates,
        strict_calls=(
            ()
            if denials
            else tuple(
                call
                for gate in (device_gates or gates)
                if gate.status is ClimateApplicationGateStatus.READY
                for call in gate.strict_calls
            )
        ),
        initially_aligned_room_ids=tuple(
            gate.room_id for gate in gates
            if gate.status is ClimateApplicationGateStatus.ALIGNED
        ),
        denial_reasons=denials,
        explicit_temperature_targets=tuple(
            (room_id, target)
            for room_id in target_ids
            if explicit_temperature_targets is not None
            and (target := explicit_temperature_targets.get(room_id)) is not None
        ),
        explicit_humidity_targets=tuple(
            (room_id, target)
            for room_id in target_ids
            if explicit_humidity_targets is not None
            and (target := explicit_humidity_targets.get(room_id)) is not None
        ),
    )


def _explicit_selected_devices(contour, registry, room_id, temperature, humidity):
    assignment = next((room for room in contour.rooms if room.room_id == room_id), None)
    if assignment is None:
        return ()
    kinds = set()
    if temperature is not None:
        kinds.update({ClimateDeviceKind.AIR_CONDITIONER, ClimateDeviceKind.RADIATOR_THERMOSTAT, ClimateDeviceKind.FLOOR_HEATING})
    if humidity is not None:
        kinds.add(ClimateDeviceKind.HUMIDIFIER)
    return tuple(device for device_id in assignment.device_ids
                 if (device := registry.device(device_id)) is not None and device.kind in kinds)


def _gate_explicit_device(room_id, device, registry, observation, temperature, humidity):
    reasons: list[ClimateApplicationDenialReason] = []
    if device.control_scope is not ClimateControlScope.MANAGED or device.control_owner is not ClimateControlOwner.CLIMATE_CORE:
        reasons.append(ClimateApplicationDenialReason.ACTUATOR_NOT_MANAGED)
    if device.endpoint(ClimateEndpointRole.CONTROL) is None:
        reasons.append(ClimateApplicationDenialReason.MISSING_CONTROL_ENDPOINT)
    if _control_endpoint_is_shared(device, registry):
        reasons.append(ClimateApplicationDenialReason.TRANSLATION_INCOMPLETE)
    if not reasons:
        calls = (
            _explicit_humidity_calls_if_complete((device,), observation, humidity)
            if device.kind is ClimateDeviceKind.HUMIDIFIER and humidity is not None
            else _explicit_temperature_calls_if_complete((device,), observation, temperature)
        )
        if calls is None:
            reasons.append(ClimateApplicationDenialReason.TRANSLATION_INCOMPLETE)
    else:
        calls = ()
    if reasons:
        return ClimateApplicationDeviceGate(room_id, device.device_id, ClimateApplicationGateStatus.DENIED,
            ordered_application_denial_reasons(reasons), ())
    if not calls:
        return ClimateApplicationDeviceGate(room_id, device.device_id, ClimateApplicationGateStatus.ALIGNED,
            (ClimateApplicationDenialReason.ALREADY_IN_SYNC,), ())
    return ClimateApplicationDeviceGate(room_id, device.device_id, ClimateApplicationGateStatus.READY, (), calls)


def _aggregate_explicit_room_gates(target_ids, device_gates):
    result = []
    for room_id in target_ids:
        gates = tuple(gate for gate in device_gates if gate.room_id == room_id)
        if not gates:
            result.append(ClimateApplicationRoomGate(room_id, ClimateApplicationGateStatus.DENIED,
                (ClimateApplicationDenialReason.NO_ACTIVE_ACTUATOR,), ()))
            continue
        denials = ordered_application_denial_reasons(reason for gate in gates if gate.status is ClimateApplicationGateStatus.DENIED for reason in gate.reasons)
        if denials:
            result.append(ClimateApplicationRoomGate(room_id, ClimateApplicationGateStatus.DENIED, denials, ()))
            continue
        calls = tuple(call for gate in gates if gate.status is ClimateApplicationGateStatus.READY for call in gate.strict_calls)
        result.append(ClimateApplicationRoomGate(room_id,
            ClimateApplicationGateStatus.READY if calls else ClimateApplicationGateStatus.ALIGNED,
            () if calls else (ClimateApplicationDenialReason.ALREADY_IN_SYNC,), calls))
    return tuple(result)


def _gate_room(
    room_id: str,
    contour: ContourDefinition,
    registry: ClimateRegistry,
    bridge_mode: ClimateControlMode,
    isolation: ClimateIsolationSnapshot,
    comparison: ClimateComparisonSnapshot,
    call_plan: ClimateHaCallPlanSnapshot,
    observation: ClimateObservationSnapshot,
    explicit_temperature_target: float | None,
    explicit_humidity_target: int | None,
) -> ClimateApplicationRoomGate:
    reasons: list[ClimateApplicationDenialReason] = []
    if contour.mode is not ContourMode.AUTOMATIC:
        reasons.append(ClimateApplicationDenialReason.CONTOUR_NOT_AUTOMATIC)
    if bridge_mode is not ClimateControlMode.MANAGED:
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
    # An explicit home target owns only the selected physical axis.  Do not
    # let a missing humidifier endpoint block temperature, or an unrelated
    # thermostat block humidity.  A combined request deliberately validates
    # the union, so either incomplete axis rejects the whole frozen plan.
    if explicit_temperature_target is not None or explicit_humidity_target is not None:
        selected_kinds: set[ClimateDeviceKind] = set()
        if explicit_temperature_target is not None:
            selected_kinds.update({
                ClimateDeviceKind.AIR_CONDITIONER,
                ClimateDeviceKind.RADIATOR_THERMOSTAT,
                ClimateDeviceKind.FLOOR_HEATING,
            })
        if explicit_humidity_target is not None:
            selected_kinds.add(ClimateDeviceKind.HUMIDIFIER)
        actuators = tuple(
            device for device in actuators if device.kind in selected_kinds
        )
    if assignment is not None and not actuators:
        reasons.append(ClimateApplicationDenialReason.NO_ACTIVE_ACTUATOR)
    if any(
        device.control_scope is not ClimateControlScope.MANAGED
        or device.control_owner is not ClimateControlOwner.CLIMATE_CORE
        for device in actuators
    ):
        reasons.append(ClimateApplicationDenialReason.ACTUATOR_NOT_MANAGED)
    if any(device.endpoint(ClimateEndpointRole.CONTROL) is None for device in actuators):
        reasons.append(ClimateApplicationDenialReason.MISSING_CONTROL_ENDPOINT)
    if any(_control_endpoint_is_shared(device, registry) for device in actuators):
        reasons.append(ClimateApplicationDenialReason.TRANSLATION_INCOMPLETE)
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
    strict_calls = (
        ()
        if explicit_temperature_target is not None or explicit_humidity_target is not None
        else _strict_calls_if_complete(
            room_id,
            actuators,
            compared,
            call_plan,
            reasons,
        )
    )
    if explicit_temperature_target is not None and not reasons:
        explicit_calls = _explicit_temperature_calls_if_complete(
            tuple(
                device for device in actuators
                if device.kind in {
                    ClimateDeviceKind.AIR_CONDITIONER,
                    ClimateDeviceKind.RADIATOR_THERMOSTAT,
                    ClimateDeviceKind.FLOOR_HEATING,
                }
            ),
            observation,
            explicit_temperature_target,
        )
        if explicit_calls is None:
            reasons.append(ClimateApplicationDenialReason.TRANSLATION_INCOMPLETE)
        else:
            strict_calls = explicit_calls
    if explicit_humidity_target is not None and not reasons:
        if explicit_temperature_target is None:
            strict_calls = ()
        explicit_calls = _explicit_humidity_calls_if_complete(
            tuple(
                device for device in actuators
                if device.kind is ClimateDeviceKind.HUMIDIFIER
            ),
            observation,
            explicit_humidity_target,
        )
        if explicit_calls is None:
            reasons.append(ClimateApplicationDenialReason.TRANSLATION_INCOMPLETE)
        else:
            strict_calls = strict_calls + explicit_calls
    if reasons:
        return ClimateApplicationRoomGate(
            room_id=room_id,
            status=ClimateApplicationGateStatus.DENIED,
            reasons=ordered_application_denial_reasons(reasons),
            strict_calls=(),
        )
    if not strict_calls:
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


def _control_endpoint_is_shared(device: ClimateDevice, registry: ClimateRegistry) -> bool:
    endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
    if endpoint is None:
        return False
    return sum(
        candidate.endpoint(ClimateEndpointRole.CONTROL) == endpoint
        for candidate in registry.devices
    ) != 1


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


def _explicit_temperature_calls_if_complete(
    actuators: tuple[ClimateDevice, ...],
    observation: ClimateObservationSnapshot,
    target_temperature: float,
) -> tuple[ClimateHaServiceCall, ...] | None:
    """Build target-only calls for an explicit room target request.

    A room can already be thermally aligned while an idle climate entity still
    retains a different setpoint.  An explicit target request owns only that
    setpoint: it never wakes the entity or changes its HVAC mode.
    """

    if not actuators:
        return None
    calls: list[ClimateHaServiceCall] = []
    supported_kinds = {
        ClimateDeviceKind.AIR_CONDITIONER,
        ClimateDeviceKind.RADIATOR_THERMOSTAT,
        ClimateDeviceKind.FLOOR_HEATING,
    }
    for device in actuators:
        observed = observation.device(device.device_id)
        endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
        if (
            observed is None
            or observed.current_target_temperature == target_temperature
        ):
            continue
        if (
            device.kind not in supported_kinds
            or ClimateCapability.TARGET_TEMPERATURE not in device.capabilities
            or endpoint is None
            or endpoint.entity_id.split(".", 1)[0] != "climate"
        ):
            return None
        calls.append(
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_TEMPERATURE,
                entity_id=endpoint.entity_id,
                temperature=target_temperature,
                owner_device_id=device.device_id,
            )
        )
    return tuple(calls)


def _explicit_humidity_calls_if_complete(
    actuators: tuple[ClimateDevice, ...], observation: ClimateObservationSnapshot,
    target_humidity: int,
) -> tuple[ClimateHaServiceCall, ...] | None:
    """Build only humidifier target calls, without touching temperature or mode."""
    if not actuators:
        return None
    calls: list[ClimateHaServiceCall] = []
    for device in actuators:
        if device.kind is not ClimateDeviceKind.HUMIDIFIER:
            continue
        observed = observation.device(device.device_id)
        endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
        if observed is None or observed.current_target_humidity == target_humidity:
            continue
        if (
            ClimateCapability.TARGET_HUMIDITY not in device.capabilities
            or endpoint is None or endpoint.entity_id.split(".", 1)[0] != "humidifier"
        ):
            return None
        calls.append(ClimateHaServiceCall(
            service=ClimateHaService.HUMIDIFIER_SET_HUMIDITY,
            entity_id=endpoint.entity_id, humidity=target_humidity,
            owner_device_id=device.device_id,
        ))
    return tuple(calls)


def _contour_ordered_target_ids(
    contour: ContourDefinition,
    target_room_ids: tuple[str, ...],
) -> tuple[str, ...]:
    requested = set(target_room_ids)
    selected = tuple(room.room_id for room in contour.rooms if room.room_id in requested)
    return selected + tuple(room_id for room_id in target_room_ids if room_id not in selected)
