"""Pure resolution of HausmanHub room temperature and humidity targets.

The result describes desired comfort only.  It cannot select equipment, build
an intent, call Home Assistant, or grant execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .climate import ClimateModelViolation, ClimateRoom
from .climate_observation import (
    ClimateDataStatus,
    ClimateOccupancyMode,
    ClimateRoomMode,
    ClimateRoomObservation,
)
from .contours import (
    CLIMATE_TARGET_TEMPERATURE_MAXIMUM,
    CLIMATE_TARGET_TEMPERATURE_MINIMUM,
    ClimateComfortSettings,
    ClimateProfile,
    ClimateStrategy,
    ClimateTemporaryOverride,
    ContourMode,
)


CLIMATE_TARGET_MODEL_VERSION = 1
CLIMATE_AWAY_SETBACK_CELSIUS = 2.0


class ClimateTargetViolation(ValueError):
    """A target policy or resolution is incomplete or contradictory."""


class ClimateTemperatureTargetOrigin(StrEnum):
    """The exact configured source of the effective temperature target."""

    PROFILE = "profile"
    TEMPORARY_OVERRIDE = "temporary_override"
    AWAY_SETBACK = "away_setback"


@dataclass(frozen=True, slots=True)
class ClimateRoomTargetPolicy:
    """Validated day/night comfort policy for one stable HausmanHub room."""

    room_id: str
    day_profile: ClimateComfortSettings
    night_profile: ClimateComfortSettings
    active_profile: ClimateProfile
    temporary_override: ClimateTemporaryOverride | None = None

    def __post_init__(self) -> None:
        _stable_room_id(self.room_id)
        if not isinstance(self.day_profile, ClimateComfortSettings):
            raise ClimateTargetViolation("day target profile must be validated")
        if not isinstance(self.night_profile, ClimateComfortSettings):
            raise ClimateTargetViolation("night target profile must be validated")
        if not isinstance(self.active_profile, ClimateProfile):
            raise ClimateTargetViolation("active target profile must be approved")
        if self.temporary_override is not None and not isinstance(
            self.temporary_override,
            ClimateTemporaryOverride,
        ):
            raise ClimateTargetViolation(
                "temporary temperature target must be validated"
            )

    @property
    def active_settings(self) -> ClimateComfortSettings:
        """Return the exact selected saved profile before an override."""

        return (
            self.day_profile
            if self.active_profile is ClimateProfile.DAY
            else self.night_profile
        )


@dataclass(frozen=True, slots=True)
class ClimateRoomTarget:
    """Resolved comfort values paired with observation availability."""

    room_id: str
    active_profile: ClimateProfile
    profile_temperature: float
    target_temperature: float
    target_humidity: int
    strategy: ClimateStrategy
    temperature_origin: ClimateTemperatureTargetOrigin
    observation_status: ClimateDataStatus
    observation_observed_at: int

    def __post_init__(self) -> None:
        _stable_room_id(self.room_id)
        if not isinstance(self.active_profile, ClimateProfile):
            raise ClimateTargetViolation("resolved target profile must be approved")
        _comfort(
            self.profile_temperature,
            self.target_humidity,
            self.strategy,
        )
        _comfort(
            self.target_temperature,
            self.target_humidity,
            self.strategy,
        )
        if not isinstance(
            self.temperature_origin,
            ClimateTemperatureTargetOrigin,
        ):
            raise ClimateTargetViolation("temperature target origin must be approved")
        if not isinstance(self.observation_status, ClimateDataStatus):
            raise ClimateTargetViolation("target observation status must be approved")
        _timestamp(self.observation_observed_at)
        if (
            self.temperature_origin is ClimateTemperatureTargetOrigin.PROFILE
            and self.target_temperature != self.profile_temperature
        ):
            raise ClimateTargetViolation(
                "profile target must equal the selected saved temperature"
            )
        if self.temperature_origin is ClimateTemperatureTargetOrigin.AWAY_SETBACK:
            setback = abs(self.target_temperature - self.profile_temperature)
            if setback > CLIMATE_AWAY_SETBACK_CELSIUS:
                raise ClimateTargetViolation(
                    "away setback target must stay within the bounded setback"
                )


@dataclass(frozen=True, slots=True)
class ClimateTargetSnapshot:
    """All resolved targets of one HausmanHub climate contour."""

    contour_id: str
    contour_mode: ContourMode
    rooms: tuple[ClimateRoomTarget, ...]
    version: int = CLIMATE_TARGET_MODEL_VERSION

    def __post_init__(self) -> None:
        _stable_room_id(self.contour_id)
        if not isinstance(self.contour_mode, ContourMode):
            raise ClimateTargetViolation("target contour mode must be approved")
        if type(self.rooms) is not tuple or any(
            not isinstance(room, ClimateRoomTarget) for room in self.rooms
        ):
            raise ClimateTargetViolation(
                "resolved room targets must be an immutable tuple"
            )
        if not self.rooms:
            raise ClimateTargetViolation("resolved target rooms must not be empty")
        if len(self.rooms) != len({room.room_id for room in self.rooms}):
            raise ClimateTargetViolation("resolved target room ids must be unique")
        if len({room.observation_observed_at for room in self.rooms}) != 1:
            raise ClimateTargetViolation(
                "resolved targets must use one observation time"
            )
        if self.version != CLIMATE_TARGET_MODEL_VERSION:
            raise ClimateTargetViolation("target model version is unsupported")

    @property
    def commands_enabled(self) -> bool:
        """Keep this migration stage structurally unable to authorize commands."""

        return False

    def room(self, room_id: str) -> ClimateRoomTarget | None:
        """Return resolved targets by stable HausmanHub room id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)

    @property
    def observed_at(self) -> int:
        """Return the exact internal observation time shared by all targets."""

        return self.rooms[0].observation_observed_at


def resolve_climate_room_target(
    policy: ClimateRoomTargetPolicy,
    observation: ClimateRoomObservation | None,
    *,
    observed_at: int,
    occupancy: ClimateOccupancyMode = ClimateOccupancyMode.HOME,
) -> ClimateRoomTarget:
    """Select one profile and apply only an explicit temperature override."""

    if not isinstance(policy, ClimateRoomTargetPolicy):
        raise ClimateTargetViolation("a validated room target policy is required")
    _timestamp(observed_at)
    if not isinstance(occupancy, ClimateOccupancyMode):
        raise ClimateTargetViolation("a validated home occupancy mode is required")
    if observation is not None:
        if not isinstance(observation, ClimateRoomObservation):
            raise ClimateTargetViolation("a validated room observation is required")
        if observation.room_id != policy.room_id:
            raise ClimateTargetViolation(
                "target policy and observation must reference the same room"
            )
        observation_status = observation.data_status
    else:
        observation_status = ClimateDataStatus.UNAVAILABLE
    profile = policy.active_settings
    override = policy.temporary_override
    automatic_mode = (
        observation is not None
        and observation.mode
        in {ClimateRoomMode.AUTO, ClimateRoomMode.FORCED_AUTO_ONLY}
    )
    if override is not None:
        target_temperature = override.target_temperature
        temperature_origin = ClimateTemperatureTargetOrigin.TEMPORARY_OVERRIDE
    elif occupancy is ClimateOccupancyMode.AWAY_SETBACK and automatic_mode:
        target_temperature = _away_setback_temperature(
            profile.target_temperature,
            None if observation is None else observation.temperature,
        )
        temperature_origin = ClimateTemperatureTargetOrigin.AWAY_SETBACK
    else:
        target_temperature = profile.target_temperature
        temperature_origin = ClimateTemperatureTargetOrigin.PROFILE
    return ClimateRoomTarget(
        room_id=policy.room_id,
        active_profile=policy.active_profile,
        profile_temperature=profile.target_temperature,
        target_temperature=target_temperature,
        target_humidity=profile.target_humidity,
        strategy=profile.strategy,
        temperature_origin=temperature_origin,
        observation_status=observation_status,
        observation_observed_at=observed_at,
    )


def _away_setback_temperature(
    target: float,
    current: float | None,
) -> float:
    if current is None or current == target:
        return target
    if current < target:
        # Heating setback lowers the target but never crosses the current
        # temperature: crossing it would invent a cooling demand while away.
        return max(
            target - CLIMATE_AWAY_SETBACK_CELSIUS,
            current,
            CLIMATE_TARGET_TEMPERATURE_MINIMUM,
        )
    return min(
        target + CLIMATE_AWAY_SETBACK_CELSIUS,
        current,
        CLIMATE_TARGET_TEMPERATURE_MAXIMUM,
    )


def _stable_room_id(value: object) -> None:
    try:
        ClimateRoom(value, "Room")  # type: ignore[arg-type]
    except ClimateModelViolation as error:
        raise ClimateTargetViolation("target room id must be stable") from error


def _timestamp(value: object) -> None:
    if type(value) is not int or not 0 <= value <= 9_999_999_999_999:
        raise ClimateTargetViolation(
            "target observation time must be bounded milliseconds"
        )


def _comfort(
    temperature: object,
    humidity: object,
    strategy: object,
) -> None:
    try:
        ClimateComfortSettings(
            target_temperature=temperature,  # type: ignore[arg-type]
            target_humidity=humidity,  # type: ignore[arg-type]
            strategy=strategy,  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as error:
        raise ClimateTargetViolation("resolved comfort target is invalid") from error
