"""Translate typed HASC climate intents into the existing Climate API.

Callers can never supply a backend command type, service, source identifier,
or Home Assistant entity identifier.  Every private value is resolved from a
validated registry and an equally validated fresh Climate API snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..domain.climate_bridge import ClimateBridgeMode
from .climate_import import ClimateImportSnapshot


class ClimateCommandViolation(ValueError):
    """A public action is invalid or fails an execution safety gate."""


class ClimateCommandRejected(RuntimeError):
    """The climate backend explicitly declined a well-formed command."""


@dataclass(frozen=True, slots=True)
class ClimateCommandPlan:
    """A fixed backend command plus the decision whether it may be posted."""

    action: str
    room_id: str
    device_id: str | None
    backend_command_type: str
    backend_payload: dict[str, object]
    execute: bool
    confirmation_source_id: str | None = None


ROOM_ACTIONS = frozenset(
    {
        "set_room_target",
        "set_room_mode",
        "set_room_min_target",
        "set_room_target_strategy",
        "turn_room_off",
    }
)
DEVICE_ACTIONS = frozenset(
    {
        "set_device_power",
        "set_device_target_temperature",
        "set_device_target_humidity",
        "set_device_hvac_mode",
        "set_device_fan_mode",
    }
)


def plan_climate_command(
    request: object,
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    *,
    bridge_mode: ClimateBridgeMode,
    canary_room_id: str | None = None,
) -> ClimateCommandPlan:
    """Validate one tablet intent, translate it, and apply rollout gates."""

    if not isinstance(bridge_mode, ClimateBridgeMode):
        raise ClimateCommandViolation("climate bridge mode must be approved")
    if bridge_mode is ClimateBridgeMode.DISABLED:
        raise ClimateCommandViolation("climate commands are disabled")
    payload = _mapping(request, "climate action")
    action = payload.get("action")
    if action in ROOM_ACTIONS:
        plan = _room_command(action, payload, registry, snapshot)
    elif action in DEVICE_ACTIONS:
        plan = _device_command(action, payload, registry, snapshot)
    else:
        raise ClimateCommandViolation("climate action is unsupported")

    if bridge_mode is ClimateBridgeMode.SHADOW:
        return _with_execute(plan, False)
    _require_canary_execution(plan, registry, snapshot, canary_room_id)
    return _with_execute(plan, True)


def _room_command(
    action: object,
    request: Mapping[str, Any],
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> ClimateCommandPlan:
    room_id = request.get("room_id")
    room = registry.room(room_id) if isinstance(room_id, str) else None
    if room is None:
        raise ClimateCommandViolation("room is not registered")
    device = _room_control_device(registry, room.room_id)

    if action == "set_room_target":
        _exact_keys(request, {"action", "room_id", "target_temperature"})
        value = _temperature(request["target_temperature"])
        backend = {
            "command": action,
            "roomId": room.room_id,
            "targetTemperature": value,
        }
        required = "climate.set_temperature"
    elif action == "set_room_mode":
        _exact_keys(request, {"action", "room_id", "mode"})
        mode = request["mode"]
        if mode not in {"auto", "manual"}:
            raise ClimateCommandViolation("room mode must be auto or manual")
        backend = {"command": action, "roomId": room.room_id, "mode": mode}
        required = "climate.set_temperature"
    elif action == "set_room_min_target":
        _exact_keys(request, {"action", "room_id", "min_temperature"})
        value = _temperature(request["min_temperature"])
        backend = {
            "command": action,
            "roomId": room.room_id,
            "minTemperature": value,
        }
        required = "climate.set_temperature"
    elif action == "set_room_target_strategy":
        _exact_keys(request, {"action", "room_id", "target_strategy"})
        strategy = request["target_strategy"]
        if strategy not in {"soft", "normal", "aggressive"}:
            raise ClimateCommandViolation("room strategy is unsupported")
        backend = {
            "command": action,
            "roomId": room.room_id,
            "targetStrategy": strategy,
        }
        required = "climate.set_temperature"
    else:
        _exact_keys(request, {"action", "room_id"})
        backend = {"command": "turn_room_off", "roomId": room.room_id}
        required = "climate.turn_off"

    _require_backend_type(snapshot, device, required)
    return ClimateCommandPlan(
        action=str(action),
        room_id=room.room_id,
        device_id=None,
        backend_command_type=required,
        backend_payload=backend,
        execute=False,
        confirmation_source_id=device.source_id,
    )


def _device_command(
    action: object,
    request: Mapping[str, Any],
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> ClimateCommandPlan:
    public_id = request.get("device_id")
    device = registry.device(public_id) if isinstance(public_id, str) else None
    if device is None:
        raise ClimateCommandViolation("device is not registered")
    _require_controlled_device(device)

    if action == "set_device_power":
        _exact_keys(request, {"action", "device_id", "on"})
        _require_capability(device, ClimateCapability.POWER)
        if type(request["on"]) is not bool:
            raise ClimateCommandViolation("device power must be true or false")
        command_type, command_data = _power_command(device, request["on"])
    elif action == "set_device_target_temperature":
        _exact_keys(request, {"action", "device_id", "target_temperature"})
        _require_capability(device, ClimateCapability.TARGET_TEMPERATURE)
        command_type, command_data = _temperature_command(
            device, _temperature(request["target_temperature"])
        )
    elif action == "set_device_target_humidity":
        _exact_keys(request, {"action", "device_id", "target_humidity"})
        _require_capability(device, ClimateCapability.TARGET_HUMIDITY)
        if device.kind is not ClimateDeviceKind.HUMIDIFIER:
            raise ClimateCommandViolation("target humidity requires a humidifier")
        command_type = "humidifier.set_humidity"
        command_data = {"humidity": _humidity(request["target_humidity"])}
    elif action == "set_device_hvac_mode":
        _exact_keys(request, {"action", "device_id", "hvac_mode"})
        _require_capability(device, ClimateCapability.HVAC_MODE)
        command_type, command_data = _hvac_command(device, request["hvac_mode"])
    else:
        _exact_keys(request, {"action", "device_id", "fan_mode"})
        _require_capability(device, ClimateCapability.FAN_MODE)
        if device.kind is not ClimateDeviceKind.AIR_CONDITIONER:
            raise ClimateCommandViolation("fan mode requires an air conditioner")
        fan_mode = request["fan_mode"]
        if fan_mode not in {"low", "medium", "high"}:
            raise ClimateCommandViolation("fan mode is unsupported")
        command_type = "climate.set_fan_mode"
        command_data = {"fan_mode": fan_mode}

    _require_backend_type(snapshot, device, command_type)
    return ClimateCommandPlan(
        action=str(action),
        room_id=device.room_id,
        device_id=device.device_id,
        backend_command_type=command_type,
        backend_payload={
            "command": "device_action",
            "roomId": device.room_id,
            "deviceId": device.source_id,
            "payload": {"type": command_type, **command_data},
        },
        execute=False,
        confirmation_source_id=device.source_id,
    )


def _power_command(device: ClimateDevice, turn_on: bool) -> tuple[str, dict[str, object]]:
    if device.kind is ClimateDeviceKind.AIR_CONDITIONER:
        return (
            ("climate.set_hvac_mode", {"hvac_mode": "cool"})
            if turn_on
            else ("climate.turn_off", {})
        )
    if device.kind is ClimateDeviceKind.HUMIDIFIER:
        return ("humidifier.turn_on" if turn_on else "humidifier.turn_off", {})
    if device.kind is ClimateDeviceKind.FLOOR_HEATING:
        endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
        if endpoint is not None and endpoint.entity_id.startswith("switch."):
            return ("switch.turn_on" if turn_on else "switch.turn_off", {})
        return (
            ("climate.set_hvac_mode", {"hvac_mode": "heat"})
            if turn_on
            else ("climate.turn_off", {})
        )
    raise ClimateCommandViolation("device kind has no typed power contract")


def _temperature_command(
    device: ClimateDevice, value: float
) -> tuple[str, dict[str, object]]:
    if device.kind is ClimateDeviceKind.RADIATOR_THERMOSTAT:
        return "trv.set_temperature", {"temperature": value}
    if device.kind in {
        ClimateDeviceKind.AIR_CONDITIONER,
        ClimateDeviceKind.FLOOR_HEATING,
    }:
        return "climate.set_temperature", {"temperature": value}
    raise ClimateCommandViolation("device kind has no typed temperature contract")


def _hvac_command(device: ClimateDevice, value: object) -> tuple[str, dict[str, object]]:
    allowed = {
        ClimateDeviceKind.AIR_CONDITIONER: {"cool"},
        ClimateDeviceKind.FLOOR_HEATING: {"heat"},
    }.get(device.kind, set())
    if value not in allowed:
        raise ClimateCommandViolation("HVAC mode is unsupported for this device")
    return "climate.set_hvac_mode", {"hvac_mode": value}


def _room_control_device(registry: ClimateRegistry, room_id: str) -> ClimateDevice:
    candidates = [
        device
        for device in registry.devices
        if device.room_id == room_id
        and device.kind is ClimateDeviceKind.AIR_CONDITIONER
        and device.control_owner is ClimateControlOwner.CLIMATE_CORE
        and device.control_scope is not ClimateControlScope.OBSERVED
    ]
    if len(candidates) != 1:
        raise ClimateCommandViolation("room needs exactly one controlled air conditioner")
    return candidates[0]


def _require_controlled_device(device: ClimateDevice) -> None:
    if device.control_scope is ClimateControlScope.OBSERVED:
        raise ClimateCommandViolation("device is observation-only")
    if device.control_owner is not ClimateControlOwner.CLIMATE_CORE:
        raise ClimateCommandViolation("device is not owned by climate core")


def _require_capability(device: ClimateDevice, capability: ClimateCapability) -> None:
    if not device.supports(capability):
        raise ClimateCommandViolation("device capability is not configured")


def _require_backend_type(
    snapshot: ClimateImportSnapshot,
    device: ClimateDevice,
    command_type: str,
) -> None:
    imported = snapshot.device(device.source_id)
    if imported is None or imported.room_id != device.room_id:
        raise ClimateCommandViolation("device binding does not match imported state")
    if not imported.available:
        raise ClimateCommandViolation("device is unavailable")
    if command_type not in imported.command_types:
        raise ClimateCommandViolation("backend command type is not advertised")


def _require_canary_execution(
    plan: ClimateCommandPlan,
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    canary_room_id: str | None,
) -> None:
    if not snapshot.runtime_fresh:
        raise ClimateCommandViolation("climate runtime is stale")
    if canary_room_id is None or plan.room_id != canary_room_id:
        raise ClimateCommandViolation("command is outside the configured canary room")
    room = snapshot.room(plan.room_id)
    if room is None or not room.authority_eligible:
        raise ClimateCommandViolation("room authority is not eligible")
    if plan.device_id is not None:
        device = registry.device(plan.device_id)
        if device is None or device.control_scope not in {
            ClimateControlScope.CANARY,
            ClimateControlScope.MANAGED,
        }:
            raise ClimateCommandViolation("device is outside its rollout scope")


def _with_execute(plan: ClimateCommandPlan, execute: bool) -> ClimateCommandPlan:
    return ClimateCommandPlan(
        action=plan.action,
        room_id=plan.room_id,
        device_id=plan.device_id,
        backend_command_type=plan.backend_command_type,
        backend_payload=plan.backend_payload,
        execute=execute,
        confirmation_source_id=plan.confirmation_source_id,
    )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ClimateCommandViolation(f"{label} must be an object")
    return value


def _exact_keys(values: Mapping[str, Any], expected: set[str]) -> None:
    if set(values) != expected:
        raise ClimateCommandViolation("climate action must contain only its fixed fields")


def _temperature(value: object) -> float:
    if type(value) not in {int, float}:
        raise ClimateCommandViolation("temperature must be numeric")
    number = float(value)
    exact = Decimal(str(value))
    if (
        not exact.is_finite()
        or not Decimal(18) <= exact <= Decimal(28)
        or exact % Decimal("0.5") != 0
    ):
        raise ClimateCommandViolation("temperature must be 18..28 in 0.5 steps")
    return number


def _humidity(value: object) -> int:
    if type(value) is not int or not 30 <= value <= 70:
        raise ClimateCommandViolation("humidity must be an integer from 30 to 70")
    return value
