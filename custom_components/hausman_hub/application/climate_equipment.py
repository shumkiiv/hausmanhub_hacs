"""Build command-free thermal-equipment plans from one internal snapshot."""

from __future__ import annotations

from ..domain.climate_equipment import (
    ClimateEquipmentSnapshot,
    ClimateEquipmentViolation,
    ClimateRoomEquipmentPlan,
    resolve_climate_device_plan,
)
from ..domain.climate_observation import (
    ClimateObservationDeviceKind,
    ClimateObservationSnapshot,
)
from ..domain.climate_resolution import ClimateResolutionSnapshot
from ..domain.climate_targets import ClimateTargetSnapshot
from ..domain.contours import ContourDefinition, ContourKind
from .climate_observations import climate_reference_observation
from .climate_resolutions import climate_reference_resolution
from .climate_targets import climate_reference_target


_THERMAL_KINDS = frozenset(
    {
        ClimateObservationDeviceKind.AIR_CONDITIONER,
        ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
        ClimateObservationDeviceKind.FLOOR_HEATING,
    }
)


def build_climate_equipment_snapshot(
    contour: ContourDefinition,
    targets: ClimateTargetSnapshot,
    resolutions: ClimateResolutionSnapshot,
    observation: ClimateObservationSnapshot,
) -> ClimateEquipmentSnapshot:
    """Plan configured thermal equipment without creating executable data."""

    if (
        not isinstance(contour, ContourDefinition)
        or contour.kind is not ContourKind.CLIMATE
    ):
        raise ClimateEquipmentViolation("a validated climate contour is required")
    if not isinstance(targets, ClimateTargetSnapshot):
        raise ClimateEquipmentViolation(
            "a validated climate target snapshot is required"
        )
    if not isinstance(resolutions, ClimateResolutionSnapshot):
        raise ClimateEquipmentViolation(
            "a validated thermal resolution snapshot is required"
        )
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateEquipmentViolation(
            "a validated climate observation snapshot is required"
        )
    if (
        contour.contour_id != targets.contour_id
        or contour.mode is not targets.contour_mode
        or resolutions.contour_id != targets.contour_id
        or resolutions.contour_mode is not targets.contour_mode
    ):
        raise ClimateEquipmentViolation(
            "contour, targets, and thermal resolutions must match"
        )
    target_room_ids = {room.room_id for room in targets.rooms}
    resolution_room_ids = {room.room_id for room in resolutions.rooms}
    observation_room_ids = {room.room_id for room in observation.rooms}
    contour_room_ids = {room.room_id for room in contour.rooms}
    if (
        target_room_ids != contour_room_ids
        or target_room_ids != resolution_room_ids
        or not target_room_ids.issubset(observation_room_ids)
    ):
        raise ClimateEquipmentViolation(
            "equipment planning requires matching room snapshots"
        )

    rooms: list[ClimateRoomEquipmentPlan] = []
    for target in targets.rooms:
        contour_room = next(
            room for room in contour.rooms if room.room_id == target.room_id
        )
        resolution = resolutions.room(target.room_id)
        observed_room = observation.room(target.room_id)
        if resolution is None or observed_room is None:
            raise ClimateEquipmentViolation(
                "equipment room inputs must not be missing"
            )
        if target.observation_status is not observed_room.data_status:
            raise ClimateEquipmentViolation(
                "equipment target and observation must come from one snapshot"
            )
        selected_devices = tuple(
            observation.device(device_id) for device_id in contour_room.device_ids
        )
        if any(
            device is None or device.room_id != target.room_id
            for device in selected_devices
        ):
            raise ClimateEquipmentViolation(
                "configured equipment must match the observation snapshot"
            )
        devices = tuple(
            resolve_climate_device_plan(
                device,
                target,
                resolution,
                observation.home,
            )
            for device in selected_devices
            if device is not None and device.kind in _THERMAL_KINDS
        )
        rooms.append(
            ClimateRoomEquipmentPlan(
                room_id=target.room_id,
                thermal=resolution.thermal,
                devices=devices,
            )
        )
    return ClimateEquipmentSnapshot(
        contour_id=targets.contour_id,
        contour_mode=targets.contour_mode,
        rooms=tuple(rooms),
    )


def climate_reference_equipment(case_id: str) -> ClimateRoomEquipmentPlan:
    """Plan thermal devices for one frozen migration case."""

    observation = climate_reference_observation(case_id)
    target = climate_reference_target(case_id)
    resolution = climate_reference_resolution(case_id)
    devices = tuple(
        resolve_climate_device_plan(device, target, resolution, observation.home)
        for device in observation.devices
        if device.kind in _THERMAL_KINDS
    )
    return ClimateRoomEquipmentPlan(
        room_id=target.room_id,
        thermal=resolution.thermal,
        devices=devices,
    )
