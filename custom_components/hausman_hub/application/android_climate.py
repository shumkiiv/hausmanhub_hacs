"""Public Android and private administrator climate projections.

The normal tablet contract deliberately contains only stable HausmanHub identifiers.
Private Climate API device identifiers and Home Assistant entity bindings are
available only through the separate administrator projection.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from ..domain.climate import (
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateRegistry,
)
from ..domain.climate_bridge import ClimateBridgeMode
from ..domain.contours import ContourRegistry
from .android_climate_values import (
    ANDROID_CLIMATE_CONTRACT_NAME,
    ANDROID_CLIMATE_CONTRACT_VERSION,
    ANDROID_ROOM_CONTROL_ACTIONS,
    ROOM_ACTION_COMMAND_TYPES as _ROOM_ACTION_COMMAND_TYPES,
    public_state_revision as _public_state_revision,
    room_action_availability as _room_action_availability,
    room_action_inputs as _room_action_inputs,
    room_action_presentations as _room_action_presentations,
    saved_profiles_by_room as _saved_profiles_by_room,
)
from .climate_import import ClimateImportSnapshot
from .climate_registry import reconcile_climate_registry
from .contours import contour_snapshot
from .public_climate_values import (
    public_climate_display_names,
    public_device_state,
    public_room_data_status,
    public_room_mode,
    public_strategy,
)


def android_climate_snapshot(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    *,
    contours: ContourRegistry | None = None,
    bridge_mode: ClimateBridgeMode,
    canary_room_id: str | None = None,
    candidate_ready: bool = False,
    pending_room_ids: Collection[str] = (),
    local_now: datetime | None = None,
) -> dict[str, object]:
    """Build the fixed tablet contract without any private source binding."""

    if not isinstance(bridge_mode, ClimateBridgeMode):
        raise ValueError("climate bridge mode must be approved")
    pending = frozenset(pending_room_ids)
    if any(registry.room(room_id) is None for room_id in pending):
        raise ValueError("pending climate rooms must be registered")
    reconciliation = reconcile_climate_registry(registry, snapshot)
    imported_by_source = {device.source_id: device for device in snapshot.devices}
    devices_by_room: dict[str, list[dict[str, object]]] = {
        room.room_id: [] for room in registry.rooms
    }
    for device in registry.devices:
        imported = imported_by_source.get(device.source_id)
        exact_match = imported is not None and imported.room_id == device.room_id
        devices_by_room[device.room_id].append(
            {
                "id": device.device_id,
                "name": device.name,
                "kind": device.kind.value,
                "control_scope": device.control_scope.value,
                "capabilities": [value.value for value in device.capabilities],
                "available": bool(exact_match and imported.available),
                "state": public_device_state(
                    None if imported is None else imported.state,
                    available=bool(exact_match and imported.available),
                ),
            }
        )

    public_contours = contour_snapshot(
        contours or ContourRegistry(),
        registry,
        snapshot,
        settings_apply_enabled=(bridge_mode is ClimateBridgeMode.MANAGED),
        local_now=local_now,
    )["contours"]
    saved_profiles_by_room = _saved_profiles_by_room(public_contours)

    rooms: list[dict[str, object]] = []
    room_control_enabled = False
    for room in registry.rooms:
        imported = snapshot.room(room.room_id)
        public_mode = public_room_mode(None if imported is None else imported.mode)
        control = _room_control_projection(
            registry,
            snapshot,
            bridge_mode=bridge_mode,
            canary_room_id=canary_room_id,
            candidate_ready=candidate_ready,
            pending=room.room_id in pending,
            room_id=room.room_id,
        )
        room_control_enabled = room_control_enabled or control["enabled"] is True
        rooms.append(
            {
                "id": room.room_id,
                "name": room.name,
                "temperature": imported.temperature if imported else None,
                "humidity": imported.humidity if imported else None,
                "target_temperature": (
                    imported.target_temperature if imported else None
                ),
                "target_humidity": imported.target_humidity if imported else None,
                "mode": public_mode,
                "active_target": {
                    "temperature": (
                        imported.target_temperature if imported else None
                    ),
                    "humidity": imported.target_humidity if imported else None,
                    "strategy": public_strategy(
                        None if imported is None else imported.target_strategy
                    ),
                },
                "saved_profiles": saved_profiles_by_room.get(
                    room.room_id,
                    {
                        "active": None,
                        "day": None,
                        "night": None,
                    },
                ),
                "actual": {
                    "data_status": public_room_data_status(
                        present=imported is not None,
                        fresh=snapshot.runtime_fresh,
                    ),
                    "temperature": imported.temperature if imported else None,
                    "humidity": imported.humidity if imported else None,
                    "mode": public_mode,
                },
                "authority_eligible": bool(
                    imported is not None and imported.authority_eligible
                ),
                "control": control,
                "devices": devices_by_room[room.room_id],
            }
        )

    result: dict[str, object] = {
        "contract": {
            "name": ANDROID_CLIMATE_CONTRACT_NAME,
            "version": ANDROID_CLIMATE_CONTRACT_VERSION,
        },
        "generated_at": snapshot.generated_at,
        "climate": {
            "fresh": snapshot.runtime_fresh,
            "commands_enabled": room_control_enabled,
        },
        "display_names": public_climate_display_names(),
        "rooms": rooms,
        "contours": public_contours,
        "reconciliation": {
            "matches": reconciliation.matches,
            "matched_device_ids": list(reconciliation.matched_device_ids),
            "missing_device_ids": list(reconciliation.missing_device_ids),
            "room_mismatch_device_ids": list(
                reconciliation.room_mismatch_device_ids
            ),
            "unregistered_device_count": len(
                reconciliation.unregistered_source_ids
            ),
        },
    }
    result["state_revision"] = _public_state_revision(result)
    return result


def _room_control_projection(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    *,
    bridge_mode: ClimateBridgeMode,
    canary_room_id: str | None,
    candidate_ready: bool,
    pending: bool,
    room_id: str,
) -> dict[str, object]:
    """Project only coarse gates used to enable the first room controls."""

    reasons: list[str] = []
    if bridge_mode is ClimateBridgeMode.DISABLED:
        reasons.append("bridge_disabled")
    elif bridge_mode is ClimateBridgeMode.SHADOW:
        reasons.append("shadow_only")
    elif room_id != canary_room_id:
        reasons.append("room_not_selected")

    if not snapshot.runtime_fresh:
        reasons.append("state_stale")
    imported_room = snapshot.room(room_id)
    if imported_room is None:
        reasons.append("registry_mismatch")
    elif not imported_room.authority_eligible:
        reasons.append("authority_not_ready")

    controlled = [
        device
        for device in registry.devices
        if device.room_id == room_id
        and device.kind is ClimateDeviceKind.AIR_CONDITIONER
        and device.control_owner is ClimateControlOwner.CLIMATE_CORE
        and device.control_scope is not ClimateControlScope.OBSERVED
    ]
    actions: list[str] = []
    if len(controlled) != 1:
        reasons.append("actions_unsupported")
    else:
        imported_device = snapshot.device(controlled[0].source_id)
        if imported_device is None or imported_device.room_id != room_id:
            reasons.append("registry_mismatch")
        else:
            actions = [
                action
                for action in ANDROID_ROOM_CONTROL_ACTIONS
                if _ROOM_ACTION_COMMAND_TYPES[action]
                in imported_device.command_types
            ]
            if not imported_device.available:
                reasons.append("device_unavailable")
            if tuple(actions) != ANDROID_ROOM_CONTROL_ACTIONS:
                reasons.append("actions_unsupported")

    if (
        bridge_mode is ClimateBridgeMode.CANARY
        and room_id == canary_room_id
        and not candidate_ready
    ):
        reasons.append("evidence_not_ready")
    if pending:
        reasons.append("operation_pending")

    blocked_reasons = list(dict.fromkeys(reasons))
    allowed_actions = list(actions) if not blocked_reasons else []
    return {
        "enabled": bool(allowed_actions),
        "actions": actions,
        "allowed_actions": allowed_actions,
        "action_availability": _room_action_availability(
            actions,
            allowed_actions,
            blocked_reasons,
        ),
        "action_inputs": _room_action_inputs(actions),
        "action_presentations": _room_action_presentations(actions),
        "blocked_reasons": blocked_reasons,
    }


def admin_climate_import_snapshot(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> dict[str, object]:
    """Build the local-administrator discovery and reconciliation payload."""

    reconciliation = reconcile_climate_registry(registry, snapshot)
    return {
        "generated_at": snapshot.generated_at,
        "fresh": snapshot.runtime_fresh,
        "rooms": [
            {
                "id": room.room_id,
                "name": room.name,
                "authority_eligible": room.authority_eligible,
            }
            for room in snapshot.rooms
        ],
        "candidates": [
            {
                "source_id": device.source_id,
                "name": device.name,
                "room_id": device.room_id,
                "available": device.available,
                "command_types": list(device.command_types),
                "suggested_kinds": [value.value for value in device.suggested_kinds],
            }
            for device in snapshot.devices
        ],
        "reconciliation": {
            "matches": reconciliation.matches,
            "matched_device_ids": list(reconciliation.matched_device_ids),
            "missing_device_ids": list(reconciliation.missing_device_ids),
            "room_mismatch_device_ids": list(
                reconciliation.room_mismatch_device_ids
            ),
            "unregistered_source_ids": list(
                reconciliation.unregistered_source_ids
            ),
        },
    }
