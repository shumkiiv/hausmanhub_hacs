"""Pure rollout gate from command-free shadow evidence to one canary room."""

from __future__ import annotations

from collections.abc import Mapping

from ..domain.climate import (
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateRegistry,
)
from ..domain.climate_bridge import ClimateControlMode
from ..domain.contours import ContourMode, ContourRegistry


_PASSIVE_KINDS = frozenset(
    {
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        ClimateDeviceKind.HUMIDITY_SENSOR,
    }
)
_REASON_ORDER = (
    "contour_not_configured",
    "contour_not_automatic",
    "canary_room_not_selected",
    "multiple_canary_rooms",
    "managed_scope_already_present",
    "mixed_device_scopes",
    "shadow_evidence_missing",
    "shadow_evidence_not_ready",
)


def climate_rollout_status(
    registry: ClimateRegistry,
    contours: ContourRegistry,
    *,
    bridge_mode: ClimateControlMode,
    shadow_window: object,
) -> dict[str, object]:
    """Return a redacted fail-closed rollout status without creating commands."""

    if not isinstance(registry, ClimateRegistry):
        raise TypeError("validated climate registry is required")
    if not isinstance(contours, ContourRegistry):
        raise TypeError("validated contour registry is required")
    if not isinstance(bridge_mode, ClimateControlMode):
        raise TypeError("validated climate mode is required")

    contour = contours.contour("climate")
    reasons: set[str] = set()
    if contour is None:
        reasons.add("contour_not_configured")
        room_scopes: dict[str, set[ClimateControlScope]] = {}
    else:
        if contour.mode is not ContourMode.AUTOMATIC:
            reasons.add("contour_not_automatic")
        room_scopes = {}
        for room in contour.rooms:
            device_ids = set(room.device_ids)
            scopes = {
                device.control_scope
                for device in registry.devices
                if device.device_id in device_ids and device.kind not in _PASSIVE_KINDS
            }
            if scopes:
                room_scopes[room.room_id] = scopes

    canary_rooms = tuple(
        sorted(
            room_id
            for room_id, scopes in room_scopes.items()
            if scopes == {ClimateControlScope.CANARY}
        )
    )
    managed_rooms = tuple(
        sorted(
            room_id
            for room_id, scopes in room_scopes.items()
            if scopes == {ClimateControlScope.MANAGED}
        )
    )
    mixed_rooms = tuple(
        sorted(
            room_id
            for room_id, scopes in room_scopes.items()
            if len(scopes) > 1
        )
    )
    if not canary_rooms:
        reasons.add("canary_room_not_selected")
    elif len(canary_rooms) > 1:
        reasons.add("multiple_canary_rooms")
    if managed_rooms and bridge_mode is ClimateControlMode.DISABLED:
        reasons.add("managed_scope_already_present")
    if mixed_rooms:
        reasons.add("mixed_device_scopes")

    ready_shadow_rooms, sample_count = _ready_shadow_rooms(shadow_window)
    if sample_count == 0:
        reasons.add("shadow_evidence_missing")
    canary_room_id = canary_rooms[0] if len(canary_rooms) == 1 else None
    if canary_room_id is not None and canary_room_id not in ready_shadow_rooms:
        reasons.add("shadow_evidence_not_ready")

    enable_blockers = {
        "contour_not_configured",
        "contour_not_automatic",
        "canary_room_not_selected",
        "multiple_canary_rooms",
        "managed_scope_already_present",
        "mixed_device_scopes",
        "shadow_evidence_missing",
        "shadow_evidence_not_ready",
    }
    enable_allowed = (
        bridge_mode is ClimateControlMode.DISABLED
        and not (reasons & enable_blockers)
    )
    commands_enabled = bridge_mode is ClimateControlMode.MANAGED and bool(
        canary_rooms or managed_rooms
    )
    if bridge_mode is ClimateControlMode.MANAGED and managed_rooms:
        phase = "managed"
    elif bridge_mode is ClimateControlMode.MANAGED and canary_room_id is not None:
        phase = "canary"
    elif enable_allowed:
        phase = "ready_for_canary"
    elif contour is None:
        phase = "not_configured"
    else:
        phase = "shadow"
    return {
        "phase": phase,
        "enable_allowed": enable_allowed,
        "commands_enabled": commands_enabled,
        "canary_room_id": canary_room_id,
        "managed_room_count": len(managed_rooms),
        "shadow_ready_room_count": len(ready_shadow_rooms),
        "shadow_sample_count": sample_count,
        "reasons": [reason for reason in _REASON_ORDER if reason in reasons],
    }


def _ready_shadow_rooms(shadow_window: object) -> tuple[frozenset[str], int]:
    if not isinstance(shadow_window, Mapping):
        return frozenset(), 0
    summary = shadow_window.get("summary")
    rooms = shadow_window.get("rooms")
    sample_count = summary.get("sample_count") if isinstance(summary, Mapping) else 0
    if type(sample_count) is not int or sample_count < 0 or not isinstance(rooms, list):
        return frozenset(), 0
    ready: set[str] = set()
    for room in rooms:
        if not isinstance(room, Mapping):
            continue
        room_id = room.get("room_id")
        if (
            isinstance(room_id, str)
            and room.get("verdict") == "ready"
            and room.get("fresh") is True
        ):
            ready.add(room_id)
    return frozenset(ready), sample_count
