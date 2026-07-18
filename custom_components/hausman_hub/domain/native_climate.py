"""Pure preview policy for the climate controller being built inside HASC.

The first native-controller stage deliberately calculates demand only.  It has
no transport, Home Assistant service, or command dependency, so turning the
preview on cannot change a physical device.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol

from .climate import (
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateModelViolation,
    ClimateRegistry,
    ClimateRoom,
)


NATIVE_TARGET_TEMPERATURE_DEFAULT = 22.0
NATIVE_TARGET_HUMIDITY_DEFAULT = 45
NATIVE_TEMPERATURE_DEADBAND = 0.5
NATIVE_HUMIDITY_DEADBAND = 5.0


class NativeClimateViolation(ValueError):
    """A native-controller policy is incomplete or outside fixed bounds."""


class NativeClimateRoomState(Protocol):
    """Read-only room values required by the pure decision function."""

    temperature: float | None
    humidity: float | None


class NativeClimateDeviceState(Protocol):
    """Read-only device value required by equipment readiness checks."""

    available: bool


class NativeClimateSnapshot(Protocol):
    """Small observation boundary implemented by the existing Climate API."""

    runtime_fresh: bool

    def room(self, room_id: str) -> NativeClimateRoomState | None:
        """Return one observed room."""

    def device(self, source_id: str) -> NativeClimateDeviceState | None:
        """Return one observed device."""


class NativeClimateMode(StrEnum):
    """Approved rollout stages for decisions owned by HASC itself."""

    DISABLED = "disabled"
    PREVIEW = "preview"


class TemperatureDemand(StrEnum):
    """Temperature result calculated without creating a device command."""

    UNAVAILABLE = "unavailable"
    HEATING = "heating"
    HOLD = "hold"
    COOLING = "cooling"


class HumidityDemand(StrEnum):
    """Humidity result calculated without creating a device command."""

    UNAVAILABLE = "unavailable"
    HUMIDIFYING = "humidifying"
    HOLD = "hold"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class NativeClimatePolicy:
    """One-room target policy stored by HASC."""

    mode: NativeClimateMode = NativeClimateMode.DISABLED
    room_id: str | None = None
    target_temperature: float | None = None
    target_humidity: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, NativeClimateMode):
            raise NativeClimateViolation("native climate mode must be approved")
        if self.mode is NativeClimateMode.DISABLED:
            if any(
                value is not None
                for value in (
                    self.room_id,
                    self.target_temperature,
                    self.target_humidity,
                )
            ):
                raise NativeClimateViolation(
                    "disabled native climate must not retain a room or targets"
                )
            return
        try:
            ClimateRoom(self.room_id, "Temporary")  # type: ignore[arg-type]
        except ClimateModelViolation as error:
            raise NativeClimateViolation(
                "native climate room must be a stable lowercase id"
            ) from error
        _target_temperature(self.target_temperature)
        _target_humidity(self.target_humidity)


@dataclass(frozen=True, slots=True)
class NativeClimateDecision:
    """Redacted one-room decision that can never grant command authority."""

    status: str
    room_id: str | None
    room_name: str | None
    current_temperature: float | None
    current_humidity: float | None
    target_temperature: float | None
    target_humidity: int | None
    temperature_demand: TemperatureDemand
    humidity_demand: HumidityDemand
    temperature_device_ready: bool
    humidity_device_ready: bool
    reasons: tuple[str, ...]

    def as_payload(self) -> dict[str, object]:
        """Return the fixed local-admin preview contract."""

        return {
            "contract": {
                "name": "hausman-hasc-native-climate-preview",
                "version": 1,
            },
            "status": self.status,
            "room_id": self.room_id,
            "room_name": self.room_name,
            "current": {
                "temperature": self.current_temperature,
                "humidity": self.current_humidity,
            },
            "targets": {
                "temperature": self.target_temperature,
                "humidity": self.target_humidity,
            },
            "decision": {
                "temperature": self.temperature_demand.value,
                "humidity": self.humidity_demand.value,
            },
            "equipment": {
                "temperature_ready": self.temperature_device_ready,
                "humidity_ready": self.humidity_device_ready,
            },
            "execution": {
                "mode": "preview_only",
                "commands_enabled": False,
            },
            "reasons": list(self.reasons),
        }


def native_climate_policy(
    mode_value: object,
    room_id_value: object = None,
    target_temperature_value: object = None,
    target_humidity_value: object = None,
) -> NativeClimatePolicy:
    """Build one exact policy from saved or form values."""

    try:
        mode = NativeClimateMode(mode_value)
    except (TypeError, ValueError) as error:
        raise NativeClimateViolation("native climate mode must be approved") from error
    if mode is NativeClimateMode.DISABLED:
        if any(
            value is not None
            for value in (
                room_id_value,
                target_temperature_value,
                target_humidity_value,
            )
        ):
            raise NativeClimateViolation(
                "disabled native climate must not retain a room or targets"
            )
        return NativeClimatePolicy(mode=mode)
    if not isinstance(room_id_value, str):
        raise NativeClimateViolation("native climate room is required")
    return NativeClimatePolicy(
        mode=mode,
        room_id=room_id_value,
        target_temperature=_target_temperature(target_temperature_value),
        target_humidity=_target_humidity(target_humidity_value),
    )


def preview_native_climate(
    policy: NativeClimatePolicy,
    registry: ClimateRegistry,
    snapshot: NativeClimateSnapshot | None,
) -> NativeClimateDecision:
    """Calculate one room's demand without constructing or sending commands."""

    if policy.mode is NativeClimateMode.DISABLED:
        return _empty_decision(policy, status="disabled", reasons=("controller_disabled",))
    room = registry.room(policy.room_id or "")
    if room is None:
        return _empty_decision(
            policy,
            status="room_missing",
            reasons=("room_not_registered",),
        )
    if snapshot is None:
        return _empty_decision(
            policy,
            status="unavailable",
            room_name=room.name,
            reasons=("climate_state_unavailable",),
        )
    imported_room = snapshot.room(room.room_id)
    if imported_room is None:
        return _empty_decision(
            policy,
            status="unavailable",
            room_name=room.name,
            reasons=("room_state_unavailable",),
        )
    if not snapshot.runtime_fresh:
        return _empty_decision(
            policy,
            status="stale",
            room_name=room.name,
            current_temperature=imported_room.temperature,
            current_humidity=imported_room.humidity,
            reasons=("state_stale",),
        )

    temperature = imported_room.temperature
    humidity = imported_room.humidity
    temperature_demand = _temperature_demand(
        temperature,
        policy.target_temperature,
    )
    humidity_demand = _humidity_demand(humidity, policy.target_humidity)
    temperature_ready = _temperature_device_ready(
        temperature_demand,
        room.room_id,
        registry,
        snapshot,
    )
    humidity_ready = _humidity_device_ready(
        humidity_demand,
        room.room_id,
        registry,
        snapshot,
    )
    reasons: list[str] = []
    if temperature is None:
        reasons.append("temperature_unavailable")
    elif temperature_demand in {TemperatureDemand.HEATING, TemperatureDemand.COOLING}:
        if not temperature_ready:
            reasons.append("temperature_device_unavailable")
    if humidity is None:
        reasons.append("humidity_unavailable")
    elif humidity_demand is HumidityDemand.HUMIDIFYING and not humidity_ready:
        reasons.append("humidity_device_unavailable")
    if humidity_demand is HumidityDemand.HIGH:
        reasons.append("humidity_above_target")
    return NativeClimateDecision(
        status="ready" if temperature is not None or humidity is not None else "unavailable",
        room_id=room.room_id,
        room_name=room.name,
        current_temperature=temperature,
        current_humidity=humidity,
        target_temperature=policy.target_temperature,
        target_humidity=policy.target_humidity,
        temperature_demand=temperature_demand,
        humidity_demand=humidity_demand,
        temperature_device_ready=temperature_ready,
        humidity_device_ready=humidity_ready,
        reasons=tuple(reasons),
    )


def _empty_decision(
    policy: NativeClimatePolicy,
    *,
    status: str,
    reasons: tuple[str, ...],
    room_name: str | None = None,
    current_temperature: float | None = None,
    current_humidity: float | None = None,
) -> NativeClimateDecision:
    return NativeClimateDecision(
        status=status,
        room_id=policy.room_id,
        room_name=room_name,
        current_temperature=current_temperature,
        current_humidity=current_humidity,
        target_temperature=policy.target_temperature,
        target_humidity=policy.target_humidity,
        temperature_demand=TemperatureDemand.UNAVAILABLE,
        humidity_demand=HumidityDemand.UNAVAILABLE,
        temperature_device_ready=False,
        humidity_device_ready=False,
        reasons=reasons,
    )


def _target_temperature(value: object) -> float:
    if isinstance(value, bool):
        raise NativeClimateViolation("native target temperature is invalid")
    try:
        target = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise NativeClimateViolation("native target temperature is invalid") from error
    if not target.is_finite() or not Decimal("18") <= target <= Decimal("28"):
        raise NativeClimateViolation("native target temperature must be 18 to 28")
    if target * 2 != (target * 2).to_integral_value():
        raise NativeClimateViolation(
            "native target temperature must use half-degree steps"
        )
    return float(target)


def _target_humidity(value: object) -> int:
    if type(value) is not int or not 30 <= value <= 70 or value % 5:
        raise NativeClimateViolation(
            "native target humidity must be 30 to 70 in five-percent steps"
        )
    return value


def _temperature_demand(
    current: float | None,
    target: float | None,
) -> TemperatureDemand:
    if current is None or target is None:
        return TemperatureDemand.UNAVAILABLE
    if current < target - NATIVE_TEMPERATURE_DEADBAND:
        return TemperatureDemand.HEATING
    if current > target + NATIVE_TEMPERATURE_DEADBAND:
        return TemperatureDemand.COOLING
    return TemperatureDemand.HOLD


def _humidity_demand(
    current: float | None,
    target: int | None,
) -> HumidityDemand:
    if current is None or target is None:
        return HumidityDemand.UNAVAILABLE
    if current < target - NATIVE_HUMIDITY_DEADBAND:
        return HumidityDemand.HUMIDIFYING
    if current > target + NATIVE_HUMIDITY_DEADBAND:
        return HumidityDemand.HIGH
    return HumidityDemand.HOLD


def _temperature_device_ready(
    demand: TemperatureDemand,
    room_id: str,
    registry: ClimateRegistry,
    snapshot: NativeClimateSnapshot,
) -> bool:
    if demand is TemperatureDemand.UNAVAILABLE:
        return False
    if demand is TemperatureDemand.COOLING:
        kinds = {ClimateDeviceKind.AIR_CONDITIONER}
    elif demand is TemperatureDemand.HEATING:
        kinds = {
            ClimateDeviceKind.RADIATOR_THERMOSTAT,
            ClimateDeviceKind.FLOOR_HEATING,
        }
    else:
        kinds = {
            ClimateDeviceKind.AIR_CONDITIONER,
            ClimateDeviceKind.RADIATOR_THERMOSTAT,
            ClimateDeviceKind.FLOOR_HEATING,
        }
    return _available_control_device(room_id, kinds, registry, snapshot)


def _humidity_device_ready(
    demand: HumidityDemand,
    room_id: str,
    registry: ClimateRegistry,
    snapshot: NativeClimateSnapshot,
) -> bool:
    if demand not in {HumidityDemand.HOLD, HumidityDemand.HUMIDIFYING}:
        return False
    return _available_control_device(
        room_id,
        {ClimateDeviceKind.HUMIDIFIER},
        registry,
        snapshot,
    )


def _available_control_device(
    room_id: str,
    kinds: set[ClimateDeviceKind],
    registry: ClimateRegistry,
    snapshot: NativeClimateSnapshot,
) -> bool:
    for device in registry.devices:
        if (
            device.room_id != room_id
            or device.kind not in kinds
            or device.control_scope is ClimateControlScope.OBSERVED
        ):
            continue
        imported = snapshot.device(device.source_id)
        if imported is not None and imported.available:
            return True
    return False
