"""Build command-free room targets from HausmanHub contour settings."""

from __future__ import annotations

from ..domain.climate_observation import ClimateDayPeriod, ClimateObservationSnapshot
from ..domain.climate_targets import (
    ClimateRoomTarget,
    ClimateRoomTargetPolicy,
    ClimateTargetSnapshot,
    ClimateTargetViolation,
    resolve_climate_room_target,
)
from ..domain.contours import (
    ClimateComfortSettings,
    ClimateProfile,
    ClimateStrategy,
    ContourDefinition,
    ContourKind,
)
from .climate_observations import climate_reference_observation


def build_climate_target_snapshot(
    contour: ContourDefinition,
    observation: ClimateObservationSnapshot,
) -> ClimateTargetSnapshot:
    """Resolve saved profiles and overrides for every configured contour room."""

    if not isinstance(contour, ContourDefinition) or contour.kind is not ContourKind.CLIMATE:
        raise ClimateTargetViolation("a validated climate contour is required")
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateTargetViolation("a validated climate observation is required")
    rooms = tuple(
        resolve_climate_room_target(
            ClimateRoomTargetPolicy(
                room_id=room.room_id,
                day_profile=room.day_profile,
                night_profile=room.night_profile,
                active_profile=room.active_profile,
                temporary_override=room.temporary_override,
            ),
            observation.room(room.room_id),
            observed_at=observation.observed_at,
            occupancy=observation.home.occupancy,
        )
        for room in contour.rooms
    )
    return ClimateTargetSnapshot(
        contour_id=contour.contour_id,
        contour_mode=contour.mode,
        rooms=rooms,
    )


def climate_reference_target(case_id: str) -> ClimateRoomTarget:
    """Resolve the exact comfort target of one frozen migration case."""

    observation = climate_reference_observation(case_id)
    room = observation.rooms[0]
    if (
        room.observed_target_temperature is None
        or room.observed_target_humidity is None
        or not room.observed_target_humidity.is_integer()
    ):
        raise ClimateTargetViolation("reference room target is incomplete")
    if observation.home.period is ClimateDayPeriod.DAY:
        active_profile = ClimateProfile.DAY
    elif observation.home.period is ClimateDayPeriod.NIGHT:
        active_profile = ClimateProfile.NIGHT
    else:
        raise ClimateTargetViolation("reference target period is unavailable")
    settings = ClimateComfortSettings(
        target_temperature=room.observed_target_temperature,
        target_humidity=int(room.observed_target_humidity),
        strategy=ClimateStrategy.NORMAL,
    )
    return resolve_climate_room_target(
        ClimateRoomTargetPolicy(
            room_id=room.room_id,
            day_profile=settings,
            night_profile=settings,
            active_profile=active_profile,
        ),
        room,
        observed_at=observation.observed_at,
    )
