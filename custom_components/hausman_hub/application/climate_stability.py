"""Build protected, command-free climate plans from one observation."""

from __future__ import annotations

from ..domain.climate_equipment import ClimateEquipmentSnapshot
from ..domain.climate_observation import (
    ClimateDeviceActivity,
    ClimateObservationDeviceKind,
    ClimateObservationSnapshot,
)
from ..domain.climate_stability import (
    ClimateRoomStabilityPlan,
    ClimateStabilitySnapshot,
    ClimateStabilityViolation,
    resolve_climate_stability_plan,
)
from ..domain.climate_targets import ClimateTargetSnapshot
from ..domain.contours import ContourDefinition, ContourKind
from .climate_equipment import climate_reference_equipment
from .climate_observations import climate_reference_observation
from .climate_targets import climate_reference_target


_STABILITY_KINDS = frozenset(
    {
        ClimateObservationDeviceKind.AIR_CONDITIONER,
        ClimateObservationDeviceKind.HUMIDIFIER,
    }
)
_ACTIVE_COOLING = frozenset(
    {ClimateDeviceActivity.RUNNING, ClimateDeviceActivity.COOLING}
)


def build_climate_stability_snapshot(
    contour: ContourDefinition,
    targets: ClimateTargetSnapshot,
    equipment: ClimateEquipmentSnapshot,
    observation: ClimateObservationSnapshot,
) -> ClimateStabilitySnapshot:
    """Apply hysteresis and cycle timing to explicitly selected devices."""

    if (
        not isinstance(contour, ContourDefinition)
        or contour.kind is not ContourKind.CLIMATE
    ):
        raise ClimateStabilityViolation("a validated climate contour is required")
    if not isinstance(targets, ClimateTargetSnapshot):
        raise ClimateStabilityViolation("validated climate targets are required")
    if not isinstance(equipment, ClimateEquipmentSnapshot):
        raise ClimateStabilityViolation("validated equipment plans are required")
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateStabilityViolation("validated climate observation is required")
    if (
        contour.contour_id != targets.contour_id
        or contour.contour_id != equipment.contour_id
        or contour.mode is not targets.contour_mode
        or contour.mode is not equipment.contour_mode
    ):
        raise ClimateStabilityViolation(
            "contour, targets, and equipment must describe one contour"
        )
    if targets.observed_at != observation.observed_at:
        raise ClimateStabilityViolation(
            "stability targets and observation must use one observation time"
        )
    contour_room_ids = {room.room_id for room in contour.rooms}
    if (
        contour_room_ids != {room.room_id for room in targets.rooms}
        or contour_room_ids != {room.room_id for room in equipment.rooms}
        or not contour_room_ids.issubset(
            {room.room_id for room in observation.rooms}
        )
    ):
        raise ClimateStabilityViolation(
            "stability planning requires matching room snapshots"
        )

    rooms: list[ClimateRoomStabilityPlan] = []
    for contour_room in contour.rooms:
        target = targets.room(contour_room.room_id)
        equipment_room = equipment.room(contour_room.room_id)
        observed_room = observation.room(contour_room.room_id)
        if target is None or equipment_room is None or observed_room is None:
            raise ClimateStabilityViolation("stability room inputs are missing")
        if target.observation_status is not observed_room.data_status:
            raise ClimateStabilityViolation(
                "stability target and room must come from one observation"
            )
        selected = tuple(
            observation.device(device_id) for device_id in contour_room.device_ids
        )
        if any(
            device is None or device.room_id != contour_room.room_id
            for device in selected
        ):
            raise ClimateStabilityViolation(
                "configured stability devices must match the observation"
            )
        cooling_active = any(
            device is not None
            and device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER
            and device.activity in _ACTIVE_COOLING
            for device in selected
        )
        devices = []
        for device in selected:
            if device is None or device.kind not in _STABILITY_KINDS:
                continue
            base = (
                equipment_room.device(device.device_id)
                if device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER
                else None
            )
            if (
                device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER
                and base is None
            ):
                raise ClimateStabilityViolation(
                    "selected air conditioner has no base equipment plan"
                )
            devices.append(
                resolve_climate_stability_plan(
                    device,
                    observed_room,
                    target,
                    observation.home,
                    observed_at=observation.observed_at,
                    base=base,
                    cooling_active=cooling_active,
                )
            )
        rooms.append(
            ClimateRoomStabilityPlan(
                room_id=contour_room.room_id,
                devices=tuple(devices),
            )
        )
    return ClimateStabilitySnapshot(
        contour_id=contour.contour_id,
        contour_mode=contour.mode,
        observed_at=observation.observed_at,
        rooms=tuple(rooms),
    )


def climate_reference_stability(case_id: str) -> ClimateRoomStabilityPlan:
    """Apply stable device timing to one frozen migration case."""

    observation = climate_reference_observation(case_id)
    target = climate_reference_target(case_id)
    equipment = climate_reference_equipment(case_id)
    cooling_active = any(
        device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER
        and device.activity in _ACTIVE_COOLING
        for device in observation.devices
    )
    devices = tuple(
        resolve_climate_stability_plan(
            device,
            observation.rooms[0],
            target,
            observation.home,
            observed_at=observation.observed_at,
            base=(
                equipment.device(device.device_id)
                if device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER
                else None
            ),
            cooling_active=cooling_active,
        )
        for device in observation.devices
        if device.kind in _STABILITY_KINDS
    )
    return ClimateRoomStabilityPlan(room_id=target.room_id, devices=devices)
