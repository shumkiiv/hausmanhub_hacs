"""Translate proven climate plans into strict Home Assistant call plans."""

from __future__ import annotations

from ..domain.climate import (
    ClimateCapability,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..domain.climate_equipment import CLIMATE_TRV_SAFE_OFF_TARGET
from ..domain.climate_ha_calls import (
    ClimateHaCallLimit,
    ClimateHaCallPlanSnapshot,
    ClimateHaCallViolation,
    ClimateHaDeviceCallPlan,
    ClimateHaHvacMode,
    ClimateHaRoomCallPlan,
    ClimateHaService,
    ClimateHaServiceCall,
)
from ..domain.climate_isolation import ClimateIsolationSnapshot
from ..domain.climate_policy import ClimateFinalDeviceAction, ClimateFinalDevicePlan
from ..domain.ir_codes import required_ir_command_key


_OBSERVE_ACTIONS = frozenset(
    {
        ClimateFinalDeviceAction.OBSERVE,
        ClimateFinalDeviceAction.UNAVAILABLE,
    }
)
_STOP_ACTIONS = frozenset(
    {
        ClimateFinalDeviceAction.OFF,
        ClimateFinalDeviceAction.SAFE_OFF,
    }
)


def build_climate_ha_call_plan(
    registry: ClimateRegistry,
    isolation: ClimateIsolationSnapshot,
    ir_code_service: object | None = None,
) -> ClimateHaCallPlanSnapshot:
    """Translate one isolated policy snapshot into strict HA call plans."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateHaCallViolation("validated climate registry is required")
    if not isinstance(isolation, ClimateIsolationSnapshot):
        raise ClimateHaCallViolation("validated isolation snapshot is required")
    rooms = tuple(
        ClimateHaRoomCallPlan(
            room_id=result.room_id,
            devices=tuple(
                _translate_device(
                    _registry_device(registry, plan.device_id),
                    plan,
                    ir_code_service=ir_code_service,
                )
                for plan in (result.policy.devices if result.policy is not None else ())
            ),
        )
        for result in isolation.rooms
    )
    return ClimateHaCallPlanSnapshot(
        contour_id=isolation.contour_id,
        contour_mode=isolation.contour_mode,
        observed_at=isolation.observed_at,
        rooms=rooms,
    )


def _registry_device(
    registry: ClimateRegistry,
    device_id: str,
) -> ClimateDevice | None:
    return next(
        (device for device in registry.devices if device.device_id == device_id),
        None,
    )


def _translate_device(
    device: ClimateDevice | None,
    plan: ClimateFinalDevicePlan,
    ir_code_service: object | None = None,
) -> ClimateHaDeviceCallPlan:
    limits: list[ClimateHaCallLimit] = []
    if device is None:
        raise ClimateHaCallViolation("call plan requires a registered device")
    calls: tuple[ClimateHaServiceCall, ...] = ()
    if plan.action in _OBSERVE_ACTIONS:
        limits.append(ClimateHaCallLimit.OBSERVE_ONLY)
    elif plan.action is ClimateFinalDeviceAction.HOLD:
        limits.append(ClimateHaCallLimit.HOLD_STATE)
    else:
        calls = _service_calls(device, plan, limits, ir_code_service=ir_code_service)
    # quiet=False is the absence of a quiet requirement, not a request;
    # only an explicit quiet=True that cannot be expressed limits the plan.
    if plan.quiet and not any(
        call.service is ClimateHaService.REMOTE_SEND_COMMAND for call in calls
    ):
        limits.append(ClimateHaCallLimit.QUIET_NOT_TRANSLATED)
    ordered = tuple(limit for limit in ClimateHaCallLimit if limit in limits)
    return ClimateHaDeviceCallPlan(
        device_id=plan.device_id,
        room_id=plan.room_id,
        kind=device.kind,
        action=plan.action,
        calls=calls,
        limits=ordered,
    )


def _service_calls(
    device: ClimateDevice,
    plan: ClimateFinalDevicePlan,
    limits: list[ClimateHaCallLimit],
    ir_code_service: object | None = None,
) -> tuple[ClimateHaServiceCall, ...]:
    endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
    if endpoint is not None and endpoint.entity_id.split(".", 1)[0] == "remote":
        return _remote_ir_service_call(
            device,
            plan,
            endpoint.entity_id,
            limits,
            ir_code_service,
        )
    required = _required_capabilities(device.kind, plan.action)
    if required is None or (
        plan.target_temperature is None
        and _temperature_required(device.kind, plan.action)
    ):
        limits.append(ClimateHaCallLimit.UNSUPPORTED_ACTION)
        return ()
    if plan.fan_mode is not None:
        required = required | {ClimateCapability.FAN_MODE}
    missing = required - set(device.capabilities)
    if missing:
        limits.append(ClimateHaCallLimit.MISSING_CAPABILITY)
        return ()
    if endpoint is None:
        limits.append(ClimateHaCallLimit.MISSING_CONTROL_ENDPOINT)
        return ()
    entity_id = endpoint.entity_id
    if device.kind is ClimateDeviceKind.HUMIDIFIER:
        return (
            ClimateHaServiceCall(
                service=(
                    ClimateHaService.HUMIDIFIER_TURN_ON
                    if plan.action is ClimateFinalDeviceAction.HUMIDIFY
                    else ClimateHaService.HUMIDIFIER_TURN_OFF
                ),
                entity_id=entity_id,
            ),
        )
    calls: list[ClimateHaServiceCall] = []
    if (
        device.kind is ClimateDeviceKind.RADIATOR_THERMOSTAT
        and plan.action in _STOP_ACTIONS
    ):
        # A TRV has no power relay; driving its setpoint to the frost-protection
        # minimum closes the valve, which is the safe-off state.
        calls.append(
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_TEMPERATURE,
                entity_id=entity_id,
                temperature=CLIMATE_TRV_SAFE_OFF_TARGET,
            )
        )
        return tuple(calls)
    if plan.action in _STOP_ACTIONS:
        calls.append(
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_HVAC_MODE,
                entity_id=entity_id,
                hvac_mode=ClimateHaHvacMode.OFF,
            )
        )
        return tuple(calls)
    if device.kind is ClimateDeviceKind.AIR_CONDITIONER and plan.action in {
        ClimateFinalDeviceAction.COOL,
        ClimateFinalDeviceAction.HEAT,
    }:
        calls.append(
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_HVAC_MODE,
                entity_id=entity_id,
                hvac_mode=(
                    ClimateHaHvacMode.COOL
                    if plan.action is ClimateFinalDeviceAction.COOL
                    else ClimateHaHvacMode.HEAT
                ),
            )
        )
    if plan.target_temperature is not None:
        calls.append(
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_TEMPERATURE,
                entity_id=entity_id,
                temperature=plan.target_temperature,
            )
        )
    if plan.fan_mode is not None:
        calls.append(
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_FAN_MODE,
                entity_id=entity_id,
                fan_mode=plan.fan_mode,
            )
        )
    if not calls:
        limits.append(ClimateHaCallLimit.NOTHING_TO_TRANSLATE)
    return tuple(calls)


def _required_capabilities(
    kind: ClimateDeviceKind,
    action: ClimateFinalDeviceAction,
) -> frozenset[ClimateCapability] | None:
    if kind is ClimateDeviceKind.HUMIDIFIER:
        if action in _STOP_ACTIONS or action is ClimateFinalDeviceAction.HUMIDIFY:
            return frozenset({ClimateCapability.POWER})
        return None
    if action in _STOP_ACTIONS:
        if kind is ClimateDeviceKind.RADIATOR_THERMOSTAT:
            return frozenset({ClimateCapability.TARGET_TEMPERATURE})
        if kind in {
            ClimateDeviceKind.AIR_CONDITIONER,
            ClimateDeviceKind.FLOOR_HEATING,
        }:
            return frozenset({ClimateCapability.POWER, ClimateCapability.HVAC_MODE})
        return None
    if kind is ClimateDeviceKind.AIR_CONDITIONER:
        if action is ClimateFinalDeviceAction.COOL:
            return frozenset(
                {
                    ClimateCapability.POWER,
                    ClimateCapability.HVAC_MODE,
                    ClimateCapability.TARGET_TEMPERATURE,
                }
            )
        if action is ClimateFinalDeviceAction.HEAT:
            return frozenset({ClimateCapability.POWER, ClimateCapability.HVAC_MODE})
        if action in {
            ClimateFinalDeviceAction.MAINTAIN,
            ClimateFinalDeviceAction.SET_TEMPERATURE,
        }:
            return frozenset({ClimateCapability.TARGET_TEMPERATURE})
    if kind is ClimateDeviceKind.RADIATOR_THERMOSTAT:
        if action is ClimateFinalDeviceAction.SET_TEMPERATURE:
            return frozenset({ClimateCapability.TARGET_TEMPERATURE})
    if kind is ClimateDeviceKind.FLOOR_HEATING:
        if action is ClimateFinalDeviceAction.SET_TEMPERATURE:
            return frozenset({ClimateCapability.TARGET_TEMPERATURE})
    return None


def _temperature_required(kind: ClimateDeviceKind, action: ClimateFinalDeviceAction) -> bool:
    return (
        kind in {
            ClimateDeviceKind.RADIATOR_THERMOSTAT,
            ClimateDeviceKind.FLOOR_HEATING,
        }
        and action is ClimateFinalDeviceAction.SET_TEMPERATURE
    )


def _remote_ir_service_call(
    device: ClimateDevice,
    plan: ClimateFinalDevicePlan,
    remote_entity_id: str,
    limits: list[ClimateHaCallLimit],
    ir_code_service: object | None,
) -> tuple[ClimateHaServiceCall, ...]:
    command_name = required_ir_command_key(
        device.kind,
        plan.action,
        plan.target_temperature,
    )
    if command_name is None:
        limits.append(ClimateHaCallLimit.UNSUPPORTED_ACTION)
        return ()
    getter = None if ir_code_service is None else getattr(
        ir_code_service, "code_for_command", None
    )
    code = (
        None
        if getter is None
        else getter(device.device_id, command_name)
    )
    if code is None:
        limits.append(ClimateHaCallLimit.IR_COMMAND_NOT_LEARNED)
        return ()
    code_data = code.code_data
    return (
        ClimateHaServiceCall(
            service=ClimateHaService.REMOTE_SEND_COMMAND,
            entity_id=remote_entity_id,
            device=device.device_id,
            command=(
                code_data if code_data.startswith("b64:") else f"b64:{code_data}"
            ),
        ),
    )
