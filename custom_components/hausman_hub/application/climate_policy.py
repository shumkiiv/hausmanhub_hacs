"""Build final command-free room policies from one climate observation."""

from __future__ import annotations

from ..domain.climate_equipment import ClimateEquipmentSnapshot
from ..domain.climate_observation import ClimateObservationSnapshot
from ..domain.climate_policy import (
    ClimatePolicySnapshot,
    ClimatePolicyViolation,
    resolve_climate_room_policy,
)
from ..domain.climate_resolution import ClimateResolutionSnapshot
from ..domain.climate_stability import ClimateStabilitySnapshot
from ..domain.contours import ContourDefinition, ContourKind
from .climate_equipment import climate_reference_equipment
from .climate_observations import climate_reference_observation
from .climate_resolutions import climate_reference_resolution
from .climate_stability import climate_reference_stability


def build_climate_policy_snapshot(
    contour: ContourDefinition,
    resolutions: ClimateResolutionSnapshot,
    equipment: ClimateEquipmentSnapshot,
    stability: ClimateStabilitySnapshot,
    observation: ClimateObservationSnapshot,
) -> ClimatePolicySnapshot:
    """Apply the complete priority ladder to all configured contour rooms."""

    if (
        not isinstance(contour, ContourDefinition)
        or contour.kind is not ContourKind.CLIMATE
    ):
        raise ClimatePolicyViolation("a validated climate contour is required")
    if not isinstance(resolutions, ClimateResolutionSnapshot):
        raise ClimatePolicyViolation("validated thermal resolutions are required")
    if not isinstance(equipment, ClimateEquipmentSnapshot):
        raise ClimatePolicyViolation("validated equipment plans are required")
    if not isinstance(stability, ClimateStabilitySnapshot):
        raise ClimatePolicyViolation("validated stability plans are required")
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimatePolicyViolation("a validated climate observation is required")
    if (
        contour.contour_id != resolutions.contour_id
        or contour.contour_id != equipment.contour_id
        or contour.contour_id != stability.contour_id
        or contour.mode is not resolutions.contour_mode
        or contour.mode is not equipment.contour_mode
        or contour.mode is not stability.contour_mode
    ):
        raise ClimatePolicyViolation("all policy snapshots must describe one contour")
    if stability.observed_at != observation.observed_at:
        raise ClimatePolicyViolation(
            "policy stability and observation must use one observation time"
        )
    contour_room_ids = {room.room_id for room in contour.rooms}
    if (
        contour_room_ids != {room.room_id for room in resolutions.rooms}
        or contour_room_ids != {room.room_id for room in equipment.rooms}
        or contour_room_ids != {room.room_id for room in stability.rooms}
        or not contour_room_ids.issubset(
            {room.room_id for room in observation.rooms}
        )
    ):
        raise ClimatePolicyViolation("policy planning requires matching rooms")

    rooms = []
    for contour_room in contour.rooms:
        observed_room = observation.room(contour_room.room_id)
        resolution = resolutions.room(contour_room.room_id)
        equipment_room = equipment.room(contour_room.room_id)
        stability_room = stability.room(contour_room.room_id)
        if any(
            item is None
            for item in (observed_room, resolution, equipment_room, stability_room)
        ):
            raise ClimatePolicyViolation("one or more policy room inputs are missing")
        selected = tuple(
            observation.device(device_id) for device_id in contour_room.device_ids
        )
        if any(
            device is None or device.room_id != contour_room.room_id
            for device in selected
        ):
            raise ClimatePolicyViolation(
                "configured policy devices must match the observation"
            )
        rooms.append(
            resolve_climate_room_policy(
                observed_room,  # type: ignore[arg-type]
                observation.home,
                observation.control,
                resolution,  # type: ignore[arg-type]
                equipment_room,  # type: ignore[arg-type]
                stability_room,  # type: ignore[arg-type]
                selected,  # type: ignore[arg-type]
                contour_mode=contour.mode,
                observed_at=observation.observed_at,
            )
        )
    return ClimatePolicySnapshot(
        contour_id=contour.contour_id,
        contour_mode=contour.mode,
        observed_at=observation.observed_at,
        rooms=tuple(rooms),
    )


def climate_reference_policy(case_id: str):
    """Apply the final policy ladder to one frozen migration case."""

    observation = climate_reference_observation(case_id)
    return resolve_climate_room_policy(
        observation.rooms[0],
        observation.home,
        observation.control,
        climate_reference_resolution(case_id),
        climate_reference_equipment(case_id),
        climate_reference_stability(case_id),
        observation.devices,
        observed_at=observation.observed_at,
    )
