"""Build command-free thermal resolutions from one observation and demand."""

from __future__ import annotations

from ..domain.climate_demand import ClimateDemandSnapshot
from ..domain.climate_observation import ClimateObservationSnapshot
from ..domain.climate_resolution import (
    ClimateResolutionSnapshot,
    ClimateResolutionViolation,
    ClimateRoomThermalResolution,
    resolve_climate_room_thermal,
)
from .climate_demands import climate_reference_demand
from .climate_observations import climate_reference_observation


def build_climate_resolution_snapshot(
    demands: ClimateDemandSnapshot,
    observation: ClimateObservationSnapshot,
) -> ClimateResolutionSnapshot:
    """Resolve every contour room against the same observed home mode."""

    if not isinstance(demands, ClimateDemandSnapshot):
        raise ClimateResolutionViolation(
            "a validated climate demand snapshot is required"
        )
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateResolutionViolation(
            "a validated climate observation snapshot is required"
        )
    observation_room_ids = {room.room_id for room in observation.rooms}
    demand_room_ids = {room.room_id for room in demands.rooms}
    if not demand_room_ids.issubset(observation_room_ids):
        raise ClimateResolutionViolation(
            "every thermal demand requires an observation placeholder"
        )
    rooms: list[ClimateRoomThermalResolution] = []
    for demand in demands.rooms:
        observed = observation.room(demand.room_id)
        if observed is None or (
            demand.observation_status is not observed.data_status
            or demand.temperature_quality is not observed.temperature_quality
            or demand.current_temperature != observed.temperature
            or demand.current_humidity != observed.humidity
        ):
            raise ClimateResolutionViolation(
                "thermal demand and observation must come from one snapshot"
            )
        rooms.append(resolve_climate_room_thermal(demand, observation.home))
    return ClimateResolutionSnapshot(
        contour_id=demands.contour_id,
        contour_mode=demands.contour_mode,
        rooms=tuple(rooms),
    )


def climate_reference_resolution(case_id: str) -> ClimateRoomThermalResolution:
    """Resolve one frozen migration case without equipment or commands."""

    observation = climate_reference_observation(case_id)
    demand = climate_reference_demand(case_id)
    return resolve_climate_room_thermal(demand, observation.home)
