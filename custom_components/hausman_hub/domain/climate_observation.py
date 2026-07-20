"""Pure internal observations consumed by HausmanHub's climate algorithm.

This model contains only stable HausmanHub identifiers and bounded facts.  It
does not know about Climate API payloads, Home Assistant entities, services,
transports, storage, or commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

from .climate import ClimateModelViolation, ClimateRoom


CLIMATE_OBSERVATION_MODEL_VERSION = 1


class ClimateObservationViolation(ValueError):
    """An internal climate observation is unsafe or contradictory."""


class ClimateDataStatus(StrEnum):
    """Freshness of a snapshot or one configured room."""

    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class ClimateObservationDeviceKind(StrEnum):
    """Device kinds that can appear in observations, including limitations."""

    AIR_CONDITIONER = "air_conditioner"
    RADIATOR_THERMOSTAT = "radiator_thermostat"
    HUMIDIFIER = "humidifier"
    FLOOR_HEATING = "floor_heating"
    TEMPERATURE_SENSOR = "temperature_sensor"
    HUMIDITY_SENSOR = "humidity_sensor"
    CURTAINS = "curtains"


class ClimateDeviceAvailability(StrEnum):
    """Whether a configured logical device can currently be observed."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    MISSING = "missing"


class ClimateDeviceActivity(StrEnum):
    """Normalized physical activity, never a vendor or HA state string."""

    RUNNING = "running"
    STOPPED = "stopped"
    IDLE = "idle"
    COOLING = "cooling"
    HEATING = "heating"
    HUMIDIFYING = "humidifying"
    UNKNOWN = "unknown"


class ClimateRoomMode(StrEnum):
    """Observed room policy mode."""

    AUTO = "auto"
    MANUAL = "manual"
    FORCED_AUTO_ONLY = "forced_auto_only"
    UNKNOWN = "unknown"


class ClimateTemperatureQuality(StrEnum):
    """Plausibility state of the current room temperature."""

    NORMAL = "normal"
    SUSPECT = "suspect"
    UNKNOWN = "unknown"


class ClimateWindowState(StrEnum):
    """Safe normalized window state."""

    CLOSED = "closed"
    OPEN = "open"
    UNKNOWN = "unknown"
    NOT_CONFIGURED = "not_configured"


class ClimatePhysicalFeedback(StrEnum):
    """Quality of direct physical equipment feedback."""

    CONFIRMED = "confirmed"
    STALE = "stale"
    UNKNOWN = "unknown"


class ClimateFanMode(StrEnum):
    """Fan modes used by the frozen climate reference."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    AUTO = "auto"


class ClimateSeason(StrEnum):
    """Observed or configured thermal season."""

    SUMMER = "summer"
    WINTER = "winter"
    UNKNOWN = "unknown"


class ClimateDayPeriod(StrEnum):
    """Comfort period selected by the climate schedule."""

    DAY = "day"
    NIGHT = "night"
    UNKNOWN = "unknown"


class ClimateOccupancyMode(StrEnum):
    """Home presence policy observed by the climate decision boundary."""

    HOME = "home"
    AWAY_SAFE_OFF = "safe_off"
    AWAY_KEEP = "keep"


class ClimateDelayedIntentState(StrEnum):
    """State of a previously deferred abstract intent."""

    NONE = "none"
    STALE_AFTER_CONTROL_CHANGE = "stale_after_control_change"


class ClimateExecutionGuardState(StrEnum):
    """Relevant bounded executor state without any command payload."""

    NONE = "none"
    COOLDOWN_ACTIVE = "cooldown_active"
    DUPLICATE = "duplicate"
    AUTHORITY_MISSING = "authority_missing"


@dataclass(frozen=True, slots=True)
class ClimateHomeObservation:
    """Home-wide facts used by room decisions."""

    season: ClimateSeason = ClimateSeason.UNKNOWN
    period: ClimateDayPeriod = ClimateDayPeriod.UNKNOWN
    outdoor_temperature: float | None = None
    heat_load_temperature: float | None = None
    central_heating_on: bool | None = None
    occupancy: ClimateOccupancyMode = ClimateOccupancyMode.HOME

    def __post_init__(self) -> None:
        _require_enum(self.season, ClimateSeason, "season")
        _require_enum(self.period, ClimateDayPeriod, "day period")
        _optional_number(
            self.outdoor_temperature,
            -80,
            80,
            "outdoor temperature",
        )
        _optional_number(
            self.heat_load_temperature,
            -80,
            100,
            "outdoor heat-load temperature",
        )
        _optional_bool(self.central_heating_on, "central heating state")
        _require_enum(self.occupancy, ClimateOccupancyMode, "occupancy")


@dataclass(frozen=True, slots=True)
class ClimateControlObservation:
    """Controller facts that affect priority or safe intent acceptance."""

    manual_request: bool = False
    delayed_intent: ClimateDelayedIntentState = ClimateDelayedIntentState.NONE
    execution_guard: ClimateExecutionGuardState = ClimateExecutionGuardState.NONE

    def __post_init__(self) -> None:
        if type(self.manual_request) is not bool:
            raise ClimateObservationViolation("manual request must be boolean")
        _require_enum(
            self.delayed_intent,
            ClimateDelayedIntentState,
            "delayed intent state",
        )
        _require_enum(
            self.execution_guard,
            ClimateExecutionGuardState,
            "execution guard state",
        )


@dataclass(frozen=True, slots=True)
class ClimateRoomObservation:
    """One configured room reduced to bounded climate facts."""

    room_id: str
    name: str
    data_status: ClimateDataStatus
    temperature: float | None = None
    humidity: float | None = None
    temperature_quality: ClimateTemperatureQuality = ClimateTemperatureQuality.UNKNOWN
    window: ClimateWindowState = ClimateWindowState.UNKNOWN
    mode: ClimateRoomMode = ClimateRoomMode.UNKNOWN
    observed_target_temperature: float | None = None
    hard_off_temperature: float | None = None
    observed_target_humidity: float | None = None
    observed_target_strategy: str | None = None
    authority_eligible: bool = False

    def __post_init__(self) -> None:
        _stable_room(self.room_id, self.name)
        _require_enum(self.data_status, ClimateDataStatus, "room data status")
        _optional_number(self.temperature, -50, 70, "room temperature")
        _optional_number(self.humidity, 0, 100, "room humidity")
        _require_enum(
            self.temperature_quality,
            ClimateTemperatureQuality,
            "temperature quality",
        )
        _require_enum(self.window, ClimateWindowState, "window state")
        _require_enum(self.mode, ClimateRoomMode, "room mode")
        _optional_number(
            self.observed_target_temperature,
            10,
            35,
            "observed target temperature",
        )
        _optional_number(
            self.hard_off_temperature,
            10,
            35,
            "hard-off temperature",
        )
        _optional_number(
            self.observed_target_humidity,
            0,
            100,
            "observed target humidity",
        )
        if self.observed_target_strategy not in {None, "soft", "normal", "aggressive"}:
            raise ClimateObservationViolation(
                "observed target strategy must be approved"
            )
        if type(self.authority_eligible) is not bool:
            raise ClimateObservationViolation("room authority must be boolean")
        if self.data_status is ClimateDataStatus.UNAVAILABLE and any(
            value is not None
            for value in (
                self.temperature,
                self.humidity,
                self.observed_target_temperature,
                self.hard_off_temperature,
                self.observed_target_humidity,
                self.observed_target_strategy,
            )
        ):
            raise ClimateObservationViolation(
                "unavailable room must not retain observed values"
            )
        if self.data_status is ClimateDataStatus.UNAVAILABLE and (
            self.temperature_quality is not ClimateTemperatureQuality.UNKNOWN
            or self.window is not ClimateWindowState.UNKNOWN
            or self.mode is not ClimateRoomMode.UNKNOWN
            or self.authority_eligible
        ):
            raise ClimateObservationViolation(
                "unavailable room must retain only unknown safe states"
            )


@dataclass(frozen=True, slots=True)
class ClimateDeviceObservation:
    """One logical device observation with no private source binding."""

    device_id: str
    name: str
    room_id: str
    kind: ClimateObservationDeviceKind
    availability: ClimateDeviceAvailability
    activity: ClimateDeviceActivity = ClimateDeviceActivity.UNKNOWN
    current_target_temperature: float | None = None
    current_target_humidity: float | None = None
    fan_mode: ClimateFanMode | None = None
    quiet: bool | None = None
    physical_feedback: ClimatePhysicalFeedback = ClimatePhysicalFeedback.UNKNOWN
    last_started_at: int | None = None
    last_stopped_at: int | None = None
    cooling_evidence_confirmed: bool = False
    cooling_rate_per_hour: float | None = None
    confirmed_short_cycle_count: int | None = None

    def __post_init__(self) -> None:
        _stable_room(self.device_id, self.name)
        _stable_room(self.room_id, "Room")
        _require_enum(self.kind, ClimateObservationDeviceKind, "device kind")
        _require_enum(
            self.availability,
            ClimateDeviceAvailability,
            "device availability",
        )
        _require_enum(self.activity, ClimateDeviceActivity, "device activity")
        _optional_number(
            self.current_target_temperature,
            10,
            35,
            "device target temperature",
        )
        _optional_number(
            self.current_target_humidity,
            0,
            100,
            "device target humidity",
        )
        if self.fan_mode is not None:
            _require_enum(self.fan_mode, ClimateFanMode, "fan mode")
        _optional_bool(self.quiet, "quiet state")
        _require_enum(
            self.physical_feedback,
            ClimatePhysicalFeedback,
            "physical feedback",
        )
        _optional_timestamp(self.last_started_at, "last start time")
        _optional_timestamp(self.last_stopped_at, "last stop time")
        if type(self.cooling_evidence_confirmed) is not bool:
            raise ClimateObservationViolation(
                "cooling evidence confirmation must be boolean"
            )
        _optional_number(
            self.cooling_rate_per_hour,
            -20,
            20,
            "cooling rate",
        )
        if self.confirmed_short_cycle_count is not None and (
            type(self.confirmed_short_cycle_count) is not int
            or not 0 <= self.confirmed_short_cycle_count <= 100
        ):
            raise ClimateObservationViolation(
                "confirmed short-cycle count must be bounded"
            )
        if not self.cooling_evidence_confirmed and self.cooling_rate_per_hour is not None:
            raise ClimateObservationViolation(
                "unconfirmed cooling evidence must not retain a rate"
            )
        if self.availability is not ClimateDeviceAvailability.AVAILABLE and any(
            value is not None
            for value in (
                self.current_target_temperature,
                self.current_target_humidity,
                self.fan_mode,
                self.quiet,
                self.last_started_at,
                self.last_stopped_at,
                self.cooling_rate_per_hour,
                self.confirmed_short_cycle_count,
            )
        ):
            raise ClimateObservationViolation(
                "unavailable device must not retain observed values"
            )
        if (
            self.availability is not ClimateDeviceAvailability.AVAILABLE
            and self.activity is not ClimateDeviceActivity.UNKNOWN
        ):
            raise ClimateObservationViolation(
                "unavailable device activity must remain unknown"
            )
        if self.availability is not ClimateDeviceAvailability.AVAILABLE and (
            self.physical_feedback is not ClimatePhysicalFeedback.UNKNOWN
            or self.cooling_evidence_confirmed
        ):
            raise ClimateObservationViolation(
                "unavailable device must not confirm physical evidence"
            )

    @property
    def available(self) -> bool:
        """Compatibility fact consumed by the command-free native preview."""

        return self.availability is ClimateDeviceAvailability.AVAILABLE


@dataclass(frozen=True, slots=True)
class ClimateObservationSnapshot:
    """Complete immutable input boundary for the future native algorithm."""

    observed_at: int
    source_generated_at: int | None
    data_status: ClimateDataStatus
    home: ClimateHomeObservation
    control: ClimateControlObservation
    rooms: tuple[ClimateRoomObservation, ...]
    devices: tuple[ClimateDeviceObservation, ...]
    version: int = CLIMATE_OBSERVATION_MODEL_VERSION

    def __post_init__(self) -> None:
        if self.version != CLIMATE_OBSERVATION_MODEL_VERSION:
            raise ClimateObservationViolation(
                "unsupported climate observation model version"
            )
        _timestamp(self.observed_at, "observation time")
        _optional_timestamp(self.source_generated_at, "source generation time")
        _require_enum(self.data_status, ClimateDataStatus, "snapshot data status")
        if not isinstance(self.home, ClimateHomeObservation):
            raise ClimateObservationViolation("home observation is required")
        if not isinstance(self.control, ClimateControlObservation):
            raise ClimateObservationViolation("control observation is required")
        if type(self.rooms) is not tuple or type(self.devices) is not tuple:
            raise ClimateObservationViolation(
                "room and device observations must be immutable tuples"
            )
        if any(not isinstance(room, ClimateRoomObservation) for room in self.rooms):
            raise ClimateObservationViolation("room observations are required")
        if any(not isinstance(device, ClimateDeviceObservation) for device in self.devices):
            raise ClimateObservationViolation("device observations are required")
        _require_unique((room.room_id for room in self.rooms), "observation room ids")
        _require_unique(
            (device.device_id for device in self.devices),
            "observation device ids",
        )
        room_ids = {room.room_id for room in self.rooms}
        if any(device.room_id not in room_ids for device in self.devices):
            raise ClimateObservationViolation(
                "observed devices must reference observed rooms"
            )
        if any(
            timestamp is not None and timestamp > self.observed_at
            for device in self.devices
            for timestamp in (device.last_started_at, device.last_stopped_at)
        ):
            raise ClimateObservationViolation(
                "device transition time must not be newer than the observation"
            )
        if self.data_status is ClimateDataStatus.UNAVAILABLE:
            if self.source_generated_at is not None:
                raise ClimateObservationViolation(
                    "unavailable snapshot must not retain source time"
                )
            if any(
                room.data_status is not ClimateDataStatus.UNAVAILABLE
                for room in self.rooms
            ):
                raise ClimateObservationViolation(
                    "unavailable snapshot requires unavailable rooms"
                )
        elif self.source_generated_at is None:
            raise ClimateObservationViolation(
                "available snapshot requires source generation time"
            )

    @property
    def runtime_fresh(self) -> bool:
        """Return whether the complete source snapshot is current."""

        return self.data_status is ClimateDataStatus.FRESH

    def room(self, room_id: str) -> ClimateRoomObservation | None:
        """Return one room by stable HausmanHub id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)

    def device(self, device_id: str) -> ClimateDeviceObservation | None:
        """Return one logical device by stable HausmanHub id."""

        return next(
            (device for device in self.devices if device.device_id == device_id),
            None,
        )

    def devices_for_room(self, room_id: str) -> tuple[ClimateDeviceObservation, ...]:
        """Return deterministic logical device observations for one room."""

        return tuple(device for device in self.devices if device.room_id == room_id)


def _stable_room(stable_id: object, name: object) -> None:
    try:
        ClimateRoom(stable_id, name)  # type: ignore[arg-type]
    except ClimateModelViolation as error:
        raise ClimateObservationViolation("observation id or name is invalid") from error


def _require_enum(value: object, expected: type[StrEnum], label: str) -> None:
    if not isinstance(value, expected):
        raise ClimateObservationViolation(f"{label} must be approved")


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
        or value < minimum
        or value > maximum
    ):
        raise ClimateObservationViolation(f"{label} is outside fixed bounds")


def _optional_bool(value: object, label: str) -> None:
    if value is not None and type(value) is not bool:
        raise ClimateObservationViolation(f"{label} must be boolean or unavailable")


def _timestamp(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ClimateObservationViolation(f"{label} must be a non-negative integer")


def _optional_timestamp(value: object, label: str) -> None:
    if value is not None:
        _timestamp(value, label)


def _require_unique(values: object, label: str) -> None:
    items = tuple(values)  # type: ignore[arg-type]
    if len(items) != len(set(items)):
        raise ClimateObservationViolation(f"{label} must be unique")
