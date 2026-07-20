"""Build command-free plans for configured thermal equipment.

The plans use stable HausmanHub device ids and typed values only.  They are
descriptive algorithm output, not Home Assistant service calls or executable
intents.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
import math

from .climate import ClimateModelViolation, ClimateRoom
from .climate_observation import (
    ClimateDataStatus,
    ClimateDayPeriod,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateFanMode,
    ClimateHomeObservation,
    ClimateObservationDeviceKind,
    ClimateOccupancyMode,
    ClimateSeason,
)
from .climate_resolution import (
    ClimateRoomThermalResolution,
    ClimateThermalResolution,
)
from .climate_targets import ClimateRoomTarget
from .contours import ClimateStrategy, ContourMode


CLIMATE_EQUIPMENT_MODEL_VERSION = 1
CLIMATE_GENERIC_COOLING_SETPOINT = 26.0
CLIMATE_TRV_DAY_TARGET = 19.0
CLIMATE_TRV_NIGHT_TARGET = 17.0
CLIMATE_TRV_COLD_ADJUSTMENT = 0.5
CLIMATE_TRV_WARM_LOAD_ADJUSTMENT = 0.5

_THERMAL_KINDS = frozenset(
    {
        ClimateObservationDeviceKind.AIR_CONDITIONER,
        ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
        ClimateObservationDeviceKind.FLOOR_HEATING,
    }
)


class ClimateEquipmentViolation(ValueError):
    """A thermal equipment plan is mutable, incomplete, or contradictory."""


class ClimateEquipmentAction(StrEnum):
    """Typed device-level result which still cannot execute."""

    COOL = "cool"
    SET_TEMPERATURE = "set_temperature"
    HEAT = "heat"
    SAFE_OFF = "safe_off"
    HOLD = "hold"
    OBSERVE = "observe"
    UNAVAILABLE = "unavailable"


class ClimateEquipmentReason(StrEnum):
    """Stable reason for one command-free equipment plan."""

    DEVICE_UNAVAILABLE = "device_unavailable"
    COOLING_REQUIRED = "cooling_required"
    HEATING_SCHEDULE = "heating_schedule"
    HEATING_REQUIRED = "heating_required"
    AWAY_SAFE_OFF = "away_safe_off"
    THERMAL_OBSERVE = "thermal_observe"
    NO_THERMAL_CHANGE = "no_thermal_change"
    CENTRAL_HEATING_OFF = "central_heating_off"
    PERIOD_UNAVAILABLE = "period_unavailable"


@dataclass(frozen=True, slots=True)
class ClimateDevicePlan:
    """One validated thermal-device plan with all policy inputs retained."""

    device_id: str
    room_id: str
    kind: ClimateObservationDeviceKind
    availability: ClimateDeviceAvailability
    activity: ClimateDeviceActivity
    room_data_status: ClimateDataStatus
    thermal: ClimateThermalResolution
    season: ClimateSeason
    period: ClimateDayPeriod
    occupancy: ClimateOccupancyMode
    central_heating_on: bool | None
    outdoor_temperature: float | None
    heat_load_temperature: float | None
    comfort_temperature: float
    strategy: ClimateStrategy
    action: ClimateEquipmentAction
    target_temperature: float | None
    fan_mode: ClimateFanMode | None
    quiet: bool | None
    reason: ClimateEquipmentReason

    def __post_init__(self) -> None:
        _stable_id(self.device_id, "thermal device id")
        _stable_id(self.room_id, "thermal device room id")
        if self.kind not in _THERMAL_KINDS:
            raise ClimateEquipmentViolation("device kind has no thermal policy")
        _enum(self.availability, ClimateDeviceAvailability, "device availability")
        _enum(self.activity, ClimateDeviceActivity, "device activity")
        _enum(self.room_data_status, ClimateDataStatus, "room data status")
        _enum(self.thermal, ClimateThermalResolution, "thermal resolution")
        _enum(self.season, ClimateSeason, "thermal season")
        _enum(self.period, ClimateDayPeriod, "day period")
        _enum(self.occupancy, ClimateOccupancyMode, "home occupancy mode")
        _optional_bool(self.central_heating_on, "central heating state")
        _optional_number(self.outdoor_temperature, -80, 80, "outdoor temperature")
        _optional_number(
            self.heat_load_temperature,
            -80,
            100,
            "outdoor heat-load temperature",
        )
        _number(self.comfort_temperature, 10, 35, "comfort temperature")
        _enum(self.strategy, ClimateStrategy, "climate strategy")
        _enum(self.action, ClimateEquipmentAction, "equipment action")
        _optional_number(
            self.target_temperature,
            10,
            35,
            "equipment target temperature",
        )
        if self.fan_mode is not None:
            _enum(self.fan_mode, ClimateFanMode, "equipment fan mode")
        _optional_bool(self.quiet, "equipment quiet flag")
        _enum(self.reason, ClimateEquipmentReason, "equipment reason")
        expected = _expected_device_output(
            kind=self.kind,
            availability=self.availability,
            room_data_status=self.room_data_status,
            thermal=self.thermal,
            season=self.season,
            period=self.period,
            occupancy=self.occupancy,
            central_heating_on=self.central_heating_on,
            outdoor_temperature=self.outdoor_temperature,
            heat_load_temperature=self.heat_load_temperature,
            comfort_temperature=self.comfort_temperature,
            strategy=self.strategy,
        )
        actual = (
            self.action,
            self.target_temperature,
            self.fan_mode,
            self.quiet,
            self.reason,
        )
        if actual != expected:
            raise ClimateEquipmentViolation(
                "equipment output must match its validated policy inputs"
            )


@dataclass(frozen=True, slots=True)
class ClimateRoomEquipmentPlan:
    """All configured thermal equipment for one contour room."""

    room_id: str
    thermal: ClimateThermalResolution
    devices: tuple[ClimateDevicePlan, ...]

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "equipment room id")
        _enum(self.thermal, ClimateThermalResolution, "thermal resolution")
        if type(self.devices) is not tuple or any(
            not isinstance(device, ClimateDevicePlan) for device in self.devices
        ):
            raise ClimateEquipmentViolation(
                "equipment plans must be an immutable typed tuple"
            )
        if len(self.devices) != len({device.device_id for device in self.devices}):
            raise ClimateEquipmentViolation("equipment device ids must be unique")
        if any(
            device.room_id != self.room_id or device.thermal is not self.thermal
            for device in self.devices
        ):
            raise ClimateEquipmentViolation(
                "equipment plans must match their room resolution"
            )

    def device(self, device_id: str) -> ClimateDevicePlan | None:
        """Return one device plan by stable HausmanHub id."""

        return next(
            (device for device in self.devices if device.device_id == device_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class ClimateEquipmentSnapshot:
    """Command-free thermal-equipment policy for one climate contour."""

    contour_id: str
    contour_mode: ContourMode
    rooms: tuple[ClimateRoomEquipmentPlan, ...]
    version: int = CLIMATE_EQUIPMENT_MODEL_VERSION

    def __post_init__(self) -> None:
        _stable_id(self.contour_id, "equipment contour id")
        _enum(self.contour_mode, ContourMode, "equipment contour mode")
        if type(self.rooms) is not tuple or any(
            not isinstance(room, ClimateRoomEquipmentPlan) for room in self.rooms
        ):
            raise ClimateEquipmentViolation(
                "room equipment plans must be an immutable typed tuple"
            )
        if not self.rooms:
            raise ClimateEquipmentViolation("room equipment plans must not be empty")
        if len(self.rooms) != len({room.room_id for room in self.rooms}):
            raise ClimateEquipmentViolation("equipment room ids must be unique")
        if self.version != CLIMATE_EQUIPMENT_MODEL_VERSION:
            raise ClimateEquipmentViolation("equipment model version is unsupported")

    @property
    def commands_enabled(self) -> bool:
        """Equipment planning cannot grant execution authority."""

        return False

    def room(self, room_id: str) -> ClimateRoomEquipmentPlan | None:
        """Return one room plan by stable HausmanHub id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)


def resolve_climate_device_plan(
    device: ClimateDeviceObservation,
    target: ClimateRoomTarget,
    resolution: ClimateRoomThermalResolution,
    home: ClimateHomeObservation,
) -> ClimateDevicePlan:
    """Resolve one configured thermal device without creating an intent."""

    if not isinstance(device, ClimateDeviceObservation):
        raise ClimateEquipmentViolation(
            "a validated climate device observation is required"
        )
    if device.kind not in _THERMAL_KINDS:
        raise ClimateEquipmentViolation("device kind has no thermal policy")
    if not isinstance(target, ClimateRoomTarget):
        raise ClimateEquipmentViolation("a validated climate room target is required")
    if not isinstance(resolution, ClimateRoomThermalResolution):
        raise ClimateEquipmentViolation(
            "a validated thermal resolution is required"
        )
    if not isinstance(home, ClimateHomeObservation):
        raise ClimateEquipmentViolation(
            "a validated climate home observation is required"
        )
    if len({device.room_id, target.room_id, resolution.room_id}) != 1:
        raise ClimateEquipmentViolation(
            "device, target, and resolution must reference one room"
        )
    if resolution.season is not home.season or (
        resolution.occupancy is not home.occupancy
        or resolution.central_heating_on is not home.central_heating_on
    ):
        raise ClimateEquipmentViolation(
            "thermal resolution and home mode must come from one snapshot"
        )
    output = _expected_device_output(
        kind=device.kind,
        availability=device.availability,
        room_data_status=target.observation_status,
        thermal=resolution.thermal,
        season=home.season,
        period=home.period,
        occupancy=home.occupancy,
        central_heating_on=home.central_heating_on,
        outdoor_temperature=home.outdoor_temperature,
        heat_load_temperature=home.heat_load_temperature,
        comfort_temperature=target.target_temperature,
        strategy=target.strategy,
    )
    action, equipment_target, fan_mode, quiet, reason = output
    return ClimateDevicePlan(
        device_id=device.device_id,
        room_id=device.room_id,
        kind=device.kind,
        availability=device.availability,
        activity=device.activity,
        room_data_status=target.observation_status,
        thermal=resolution.thermal,
        season=home.season,
        period=home.period,
        occupancy=home.occupancy,
        central_heating_on=home.central_heating_on,
        outdoor_temperature=home.outdoor_temperature,
        heat_load_temperature=home.heat_load_temperature,
        comfort_temperature=target.target_temperature,
        strategy=target.strategy,
        action=action,
        target_temperature=equipment_target,
        fan_mode=fan_mode,
        quiet=quiet,
        reason=reason,
    )


def _expected_device_output(
    *,
    kind: ClimateObservationDeviceKind,
    availability: ClimateDeviceAvailability,
    room_data_status: ClimateDataStatus,
    thermal: ClimateThermalResolution,
    season: ClimateSeason,
    period: ClimateDayPeriod,
    occupancy: ClimateOccupancyMode,
    central_heating_on: bool | None,
    outdoor_temperature: float | None,
    heat_load_temperature: float | None,
    comfort_temperature: float,
    strategy: ClimateStrategy,
) -> tuple[
    ClimateEquipmentAction,
    float | None,
    ClimateFanMode | None,
    bool | None,
    ClimateEquipmentReason,
]:
    if availability is not ClimateDeviceAvailability.AVAILABLE:
        return (
            ClimateEquipmentAction.UNAVAILABLE,
            None,
            None,
            None,
            ClimateEquipmentReason.DEVICE_UNAVAILABLE,
        )
    if kind is ClimateObservationDeviceKind.AIR_CONDITIONER:
        return _air_conditioner_output(thermal, comfort_temperature, strategy)
    if kind is ClimateObservationDeviceKind.RADIATOR_THERMOSTAT:
        return _radiator_output(
            room_data_status=room_data_status,
            thermal=thermal,
            season=season,
            period=period,
            occupancy=occupancy,
            central_heating_on=central_heating_on,
            outdoor_temperature=outdoor_temperature,
            heat_load_temperature=heat_load_temperature,
        )
    if kind is ClimateObservationDeviceKind.FLOOR_HEATING:
        return _floor_output(thermal, comfort_temperature)
    raise ClimateEquipmentViolation("device kind has no thermal policy")


def _air_conditioner_output(
    thermal: ClimateThermalResolution,
    comfort_temperature: float,
    strategy: ClimateStrategy,
) -> tuple[
    ClimateEquipmentAction,
    float | None,
    ClimateFanMode | None,
    bool | None,
    ClimateEquipmentReason,
]:
    if thermal is ClimateThermalResolution.COOLING:
        if strategy is ClimateStrategy.AGGRESSIVE:
            target = float(_decimal(comfort_temperature))
            fan_mode = ClimateFanMode.MEDIUM
            quiet = False
        else:
            target = float(
                max(
                    _decimal(comfort_temperature),
                    _decimal(CLIMATE_GENERIC_COOLING_SETPOINT),
                )
            )
            fan_mode = ClimateFanMode.LOW
            quiet = strategy is ClimateStrategy.SOFT
        return (
            ClimateEquipmentAction.COOL,
            target,
            fan_mode,
            quiet,
            ClimateEquipmentReason.COOLING_REQUIRED,
        )
    if thermal is ClimateThermalResolution.SAFE_OFF:
        return (
            ClimateEquipmentAction.SAFE_OFF,
            None,
            None,
            None,
            ClimateEquipmentReason.AWAY_SAFE_OFF,
        )
    if thermal in {
        ClimateThermalResolution.OBSERVE,
        ClimateThermalResolution.UNAVAILABLE,
    }:
        return (
            ClimateEquipmentAction.OBSERVE,
            None,
            None,
            None,
            ClimateEquipmentReason.THERMAL_OBSERVE,
        )
    return (
        ClimateEquipmentAction.HOLD,
        None,
        None,
        None,
        ClimateEquipmentReason.NO_THERMAL_CHANGE,
    )


def _radiator_output(
    *,
    room_data_status: ClimateDataStatus,
    thermal: ClimateThermalResolution,
    season: ClimateSeason,
    period: ClimateDayPeriod,
    occupancy: ClimateOccupancyMode,
    central_heating_on: bool | None,
    outdoor_temperature: float | None,
    heat_load_temperature: float | None,
) -> tuple[
    ClimateEquipmentAction,
    float | None,
    ClimateFanMode | None,
    bool | None,
    ClimateEquipmentReason,
]:
    if (
        room_data_status is not ClimateDataStatus.FRESH
        or thermal
        in {
            ClimateThermalResolution.SAFE_OFF,
            ClimateThermalResolution.OBSERVE,
            ClimateThermalResolution.UNAVAILABLE,
        }
        or occupancy is not ClimateOccupancyMode.HOME
    ):
        return (
            ClimateEquipmentAction.OBSERVE,
            None,
            None,
            None,
            ClimateEquipmentReason.THERMAL_OBSERVE,
        )
    if central_heating_on is not True or season is ClimateSeason.SUMMER:
        return (
            ClimateEquipmentAction.OBSERVE,
            None,
            None,
            None,
            ClimateEquipmentReason.CENTRAL_HEATING_OFF,
        )
    if period is ClimateDayPeriod.UNKNOWN:
        return (
            ClimateEquipmentAction.OBSERVE,
            None,
            None,
            None,
            ClimateEquipmentReason.PERIOD_UNAVAILABLE,
        )
    target = (
        _decimal(CLIMATE_TRV_NIGHT_TARGET)
        if period is ClimateDayPeriod.NIGHT
        else _decimal(CLIMATE_TRV_DAY_TARGET)
    )
    if outdoor_temperature is not None and _decimal(outdoor_temperature) < Decimal(-10):
        target += _decimal(CLIMATE_TRV_COLD_ADJUSTMENT)
    if (
        period is ClimateDayPeriod.DAY
        and heat_load_temperature is not None
        and _decimal(heat_load_temperature) > Decimal(18)
    ):
        target -= _decimal(CLIMATE_TRV_WARM_LOAD_ADJUSTMENT)
    return (
        ClimateEquipmentAction.SET_TEMPERATURE,
        float(target),
        None,
        None,
        ClimateEquipmentReason.HEATING_SCHEDULE,
    )


def _floor_output(
    thermal: ClimateThermalResolution,
    comfort_temperature: float,
) -> tuple[
    ClimateEquipmentAction,
    float | None,
    ClimateFanMode | None,
    bool | None,
    ClimateEquipmentReason,
]:
    if thermal is ClimateThermalResolution.HEATING:
        return (
            ClimateEquipmentAction.HEAT,
            float(_decimal(comfort_temperature)),
            None,
            None,
            ClimateEquipmentReason.HEATING_REQUIRED,
        )
    if thermal is ClimateThermalResolution.SAFE_OFF:
        return (
            ClimateEquipmentAction.SAFE_OFF,
            None,
            None,
            None,
            ClimateEquipmentReason.AWAY_SAFE_OFF,
        )
    if thermal in {
        ClimateThermalResolution.OBSERVE,
        ClimateThermalResolution.UNAVAILABLE,
    }:
        return (
            ClimateEquipmentAction.OBSERVE,
            None,
            None,
            None,
            ClimateEquipmentReason.THERMAL_OBSERVE,
        )
    return (
        ClimateEquipmentAction.HOLD,
        None,
        None,
        None,
        ClimateEquipmentReason.NO_THERMAL_CHANGE,
    )


def _stable_id(value: object, label: str) -> None:
    try:
        ClimateRoom(value, "Object")  # type: ignore[arg-type]
    except ClimateModelViolation as error:
        raise ClimateEquipmentViolation(f"{label} must be stable") from error


def _enum(value: object, kind: type[StrEnum], label: str) -> None:
    if not isinstance(value, kind):
        raise ClimateEquipmentViolation(f"{label} must be approved")


def _number(value: object, minimum: float, maximum: float, label: str) -> None:
    if (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ClimateEquipmentViolation(f"{label} is outside fixed bounds")


def _optional_number(
    value: object,
    minimum: float,
    maximum: float,
    label: str,
) -> None:
    if value is not None:
        _number(value, minimum, maximum, label)


def _optional_bool(value: object, label: str) -> None:
    if value is not None and type(value) is not bool:
        raise ClimateEquipmentViolation(f"{label} must be boolean or unavailable")


def _decimal(value: float | int) -> Decimal:
    return Decimal(str(value))
