"""Resolve raw thermal demand against the home's current climate mode.

This pure layer selects an allowed thermal direction only.  It cannot choose
equipment, apply device hysteresis, build an intent, or authorize a command.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .climate import ClimateModelViolation, ClimateRoom
from .climate_demand import (
    ClimateDemandState,
    ClimateRoomDemand,
)
from .climate_observation import (
    ClimateHomeObservation,
    ClimateOccupancyMode,
    ClimateSeason,
)
from .climate_targets import ClimateTemperatureTargetOrigin
from .contours import ContourMode


CLIMATE_RESOLUTION_MODEL_VERSION = 1


class ClimateResolutionViolation(ValueError):
    """A thermal resolution is mutable, incomplete, or contradictory."""


class ClimateThermalResolution(StrEnum):
    """One allowed room-level thermal direction, still without equipment."""

    HEATING = "heating"
    COOLING = "cooling"
    HOLD = "hold"
    OBSERVE = "observe"
    SAFE_OFF = "safe_off"
    UNAVAILABLE = "unavailable"


class ClimateThermalReason(StrEnum):
    """Stable reason for the selected thermal direction."""

    AWAY_SAFE_OFF = "away_safe_off"
    AWAY_KEEP = "away_keep"
    THERMAL_DATA_UNAVAILABLE = "thermal_data_unavailable"
    HEATING_REQUIRED = "heating_required"
    COOLING_REQUIRED = "cooling_required"
    COMFORTABLE = "comfortable"
    HEATING_MODE_BLOCKS_COOLING = "heating_mode_blocks_cooling"
    COOLING_MODE_BLOCKS_HEATING = "cooling_mode_blocks_heating"
    THERMAL_MODE_UNKNOWN = "thermal_mode_unknown"


@dataclass(frozen=True, slots=True)
class ClimateRoomThermalResolution:
    """Conflict-free thermal direction for one stable HausmanHub room."""

    room_id: str
    season: ClimateSeason
    occupancy: ClimateOccupancyMode
    central_heating_on: bool | None
    heating_demand: ClimateDemandState
    cooling_demand: ClimateDemandState
    thermal: ClimateThermalResolution
    reason: ClimateThermalReason
    temperature_origin: ClimateTemperatureTargetOrigin = (
        ClimateTemperatureTargetOrigin.PROFILE
    )

    def __post_init__(self) -> None:
        _stable_room_id(self.room_id)
        if not isinstance(self.season, ClimateSeason):
            raise ClimateResolutionViolation("thermal season must be approved")
        if not isinstance(self.occupancy, ClimateOccupancyMode):
            raise ClimateResolutionViolation("home occupancy mode must be approved")
        if self.central_heating_on is not None and type(self.central_heating_on) is not bool:
            raise ClimateResolutionViolation(
                "central heating state must be boolean or unavailable"
            )
        for demand in (self.heating_demand, self.cooling_demand):
            if not isinstance(demand, ClimateDemandState):
                raise ClimateResolutionViolation(
                    "thermal demand state must be approved"
                )
        if not isinstance(self.thermal, ClimateThermalResolution):
            raise ClimateResolutionViolation("thermal resolution must be approved")
        if not isinstance(self.reason, ClimateThermalReason):
            raise ClimateResolutionViolation("thermal reason must be approved")
        if not isinstance(self.temperature_origin, ClimateTemperatureTargetOrigin):
            raise ClimateResolutionViolation(
                "thermal temperature origin must be approved"
            )
        expected = _resolve_thermal_fields(
            heating=self.heating_demand,
            cooling=self.cooling_demand,
            season=self.season,
            occupancy=self.occupancy,
            central_heating_on=self.central_heating_on,
        )
        if (self.thermal, self.reason) != expected:
            raise ClimateResolutionViolation(
                "thermal resolution must match demand and home mode"
            )


@dataclass(frozen=True, slots=True)
class ClimateResolutionSnapshot:
    """Conflict-free thermal directions for one configured contour."""

    contour_id: str
    contour_mode: ContourMode
    rooms: tuple[ClimateRoomThermalResolution, ...]
    version: int = CLIMATE_RESOLUTION_MODEL_VERSION

    def __post_init__(self) -> None:
        _stable_room_id(self.contour_id)
        if not isinstance(self.contour_mode, ContourMode):
            raise ClimateResolutionViolation(
                "resolution contour mode must be approved"
            )
        if type(self.rooms) is not tuple or any(
            not isinstance(room, ClimateRoomThermalResolution)
            for room in self.rooms
        ):
            raise ClimateResolutionViolation(
                "thermal resolutions must be an immutable typed tuple"
            )
        if not self.rooms:
            raise ClimateResolutionViolation(
                "thermal resolutions must not be empty"
            )
        if len(self.rooms) != len({room.room_id for room in self.rooms}):
            raise ClimateResolutionViolation(
                "thermal resolution room ids must be unique"
            )
        if self.version != CLIMATE_RESOLUTION_MODEL_VERSION:
            raise ClimateResolutionViolation(
                "thermal resolution model version is unsupported"
            )

    @property
    def commands_enabled(self) -> bool:
        """Conflict resolution cannot grant execution authority."""

        return False

    def room(self, room_id: str) -> ClimateRoomThermalResolution | None:
        """Return one room resolution by stable HausmanHub id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)


def resolve_climate_room_thermal(
    demand: ClimateRoomDemand,
    home: ClimateHomeObservation,
) -> ClimateRoomThermalResolution:
    """Select one safe thermal direction without selecting a device."""

    if not isinstance(demand, ClimateRoomDemand):
        raise ClimateResolutionViolation(
            "a validated climate room demand is required"
        )
    if not isinstance(home, ClimateHomeObservation):
        raise ClimateResolutionViolation(
            "a validated climate home observation is required"
        )
    thermal, reason = _resolve_thermal_fields(
        heating=demand.heating,
        cooling=demand.cooling,
        season=home.season,
        occupancy=home.occupancy,
        central_heating_on=home.central_heating_on,
    )
    return ClimateRoomThermalResolution(
        room_id=demand.room_id,
        season=home.season,
        occupancy=home.occupancy,
        central_heating_on=home.central_heating_on,
        heating_demand=demand.heating,
        cooling_demand=demand.cooling,
        thermal=thermal,
        reason=reason,
        temperature_origin=demand.temperature_origin,
    )


def _resolve_thermal_fields(
    *,
    heating: ClimateDemandState,
    cooling: ClimateDemandState,
    season: ClimateSeason,
    occupancy: ClimateOccupancyMode,
    central_heating_on: bool | None,
) -> tuple[ClimateThermalResolution, ClimateThermalReason]:
    _thermal_pair(heating, cooling)
    if occupancy is ClimateOccupancyMode.AWAY_SAFE_OFF:
        return (
            ClimateThermalResolution.SAFE_OFF,
            ClimateThermalReason.AWAY_SAFE_OFF,
        )
    if occupancy is ClimateOccupancyMode.AWAY_KEEP:
        return (
            ClimateThermalResolution.OBSERVE,
            ClimateThermalReason.AWAY_KEEP,
        )
    if heating is ClimateDemandState.UNAVAILABLE:
        return (
            ClimateThermalResolution.UNAVAILABLE,
            ClimateThermalReason.THERMAL_DATA_UNAVAILABLE,
        )

    heating_mode = (
        season is ClimateSeason.WINTER or central_heating_on is True
    )
    if heating_mode:
        if cooling is ClimateDemandState.REQUIRED:
            return (
                ClimateThermalResolution.OBSERVE,
                ClimateThermalReason.HEATING_MODE_BLOCKS_COOLING,
            )
        if heating is ClimateDemandState.REQUIRED:
            return (
                ClimateThermalResolution.HEATING,
                ClimateThermalReason.HEATING_REQUIRED,
            )
        return (
            ClimateThermalResolution.HOLD,
            ClimateThermalReason.COMFORTABLE,
        )

    if season is ClimateSeason.SUMMER:
        if heating is ClimateDemandState.REQUIRED:
            return (
                ClimateThermalResolution.HOLD,
                ClimateThermalReason.COOLING_MODE_BLOCKS_HEATING,
            )
    elif heating is ClimateDemandState.REQUIRED:
        return (
            ClimateThermalResolution.HOLD,
            ClimateThermalReason.THERMAL_MODE_UNKNOWN,
        )

    if cooling is ClimateDemandState.REQUIRED:
        return (
            ClimateThermalResolution.COOLING,
            ClimateThermalReason.COOLING_REQUIRED,
        )
    return (
        ClimateThermalResolution.HOLD,
        ClimateThermalReason.COMFORTABLE,
    )


def _thermal_pair(
    heating: ClimateDemandState,
    cooling: ClimateDemandState,
) -> None:
    if not isinstance(heating, ClimateDemandState) or not isinstance(
        cooling,
        ClimateDemandState,
    ):
        raise ClimateResolutionViolation("thermal demand state must be approved")
    unavailable = (
        heating is ClimateDemandState.UNAVAILABLE,
        cooling is ClimateDemandState.UNAVAILABLE,
    )
    if unavailable[0] is not unavailable[1]:
        raise ClimateResolutionViolation(
            "heating and cooling availability must change together"
        )
    if (
        heating is ClimateDemandState.REQUIRED
        and cooling is ClimateDemandState.REQUIRED
    ):
        raise ClimateResolutionViolation(
            "heating and cooling cannot both be selected"
        )


def _stable_room_id(value: object) -> None:
    try:
        ClimateRoom(value, "Room")  # type: ignore[arg-type]
    except ClimateModelViolation as error:
        raise ClimateResolutionViolation(
            "thermal resolution room id must be stable"
        ) from error
