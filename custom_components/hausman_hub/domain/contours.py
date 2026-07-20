"""Universal automatic-contour model owned by HASC.

The first supported contour is climate.  It deliberately delegates the actual
climate algorithm to the existing ``hausman-climate`` engine instead of
reimplementing that mature policy inside the Home Assistant integration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import re


CONTOUR_REGISTRY_VERSION = 4
MAX_CONTOURS = 32
MAX_CONTOUR_ROOMS = 128
MAX_CONTOUR_DEVICES = 512
CLIMATE_TARGET_TEMPERATURE_DEFAULT = 25.0
CLIMATE_TARGET_HUMIDITY_DEFAULT = 45
CLIMATE_TARGET_TEMPERATURE_MINIMUM = 18.0
CLIMATE_TARGET_TEMPERATURE_MAXIMUM = 28.0
CLIMATE_TARGET_TEMPERATURE_STEP = 0.5
CLIMATE_TARGET_HUMIDITY_MINIMUM = 30
CLIMATE_TARGET_HUMIDITY_MAXIMUM = 70
CLIMATE_TARGET_HUMIDITY_STEP = 5
CLIMATE_DAY_START_DEFAULT = "07:00"
CLIMATE_NIGHT_START_DEFAULT = "23:00"

_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CLOCK_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_MAX_SCHEDULE_LOOKAHEAD_MINUTES = 26 * 60


class ContourViolation(ValueError):
    """A contour definition is incomplete or internally inconsistent."""


class ContourKind(StrEnum):
    """Contour kinds supported by the current HASC package."""

    CLIMATE = "climate"


class ContourMode(StrEnum):
    """Common lifecycle shared by present and future contour kinds."""

    DISABLED = "disabled"
    OBSERVE = "observe"
    AUTOMATIC = "automatic"


class ContourEngine(StrEnum):
    """Typed engine implementations hidden behind the HASC contour."""

    EXISTING_CLIMATE_CORE = "existing_climate_core"


class ClimateStrategy(StrEnum):
    """Existing climate-core target strategies exposed in plain HASC UI."""

    SOFT = "soft"
    NORMAL = "normal"
    AGGRESSIVE = "aggressive"


class ClimateProfile(StrEnum):
    """Two simple comfort profiles understood by the HASC 1.x UI."""

    DAY = "day"
    NIGHT = "night"


@dataclass(frozen=True, slots=True)
class ClimateSchedule:
    """One explicitly enabled local-time switch between comfort profiles."""

    enabled: bool = False
    day_start: str = CLIMATE_DAY_START_DEFAULT
    night_start: str = CLIMATE_NIGHT_START_DEFAULT
    last_applied_profile: ClimateProfile | None = None

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ContourViolation("climate schedule enabled must be boolean")
        _clock_minutes(self.day_start, "day profile start")
        _clock_minutes(self.night_start, "night profile start")
        if self.day_start == self.night_start:
            raise ContourViolation("day and night profile starts must differ")
        if self.last_applied_profile is not None and not isinstance(
            self.last_applied_profile,
            ClimateProfile,
        ):
            raise ContourViolation("last applied climate profile must be approved")

    def profile_at(self, *, hour: int, minute: int) -> ClimateProfile:
        """Return the profile selected for one local wall-clock minute."""

        if type(hour) is not int or type(minute) is not int:
            raise ContourViolation("climate schedule time must be numeric")
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ContourViolation("climate schedule time is invalid")
        current = hour * 60 + minute
        day = _clock_minutes(self.day_start, "day profile start")
        night = _clock_minutes(self.night_start, "night profile start")
        if day < night:
            return (
                ClimateProfile.DAY
                if day <= current < night
                else ClimateProfile.NIGHT
            )
        return (
            ClimateProfile.DAY
            if current >= day or current < night
            else ClimateProfile.NIGHT
        )

    def next_transition_after(
        self,
        now: datetime,
    ) -> tuple[ClimateProfile, datetime]:
        """Return the next real local minute whose selected profile changes."""

        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ContourViolation("climate schedule needs timezone-aware local time")
        current = self.profile_at(hour=now.hour, minute=now.minute)
        utc_now = now.astimezone(timezone.utc)
        candidate = utc_now.replace(second=0, microsecond=0)
        if candidate <= utc_now:
            candidate += timedelta(minutes=1)
        for _ in range(_MAX_SCHEDULE_LOOKAHEAD_MINUTES):
            local_candidate = candidate.astimezone(now.tzinfo)
            selected = self.profile_at(
                hour=local_candidate.hour,
                minute=local_candidate.minute,
            )
            if selected is not current:
                return selected, local_candidate
            candidate += timedelta(minutes=1)
        raise ContourViolation("next climate schedule transition is unavailable")


@dataclass(frozen=True, slots=True)
class ClimateComfortSettings:
    """One validated temperature, humidity, and strategy bundle."""

    target_temperature: float
    target_humidity: int
    strategy: ClimateStrategy

    def __post_init__(self) -> None:
        _temperature(self.target_temperature)
        _humidity(self.target_humidity)
        if not isinstance(self.strategy, ClimateStrategy):
            raise ContourViolation("climate strategy must be approved")


@dataclass(frozen=True, slots=True)
class ClimateTemporaryOverride:
    """One room temperature that lasts until the next schedule transition."""

    target_temperature: float

    def __post_init__(self) -> None:
        _temperature(self.target_temperature)


@dataclass(frozen=True, slots=True)
class ClimateContourRoom:
    """One room assignment and its user-facing comfort parameters."""

    room_id: str
    device_ids: tuple[str, ...]
    day_profile: ClimateComfortSettings
    night_profile: ClimateComfortSettings
    active_profile: ClimateProfile = ClimateProfile.DAY
    temporary_override: ClimateTemporaryOverride | None = None

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "contour room id")
        if not self.device_ids:
            raise ContourViolation("contour room needs at least one device")
        if len(self.device_ids) > MAX_CONTOUR_DEVICES:
            raise ContourViolation("contour room has too many devices")
        for device_id in self.device_ids:
            _stable_id(device_id, "contour device id")
        _unique(self.device_ids, "contour device ids")
        if not isinstance(self.day_profile, ClimateComfortSettings):
            raise ContourViolation("day climate profile must be validated")
        if not isinstance(self.night_profile, ClimateComfortSettings):
            raise ContourViolation("night climate profile must be validated")
        if not isinstance(self.active_profile, ClimateProfile):
            raise ContourViolation("active climate profile must be approved")
        if self.temporary_override is not None and not isinstance(
            self.temporary_override,
            ClimateTemporaryOverride,
        ):
            raise ContourViolation("temporary climate override must be validated")

    @property
    def profile_settings(self) -> ClimateComfortSettings:
        """Return the unchanged settings saved in the active day/night profile."""

        return (
            self.day_profile
            if self.active_profile is ClimateProfile.DAY
            else self.night_profile
        )

    @property
    def active_settings(self) -> ClimateComfortSettings:
        """Return effective values, including an optional temporary temperature."""

        saved = self.profile_settings
        if self.temporary_override is None:
            return saved
        return ClimateComfortSettings(
            target_temperature=self.temporary_override.target_temperature,
            target_humidity=saved.target_humidity,
            strategy=saved.strategy,
        )

    @property
    def target_temperature(self) -> float:
        """Keep existing contour consumers on the active profile target."""

        return self.active_settings.target_temperature

    @property
    def target_humidity(self) -> int:
        """Keep existing contour consumers on the active profile target."""

        return self.active_settings.target_humidity

    @property
    def strategy(self) -> ClimateStrategy:
        """Keep existing contour consumers on the active profile strategy."""

        return self.active_settings.strategy


@dataclass(frozen=True, slots=True)
class ContourDefinition:
    """One autonomous contour exposed to Home Assistant and Android."""

    contour_id: str
    name: str
    kind: ContourKind
    mode: ContourMode
    engine: ContourEngine
    rooms: tuple[ClimateContourRoom, ...]
    schedule: ClimateSchedule = field(default_factory=ClimateSchedule)

    def __post_init__(self) -> None:
        _stable_id(self.contour_id, "contour id")
        _name(self.name, "contour name")
        if self.kind is not ContourKind.CLIMATE:
            raise ContourViolation("contour kind is unsupported")
        if not isinstance(self.mode, ContourMode):
            raise ContourViolation("contour mode must be approved")
        if self.engine is not ContourEngine.EXISTING_CLIMATE_CORE:
            raise ContourViolation("contour engine is unsupported")
        if not self.rooms:
            raise ContourViolation("climate contour needs at least one room")
        if len(self.rooms) > MAX_CONTOUR_ROOMS:
            raise ContourViolation("climate contour has too many rooms")
        if any(not isinstance(room, ClimateContourRoom) for room in self.rooms):
            raise ContourViolation("climate contour room must be validated")
        if not isinstance(self.schedule, ClimateSchedule):
            raise ContourViolation("climate schedule must be validated")
        if self.schedule.enabled and self.mode is not ContourMode.AUTOMATIC:
            raise ContourViolation("climate schedule requires automatic mode")
        if any(room.temporary_override is not None for room in self.rooms) and (
            not self.schedule.enabled or self.mode is not ContourMode.AUTOMATIC
        ):
            raise ContourViolation(
                "temporary climate override requires an automatic schedule"
            )
        _unique((room.room_id for room in self.rooms), "contour room ids")
        _unique(
            (device_id for room in self.rooms for device_id in room.device_ids),
            "devices assigned to contour rooms",
        )


@dataclass(frozen=True, slots=True)
class ContourRegistry:
    """Complete versioned collection of HASC-owned contours."""

    contours: tuple[ContourDefinition, ...] = ()
    version: int = CONTOUR_REGISTRY_VERSION

    def __post_init__(self) -> None:
        if self.version != CONTOUR_REGISTRY_VERSION:
            raise ContourViolation("unsupported contour registry version")
        if len(self.contours) > MAX_CONTOURS:
            raise ContourViolation("too many contours")
        if any(not isinstance(item, ContourDefinition) for item in self.contours):
            raise ContourViolation("contour must be validated")
        _unique((item.contour_id for item in self.contours), "contour ids")

    def contour(self, contour_id: str) -> ContourDefinition | None:
        """Return one contour by its stable public identifier."""

        return next(
            (item for item in self.contours if item.contour_id == contour_id),
            None,
        )


def climate_contour_room(
    *,
    room_id: object,
    device_ids: object,
    target_temperature: object = None,
    target_humidity: object = None,
    strategy: object = None,
    profiles: object = None,
    active_profile: object = None,
    temporary_override: object = None,
) -> ClimateContourRoom:
    """Build one exact room policy from a form or persisted payload."""

    if not isinstance(room_id, str):
        raise ContourViolation("contour room id is required")
    if not isinstance(device_ids, (list, tuple)) or any(
        not isinstance(value, str) for value in device_ids
    ):
        raise ContourViolation("contour device ids must be a list of strings")
    if profiles is None:
        if active_profile is not None:
            raise ContourViolation("active profile requires climate profiles")
        settings = _comfort_settings(
            target_temperature=target_temperature,
            target_humidity=target_humidity,
            strategy=strategy,
        )
        day_profile = settings
        night_profile = settings
        selected_profile = ClimateProfile.DAY
    else:
        if any(
            value is not None
            for value in (target_temperature, target_humidity, strategy)
        ):
            raise ContourViolation(
                "shared room targets cannot be mixed with climate profiles"
            )
        if not isinstance(profiles, Mapping) or set(profiles) != {
            ClimateProfile.DAY.value,
            ClimateProfile.NIGHT.value,
        }:
            raise ContourViolation("climate profiles must contain day and night")
        day_profile = _comfort_settings_from_payload(
            profiles[ClimateProfile.DAY.value]
        )
        night_profile = _comfort_settings_from_payload(
            profiles[ClimateProfile.NIGHT.value]
        )
        try:
            selected_profile = ClimateProfile(active_profile)
        except (TypeError, ValueError) as error:
            raise ContourViolation("active climate profile must be approved") from error
    selected_override = _temporary_override_from_payload(temporary_override)
    return ClimateContourRoom(
        room_id=room_id,
        device_ids=tuple(device_ids),
        day_profile=day_profile,
        night_profile=night_profile,
        active_profile=selected_profile,
        temporary_override=selected_override,
    )


def _temporary_override_from_payload(
    payload: object,
) -> ClimateTemporaryOverride | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping) or set(payload) != {"target_temperature"}:
        raise ContourViolation("temporary climate override fields are invalid")
    return ClimateTemporaryOverride(
        target_temperature=_temperature(payload.get("target_temperature")),
    )


def _comfort_settings_from_payload(payload: object) -> ClimateComfortSettings:
    if not isinstance(payload, Mapping) or set(payload) != {
        "target_temperature",
        "target_humidity",
        "strategy",
    }:
        raise ContourViolation("climate profile fields are invalid")
    return _comfort_settings(
        target_temperature=payload.get("target_temperature"),
        target_humidity=payload.get("target_humidity"),
        strategy=payload.get("strategy"),
    )


def _comfort_settings(
    *,
    target_temperature: object,
    target_humidity: object,
    strategy: object,
) -> ClimateComfortSettings:
    try:
        selected_strategy = ClimateStrategy(strategy)
    except (TypeError, ValueError) as error:
        raise ContourViolation("climate strategy must be approved") from error
    return ClimateComfortSettings(
        target_temperature=_temperature(target_temperature),
        target_humidity=_humidity(target_humidity),
        strategy=selected_strategy,
    )


def climate_target_temperature(value: object) -> float:
    """Validate one public climate target and return its normalized value."""

    return _temperature(value)


def _temperature(value: object) -> float:
    if isinstance(value, bool):
        raise ContourViolation("target temperature must be numeric")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ContourViolation("target temperature must be numeric") from error
    if (
        not number.is_finite()
        or not Decimal(str(CLIMATE_TARGET_TEMPERATURE_MINIMUM))
        <= number
        <= Decimal(str(CLIMATE_TARGET_TEMPERATURE_MAXIMUM))
        or number % Decimal(str(CLIMATE_TARGET_TEMPERATURE_STEP)) != 0
    ):
        raise ContourViolation("target temperature must be 18..28 in 0.5 steps")
    return float(number)


def _clock_minutes(value: object, field: str) -> int:
    """Validate one exact 24-hour clock string and return minutes after midnight."""

    if not isinstance(value, str) or _CLOCK_TIME.fullmatch(value) is None:
        raise ContourViolation(f"{field} must use HH:MM")
    hour, minute = value.split(":", maxsplit=1)
    return int(hour) * 60 + int(minute)


def _humidity(value: object) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if (
        type(value) is not int
        or not CLIMATE_TARGET_HUMIDITY_MINIMUM
        <= value
        <= CLIMATE_TARGET_HUMIDITY_MAXIMUM
        or value % CLIMATE_TARGET_HUMIDITY_STEP != 0
    ):
        raise ContourViolation("target humidity must be 30..70 in 5 steps")
    return value


def _stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ContourViolation(f"{label} must be a stable lowercase id")


def _name(value: object, label: str) -> None:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 120:
        raise ContourViolation(f"{label} must be non-empty and at most 120 characters")


def _unique(values: object, label: str) -> None:
    items = tuple(values)  # type: ignore[arg-type]
    if len(items) != len(set(items)):
        raise ContourViolation(f"{label} must be unique")
