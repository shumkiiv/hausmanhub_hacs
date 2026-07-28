"""Translate proven climate plans into strict Home Assistant call plans."""

from __future__ import annotations

from ..domain.climate import (
    ClimateCapability,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
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
    if plan.quiet is not None:
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
        # Check whether IR codes are available for this device before
        # marking the channel as unsupported.
        if _has_ir_codes_for_device(ir_code_service, device.device_id):
            # IR codes exist — the remote endpoint is usable but the
            # standard climate service calls do not apply.  The caller
            # should fall back to IR command translation separately.
            limits.append(ClimateHaCallLimit.NOTHING_TO_TRANSLATE)
            return ()
        limits.append(ClimateHaCallLimit.IR_COMMAND_NOT_LEARNED)
        return ()
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


def _has_ir_codes_for_device(ir_code_service: object | None, device_id: str) -> bool:
    if ir_code_service is None:
        return False
    getter = getattr(ir_code_service, "codes_for_device", None)
    if getter is None:
        return False
    try:
        return len(getter(device_id)) > 0
    except Exception:  # noqa: BLE001
        return False
