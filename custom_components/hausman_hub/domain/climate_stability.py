"""Protect climate equipment from oscillation and short switching cycles.

This is a pure, command-free policy layer.  It consumes validated HausmanHub
observations and the base equipment plan, then describes a stable device state.
It never contains a Home Assistant entity, service, transport, or authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
import math

from .climate import ClimateModelViolation, ClimateRoom
from .climate_equipment import ClimateDevicePlan, ClimateEquipmentAction
from .climate_observation import (
    ClimateDataStatus,
    ClimateDayPeriod,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateFanMode,
    ClimateHomeObservation,
    ClimateObservationDeviceKind,
    ClimatePhysicalFeedback,
    ClimateRoomObservation,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from .climate_targets import ClimateRoomTarget
from .contours import ContourMode


CLIMATE_STABILITY_MODEL_VERSION = 1
CLIMATE_DEFAULT_MINIMUM_RUN_MINUTES = 8
CLIMATE_DEFAULT_MINIMUM_OFF_MINUTES = 6
CLIMATE_FAST_MINIMUM_RUN_MINUTES = 5
CLIMATE_FAST_MINIMUM_OFF_MINUTES = 8
CLIMATE_SLOW_MINIMUM_RUN_MINUTES = 10
CLIMATE_SLOW_MINIMUM_OFF_MINUTES = 5
CLIMATE_FAST_COOLING_RATE_PER_HOUR = 1.2
CLIMATE_SLOW_COOLING_RATE_PER_HOUR = 0.35
CLIMATE_MAXIMUM_OFF_MINUTES = 10
CLIMATE_COOL_START_GAP = 0.7
CLIMATE_SOFTENED_COOLING_SETPOINT = 27.0

_RUNNING_ACTIVITIES = frozenset(
    {ClimateDeviceActivity.RUNNING, ClimateDeviceActivity.COOLING}
)
_HUMIDIFYING_ACTIVITIES = frozenset(
    {ClimateDeviceActivity.RUNNING, ClimateDeviceActivity.HUMIDIFYING}
)
_STOPPED_ACTIVITIES = frozenset(
    {ClimateDeviceActivity.STOPPED, ClimateDeviceActivity.IDLE}
)
_SUPPORTED_KINDS = frozenset(
    {
        ClimateObservationDeviceKind.AIR_CONDITIONER,
        ClimateObservationDeviceKind.HUMIDIFIER,
    }
)


class ClimateStabilityViolation(ValueError):
    """A stability plan is incomplete, mixed, or contradicts its inputs."""


class ClimateStabilityAction(StrEnum):
    """Stable device result which is still not an executable intent."""

    COOL = "cool"
    MAINTAIN = "maintain"
    OFF = "off"
    SAFE_OFF = "safe_off"
    HUMIDIFY = "humidify"
    HOLD = "hold"
    OBSERVE = "observe"
    UNAVAILABLE = "unavailable"


class ClimateStabilityReason(StrEnum):
    """Stable explanation of one protected result."""

    DEVICE_UNAVAILABLE = "device_unavailable"
    DATA_UNAVAILABLE = "data_unavailable"
    ACTIVITY_UNKNOWN = "activity_unknown"
    UPSTREAM_OBSERVE = "upstream_observe"
    UPSTREAM_SAFE_OFF = "upstream_safe_off"
    COOLING_REQUIRED = "cooling_required"
    COOLING_HYSTERESIS = "cooling_hysteresis"
    SOFTEN_BEFORE_STOP = "soften_before_stop"
    MINIMUM_RUN_HOLD = "minimum_run_hold"
    MINIMUM_OFF_PAUSE = "minimum_off_pause"
    HARD_OFF_THRESHOLD = "hard_off_threshold"
    COOLING_NOT_REQUIRED = "cooling_not_required"
    WEAK_COOLING_RAISE_FAN = "weak_cooling_raise_fan"
    WEAK_COOLING_LOWER_TARGET = "weak_cooling_lower_target"
    HUMIDITY_LOW = "humidity_low"
    HUMIDITY_HIGH = "humidity_high"
    HUMIDITY_HYSTERESIS = "humidity_hysteresis"
    WINDOW_NOT_CLOSED = "window_not_closed"


class ClimateStabilityProtection(StrEnum):
    """One active anti-oscillation or safety protection."""

    NONE = "none"
    COOLING_HYSTERESIS = "cooling_hysteresis"
    MINIMUM_RUN = "minimum_run"
    MINIMUM_OFF = "minimum_off"
    HUMIDITY_HYSTERESIS = "humidity_hysteresis"
    WINDOW = "window"


class ClimateCycleTimingReason(StrEnum):
    """Why the current minimum run/off intervals were selected."""

    DEFAULT = "default"
    AGGRESSIVE_DEFAULT = "aggressive_default"
    CONFIRMED_FAST_COOLING = "confirmed_fast_cooling"
    CONFIRMED_SLOW_COOLING = "confirmed_slow_cooling"
    CONFIRMED_SHORT_CYCLES = "confirmed_short_cycles"
    AGGRESSIVE_DEFAULT_AND_SHORT_CYCLES = (
        "aggressive_default_and_short_cycles"
    )
    CONFIRMED_FAST_COOLING_AND_SHORT_CYCLES = (
        "confirmed_fast_cooling_and_short_cycles"
    )
    CONFIRMED_SLOW_COOLING_AND_SHORT_CYCLES = (
        "confirmed_slow_cooling_and_short_cycles"
    )


@dataclass(frozen=True, slots=True)
class ClimateCycleTiming:
    """Bounded minimum cycle intervals for one air conditioner."""

    minimum_run_minutes: int
    minimum_off_minutes: int
    reason: ClimateCycleTimingReason

    def __post_init__(self) -> None:
        for value in (self.minimum_run_minutes, self.minimum_off_minutes):
            if type(value) is not int or not 0 <= value <= 120:
                raise ClimateStabilityViolation(
                    "climate cycle interval must be a bounded whole minute"
                )
        if not isinstance(self.reason, ClimateCycleTimingReason):
            raise ClimateStabilityViolation(
                "climate cycle timing reason must be approved"
            )


@dataclass(frozen=True, slots=True)
class ClimateStableDevicePlan:
    """One strictly reproducible, command-free protected device plan."""

    device: ClimateDeviceObservation
    room: ClimateRoomObservation
    target: ClimateRoomTarget
    home: ClimateHomeObservation
    observed_at: int
    base: ClimateDevicePlan | None
    cooling_active: bool
    action: ClimateStabilityAction
    target_temperature: float | None
    fan_mode: ClimateFanMode | None
    quiet: bool | None
    humidity_on_threshold: int | None
    humidity_off_threshold: int | None
    cycle_timing: ClimateCycleTiming | None
    remaining_seconds: int | None
    protection: ClimateStabilityProtection
    reason: ClimateStabilityReason

    def __post_init__(self) -> None:
        if not isinstance(self.device, ClimateDeviceObservation):
            raise ClimateStabilityViolation("a validated device is required")
        if self.device.kind not in _SUPPORTED_KINDS:
            raise ClimateStabilityViolation("device has no stability policy")
        if not isinstance(self.room, ClimateRoomObservation):
            raise ClimateStabilityViolation("a validated room is required")
        if not isinstance(self.target, ClimateRoomTarget):
            raise ClimateStabilityViolation("a validated target is required")
        if not isinstance(self.home, ClimateHomeObservation):
            raise ClimateStabilityViolation("a validated home state is required")
        _timestamp(self.observed_at, "stability observation time")
        if len({self.device.room_id, self.room.room_id, self.target.room_id}) != 1:
            raise ClimateStabilityViolation(
                "stability inputs must reference one room"
            )
        if self.room.data_status is not self.target.observation_status:
            raise ClimateStabilityViolation(
                "stability room and target must come from one snapshot"
            )
        if self.target.observation_observed_at != self.observed_at:
            raise ClimateStabilityViolation(
                "stability target and room must use one observation time"
            )
        if type(self.cooling_active) is not bool:
            raise ClimateStabilityViolation("cooling activity must be boolean")
        if self.device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER:
            if self.base is None:
                raise ClimateStabilityViolation(
                    "air conditioner stability requires its base plan"
                )
            if (
                self.base.device_id != self.device.device_id
                or self.base.room_id != self.room.room_id
                or self.base.kind is not self.device.kind
                or self.base.availability is not self.device.availability
                or self.base.activity is not self.device.activity
                or self.base.room_data_status is not self.room.data_status
                or self.base.comfort_temperature != self.target.target_temperature
                or self.base.strategy is not self.target.strategy
                or self.base.observed_at != self.observed_at
                or self.base.season is not self.home.season
                or self.base.period is not self.home.period
                or self.base.occupancy is not self.home.occupancy
                or self.base.central_heating_on is not self.home.central_heating_on
                or self.base.outdoor_temperature != self.home.outdoor_temperature
                or self.base.heat_load_temperature
                != self.home.heat_load_temperature
            ):
                raise ClimateStabilityViolation(
                    "air conditioner stability inputs must match its base plan"
                )
        elif self.base is not None:
            raise ClimateStabilityViolation(
                "humidifier stability must not retain a thermal base plan"
            )
        if not isinstance(self.action, ClimateStabilityAction):
            raise ClimateStabilityViolation("stability action must be approved")
        _optional_number(
            self.target_temperature,
            10,
            35,
            "stable target temperature",
        )
        if self.fan_mode is not None and not isinstance(
            self.fan_mode,
            ClimateFanMode,
        ):
            raise ClimateStabilityViolation("stable fan mode must be approved")
        if self.quiet is not None and type(self.quiet) is not bool:
            raise ClimateStabilityViolation("stable quiet flag must be boolean")
        for threshold in (
            self.humidity_on_threshold,
            self.humidity_off_threshold,
        ):
            if threshold is not None and (
                type(threshold) is not int or not 0 <= threshold <= 100
            ):
                raise ClimateStabilityViolation(
                    "humidity threshold must be a bounded whole percent"
                )
        if self.cycle_timing is not None and not isinstance(
            self.cycle_timing,
            ClimateCycleTiming,
        ):
            raise ClimateStabilityViolation("cycle timing must be validated")
        if self.remaining_seconds is not None and (
            type(self.remaining_seconds) is not int
            or not 1 <= self.remaining_seconds <= 7200
        ):
            raise ClimateStabilityViolation(
                "remaining protection time must be bounded seconds"
            )
        if not isinstance(self.protection, ClimateStabilityProtection):
            raise ClimateStabilityViolation("stability protection must be approved")
        if not isinstance(self.reason, ClimateStabilityReason):
            raise ClimateStabilityViolation("stability reason must be approved")
        expected = _expected_stability_output(
            device=self.device,
            room=self.room,
            target=self.target,
            home=self.home,
            observed_at=self.observed_at,
            base=self.base,
            cooling_active=self.cooling_active,
        )
        actual = (
            self.action,
            self.target_temperature,
            self.fan_mode,
            self.quiet,
            self.humidity_on_threshold,
            self.humidity_off_threshold,
            self.cycle_timing,
            self.remaining_seconds,
            self.protection,
            self.reason,
        )
        if actual != expected:
            raise ClimateStabilityViolation(
                "stability output must match its validated policy inputs"
            )

    @property
    def device_id(self) -> str:
        """Return the stable HausmanHub device id."""

        return self.device.device_id

    @property
    def room_id(self) -> str:
        """Return the stable HausmanHub room id."""

        return self.room.room_id

    @property
    def kind(self) -> ClimateObservationDeviceKind:
        """Return the normalized device kind."""

        return self.device.kind


@dataclass(frozen=True, slots=True)
class ClimateRoomStabilityPlan:
    """Protected AC and humidifier plans for one contour room."""

    room_id: str
    devices: tuple[ClimateStableDevicePlan, ...]

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "stability room id")
        if type(self.devices) is not tuple or any(
            not isinstance(device, ClimateStableDevicePlan)
            for device in self.devices
        ):
            raise ClimateStabilityViolation(
                "stability devices must be an immutable typed tuple"
            )
        if len(self.devices) != len({device.device_id for device in self.devices}):
            raise ClimateStabilityViolation("stability device ids must be unique")
        if any(device.room_id != self.room_id for device in self.devices):
            raise ClimateStabilityViolation(
                "stability devices must match their room"
            )

    def device(self, device_id: str) -> ClimateStableDevicePlan | None:
        """Return one protected device plan by stable HausmanHub id."""

        return next(
            (device for device in self.devices if device.device_id == device_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class ClimateStabilitySnapshot:
    """Complete command-free anti-oscillation result for one contour."""

    contour_id: str
    contour_mode: ContourMode
    observed_at: int
    rooms: tuple[ClimateRoomStabilityPlan, ...]
    version: int = CLIMATE_STABILITY_MODEL_VERSION

    def __post_init__(self) -> None:
        _stable_id(self.contour_id, "stability contour id")
        if not isinstance(self.contour_mode, ContourMode):
            raise ClimateStabilityViolation("stability contour mode is invalid")
        _timestamp(self.observed_at, "stability snapshot time")
        if type(self.rooms) is not tuple or any(
            not isinstance(room, ClimateRoomStabilityPlan) for room in self.rooms
        ):
            raise ClimateStabilityViolation(
                "stability rooms must be an immutable typed tuple"
            )
        if not self.rooms:
            raise ClimateStabilityViolation("stability rooms must not be empty")
        if len(self.rooms) != len({room.room_id for room in self.rooms}):
            raise ClimateStabilityViolation("stability room ids must be unique")
        if any(
            device.observed_at != self.observed_at
            for room in self.rooms
            for device in room.devices
        ):
            raise ClimateStabilityViolation(
                "stability plans must use one observation time"
            )
        if self.version != CLIMATE_STABILITY_MODEL_VERSION:
            raise ClimateStabilityViolation("stability model version is unsupported")

    @property
    def commands_enabled(self) -> bool:
        """The stability layer cannot grant execution authority."""

        return False

    def room(self, room_id: str) -> ClimateRoomStabilityPlan | None:
        """Return one room stability plan by stable HausmanHub id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)


def resolve_climate_stability_plan(
    device: ClimateDeviceObservation,
    room: ClimateRoomObservation,
    target: ClimateRoomTarget,
    home: ClimateHomeObservation,
    *,
    observed_at: int,
    base: ClimateDevicePlan | None,
    cooling_active: bool,
) -> ClimateStableDevicePlan:
    """Resolve one AC or humidifier without creating an executable intent."""

    output = _expected_stability_output(
        device=device,
        room=room,
        target=target,
        home=home,
        observed_at=observed_at,
        base=base,
        cooling_active=cooling_active,
    )
    return ClimateStableDevicePlan(
        device=device,
        room=room,
        target=target,
        home=home,
        observed_at=observed_at,
        base=base,
        cooling_active=cooling_active,
        action=output[0],
        target_temperature=output[1],
        fan_mode=output[2],
        quiet=output[3],
        humidity_on_threshold=output[4],
        humidity_off_threshold=output[5],
        cycle_timing=output[6],
        remaining_seconds=output[7],
        protection=output[8],
        reason=output[9],
    )


def climate_cycle_timing(
    device: ClimateDeviceObservation,
    target: ClimateRoomTarget,
) -> ClimateCycleTiming:
    """Select the bounded cycle intervals preserved from the working module."""

    if device.kind is not ClimateObservationDeviceKind.AIR_CONDITIONER:
        raise ClimateStabilityViolation("cycle timing requires an air conditioner")
    minimum_run = CLIMATE_DEFAULT_MINIMUM_RUN_MINUTES
    minimum_off = CLIMATE_DEFAULT_MINIMUM_OFF_MINUTES
    reason = ClimateCycleTimingReason.DEFAULT
    if target.strategy.value == "aggressive":
        reason = ClimateCycleTimingReason.AGGRESSIVE_DEFAULT
    elif device.cooling_evidence_confirmed and device.cooling_rate_per_hour is not None:
        if device.cooling_rate_per_hour >= CLIMATE_FAST_COOLING_RATE_PER_HOUR:
            minimum_run = CLIMATE_FAST_MINIMUM_RUN_MINUTES
            minimum_off = CLIMATE_FAST_MINIMUM_OFF_MINUTES
            reason = ClimateCycleTimingReason.CONFIRMED_FAST_COOLING
        elif device.cooling_rate_per_hour <= CLIMATE_SLOW_COOLING_RATE_PER_HOUR:
            minimum_run = CLIMATE_SLOW_MINIMUM_RUN_MINUTES
            minimum_off = CLIMATE_SLOW_MINIMUM_OFF_MINUTES
            reason = ClimateCycleTimingReason.CONFIRMED_SLOW_COOLING
    short_cycles = device.confirmed_short_cycle_count
    if short_cycles is not None and short_cycles > 0:
        minimum_off = min(
            CLIMATE_MAXIMUM_OFF_MINUTES,
            minimum_off + min(2, short_cycles),
        )
        if reason is ClimateCycleTimingReason.CONFIRMED_FAST_COOLING:
            reason = ClimateCycleTimingReason.CONFIRMED_FAST_COOLING_AND_SHORT_CYCLES
        elif reason is ClimateCycleTimingReason.CONFIRMED_SLOW_COOLING:
            reason = ClimateCycleTimingReason.CONFIRMED_SLOW_COOLING_AND_SHORT_CYCLES
        elif reason is ClimateCycleTimingReason.AGGRESSIVE_DEFAULT:
            reason = ClimateCycleTimingReason.AGGRESSIVE_DEFAULT_AND_SHORT_CYCLES
        else:
            reason = ClimateCycleTimingReason.CONFIRMED_SHORT_CYCLES
    return ClimateCycleTiming(minimum_run, minimum_off, reason)


def _expected_stability_output(
    *,
    device: ClimateDeviceObservation,
    room: ClimateRoomObservation,
    target: ClimateRoomTarget,
    home: ClimateHomeObservation,
    observed_at: int,
    base: ClimateDevicePlan | None,
    cooling_active: bool,
) -> tuple[
    ClimateStabilityAction,
    float | None,
    ClimateFanMode | None,
    bool | None,
    int | None,
    int | None,
    ClimateCycleTiming | None,
    int | None,
    ClimateStabilityProtection,
    ClimateStabilityReason,
]:
    if not isinstance(device, ClimateDeviceObservation):
        raise ClimateStabilityViolation("a validated climate device is required")
    if device.kind not in _SUPPORTED_KINDS:
        raise ClimateStabilityViolation("device has no stability policy")
    if not isinstance(room, ClimateRoomObservation):
        raise ClimateStabilityViolation("a validated climate room is required")
    if not isinstance(target, ClimateRoomTarget):
        raise ClimateStabilityViolation("a validated climate target is required")
    if not isinstance(home, ClimateHomeObservation):
        raise ClimateStabilityViolation("a validated climate home is required")
    _timestamp(observed_at, "stability observation time")
    if type(cooling_active) is not bool:
        raise ClimateStabilityViolation("cooling activity must be boolean")
    if len({device.room_id, room.room_id, target.room_id}) != 1:
        raise ClimateStabilityViolation("stability inputs must reference one room")
    if room.data_status is not target.observation_status:
        raise ClimateStabilityViolation(
            "stability room and target status must match"
        )
    if target.observation_observed_at != observed_at:
        raise ClimateStabilityViolation(
            "stability inputs must use one observation time"
        )
    if device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER:
        if not isinstance(base, ClimateDevicePlan):
            raise ClimateStabilityViolation(
                "air conditioner stability requires a validated base plan"
            )
        return _air_conditioner_output(
            device,
            room,
            target,
            home,
            observed_at,
            base,
        )
    if base is not None:
        raise ClimateStabilityViolation(
            "humidifier stability must not have a thermal base plan"
        )
    return _humidifier_output(device, room, target, home, cooling_active)


def _air_conditioner_output(
    device: ClimateDeviceObservation,
    room: ClimateRoomObservation,
    target: ClimateRoomTarget,
    home: ClimateHomeObservation,
    observed_at: int,
    base: ClimateDevicePlan,
) -> tuple[
    ClimateStabilityAction,
    float | None,
    ClimateFanMode | None,
    bool | None,
    int | None,
    int | None,
    ClimateCycleTiming | None,
    int | None,
    ClimateStabilityProtection,
    ClimateStabilityReason,
]:
    empty = (None, None, None, None, None)
    if device.availability is not ClimateDeviceAvailability.AVAILABLE:
        return (
            ClimateStabilityAction.UNAVAILABLE,
            *empty,
            None,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.DEVICE_UNAVAILABLE,
        )
    timing = climate_cycle_timing(device, target)
    if base.action is ClimateEquipmentAction.SAFE_OFF:
        return (
            ClimateStabilityAction.SAFE_OFF,
            *empty,
            timing,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.UPSTREAM_SAFE_OFF,
        )
    if (
        room.data_status is not ClimateDataStatus.FRESH
        or room.temperature is None
        or room.temperature_quality is not ClimateTemperatureQuality.NORMAL
        or base.action in {
            ClimateEquipmentAction.OBSERVE,
            ClimateEquipmentAction.UNAVAILABLE,
        }
    ):
        return (
            ClimateStabilityAction.OBSERVE,
            *empty,
            timing,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.UPSTREAM_OBSERVE,
        )
    running = device.activity in _RUNNING_ACTIVITIES
    stopped = device.activity in _STOPPED_ACTIVITIES
    if not running and not stopped:
        return (
            ClimateStabilityAction.OBSERVE,
            *empty,
            timing,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.ACTIVITY_UNKNOWN,
        )
    temperature = _decimal(room.temperature)
    comfort = _decimal(target.target_temperature)
    if (
        running
        and room.hard_off_temperature is not None
        and temperature <= _decimal(room.hard_off_temperature)
    ):
        return (
            ClimateStabilityAction.OFF,
            *empty,
            timing,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.HARD_OFF_THRESHOLD,
        )
    profile_target, fan_mode, quiet = _cooling_profile(target, home)
    delta = temperature - comfort
    if running:
        minimum_run_remaining = _remaining_seconds(
            observed_at,
            device.last_started_at,
            timing.minimum_run_minutes,
        )
        if temperature < comfort:
            return (
                ClimateStabilityAction.MAINTAIN,
                max(CLIMATE_SOFTENED_COOLING_SETPOINT, target.target_temperature),
                ClimateFanMode.LOW,
                quiet,
                None,
                None,
                timing,
                minimum_run_remaining,
                (
                    ClimateStabilityProtection.MINIMUM_RUN
                    if minimum_run_remaining is not None
                    else ClimateStabilityProtection.COOLING_HYSTERESIS
                ),
                (
                    ClimateStabilityReason.MINIMUM_RUN_HOLD
                    if minimum_run_remaining is not None
                    else ClimateStabilityReason.SOFTEN_BEFORE_STOP
                ),
            )
        near_target_band = (
            Decimal("0.5")
            if home.period is ClimateDayPeriod.NIGHT
            else Decimal("0.3")
        )
        if delta <= near_target_band:
            return (
                ClimateStabilityAction.MAINTAIN,
                profile_target,
                fan_mode,
                quiet,
                None,
                None,
                timing,
                None,
                ClimateStabilityProtection.COOLING_HYSTERESIS,
                ClimateStabilityReason.COOLING_HYSTERESIS,
            )
        escalation = _weak_cooling_escalation(
            device,
            target,
            home,
            observed_at,
            profile_target,
            quiet,
        )
        if escalation is not None:
            return escalation
        return (
            ClimateStabilityAction.COOL,
            profile_target,
            fan_mode,
            quiet,
            None,
            None,
            timing,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.COOLING_REQUIRED,
        )
    if delta >= Decimal(str(CLIMATE_COOL_START_GAP)):
        minimum_off_remaining = _remaining_seconds(
            observed_at,
            device.last_stopped_at,
            timing.minimum_off_minutes,
        )
        if minimum_off_remaining is not None:
            return (
                ClimateStabilityAction.OFF,
                *empty,
                timing,
                minimum_off_remaining,
                ClimateStabilityProtection.MINIMUM_OFF,
                ClimateStabilityReason.MINIMUM_OFF_PAUSE,
            )
        return (
            ClimateStabilityAction.COOL,
            profile_target,
            fan_mode,
            quiet,
            None,
            None,
            timing,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.COOLING_REQUIRED,
        )
    return (
        ClimateStabilityAction.OFF,
        *empty,
        timing,
        None,
        ClimateStabilityProtection.NONE,
        ClimateStabilityReason.COOLING_NOT_REQUIRED,
    )


def _weak_cooling_escalation(
    device: ClimateDeviceObservation,
    target: ClimateRoomTarget,
    home: ClimateHomeObservation,
    observed_at: int,
    profile_target: float,
    quiet: bool,
) -> tuple[
    ClimateStabilityAction,
    float | None,
    ClimateFanMode | None,
    bool | None,
    int | None,
    int | None,
    ClimateCycleTiming | None,
    int | None,
    ClimateStabilityProtection,
    ClimateStabilityReason,
] | None:
    slow_limit = (
        0.2 if home.period is ClimateDayPeriod.NIGHT else 0.35
    )
    dwell_minutes = 18 if home.period is ClimateDayPeriod.NIGHT else 10
    if (
        not device.cooling_evidence_confirmed
        or device.cooling_rate_per_hour is None
        or device.cooling_rate_per_hour > slow_limit
        or device.physical_feedback is not ClimatePhysicalFeedback.CONFIRMED
        or device.current_target_temperature is None
        or abs(device.current_target_temperature - profile_target) > 0.1
        or not _elapsed_at_least(
            observed_at,
            device.last_started_at,
            dwell_minutes,
        )
    ):
        return None
    timing = climate_cycle_timing(device, target)
    if device.fan_mode in {ClimateFanMode.LOW, ClimateFanMode.AUTO}:
        return (
            ClimateStabilityAction.MAINTAIN,
            profile_target,
            ClimateFanMode.MEDIUM,
            quiet,
            None,
            None,
            timing,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.WEAK_COOLING_RAISE_FAN,
        )
    if device.fan_mode is ClimateFanMode.MEDIUM and target.target_temperature < 26:
        return (
            ClimateStabilityAction.MAINTAIN,
            target.target_temperature,
            ClimateFanMode.MEDIUM,
            quiet,
            None,
            None,
            timing,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.WEAK_COOLING_LOWER_TARGET,
        )
    return None


def _humidifier_output(
    device: ClimateDeviceObservation,
    room: ClimateRoomObservation,
    target: ClimateRoomTarget,
    home: ClimateHomeObservation,
    cooling_active: bool,
) -> tuple[
    ClimateStabilityAction,
    float | None,
    ClimateFanMode | None,
    bool | None,
    int | None,
    int | None,
    ClimateCycleTiming | None,
    int | None,
    ClimateStabilityProtection,
    ClimateStabilityReason,
]:
    if device.availability is not ClimateDeviceAvailability.AVAILABLE:
        return (
            ClimateStabilityAction.UNAVAILABLE,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.DEVICE_UNAVAILABLE,
        )
    if room.window is not ClimateWindowState.CLOSED:
        return (
            ClimateStabilityAction.OFF,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            ClimateStabilityProtection.WINDOW,
            ClimateStabilityReason.WINDOW_NOT_CLOSED,
        )
    if room.data_status is not ClimateDataStatus.FRESH or room.humidity is None:
        return (
            ClimateStabilityAction.OBSERVE,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.DATA_UNAVAILABLE,
        )
    active = device.activity in _HUMIDIFYING_ACTIVITIES
    stopped = device.activity in _STOPPED_ACTIVITIES
    if not active and not stopped:
        return (
            ClimateStabilityAction.OBSERVE,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.ACTIVITY_UNKNOWN,
        )
    raised = cooling_active or (
        home.heat_load_temperature is not None
        and home.heat_load_temperature >= 26
    )
    on_threshold = target.target_humidity - (5 if raised else 6)
    off_threshold = target.target_humidity if raised else target.target_humidity - 1
    if room.humidity <= on_threshold and stopped:
        return (
            ClimateStabilityAction.HUMIDIFY,
            None,
            None,
            None,
            on_threshold,
            off_threshold,
            None,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.HUMIDITY_LOW,
        )
    if room.humidity >= off_threshold and active:
        return (
            ClimateStabilityAction.OFF,
            None,
            None,
            None,
            on_threshold,
            off_threshold,
            None,
            None,
            ClimateStabilityProtection.NONE,
            ClimateStabilityReason.HUMIDITY_HIGH,
        )
    return (
        ClimateStabilityAction.HOLD,
        None,
        None,
        None,
        on_threshold,
        off_threshold,
        None,
        None,
        ClimateStabilityProtection.HUMIDITY_HYSTERESIS,
        ClimateStabilityReason.HUMIDITY_HYSTERESIS,
    )


def _cooling_profile(
    target: ClimateRoomTarget,
    home: ClimateHomeObservation,
) -> tuple[float, ClimateFanMode, bool]:
    if target.strategy.value == "aggressive":
        return target.target_temperature, ClimateFanMode.MEDIUM, False
    return (
        max(26.0, target.target_temperature),
        ClimateFanMode.LOW,
        target.strategy.value == "soft" or home.period is ClimateDayPeriod.NIGHT,
    )


def _remaining_seconds(
    observed_at: int,
    transition_at: int | None,
    minimum_minutes: int,
) -> int | None:
    if transition_at is None or transition_at > observed_at or minimum_minutes <= 0:
        return None
    remaining_ms = minimum_minutes * 60_000 - (observed_at - transition_at)
    return None if remaining_ms <= 0 else math.ceil(remaining_ms / 1000)


def _elapsed_at_least(
    observed_at: int,
    transition_at: int | None,
    minimum_minutes: int,
) -> bool:
    return (
        transition_at is not None
        and transition_at <= observed_at
        and observed_at - transition_at >= minimum_minutes * 60_000
    )


def _timestamp(value: object, label: str) -> None:
    if type(value) is not int or not 0 <= value <= 9_999_999_999_999:
        raise ClimateStabilityViolation(f"{label} must be bounded milliseconds")


def _stable_id(value: object, label: str) -> None:
    try:
        ClimateRoom(value, "Object")  # type: ignore[arg-type]
    except ClimateModelViolation as error:
        raise ClimateStabilityViolation(f"{label} must be stable") from error


def _optional_number(
    value: object,
    minimum: float,
    maximum: float,
    label: str,
) -> None:
    if value is not None and (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ClimateStabilityViolation(f"{label} is outside fixed bounds")


def _decimal(value: float | int) -> Decimal:
    return Decimal(str(value))
