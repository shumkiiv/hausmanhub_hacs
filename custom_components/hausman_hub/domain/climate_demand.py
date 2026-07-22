"""Pure climate demand calculated from one room observation and target.

Demand is descriptive only.  This module cannot choose equipment, resolve
seasonal conflicts, apply hysteresis to a running device, build an intent, or
authorize a command.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
import math

from .climate import ClimateModelViolation, ClimateRoom
from .climate_observation import (
    ClimateDataStatus,
    ClimateRoomObservation,
    ClimateTemperatureQuality,
)
from .climate_targets import ClimateRoomTarget, ClimateTemperatureTargetOrigin
from .contours import ClimateComfortSettings, ClimateStrategy, ContourMode


CLIMATE_DEMAND_MODEL_VERSION = 1
CLIMATE_COOLING_START_GAP = 0.7
CLIMATE_HEATING_COMFORT_GAP = 0.5
CLIMATE_HUMIDIFYING_COMFORT_GAP = 5.0


class ClimateDemandViolation(ValueError):
    """A climate demand is mutable, incomplete, or contradictory."""


class ClimateDemandState(StrEnum):
    """Whether one independent comfort channel currently needs attention."""

    REQUIRED = "required"
    NOT_REQUIRED = "not_required"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ClimateRoomDemand:
    """Independent thermal and humidity needs for one stable room."""

    room_id: str
    observation_status: ClimateDataStatus
    temperature_quality: ClimateTemperatureQuality
    strategy: ClimateStrategy
    current_temperature: float | None
    current_humidity: float | None
    target_temperature: float
    target_humidity: int
    heating_gap: float | None
    cooling_gap: float | None
    humidifying_gap: float | None
    heating: ClimateDemandState
    cooling: ClimateDemandState
    humidifying: ClimateDemandState
    temperature_origin: ClimateTemperatureTargetOrigin = (
        ClimateTemperatureTargetOrigin.PROFILE
    )

    def __post_init__(self) -> None:
        _stable_room_id(self.room_id)
        if not isinstance(self.observation_status, ClimateDataStatus):
            raise ClimateDemandViolation("demand observation status must be approved")
        if not isinstance(self.temperature_quality, ClimateTemperatureQuality):
            raise ClimateDemandViolation("demand temperature quality must be approved")
        _comfort(self.target_temperature, self.target_humidity, self.strategy)
        _optional_number(self.current_temperature, -50, 70, "current temperature")
        _optional_number(self.current_humidity, 0, 100, "current humidity")
        for state in (self.heating, self.cooling, self.humidifying):
            if not isinstance(state, ClimateDemandState):
                raise ClimateDemandViolation("demand state must be approved")
        if not isinstance(self.temperature_origin, ClimateTemperatureTargetOrigin):
            raise ClimateDemandViolation(
                "demand temperature origin must be approved"
            )
        _optional_gap(self.heating_gap, "heating gap")
        _optional_gap(self.cooling_gap, "cooling gap")
        _optional_gap(self.humidifying_gap, "humidifying gap")
        self._validate_signal_availability()
        self._validate_thermal_channel()
        self._validate_humidity_channel()

    def _validate_signal_availability(self) -> None:
        thermal_available = not all(
            state is ClimateDemandState.UNAVAILABLE
            for state in (self.heating, self.cooling)
        )
        expected_thermal_available = (
            self.observation_status is ClimateDataStatus.FRESH
            and self.current_temperature is not None
            and self.temperature_quality is ClimateTemperatureQuality.NORMAL
        )
        if thermal_available is not expected_thermal_available:
            raise ClimateDemandViolation(
                "thermal demand availability must match observation quality"
            )
        humidity_available = self.humidifying is not ClimateDemandState.UNAVAILABLE
        expected_humidity_available = (
            self.observation_status is ClimateDataStatus.FRESH
            and self.current_humidity is not None
        )
        if humidity_available is not expected_humidity_available:
            raise ClimateDemandViolation(
                "humidity demand availability must match observation freshness"
            )
        if self.observation_status is ClimateDataStatus.UNAVAILABLE and (
            self.current_temperature is not None
            or self.current_humidity is not None
            or self.temperature_quality is not ClimateTemperatureQuality.UNKNOWN
        ):
            raise ClimateDemandViolation(
                "unavailable demand must not retain observed signals"
            )

    def _validate_thermal_channel(self) -> None:
        thermal_states = (self.heating, self.cooling)
        thermal_gaps = (self.heating_gap, self.cooling_gap)
        unavailable = all(
            state is ClimateDemandState.UNAVAILABLE for state in thermal_states
        )
        if unavailable:
            if any(gap is not None for gap in thermal_gaps):
                raise ClimateDemandViolation(
                    "unavailable thermal demand must not retain calculated gaps"
                )
            return
        if any(state is ClimateDemandState.UNAVAILABLE for state in thermal_states):
            raise ClimateDemandViolation(
                "heating and cooling availability must change together"
            )
        if self.current_temperature is None or any(
            gap is None for gap in thermal_gaps
        ):
            raise ClimateDemandViolation(
                "available thermal demand requires current temperature and gaps"
            )
        if (
            self.heating is ClimateDemandState.REQUIRED
            and self.cooling is ClimateDemandState.REQUIRED
        ):
            raise ClimateDemandViolation(
                "raw heating and cooling demand cannot both be required"
            )
        expected_heating = max(
            _decimal(self.target_temperature) - _decimal(self.current_temperature),
            Decimal(0),
        )
        expected_cooling = max(
            _decimal(self.current_temperature) - _decimal(self.target_temperature),
            Decimal(0),
        )
        if _decimal(self.heating_gap) != expected_heating or _decimal(
            self.cooling_gap
        ) != expected_cooling:
            raise ClimateDemandViolation(
                "thermal gaps must match current and target temperature"
            )
        if (
            self.heating is ClimateDemandState.REQUIRED
        ) != (expected_heating > _decimal(CLIMATE_HEATING_COMFORT_GAP)):
            raise ClimateDemandViolation("heating demand does not match its gap")
        if (
            self.cooling is ClimateDemandState.REQUIRED
        ) != (expected_cooling >= _decimal(CLIMATE_COOLING_START_GAP)):
            raise ClimateDemandViolation("cooling demand does not match its gap")

    def _validate_humidity_channel(self) -> None:
        if self.humidifying is ClimateDemandState.UNAVAILABLE:
            if self.humidifying_gap is not None:
                raise ClimateDemandViolation(
                    "unavailable humidity demand must not retain a calculated gap"
                )
            return
        if self.current_humidity is None or self.humidifying_gap is None:
            raise ClimateDemandViolation(
                "available humidity demand requires current humidity and gap"
            )
        expected = max(
            _decimal(self.target_humidity) - _decimal(self.current_humidity),
            Decimal(0),
        )
        if _decimal(self.humidifying_gap) != expected:
            raise ClimateDemandViolation(
                "humidity gap must match current and target humidity"
            )
        if (
            self.humidifying is ClimateDemandState.REQUIRED
        ) != (expected > _decimal(CLIMATE_HUMIDIFYING_COMFORT_GAP)):
            raise ClimateDemandViolation(
                "humidifying demand does not match its comfort gap"
            )


@dataclass(frozen=True, slots=True)
class ClimateDemandSnapshot:
    """Command-free demand for every room in one climate contour."""

    contour_id: str
    contour_mode: ContourMode
    rooms: tuple[ClimateRoomDemand, ...]
    version: int = CLIMATE_DEMAND_MODEL_VERSION

    def __post_init__(self) -> None:
        _stable_room_id(self.contour_id)
        if not isinstance(self.contour_mode, ContourMode):
            raise ClimateDemandViolation("demand contour mode must be approved")
        if type(self.rooms) is not tuple or any(
            not isinstance(room, ClimateRoomDemand) for room in self.rooms
        ):
            raise ClimateDemandViolation(
                "room demands must be an immutable typed tuple"
            )
        if not self.rooms:
            raise ClimateDemandViolation("room demands must not be empty")
        if len(self.rooms) != len({room.room_id for room in self.rooms}):
            raise ClimateDemandViolation("demand room ids must be unique")
        if self.version != CLIMATE_DEMAND_MODEL_VERSION:
            raise ClimateDemandViolation("demand model version is unsupported")

    @property
    def commands_enabled(self) -> bool:
        """This migration layer cannot grant execution authority."""

        return False

    def room(self, room_id: str) -> ClimateRoomDemand | None:
        """Return one room demand by stable HausmanHub id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)


def resolve_climate_room_demand(
    target: ClimateRoomTarget,
    observation: ClimateRoomObservation | None,
) -> ClimateRoomDemand:
    """Calculate three independent needs without applying device policy."""

    if not isinstance(target, ClimateRoomTarget):
        raise ClimateDemandViolation("a validated climate room target is required")
    if observation is not None:
        if not isinstance(observation, ClimateRoomObservation):
            raise ClimateDemandViolation(
                "a validated climate room observation is required"
            )
        if observation.room_id != target.room_id:
            raise ClimateDemandViolation(
                "demand target and observation must reference the same room"
            )
        if observation.data_status is not target.observation_status:
            raise ClimateDemandViolation(
                "demand target and observation status must come from one snapshot"
            )
        observation_status = observation.data_status
        temperature_quality = observation.temperature_quality
        current_temperature = observation.temperature
        current_humidity = observation.humidity
    else:
        if target.observation_status is not ClimateDataStatus.UNAVAILABLE:
            raise ClimateDemandViolation(
                "missing observation requires an unavailable target status"
            )
        observation_status = ClimateDataStatus.UNAVAILABLE
        temperature_quality = ClimateTemperatureQuality.UNKNOWN
        current_temperature = None
        current_humidity = None

    thermal_available = (
        observation_status is ClimateDataStatus.FRESH
        and current_temperature is not None
        and observation is not None
        and observation.temperature_quality is ClimateTemperatureQuality.NORMAL
    )
    humidity_available = (
        observation_status is ClimateDataStatus.FRESH
        and current_humidity is not None
    )
    if thermal_available:
        heating_gap = _nonnegative_gap(
            target.target_temperature,
            current_temperature,
        )
        cooling_gap = _nonnegative_gap(
            current_temperature,
            target.target_temperature,
        )
        heating = (
            ClimateDemandState.REQUIRED
            if heating_gap > CLIMATE_HEATING_COMFORT_GAP
            else ClimateDemandState.NOT_REQUIRED
        )
        cooling = _required_at_or_above(
            cooling_gap,
            CLIMATE_COOLING_START_GAP,
        )
    else:
        heating_gap = None
        cooling_gap = None
        heating = ClimateDemandState.UNAVAILABLE
        cooling = ClimateDemandState.UNAVAILABLE
    if humidity_available:
        humidifying_gap = _nonnegative_gap(
            target.target_humidity,
            current_humidity,
        )
        humidifying = (
            ClimateDemandState.REQUIRED
            if humidifying_gap > CLIMATE_HUMIDIFYING_COMFORT_GAP
            else ClimateDemandState.NOT_REQUIRED
        )
    else:
        humidifying_gap = None
        humidifying = ClimateDemandState.UNAVAILABLE
    return ClimateRoomDemand(
        room_id=target.room_id,
        observation_status=observation_status,
        temperature_quality=temperature_quality,
        strategy=target.strategy,
        current_temperature=current_temperature,
        current_humidity=current_humidity,
        target_temperature=target.target_temperature,
        target_humidity=target.target_humidity,
        heating_gap=heating_gap,
        cooling_gap=cooling_gap,
        humidifying_gap=humidifying_gap,
        heating=heating,
        cooling=cooling,
        humidifying=humidifying,
        temperature_origin=target.temperature_origin,
    )


def _required_at_or_above(gap: float, threshold: float) -> ClimateDemandState:
    return (
        ClimateDemandState.REQUIRED
        if _decimal(gap) >= _decimal(threshold)
        else ClimateDemandState.NOT_REQUIRED
    )


def _nonnegative_gap(high: float | int, low: float | int) -> float:
    return float(max(_decimal(high) - _decimal(low), Decimal(0)))


def _decimal(value: float | int) -> Decimal:
    return Decimal(str(value))


def _stable_room_id(value: object) -> None:
    try:
        ClimateRoom(value, "Room")  # type: ignore[arg-type]
    except ClimateModelViolation as error:
        raise ClimateDemandViolation("demand room id must be stable") from error


def _comfort(temperature: object, humidity: object, strategy: object) -> None:
    try:
        ClimateComfortSettings(
            target_temperature=temperature,  # type: ignore[arg-type]
            target_humidity=humidity,  # type: ignore[arg-type]
            strategy=strategy,  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as error:
        raise ClimateDemandViolation("demand comfort target is invalid") from error


def _optional_number(
    value: object,
    minimum: float,
    maximum: float,
    label: str,
) -> None:
    if value is None:
        return
    if (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ClimateDemandViolation(f"{label} is outside fixed bounds")


def _optional_gap(value: object, label: str) -> None:
    if value is None:
        return
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ClimateDemandViolation(f"{label} must be finite and non-negative")
