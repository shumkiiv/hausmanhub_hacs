"""Shared presentation helpers for the public tablet climate contract.

Both the legacy bridge-derived builder (`android_climate.py`) and the native
builder (`climate_native_projections.py`) use these helpers so the external
payload presentation stays identical by construction.
"""

from __future__ import annotations

from collections.abc import Collection
import hashlib
import json

from .climate_commands import (
    CLIMATE_TEMPERATURE_MAXIMUM,
    CLIMATE_TEMPERATURE_MINIMUM,
    CLIMATE_TEMPERATURE_STEP,
)

ANDROID_CLIMATE_CONTRACT_NAME = "hausman-hub-home"
ANDROID_CLIMATE_CONTRACT_VERSION = 12
ANDROID_STATE_REVISION_MAXIMUM = 9_007_199_254_740_991
ANDROID_ROOM_CONTROL_ACTIONS = (
    "set_room_target",
    "turn_room_off",
)
ROOM_ACTION_COMMAND_TYPES = {
    "set_room_target": "climate.set_temperature",
    "turn_room_off": "climate.turn_off",
}


def public_state_revision(payload: dict[str, object]) -> int:
    """Return a JSON-safe number that changes only with public state content."""

    revision_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"generated_at", "state_revision"}
    }
    encoded = json.dumps(
        revision_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).digest()
    return int.from_bytes(digest[:8], "big") % (ANDROID_STATE_REVISION_MAXIMUM + 1)


def saved_profiles_by_room(
    contours: object,
) -> dict[str, dict[str, object]]:
    """Copy configured day/night profiles beside each matching public room."""

    result: dict[str, dict[str, object]] = {}
    if not isinstance(contours, list):
        return result
    for contour in contours:
        if not isinstance(contour, dict) or contour.get("kind") != "climate":
            continue
        contour_rooms = contour.get("rooms")
        if not isinstance(contour_rooms, list):
            continue
        for room in contour_rooms:
            if not isinstance(room, dict) or not isinstance(room.get("id"), str):
                continue
            profiles = room.get("comfort_profiles")
            if not isinstance(profiles, dict):
                continue
            day = profiles.get("day")
            night = profiles.get("night")
            if not isinstance(day, dict) or not isinstance(night, dict):
                continue
            result[room["id"]] = {
                "active": profiles.get("active"),
                "day": dict(day),
                "night": dict(night),
            }
    return result


def room_action_availability(
    actions: Collection[str],
    allowed_actions: Collection[str],
    blocked_reasons: Collection[str],
) -> dict[str, object]:
    """Explain the current permission of every advertised room action."""

    allowed = frozenset(allowed_actions)
    reasons = list(blocked_reasons)
    return {
        action: {
            "allowed": action in allowed,
            "blocked_reasons": [] if action in allowed else list(reasons),
        }
        for action in actions
    }


def room_action_inputs(actions: Collection[str]) -> dict[str, object]:
    """Describe only the typed values accepted by the advertised actions."""

    if "set_room_target" not in actions:
        return {}
    return {
        "set_room_target": {
            "target_temperature": {
                "type": "number",
                "required": True,
                "minimum": CLIMATE_TEMPERATURE_MINIMUM,
                "maximum": CLIMATE_TEMPERATURE_MAXIMUM,
                "step": CLIMATE_TEMPERATURE_STEP,
                "unit": "°C",
            }
        }
    }


def room_action_presentations(actions: Collection[str]) -> dict[str, object]:
    """Return fixed Russian text only for actions advertised to Android."""

    presentations: dict[str, object] = {}
    if "set_room_target" in actions:
        presentations["set_room_target"] = {
            "title": "Установить температуру",
            "description": "Изменить желаемую температуру в комнате.",
            "confirmation_required": False,
            "fields": {
                "target_temperature": {
                    "title": "Желаемая температура",
                    "description": (
                        "Значение, которое должен поддерживать климатический контур."
                    ),
                }
            },
        }
    if "turn_room_off" in actions:
        presentations["turn_room_off"] = {
            "title": "Выключить климат",
            "description": "Остановить поддержание климата в комнате.",
            "confirmation_required": True,
            "fields": {},
        }
    return presentations
