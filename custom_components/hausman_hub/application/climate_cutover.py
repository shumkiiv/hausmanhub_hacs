"""Fail-closed readiness contract for retiring the legacy Node-RED contour."""

from __future__ import annotations

from collections.abc import Mapping

from ..domain.climate import ClimateControlScope, ClimateDeviceKind, ClimateRegistry
from ..domain.climate_bridge import ClimateControlMode
from ..domain.contours import ContourMode, ContourRegistry


CUTOVER_CONTRACT_NAME = "hausman-hub-node-red-cutover"
CUTOVER_CONTRACT_VERSION = 1

_PASSIVE_KINDS = frozenset(
    {
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        ClimateDeviceKind.HUMIDITY_SENSOR,
    }
)
_REASON_ORDER = (
    "contour_not_configured",
    "contour_not_automatic",
    "active_rooms_missing",
    "native_control_disabled",
    "rooms_not_fully_managed",
    "mixed_device_scopes",
    "shadow_evidence_missing",
    "rooms_not_shadow_ready",
)


def climate_cutover_status(
    registry: ClimateRegistry,
    contours: ContourRegistry,
    *,
    bridge_mode: ClimateControlMode,
    shadow_window: object,
) -> dict[str, object]:
    """Return bounded retirement readiness without changing either controller."""

    if not isinstance(registry, ClimateRegistry):
        raise TypeError("validated climate registry is required")
    if not isinstance(contours, ContourRegistry):
        raise TypeError("validated contour registry is required")
    if not isinstance(bridge_mode, ClimateControlMode):
        raise TypeError("validated climate mode is required")

    contour = contours.contour("climate")
    reasons: set[str] = set()
    active_room_ids: set[str] = set()
    managed_room_ids: set[str] = set()
    mixed_room_ids: set[str] = set()
    if contour is None:
        reasons.add("contour_not_configured")
    else:
        if contour.mode is not ContourMode.AUTOMATIC:
            reasons.add("contour_not_automatic")
        devices = {device.device_id: device for device in registry.devices}
        for room in contour.rooms:
            scopes = {
                device.control_scope
                for device_id in room.device_ids
                if (device := devices.get(device_id)) is not None
                and device.kind not in _PASSIVE_KINDS
            }
            if not scopes:
                continue
            active_room_ids.add(room.room_id)
            if scopes == {ClimateControlScope.MANAGED}:
                managed_room_ids.add(room.room_id)
            elif len(scopes) > 1:
                mixed_room_ids.add(room.room_id)

    if not active_room_ids:
        reasons.add("active_rooms_missing")
    if bridge_mode is not ClimateControlMode.MANAGED:
        reasons.add("native_control_disabled")
    if managed_room_ids != active_room_ids:
        reasons.add("rooms_not_fully_managed")
    if mixed_room_ids:
        reasons.add("mixed_device_scopes")

    shadow_ready_room_ids, sample_count = _ready_shadow_rooms(shadow_window)
    if sample_count == 0:
        reasons.add("shadow_evidence_missing")
    pending_shadow_room_ids = active_room_ids - shadow_ready_room_ids
    if pending_shadow_room_ids:
        reasons.add("rooms_not_shadow_ready")

    ready = not reasons
    if ready:
        phase = "ready_to_retire"
    elif bridge_mode is ClimateControlMode.MANAGED and managed_room_ids:
        phase = "native_validation"
    elif sample_count:
        phase = "shadow"
    else:
        phase = "not_ready"
    return {
        "contract": {
            "name": CUTOVER_CONTRACT_NAME,
            "version": CUTOVER_CONTRACT_VERSION,
        },
        "phase": phase,
        "node_red_can_be_disabled": ready,
        "physical_commands_sent": False,
        "active_room_count": len(active_room_ids),
        "native_managed_room_count": len(managed_room_ids),
        "shadow_ready_room_count": len(active_room_ids & shadow_ready_room_ids),
        "shadow_sample_count": sample_count,
        "pending_room_ids": sorted(
            (active_room_ids - managed_room_ids) | pending_shadow_room_ids
        ),
        "reasons": [reason for reason in _REASON_ORDER if reason in reasons],
    }


def _ready_shadow_rooms(shadow_window: object) -> tuple[set[str], int]:
    if not isinstance(shadow_window, Mapping):
        return set(), 0
    summary = shadow_window.get("summary")
    rooms = shadow_window.get("rooms")
    sample_count = summary.get("sample_count") if isinstance(summary, Mapping) else 0
    if type(sample_count) is not int or sample_count < 0 or not isinstance(rooms, list):
        return set(), 0
    return (
        {
            room_id
            for room in rooms
            if isinstance(room, Mapping)
            and isinstance((room_id := room.get("room_id")), str)
            and room.get("verdict") == "ready"
            and room.get("fresh") is True
        },
        sample_count,
    )
