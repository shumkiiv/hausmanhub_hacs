"""Read-only preview of settings held in retired Node-RED global context.

The parser is deliberately detached from storage and network adapters.  It
accepts only an explicitly supplied, bounded export and returns a redacted
plan.  Applying that plan is a separate future operation.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any


EXPORT_CONTRACT_NAME = "hausman-hub-legacy-settings-export"
EXPORT_CONTRACT_VERSION = 1
PREVIEW_CONTRACT_NAME = "hausman-hub-legacy-settings-preview"
PREVIEW_CONTRACT_VERSION = 1
MAX_GLOBAL_KEYS = 128
MAX_ROOMS = 32
_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_ROOM_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# These settings are real user choices, but no matching native storage exists
# yet.  Previewing them prevents silent loss while keeping this increment
# strictly read-only.
RECOGNIZED_PENDING_KEYS = frozenset(
    {
        "climate_telegram_reports_enabled",
        "kitchen_curtain_holidays",
        "smart_home_light_off_preset",
        "smart_home_light_preset",
        "smart_home_tv_off_entities",
    }
)

# Volatile observations, latches and controller bookkeeping must never become
# persisted configuration merely because they happen to be in global context.
RUNTIME_KEYS = frozenset(
    {
        "ac_manual_overrides",
        "ac_pause_until",
        "ac_start_sequence_count",
        "can_use_air_condition",
        "central_heating_on",
        "climate_learning",
        "climate_stats",
        "climate_telegram_events",
        "holiday_mode",
        "last_ac_start_room",
        "last_ac_start_ts",
        "last_request",
        "next_ac_start_allowed_ts",
        "not_home_mode",
        "outdoor_lux",
        "outdoor_lux_ts",
        "outdoorTemp",
        "outdoorWeather",
        "outdoors_temp",
        "smart_home_events",
    }
)

SENSITIVE_KEYS = frozenset(
    {
        "max_alert_chat_ids",
        "max_alert_user_ids",
        "max_bot_access_token",
    }
)
_SENSITIVE_MARKERS = ("token", "password", "secret", "credential")


class LegacySettingsImportViolation(ValueError):
    """The supplied export is unsupported, malformed or unbounded."""


def preview_legacy_settings(payload: object) -> dict[str, object]:
    """Return a deterministic redacted plan and perform no writes."""

    root = _mapping(payload, "legacy settings export")
    if set(root) != {"contract", "globals"}:
        raise LegacySettingsImportViolation("legacy settings export fields are invalid")
    contract = _mapping(root.get("contract"), "legacy settings contract")
    if set(contract) != {"name", "version"}:
        raise LegacySettingsImportViolation("legacy settings contract fields are invalid")
    if contract.get("name") != EXPORT_CONTRACT_NAME:
        raise LegacySettingsImportViolation("unsupported legacy settings contract name")
    if type(contract.get("version")) is not int or contract["version"] != 1:
        raise LegacySettingsImportViolation("unsupported legacy settings contract version")

    globals_payload = _mapping(root.get("globals"), "legacy global context")
    if len(globals_payload) > MAX_GLOBAL_KEYS:
        raise LegacySettingsImportViolation("legacy global context is too large")
    if any(not _KEY.fullmatch(key) for key in globals_payload):
        raise LegacySettingsImportViolation("legacy global context contains an invalid key")

    keys = set(globals_payload)
    rejected_sensitive = sorted(key for key in keys if _looks_sensitive(key))
    recognized_pending = sorted((keys - set(rejected_sensitive)) & RECOGNIZED_PENDING_KEYS)
    ignored_runtime = sorted((keys - set(rejected_sensitive)) & RUNTIME_KEYS)
    handled = {
        "home_target_temp",
        "climate_rooms",
        *recognized_pending,
        *ignored_runtime,
        *rejected_sensitive,
    }
    unknown = sorted(keys - handled)

    migratable: dict[str, object] = {"rooms": []}
    if "home_target_temp" in globals_payload:
        migratable["home_target_temperature"] = _temperature(
            globals_payload["home_target_temp"],
            minimum=22,
            maximum=28,
            label="home target temperature",
        )
    if "climate_rooms" in globals_payload:
        migratable["rooms"] = _room_targets(globals_payload["climate_rooms"])

    warnings: list[str] = []
    if migratable["rooms"]:
        warnings.append("derived_room_targets_require_confirmation")
    if recognized_pending:
        warnings.append("recognized_settings_need_native_storage")
    if ignored_runtime:
        warnings.append("runtime_values_ignored")
    if rejected_sensitive:
        warnings.append("sensitive_values_rejected")
    if unknown:
        warnings.append("unknown_values_ignored")
    if "home_target_temperature" not in migratable and not migratable["rooms"]:
        warnings.append("nothing_to_migrate")

    safe_plan = {
        "migratable": migratable,
        "recognized_pending": recognized_pending,
        "ignored_runtime": ignored_runtime,
        "rejected_sensitive": rejected_sensitive,
        "unknown": unknown,
        "warnings": warnings,
    }
    preview_id = hashlib.sha256(
        json.dumps(
            safe_plan,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "contract": {"name": PREVIEW_CONTRACT_NAME, "version": PREVIEW_CONTRACT_VERSION},
        "preview_id": preview_id,
        **safe_plan,
        "write_performed": False,
    }


def _room_targets(value: object) -> list[dict[str, object]]:
    rooms = _mapping(value, "legacy climate rooms")
    if len(rooms) > MAX_ROOMS:
        raise LegacySettingsImportViolation("legacy climate room count is too large")
    result: list[dict[str, object]] = []
    for room_id in sorted(rooms):
        if not _ROOM_ID.fullmatch(room_id):
            raise LegacySettingsImportViolation("legacy climate room id is invalid")
        room = _mapping(rooms[room_id], f"legacy climate room {room_id}")
        target: dict[str, object] = {"legacy_room_id": room_id}
        temperature_value = _first_present(
            room,
            "targetTemperature",
            "comfortTemp",
            "target_temperature",
        )
        if temperature_value is not None:
            target["target_temperature"] = _temperature(
                temperature_value,
                minimum=16,
                maximum=32,
                label=f"target temperature for {room_id}",
            )
        humidity_value = _first_present(
            room,
            "targetHumidity",
            "comfortHumidity",
            "target_humidity",
        )
        if humidity_value is not None:
            target["target_humidity"] = _humidity(humidity_value, room_id)
        if len(target) > 1:
            result.append(target)
    return result


def _first_present(value: Mapping[str, Any], *keys: str) -> object | None:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def _temperature(value: object, *, minimum: float, maximum: float, label: str) -> float:
    if type(value) not in {int, float}:
        raise LegacySettingsImportViolation(f"{label} is invalid")
    rounded = round(float(value), 1)
    if not minimum <= rounded <= maximum:
        raise LegacySettingsImportViolation(f"{label} is outside the supported range")
    return rounded


def _humidity(value: object, room_id: str) -> int:
    if type(value) not in {int, float}:
        raise LegacySettingsImportViolation(f"target humidity for {room_id} is invalid")
    rounded = round(float(value))
    if not 30 <= rounded <= 70:
        raise LegacySettingsImportViolation(
            f"target humidity for {room_id} is outside the supported range"
        )
    return rounded


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise LegacySettingsImportViolation(f"{label} must be an object")
    return value


def _looks_sensitive(key: str) -> bool:
    lowered = key.casefold()
    return key in SENSITIVE_KEYS or any(marker in lowered for marker in _SENSITIVE_MARKERS)
