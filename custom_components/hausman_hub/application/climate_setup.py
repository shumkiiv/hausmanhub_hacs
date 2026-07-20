"""Stable, source-independent contracts for configuring climate in HASC."""

from __future__ import annotations

import hashlib
import json

from ..domain.climate import ClimateRegistry
from .climate_import import ClimateImportSnapshot
from .public_climate_values import public_climate_display_names


CLIMATE_ROOMS_CONTRACT_NAME = "hausman-hasc-climate-rooms"
CLIMATE_ROOMS_CONTRACT_VERSION = 1
MAX_AVAILABLE_CLIMATE_ROOMS = 256
CLIMATE_DEVICE_CANDIDATES_CONTRACT_NAME = "hausman-hasc-climate-device-candidates"
CLIMATE_DEVICE_CANDIDATES_CONTRACT_VERSION = 1
MAX_CLIMATE_DEVICE_CANDIDATES = 1024
JSON_SAFE_INTEGER_MAXIMUM = 9_007_199_254_740_991

_ROOM_STATUS_NAMES = {
    "available": "Можно выбрать",
    "data_stale": "Нужно обновить данные",
    "source_missing": "Комната больше не найдена",
}
_CANDIDATE_STATUS_NAMES = {
    "available": "Можно добавить",
    "already_configured": "Уже добавлено",
    "unavailable": "Устройство недоступно",
    "unsupported": "Тип устройства не поддерживается",
    "data_stale": "Нужно обновить данные",
    "source_missing": "Устройство больше не найдено",
    "registry_mismatch": "Нужно проверить привязку устройства",
}


def climate_available_rooms(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> dict[str, object]:
    """Return every discovered or configured room using only stable HASC IDs."""

    if not isinstance(registry, ClimateRegistry):
        raise ValueError("climate room registry must be valid")
    if not isinstance(snapshot, ClimateImportSnapshot):
        raise ValueError("climate room snapshot must be valid")

    configured = {room.room_id: room for room in registry.rooms}
    imported = {room.room_id: room for room in snapshot.rooms}
    room_ids = configured.keys() | imported.keys()
    if len(room_ids) > MAX_AVAILABLE_CLIMATE_ROOMS:
        raise ValueError("too many available climate rooms")
    rooms: list[dict[str, object]] = []
    for room_id in sorted(room_ids):
        configured_room = configured.get(room_id)
        imported_room = imported.get(room_id)
        if not snapshot.runtime_fresh:
            status = "data_stale"
        elif imported_room is None:
            status = "source_missing"
        else:
            status = "available"
        if configured_room is not None:
            room_name = configured_room.name
        elif imported_room is not None:
            room_name = imported_room.name
        else:  # pragma: no cover - the room id came from one of these mappings
            raise ValueError("available climate room has no source")
        rooms.append(
            {
                "id": room_id,
                "name": room_name,
                "configured": configured_room is not None,
                "selectable": status == "available",
                "status": status,
            }
        )

    return {
        "contract": {
            "name": CLIMATE_ROOMS_CONTRACT_NAME,
            "version": CLIMATE_ROOMS_CONTRACT_VERSION,
        },
        "generated_at": snapshot.generated_at,
        "data_status": "current" if snapshot.runtime_fresh else "stale",
        "selection_allowed": any(room["selectable"] is True for room in rooms),
        "display_names": {"room_status": dict(_ROOM_STATUS_NAMES)},
        "rooms": rooms,
    }


def climate_device_candidates(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> dict[str, object]:
    """Return bounded device choices without exposing their private bindings."""

    if not isinstance(registry, ClimateRegistry):
        raise ValueError("climate device registry must be valid")
    if not isinstance(snapshot, ClimateImportSnapshot):
        raise ValueError("climate device snapshot must be valid")

    configured = {device.source_id: device for device in registry.devices}
    imported = {device.source_id: device for device in snapshot.devices}
    source_ids = configured.keys() | imported.keys()
    if len(source_ids) > MAX_CLIMATE_DEVICE_CANDIDATES:
        raise ValueError("too many climate device candidates")

    def sort_key(source_id: str) -> tuple[str, str, str]:
        configured_device = configured.get(source_id)
        imported_device = imported.get(source_id)
        room_id = (
            configured_device.room_id
            if configured_device is not None
            else imported_device.room_id  # type: ignore[union-attr]
        )
        name = (
            configured_device.name
            if configured_device is not None
            else imported_device.name  # type: ignore[union-attr]
        )
        return (room_id, name.casefold(), source_id)

    candidates: list[dict[str, object]] = []
    private_revision_values: list[dict[str, object]] = []
    for index, source_id in enumerate(sorted(source_ids, key=sort_key), start=1):
        configured_device = configured.get(source_id)
        imported_device = imported.get(source_id)
        if configured_device is not None:
            name = configured_device.name
            room_id = (
                imported_device.room_id
                if imported_device is not None
                else configured_device.room_id
            )
        elif imported_device is not None:
            name = imported_device.name
            room_id = imported_device.room_id
        else:  # pragma: no cover - the source id came from one of these mappings
            raise ValueError("climate device candidate has no source")

        suggested_types = (
            []
            if imported_device is None
            else [kind.value for kind in imported_device.suggested_kinds]
        )
        configured_type = (
            None if configured_device is None else configured_device.kind.value
        )
        available = bool(
            snapshot.runtime_fresh
            and imported_device is not None
            and imported_device.available
        )
        if not snapshot.runtime_fresh:
            status = "data_stale"
        elif imported_device is None:
            status = "source_missing"
        elif configured_device is not None and (
            configured_device.room_id != imported_device.room_id
            or configured_device.kind not in imported_device.suggested_kinds
        ):
            status = "registry_mismatch"
        elif not suggested_types:
            status = "unsupported"
        elif not imported_device.available:
            status = "unavailable"
        elif configured_device is not None:
            status = "already_configured"
        else:
            status = "available"

        candidates.append(
            {
                "candidate_id": f"candidate_{index:04d}",
                "name": name,
                "room_id": room_id,
                "available": available,
                "configured": configured_device is not None,
                "configured_device_id": (
                    None
                    if configured_device is None
                    else configured_device.device_id
                ),
                "configured_room_id": (
                    None if configured_device is None else configured_device.room_id
                ),
                "configured_type": configured_type,
                "suggested_types": suggested_types,
                "recommended_type": (
                    suggested_types[0] if suggested_types else None
                ),
                "selectable": status == "available",
                "status": status,
            }
        )
        private_revision_values.append(
            {
                "source_id": source_id,
                "configured_device_id": (
                    None
                    if configured_device is None
                    else configured_device.device_id
                ),
                "configured_room_id": (
                    None if configured_device is None else configured_device.room_id
                ),
                "configured_type": configured_type,
                "imported": (
                    None
                    if imported_device is None
                    else {
                        "name": imported_device.name,
                        "room_id": imported_device.room_id,
                        "available": imported_device.available,
                        "command_types": list(imported_device.command_types),
                        "suggested_types": suggested_types,
                    }
                ),
            }
        )

    device_kind_names = public_climate_display_names()["device_kinds"]
    return {
        "contract": {
            "name": CLIMATE_DEVICE_CANDIDATES_CONTRACT_NAME,
            "version": CLIMATE_DEVICE_CANDIDATES_CONTRACT_VERSION,
        },
        "generated_at": snapshot.generated_at,
        "snapshot_revision": _json_safe_revision(
            {
                "fresh": snapshot.runtime_fresh,
                "candidates": private_revision_values,
            }
        ),
        "data_status": "current" if snapshot.runtime_fresh else "stale",
        "selection_allowed": any(
            candidate["selectable"] is True for candidate in candidates
        ),
        "display_names": {
            "device_types": dict(device_kind_names),
            "candidate_status": dict(_CANDIDATE_STATUS_NAMES),
        },
        "candidates": candidates,
    }


def _json_safe_revision(value: object) -> int:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).digest()
    return int.from_bytes(digest[:8], "big") % (JSON_SAFE_INTEGER_MAXIMUM + 1)
