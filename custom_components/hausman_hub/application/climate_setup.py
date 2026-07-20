"""Stable, source-independent contracts for configuring climate in HausmanHub."""

from __future__ import annotations

import hashlib
import json

from ..domain.climate import ClimateRegistry
from ..domain.contours import ContourRegistry
from .contours import (
    ContourRegistryViolation,
    build_climate_contour_setup,
    climate_room_parameters,
    climate_room_profiles,
    contour_registry_to_payload,
    validate_contour_bindings,
    with_climate_room_profiles,
)
from .climate_import import ClimateImportSnapshot
from .climate_registry_import import (
    ClimateRegistryImportViolation,
    import_managed_climate_selection,
)
from .climate_registry import registry_to_payload
from .public_climate_values import public_climate_display_names


CLIMATE_ROOMS_CONTRACT_NAME = "hausman-hub-climate-rooms"
CLIMATE_ROOMS_CONTRACT_VERSION = 1
MAX_AVAILABLE_CLIMATE_ROOMS = 256
CLIMATE_DEVICE_CANDIDATES_CONTRACT_NAME = "hausman-hub-climate-device-candidates"
CLIMATE_DEVICE_CANDIDATES_CONTRACT_VERSION = 1
MAX_CLIMATE_DEVICE_CANDIDATES = 1024
JSON_SAFE_INTEGER_MAXIMUM = 9_007_199_254_740_991
CLIMATE_ROOM_SUGGESTIONS_CONTRACT_NAME = "hausman-hub-climate-room-suggestions"
CLIMATE_ROOM_SUGGESTIONS_CONTRACT_VERSION = 1
CLIMATE_DRAFT_CONTRACT_NAME = "hausman-hub-climate-draft"
CLIMATE_DRAFT_CONTRACT_VERSION = 1
CLIMATE_SETUP_OPTIONS_CONTRACT_NAME = "hausman-hub-climate-setup-options"
CLIMATE_SETUP_OPTIONS_CONTRACT_VERSION = 1
CLIMATE_DRAFT_VALIDATION_CONTRACT_NAME = "hausman-hub-climate-draft-validation"
CLIMATE_DRAFT_VALIDATION_CONTRACT_VERSION = 1
CLIMATE_DRAFT_SAVE_CONTRACT_NAME = "hausman-hub-climate-draft-save"
CLIMATE_DRAFT_SAVE_CONTRACT_VERSION = 1
CLIMATE_CURRENT_SETUP_CONTRACT_NAME = "hausman-hub-climate-current-setup"
CLIMATE_CURRENT_SETUP_CONTRACT_VERSION = 1
CLIMATE_PROFILE_UPDATE_REQUEST_CONTRACT_NAME = (
    "hausman-hub-climate-profile-update-request"
)
CLIMATE_PROFILE_UPDATE_REQUEST_CONTRACT_VERSION = 1
CLIMATE_PROFILE_UPDATE_CONTRACT_NAME = "hausman-hub-climate-profile-update"
CLIMATE_PROFILE_UPDATE_CONTRACT_VERSION = 1
MAX_CLIMATE_DRAFT_NAME_LENGTH = 120
MAX_CLIMATE_DRAFT_ROOMS = 128
MAX_CLIMATE_DRAFT_DEVICES = 512

_CLIMATE_DRAFT_MODES = frozenset({"observe", "automatic"})
_CLIMATE_DRAFT_FIELDS = frozenset(
    {"snapshot_revision", "name", "mode", "rooms"}
)
_CLIMATE_DRAFT_ROOM_FIELDS = frozenset(
    {
        "room_id",
        "target_temperature",
        "target_humidity",
        "strategy",
        "devices",
    }
)
_CLIMATE_DRAFT_DEVICE_FIELDS = frozenset({"candidate_id", "type"})
_CLIMATE_DRAFT_RESPONSE_FIELDS = frozenset(
    {
        "contract",
        "generated_at",
        "snapshot_revision",
        "draft_revision",
        "status",
        "save_allowed",
        "validation_required",
        "display_names",
        "name",
        "mode",
        "rooms",
        "summary",
    }
)
_CLIMATE_DRAFT_RESPONSE_ROOM_FIELDS = frozenset(
    {"id", "name", "targets", "devices"}
)
_CLIMATE_DRAFT_TARGET_FIELDS = frozenset(
    {"target_temperature", "target_humidity", "strategy"}
)
_CLIMATE_DRAFT_RESPONSE_DEVICE_FIELDS = frozenset(
    {"candidate_id", "name", "type", "type_name"}
)
_CLIMATE_PROFILE_UPDATE_REQUEST_FIELDS = frozenset(
    {"contract", "setup_revision", "rooms"}
)
_CLIMATE_PROFILE_UPDATE_CONTRACT_FIELDS = frozenset({"name", "version"})
_CLIMATE_PROFILE_UPDATE_ROOM_FIELDS = frozenset({"room_id", "profiles"})
_ACTIVE_CLIMATE_DRAFT_TYPES = frozenset(
    {"air_conditioner", "radiator_thermostat", "humidifier", "floor_heating"}
)
_VALIDATION_ISSUE_NAMES = {
    "no_controllable_device": (
        "В комнате нет устройства, которое может управлять климатом."
    ),
    "unsupported_device_set": (
        "Выбранный набор устройств нельзя безопасно подготовить к сохранению."
    ),
}
_DRAFT_MODE_NAMES = {
    "observe": "Только наблюдение",
    "automatic": "Автоматическое управление",
}
_DRAFT_STRATEGY_NAMES = {
    "soft": "Плавно",
    "normal": "Обычно",
    "aggressive": "Быстро",
}
_CURRENT_SETUP_STATUS_NAMES = {
    "not_configured": "Ещё не настроен",
    "ready": "Можно редактировать",
    "attention": "Нужно проверить устройства",
}
_CURRENT_SETUP_ISSUE_NAMES = {
    "not_configured": "Климатический контур ещё не настроен.",
    "data_stale": "Данные об устройствах устарели. Обновите экран.",
    "source_missing": "Настроенное устройство больше не найдено.",
    "device_unavailable": "Настроенное устройство сейчас недоступно.",
    "registry_mismatch": "Привязка настроенного устройства изменилась.",
    "unsupported_device": "Тип настроенного устройства больше не поддерживается.",
}

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
_SUGGESTION_CONFIDENCE_NAMES = {
    "high": "Комната определена",
    "none": "Нет безопасного предложения",
}
_SUGGESTION_REASON_NAMES = {
    "detected_room": "Устройство найдено в этой комнате",
    "already_configured": "Устройство уже назначено этой комнате",
    "device_unavailable": "Устройство сейчас недоступно",
    "unsupported_device": "Тип устройства не поддерживается",
    "data_stale": "Нужно обновить данные",
    "device_missing": "Настроенное устройство больше не найдено",
    "registry_mismatch": "Текущая привязка не совпадает с настройкой HausmanHub",
    "room_unavailable": "Комната сейчас недоступна для выбора",
}


class ClimateSetupViolation(ValueError):
    """A climate setup request is unsafe, stale, or internally inconsistent."""

    def __init__(self, message: str, *, code: str = "invalid_draft") -> None:
        super().__init__(message)
        self.code = code


def climate_available_rooms(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> dict[str, object]:
    """Return every discovered or configured room using only stable HausmanHub IDs."""

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
    source_ids = _ordered_candidate_source_ids(registry, snapshot)

    candidates: list[dict[str, object]] = []
    private_revision_values: list[dict[str, object]] = []
    for index, source_id in enumerate(source_ids, start=1):
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


def climate_room_suggestions(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> dict[str, object]:
    """Suggest only explicit source-room matches and never assign automatically."""

    rooms_payload = climate_available_rooms(registry, snapshot)
    candidates_payload = climate_device_candidates(registry, snapshot)
    room_by_id = {room["id"]: room for room in rooms_payload["rooms"]}  # type: ignore[index]
    suggestions: list[dict[str, object]] = []
    for candidate in candidates_payload["candidates"]:  # type: ignore[index]
        candidate_status = candidate["status"]
        suggested_room_id: object = None
        suggested_room_name: object = None
        confidence = "none"
        if candidate_status == "already_configured":
            proposal_room_id = candidate["configured_room_id"]
        elif candidate_status in {"available", "unavailable", "unsupported"}:
            proposal_room_id = candidate["room_id"]
        else:
            proposal_room_id = None

        proposal_room = (
            room_by_id.get(proposal_room_id)
            if isinstance(proposal_room_id, str)
            else None
        )
        if proposal_room is not None and proposal_room["status"] == "available":
            suggested_room_id = proposal_room["id"]
            suggested_room_name = proposal_room["name"]
            confidence = "high"

        can_accept = bool(
            candidate_status == "available"
            and proposal_room is not None
            and proposal_room["selectable"] is True
        )
        if candidate_status == "available":
            reason = "detected_room" if can_accept else "room_unavailable"
        elif candidate_status == "already_configured":
            reason = "already_configured"
        elif candidate_status == "unavailable":
            reason = "device_unavailable"
        elif candidate_status == "unsupported":
            reason = "unsupported_device"
        elif candidate_status == "data_stale":
            reason = "data_stale"
        elif candidate_status == "source_missing":
            reason = "device_missing"
        elif candidate_status == "registry_mismatch":
            reason = "registry_mismatch"
        else:  # pragma: no cover - candidate statuses are closed above
            raise ValueError("climate candidate status is unsupported")

        suggestions.append(
            {
                "candidate_id": candidate["candidate_id"],
                "device_name": candidate["name"],
                "candidate_status": candidate_status,
                "suggested_room_id": suggested_room_id,
                "suggested_room_name": suggested_room_name,
                "confidence": confidence,
                "reason": reason,
                "can_accept": can_accept,
            }
        )

    return {
        "contract": {
            "name": CLIMATE_ROOM_SUGGESTIONS_CONTRACT_NAME,
            "version": CLIMATE_ROOM_SUGGESTIONS_CONTRACT_VERSION,
        },
        "generated_at": candidates_payload["generated_at"],
        "snapshot_revision": candidates_payload["snapshot_revision"],
        "data_status": candidates_payload["data_status"],
        "assignment_allowed": any(
            suggestion["can_accept"] is True for suggestion in suggestions
        ),
        "confirmation_required": True,
        "display_names": {
            "confidence": dict(_SUGGESTION_CONFIDENCE_NAMES),
            "reason": dict(_SUGGESTION_REASON_NAMES),
        },
        "suggestions": suggestions,
    }


def create_climate_contour_draft(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    payload: object,
) -> dict[str, object]:
    """Create one deterministic, non-persistent contour draft from fresh choices."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateSetupViolation("climate draft registry must be valid")
    if not isinstance(snapshot, ClimateImportSnapshot):
        raise ClimateSetupViolation("climate draft snapshot must be valid")
    values = _exact_mapping(payload, _CLIMATE_DRAFT_FIELDS, "climate draft")
    requested_revision = values["snapshot_revision"]
    if (
        type(requested_revision) is not int
        or not 0 <= requested_revision <= JSON_SAFE_INTEGER_MAXIMUM
    ):
        raise ClimateSetupViolation("climate draft snapshot revision is invalid")

    candidates_payload = climate_device_candidates(registry, snapshot)
    current_revision = candidates_payload["snapshot_revision"]
    if not snapshot.runtime_fresh:
        raise ClimateSetupViolation(
            "climate draft needs current discovery data",
            code="data_stale",
        )
    if requested_revision != current_revision:
        raise ClimateSetupViolation(
            "climate device choices changed",
            code="snapshot_changed",
        )

    name = values["name"]
    if (
        not isinstance(name, str)
        or not name
        or name != name.strip()
        or len(name) > MAX_CLIMATE_DRAFT_NAME_LENGTH
    ):
        raise ClimateSetupViolation("climate draft name is invalid")
    mode = values["mode"]
    if not isinstance(mode, str) or mode not in _CLIMATE_DRAFT_MODES:
        raise ClimateSetupViolation("climate draft mode is invalid")
    room_values = values["rooms"]
    if (
        not isinstance(room_values, list)
        or not 1 <= len(room_values) <= MAX_CLIMATE_DRAFT_ROOMS
    ):
        raise ClimateSetupViolation("climate draft rooms are invalid")

    rooms_payload = climate_available_rooms(registry, snapshot)
    available_rooms = {
        room["id"]: room
        for room in rooms_payload["rooms"]  # type: ignore[index]
        if room["selectable"] is True
    }
    candidates = candidates_payload["candidates"]
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in candidates  # type: ignore[union-attr]
    }
    selected_room_ids: set[str] = set()
    selected_candidate_ids: set[str] = set()
    normalized_rooms: list[dict[str, object]] = []
    total_devices = 0
    device_type_names = public_climate_display_names()["device_kinds"]

    for room_value in room_values:
        room = _exact_mapping(
            room_value,
            _CLIMATE_DRAFT_ROOM_FIELDS,
            "climate draft room",
        )
        room_id = room["room_id"]
        if not isinstance(room_id, str) or room_id not in available_rooms:
            raise ClimateSetupViolation("climate draft room is unavailable")
        if room_id in selected_room_ids:
            raise ClimateSetupViolation("climate draft room is repeated")
        selected_room_ids.add(room_id)
        try:
            targets = climate_room_parameters(
                {
                    "target_temperature": room["target_temperature"],
                    "target_humidity": room["target_humidity"],
                    "strategy": room["strategy"],
                }
            )
        except ContourRegistryViolation as error:
            raise ClimateSetupViolation(str(error)) from error

        device_values = room["devices"]
        if not isinstance(device_values, list) or not device_values:
            raise ClimateSetupViolation("climate draft room needs devices")
        total_devices += len(device_values)
        if total_devices > MAX_CLIMATE_DRAFT_DEVICES:
            raise ClimateSetupViolation("climate draft has too many devices")
        normalized_devices: list[dict[str, object]] = []
        for device_value in device_values:
            device = _exact_mapping(
                device_value,
                _CLIMATE_DRAFT_DEVICE_FIELDS,
                "climate draft device",
            )
            candidate_id = device["candidate_id"]
            selected_type = device["type"]
            if not isinstance(candidate_id, str):
                raise ClimateSetupViolation("climate draft candidate is invalid")
            if candidate_id in selected_candidate_ids:
                raise ClimateSetupViolation("climate draft candidate is repeated")
            candidate = candidate_by_id.get(candidate_id)
            if candidate is None or candidate["selectable"] is not True:
                raise ClimateSetupViolation("climate draft candidate is unavailable")
            if candidate["room_id"] != room_id:
                raise ClimateSetupViolation("climate draft candidate room differs")
            if (
                not isinstance(selected_type, str)
                or selected_type not in candidate["suggested_types"]
            ):
                raise ClimateSetupViolation("climate draft device type is invalid")
            selected_candidate_ids.add(candidate_id)
            normalized_devices.append(
                {
                    "candidate_id": candidate_id,
                    "name": candidate["name"],
                    "type": selected_type,
                    "type_name": device_type_names[selected_type],
                }
            )

        normalized_rooms.append(
            {
                "id": room_id,
                "name": available_rooms[room_id]["name"],
                "targets": targets,
                "devices": sorted(
                    normalized_devices,
                    key=lambda item: item["candidate_id"],  # type: ignore[return-value]
                ),
            }
        )

    normalized_rooms.sort(key=lambda room: room["id"])  # type: ignore[arg-type]
    draft_values = {
        "snapshot_revision": current_revision,
        "name": name,
        "mode": mode,
        "rooms": normalized_rooms,
    }
    return {
        "contract": {
            "name": CLIMATE_DRAFT_CONTRACT_NAME,
            "version": CLIMATE_DRAFT_CONTRACT_VERSION,
        },
        "generated_at": snapshot.generated_at,
        "snapshot_revision": current_revision,
        "draft_revision": _json_safe_revision(draft_values),
        "status": "created",
        "save_allowed": False,
        "validation_required": True,
        "display_names": {
            "modes": dict(_DRAFT_MODE_NAMES),
            "strategies": dict(_DRAFT_STRATEGY_NAMES),
        },
        "name": name,
        "mode": mode,
        "rooms": normalized_rooms,
        "summary": {
            "room_count": len(normalized_rooms),
            "device_count": total_devices,
        },
    }


def climate_setup_options(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> dict[str, object]:
    """Return everything an admin form needs to create a safe draft."""

    rooms_payload = climate_available_rooms(registry, snapshot)
    candidates_payload = climate_device_candidates(registry, snapshot)
    suggestions_payload = climate_room_suggestions(registry, snapshot)
    suggestions = {
        suggestion["candidate_id"]: suggestion
        for suggestion in suggestions_payload["suggestions"]  # type: ignore[index]
    }
    devices: list[dict[str, object]] = []
    for candidate in candidates_payload["candidates"]:  # type: ignore[index]
        suggestion = suggestions[candidate["candidate_id"]]
        devices.append(
            {
                "candidate_id": candidate["candidate_id"],
                "name": candidate["name"],
                "room_id": candidate["room_id"],
                "suggested_types": candidate["suggested_types"],
                "recommended_type": candidate["recommended_type"],
                "status": candidate["status"],
                "suggested_room_id": suggestion["suggested_room_id"],
                "suggested_room_name": suggestion["suggested_room_name"],
                "reason": suggestion["reason"],
                "can_add": suggestion["can_accept"],
            }
        )

    return {
        "contract": {
            "name": CLIMATE_SETUP_OPTIONS_CONTRACT_NAME,
            "version": CLIMATE_SETUP_OPTIONS_CONTRACT_VERSION,
        },
        "generated_at": candidates_payload["generated_at"],
        "snapshot_revision": candidates_payload["snapshot_revision"],
        "data_status": candidates_payload["data_status"],
        "draft_creation_allowed": any(device["can_add"] is True for device in devices),
        "display_names": {
            "room_status": dict(_ROOM_STATUS_NAMES),
            "device_types": dict(
                candidates_payload["display_names"]["device_types"]  # type: ignore[index]
            ),
            "device_status": dict(_CANDIDATE_STATUS_NAMES),
            "suggestion_reasons": dict(_SUGGESTION_REASON_NAMES),
            "modes": dict(_DRAFT_MODE_NAMES),
            "strategies": dict(_DRAFT_STRATEGY_NAMES),
        },
        "rooms": [
            {
                "id": room["id"],
                "name": room["name"],
                "status": room["status"],
                "selectable": room["selectable"],
            }
            for room in rooms_payload["rooms"]  # type: ignore[index]
        ],
        "devices": devices,
    }


def current_climate_contour_setup(
    registry: ClimateRegistry,
    contours: ContourRegistry,
    snapshot: ClimateImportSnapshot,
) -> dict[str, object]:
    """Project the exact saved climate setup for a future safe editor."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateSetupViolation("current climate registry must be valid")
    if not isinstance(contours, ContourRegistry):
        raise ClimateSetupViolation("current contour registry must be valid")
    if not isinstance(snapshot, ClimateImportSnapshot):
        raise ClimateSetupViolation("current climate snapshot must be valid")
    validate_contour_bindings(contours, registry)

    candidates_payload = climate_device_candidates(registry, snapshot)
    source_ids = _ordered_candidate_source_ids(registry, snapshot)
    candidate_id_by_source = {
        source_id: f"candidate_{index:04d}"
        for index, source_id in enumerate(source_ids, start=1)
    }
    candidates = candidates_payload["candidates"]
    if not isinstance(candidates, list):
        raise ClimateSetupViolation("current climate candidates are invalid")
    candidate_by_id = {
        candidate["candidate_id"]: candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and isinstance(candidate.get("candidate_id"), str)
    }
    setup_revision = climate_setup_revision(registry, contours)
    display_names = {
        "statuses": dict(_CURRENT_SETUP_STATUS_NAMES),
        "modes": {
            "disabled": "Выключен в HausmanHub",
            **_DRAFT_MODE_NAMES,
        },
        "strategies": dict(_DRAFT_STRATEGY_NAMES),
        "profiles": {"day": "День", "night": "Ночь"},
        "issues": dict(_CURRENT_SETUP_ISSUE_NAMES),
    }
    contour = contours.contour("climate")
    if contour is None:
        return {
            "contract": {
                "name": CLIMATE_CURRENT_SETUP_CONTRACT_NAME,
                "version": CLIMATE_CURRENT_SETUP_CONTRACT_VERSION,
            },
            "generated_at": snapshot.generated_at,
            "snapshot_revision": candidates_payload["snapshot_revision"],
            "setup_revision": setup_revision,
            "status": "not_configured",
            "editing_allowed": False,
            "display_names": display_names,
            "name": None,
            "mode": None,
            "schedule": None,
            "rooms": [],
            "issues": [
                {
                    "code": "not_configured",
                    "room_id": None,
                    "candidate_id": None,
                    "message": _CURRENT_SETUP_ISSUE_NAMES["not_configured"],
                }
            ],
            "summary": {"room_count": 0, "device_count": 0},
        }

    issues: list[dict[str, object]] = []
    if not snapshot.runtime_fresh:
        issues.append(
            {
                "code": "data_stale",
                "room_id": None,
                "candidate_id": None,
                "message": _CURRENT_SETUP_ISSUE_NAMES["data_stale"],
            }
        )
    rooms: list[dict[str, object]] = []
    device_count = 0
    device_kind_names = public_climate_display_names()["device_kinds"]
    for assignment in contour.rooms:
        room = registry.room(assignment.room_id)
        if room is None:  # pragma: no cover - checked by contour bindings
            raise ClimateSetupViolation("current climate room is unavailable")
        devices: list[dict[str, object]] = []
        for device_id in assignment.device_ids:
            device = registry.device(device_id)
            if device is None:  # pragma: no cover - checked by contour bindings
                raise ClimateSetupViolation("current climate device is unavailable")
            candidate_id = candidate_id_by_source[device.source_id]
            candidate = candidate_by_id.get(candidate_id)
            if candidate is None:  # pragma: no cover - registry sources are candidates
                raise ClimateSetupViolation("current climate candidate is unavailable")
            if snapshot.runtime_fresh and candidate["status"] != "already_configured":
                issue_code = {
                    "source_missing": "source_missing",
                    "unavailable": "device_unavailable",
                    "registry_mismatch": "registry_mismatch",
                    "unsupported": "unsupported_device",
                }.get(candidate["status"], "registry_mismatch")
                issues.append(
                    {
                        "code": issue_code,
                        "room_id": assignment.room_id,
                        "candidate_id": candidate_id,
                        "message": _CURRENT_SETUP_ISSUE_NAMES[issue_code],
                    }
                )
            devices.append(
                {
                    "candidate_id": candidate_id,
                    "name": device.name,
                    "type": device.kind.value,
                    "type_name": device_kind_names[device.kind.value],
                }
            )
            device_count += 1
        rooms.append(
            {
                "id": room.room_id,
                "name": room.name,
                "devices": sorted(
                    devices,
                    key=lambda item: item["candidate_id"],  # type: ignore[return-value]
                ),
                "profiles": {
                    "day": {
                        "target_temperature": (
                            assignment.day_profile.target_temperature
                        ),
                        "target_humidity": assignment.day_profile.target_humidity,
                        "strategy": assignment.day_profile.strategy.value,
                    },
                    "night": {
                        "target_temperature": (
                            assignment.night_profile.target_temperature
                        ),
                        "target_humidity": assignment.night_profile.target_humidity,
                        "strategy": assignment.night_profile.strategy.value,
                    },
                    "active_profile": assignment.active_profile.value,
                },
                "temporary_temperature": (
                    None
                    if assignment.temporary_override is None
                    else assignment.temporary_override.target_temperature
                ),
            }
        )

    rooms.sort(key=lambda item: item["id"])  # type: ignore[arg-type]
    return {
        "contract": {
            "name": CLIMATE_CURRENT_SETUP_CONTRACT_NAME,
            "version": CLIMATE_CURRENT_SETUP_CONTRACT_VERSION,
        },
        "generated_at": snapshot.generated_at,
        "snapshot_revision": candidates_payload["snapshot_revision"],
        "setup_revision": setup_revision,
        "status": "ready" if not issues else "attention",
        "editing_allowed": not issues,
        "display_names": display_names,
        "name": contour.name,
        "mode": contour.mode.value,
        "schedule": {
            "enabled": contour.schedule.enabled,
            "day_start": contour.schedule.day_start,
            "night_start": contour.schedule.night_start,
            "last_applied_profile": (
                None
                if contour.schedule.last_applied_profile is None
                else contour.schedule.last_applied_profile.value
            ),
        },
        "rooms": rooms,
        "issues": issues,
        "summary": {
            "room_count": len(rooms),
            "device_count": device_count,
        },
    }


def climate_setup_revision(
    registry: ClimateRegistry,
    contours: ContourRegistry,
) -> int:
    """Return one stable optimistic-lock revision for the saved setup."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateSetupViolation("current climate registry must be valid")
    if not isinstance(contours, ContourRegistry):
        raise ClimateSetupViolation("current contour registry must be valid")
    validate_contour_bindings(contours, registry)
    return _json_safe_revision(
        {
            "registry": registry_to_payload(registry),
            "contours": contour_registry_to_payload(contours),
        }
    )


def update_climate_profiles(
    registry: ClimateRegistry,
    contours: ContourRegistry,
    payload: object,
    *,
    saved_at: int,
    automatic_application_enabled: bool,
) -> tuple[ContourRegistry, dict[str, object]]:
    """Validate and replace every saved day/night profile atomically."""

    if type(saved_at) is not int or saved_at < 0:
        raise ClimateSetupViolation("climate profile save time is invalid")
    if type(automatic_application_enabled) is not bool:
        raise ClimateSetupViolation("automatic climate application flag is invalid")
    request = _exact_mapping(
        payload,
        _CLIMATE_PROFILE_UPDATE_REQUEST_FIELDS,
        "climate profile update request",
    )
    contract = _exact_mapping(
        request["contract"],
        _CLIMATE_PROFILE_UPDATE_CONTRACT_FIELDS,
        "climate profile update request contract",
    )
    if (
        contract["name"] != CLIMATE_PROFILE_UPDATE_REQUEST_CONTRACT_NAME
        or contract["version"] != CLIMATE_PROFILE_UPDATE_REQUEST_CONTRACT_VERSION
    ):
        raise ClimateSetupViolation(
            "climate profile update request contract is unsupported"
        )

    expected_revision = climate_setup_revision(registry, contours)
    supplied_revision = request["setup_revision"]
    if (
        type(supplied_revision) is not int
        or not 0 <= supplied_revision <= JSON_SAFE_INTEGER_MAXIMUM
    ):
        raise ClimateSetupViolation("climate setup revision is invalid")
    if supplied_revision != expected_revision:
        raise ClimateSetupViolation(
            "climate setup changed after it was opened",
            code="setup_changed",
        )

    contour = contours.contour("climate")
    if contour is None:
        raise ClimateSetupViolation(
            "climate contour is not configured",
            code="not_configured",
        )
    raw_rooms = request["rooms"]
    if (
        not isinstance(raw_rooms, list)
        or not 1 <= len(raw_rooms) <= MAX_CLIMATE_DRAFT_ROOMS
    ):
        raise ClimateSetupViolation("climate profile rooms are invalid")

    existing_rooms = {room.room_id: room for room in contour.rooms}
    profiles_by_room: dict[str, dict[str, object]] = {}
    for raw_room in raw_rooms:
        room = _exact_mapping(
            raw_room,
            _CLIMATE_PROFILE_UPDATE_ROOM_FIELDS,
            "climate profile room",
        )
        room_id = room["room_id"]
        if not isinstance(room_id, str) or room_id not in existing_rooms:
            raise ClimateSetupViolation("climate profile room is not configured")
        if room_id in profiles_by_room:
            raise ClimateSetupViolation("climate profile room is duplicated")
        profiles_by_room[room_id] = {
            "profiles": room["profiles"],
            "active_profile": existing_rooms[room_id].active_profile.value,
        }
    if set(profiles_by_room) != set(existing_rooms):
        raise ClimateSetupViolation(
            "climate profile rooms must exactly match configured rooms"
        )

    try:
        updated = with_climate_room_profiles(contours, profiles_by_room)
    except ContourRegistryViolation as error:
        raise ClimateSetupViolation(str(error)) from error
    updated_contour = updated.contour("climate")
    if updated_contour is None:  # pragma: no cover - preserved by the use case
        raise ClimateSetupViolation("updated climate contour is unavailable")
    schedule_enabled = updated_contour.schedule.enabled
    automatic_application_pending = (
        schedule_enabled and automatic_application_enabled
    )
    receipt_rooms = []
    for room in updated_contour.rooms:
        values = climate_room_profiles(room)
        receipt_rooms.append(
            {
                "room_id": room.room_id,
                "profiles": values["profiles"],
                "active_profile": values["active_profile"],
            }
        )
    receipt_rooms.sort(key=lambda room: room["room_id"])  # type: ignore[arg-type]
    receipt = {
        "contract": {
            "name": CLIMATE_PROFILE_UPDATE_CONTRACT_NAME,
            "version": CLIMATE_PROFILE_UPDATE_CONTRACT_VERSION,
        },
        "saved_at": saved_at,
        "setup_revision": climate_setup_revision(registry, updated),
        "status": "saved",
        "commands_sent": False,
        "schedule_enabled": schedule_enabled,
        "automatic_application_pending": automatic_application_pending,
        "message": (
            "Профили сохранены. Включённое расписание применит нужный профиль "
            "при ближайшей проверке."
            if automatic_application_pending
            else "Профили сохранены. Команды устройствам не отправлялись."
        ),
        "rooms": receipt_rooms,
    }
    return updated, receipt


def validate_climate_contour_draft(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    payload: object,
) -> dict[str, object]:
    """Validate one unchanged draft deeply without saving or commanding."""

    draft = _exact_mapping(
        payload,
        _CLIMATE_DRAFT_RESPONSE_FIELDS,
        "climate contour draft",
    )
    generated_at = draft["generated_at"]
    if type(generated_at) is not int or generated_at < 0:
        raise ClimateSetupViolation("climate draft generation time is invalid")
    rooms = draft["rooms"]
    if not isinstance(rooms, list):
        raise ClimateSetupViolation("climate draft rooms are invalid")

    request_rooms: list[dict[str, object]] = []
    for room_value in rooms:
        room = _exact_mapping(
            room_value,
            _CLIMATE_DRAFT_RESPONSE_ROOM_FIELDS,
            "climate draft response room",
        )
        targets = _exact_mapping(
            room["targets"],
            _CLIMATE_DRAFT_TARGET_FIELDS,
            "climate draft response targets",
        )
        devices = room["devices"]
        if not isinstance(devices, list):
            raise ClimateSetupViolation("climate draft response devices are invalid")
        request_devices: list[dict[str, object]] = []
        for device_value in devices:
            device = _exact_mapping(
                device_value,
                _CLIMATE_DRAFT_RESPONSE_DEVICE_FIELDS,
                "climate draft response device",
            )
            request_devices.append(
                {
                    "candidate_id": device["candidate_id"],
                    "type": device["type"],
                }
            )
        request_rooms.append(
            {
                "room_id": room["id"],
                "target_temperature": targets["target_temperature"],
                "target_humidity": targets["target_humidity"],
                "strategy": targets["strategy"],
                "devices": request_devices,
            }
        )

    expected = create_climate_contour_draft(
        registry,
        snapshot,
        {
            "snapshot_revision": draft["snapshot_revision"],
            "name": draft["name"],
            "mode": draft["mode"],
            "rooms": request_rooms,
        },
    )
    comparable_draft = dict(draft)
    comparable_draft["generated_at"] = expected["generated_at"]
    if comparable_draft != expected:
        raise ClimateSetupViolation("climate draft was changed after creation")

    source_ids = _ordered_candidate_source_ids(registry, snapshot)
    source_by_candidate = {
        f"candidate_{index:04d}": source_id
        for index, source_id in enumerate(source_ids, start=1)
    }
    selected_source_ids: list[str] = []
    selected_source_kinds: dict[str, object] = {}
    room_parameters: dict[str, object] = {}
    issues: list[dict[str, object]] = []
    for room in request_rooms:
        room_id = room["room_id"]
        devices = room["devices"]
        active = False
        for device in devices:  # type: ignore[union-attr]
            candidate_id = device["candidate_id"]
            source_id = source_by_candidate[candidate_id]
            selected_source_ids.append(source_id)
            selected_source_kinds[source_id] = device["type"]
            if device["type"] in _ACTIVE_CLIMATE_DRAFT_TYPES:
                active = True
        if not active:
            issues.append(
                {
                    "code": "no_controllable_device",
                    "room_id": room_id,
                    "message": _VALIDATION_ISSUE_NAMES["no_controllable_device"],
                }
            )
        room_parameters[room_id] = {  # type: ignore[index]
            "target_temperature": room["target_temperature"],
            "target_humidity": room["target_humidity"],
            "strategy": room["strategy"],
        }

    capabilities_supported = True
    try:
        import_managed_climate_selection(
            snapshot,
            room_ids=[room["room_id"] for room in request_rooms],
            source_ids=selected_source_ids,
            source_kinds=selected_source_kinds,
        )
    except ClimateRegistryImportViolation:
        capabilities_supported = False
        issues.append(
            {
                "code": "unsupported_device_set",
                "room_id": None,
                "message": _VALIDATION_ISSUE_NAMES["unsupported_device_set"],
            }
        )

    if not issues:
        try:
            build_climate_contour_setup(
                snapshot,
                room_ids=[room["room_id"] for room in request_rooms],
                source_ids=selected_source_ids,
                source_kinds=selected_source_kinds,
                name=draft["name"],
                mode=draft["mode"],
                room_parameters=room_parameters,
            )
        except ContourRegistryViolation:
            capabilities_supported = False
            issues.append(
                {
                    "code": "unsupported_device_set",
                    "room_id": None,
                    "message": _VALIDATION_ISSUE_NAMES["unsupported_device_set"],
                }
            )

    ready = not issues
    return {
        "contract": {
            "name": CLIMATE_DRAFT_VALIDATION_CONTRACT_NAME,
            "version": CLIMATE_DRAFT_VALIDATION_CONTRACT_VERSION,
        },
        "generated_at": snapshot.generated_at,
        "snapshot_revision": draft["snapshot_revision"],
        "draft_revision": draft["draft_revision"],
        "status": "ready" if ready else "blocked",
        "save_allowed": ready,
        "command_allowed": False,
        "checks": {
            "snapshot_current": True,
            "draft_unchanged": True,
            "rooms_have_controllable_devices": not any(
                issue["code"] == "no_controllable_device" for issue in issues
            ),
            "device_capabilities_supported": capabilities_supported,
        },
        "issues": issues,
        "summary": {
            "room_count": len(request_rooms),
            "device_count": len(selected_source_ids),
        },
    }


def build_climate_contour_draft_setup(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    payload: object,
) -> tuple[ClimateRegistry, ContourRegistry, dict[str, object]]:
    """Build the exact deeply validated pair that may be saved atomically."""

    validation = validate_climate_contour_draft(registry, snapshot, payload)
    if validation["save_allowed"] is not True:
        raise ClimateSetupViolation(
            "climate draft is not ready to save",
            code="draft_blocked",
        )
    draft = _exact_mapping(
        payload,
        _CLIMATE_DRAFT_RESPONSE_FIELDS,
        "saved climate draft",
    )
    source_ids = _ordered_candidate_source_ids(registry, snapshot)
    source_by_candidate = {
        f"candidate_{index:04d}": source_id
        for index, source_id in enumerate(source_ids, start=1)
    }
    selected_source_ids: list[str] = []
    selected_source_kinds: dict[str, object] = {}
    room_parameters: dict[str, object] = {}
    room_ids: list[str] = []
    rooms = draft["rooms"]
    if not isinstance(rooms, list):
        raise ClimateSetupViolation("saved climate draft rooms are invalid")
    for room in rooms:
        if not isinstance(room, dict):
            raise ClimateSetupViolation("saved climate draft room is invalid")
        room_id = room["id"]
        if not isinstance(room_id, str):
            raise ClimateSetupViolation("saved climate draft room id is invalid")
        room_ids.append(room_id)
        targets = room["targets"]
        devices = room["devices"]
        if not isinstance(targets, dict) or not isinstance(devices, list):
            raise ClimateSetupViolation("saved climate draft room is invalid")
        room_parameters[room_id] = dict(targets)
        for device in devices:
            if not isinstance(device, dict):
                raise ClimateSetupViolation("saved climate draft device is invalid")
            candidate_id = device["candidate_id"]
            if not isinstance(candidate_id, str):
                raise ClimateSetupViolation(
                    "saved climate draft candidate id is invalid"
                )
            source_id = source_by_candidate[candidate_id]
            selected_source_ids.append(source_id)
            selected_source_kinds[source_id] = device["type"]

    climate_registry, contours = build_climate_contour_setup(
        snapshot,
        room_ids=room_ids,
        source_ids=selected_source_ids,
        source_kinds=selected_source_kinds,
        name=draft["name"],
        mode=draft["mode"],
        room_parameters=room_parameters,
    )
    return climate_registry, contours, validation


def climate_draft_save_receipt(
    draft: object,
    validation: object,
) -> dict[str, object]:
    """Return a private-id-free receipt only after the pair was persisted."""

    values = _exact_mapping(
        draft,
        _CLIMATE_DRAFT_RESPONSE_FIELDS,
        "saved climate draft",
    )
    if not isinstance(validation, dict) or validation.get("status") != "ready":
        raise ClimateSetupViolation("saved climate draft validation is invalid")
    summary = validation.get("summary")
    if not isinstance(summary, dict):
        raise ClimateSetupViolation("saved climate draft summary is invalid")
    return {
        "contract": {
            "name": CLIMATE_DRAFT_SAVE_CONTRACT_NAME,
            "version": CLIMATE_DRAFT_SAVE_CONTRACT_VERSION,
        },
        "saved_at": validation["generated_at"],
        "snapshot_revision": validation["snapshot_revision"],
        "draft_revision": validation["draft_revision"],
        "status": "saved",
        "commands_sent": False,
        "restart_required": False,
        "contour": {
            "id": "climate",
            "name": values["name"],
            "mode": values["mode"],
        },
        "summary": dict(summary),
    }


def _ordered_candidate_source_ids(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> tuple[str, ...]:
    """Keep response-local candidate references and private resolution identical."""

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

    return tuple(sorted(source_ids, key=sort_key))


def _exact_mapping(
    value: object,
    fields: frozenset[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ClimateSetupViolation(f"{label} fields are invalid")
    return value


def _json_safe_revision(value: object) -> int:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).digest()
    return int.from_bytes(digest[:8], "big") % (JSON_SAFE_INTEGER_MAXIMUM + 1)
