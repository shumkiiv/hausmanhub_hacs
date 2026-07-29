"""Build one confirmed, command-free migration into native settings stores."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .contours import CLIMATE_CONTOUR_ID
from .legacy_settings_import import preview_legacy_settings
from ..domain.contours import (
    ClimateComfortSettings,
    ContourRegistry,
    ContourViolation,
)
from ..domain.hub_settings import HausmanHubSettings, HausmanHubSettingsViolation


APPLY_CONTRACT_NAME = "hausman-hub-legacy-settings-apply"
APPLY_CONTRACT_VERSION = 1
RECEIPT_CONTRACT_NAME = "hausman-hub-legacy-settings-apply-receipt"
RECEIPT_CONTRACT_VERSION = 1


class LegacySettingsApplyViolation(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LegacySettingsApplyPlan:
    settings: HausmanHubSettings
    contours: ContourRegistry
    receipt: dict[str, object]


def build_legacy_settings_apply(
    payload: object,
    *,
    current_settings: HausmanHubSettings,
    current_contours: ContourRegistry,
) -> LegacySettingsApplyPlan:
    """Revalidate an export and build complete replacement models."""

    root = _mapping(payload, "legacy settings apply request")
    if set(root) != {"contract", "preview_id", "confirm", "export", "room_mappings"}:
        raise LegacySettingsApplyViolation("legacy settings apply fields are invalid")
    contract = _mapping(root.get("contract"), "legacy settings apply contract")
    if set(contract) != {"name", "version"}:
        raise LegacySettingsApplyViolation("legacy settings apply contract fields are invalid")
    if (
        contract.get("name") != APPLY_CONTRACT_NAME
        or type(contract.get("version")) is not int
        or contract["version"] != APPLY_CONTRACT_VERSION
    ):
        raise LegacySettingsApplyViolation("unsupported legacy settings apply contract")
    if root.get("confirm") is not True:
        raise LegacySettingsApplyViolation("explicit confirmation is required")
    preview = preview_legacy_settings(root.get("export"))
    preview_id = root.get("preview_id")
    if not isinstance(preview_id, str) or preview_id != preview["preview_id"]:
        raise LegacySettingsApplyViolation(
            "legacy settings preview has changed",
            code="preview_changed",
        )
    if not isinstance(current_settings, HausmanHubSettings):
        raise LegacySettingsApplyViolation("current native settings are unavailable")
    if not isinstance(current_contours, ContourRegistry):
        raise LegacySettingsApplyViolation("current climate contours are unavailable")

    export = _mapping(root.get("export"), "legacy settings export")
    globals_payload = _mapping(export.get("globals"), "legacy global context")
    settings = _settings_candidate(globals_payload, current_settings)
    try:
        contours, changed_rooms = _contour_candidate(
            preview,
            root.get("room_mappings"),
            current_contours,
        )
    except ContourViolation as error:
        raise LegacySettingsApplyViolation(str(error)) from error
    settings_updated = _settings_changes(current_settings, settings)
    if not settings_updated and not changed_rooms:
        raise LegacySettingsApplyViolation("legacy settings would not change native data")

    return LegacySettingsApplyPlan(
        settings=settings,
        contours=contours,
        receipt={
            "contract": {
                "name": RECEIPT_CONTRACT_NAME,
                "version": RECEIPT_CONTRACT_VERSION,
            },
            "preview_id": preview_id,
            "status": "applied",
            "settings_updated": settings_updated,
            "climate_rooms_updated": changed_rooms,
            "ignored_runtime_count": len(preview["ignored_runtime"]),  # type: ignore[arg-type]
            "rejected_sensitive_count": len(preview["rejected_sensitive"]),  # type: ignore[arg-type]
            "unknown_count": len(preview["unknown"]),  # type: ignore[arg-type]
            "physical_commands_sent": False,
            "write_performed": True,
        },
    )


def _settings_candidate(
    globals_payload: Mapping[str, Any],
    current: HausmanHubSettings,
) -> HausmanHubSettings:
    values: dict[str, object] = {
        "light_on_entities": current.light_on_entities,
        "light_off_entities": current.light_off_entities,
        "tv_off_entities": current.tv_off_entities,
        "climate_reports_enabled": current.climate_reports_enabled,
        "curtain_holidays": current.curtain_holidays,
    }
    mapping = {
        "smart_home_light_preset": "light_on_entities",
        "smart_home_light_off_preset": "light_off_entities",
        "smart_home_tv_off_entities": "tv_off_entities",
        "kitchen_curtain_holidays": "curtain_holidays",
    }
    for legacy_key, native_key in mapping.items():
        if legacy_key in globals_payload:
            raw = globals_payload[legacy_key]
            if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
                raise LegacySettingsApplyViolation(f"{legacy_key} must be a string list")
            values[native_key] = tuple(raw)
    if "climate_telegram_reports_enabled" in globals_payload:
        enabled = globals_payload["climate_telegram_reports_enabled"]
        if type(enabled) is not bool:
            raise LegacySettingsApplyViolation("climate reports flag must be boolean")
        values["climate_reports_enabled"] = enabled
    try:
        return HausmanHubSettings(**values)  # type: ignore[arg-type]
    except HausmanHubSettingsViolation as error:
        raise LegacySettingsApplyViolation(str(error)) from error


def _contour_candidate(
    preview: Mapping[str, object],
    mappings_value: object,
    current: ContourRegistry,
) -> tuple[ContourRegistry, list[str]]:
    migratable = _mapping(preview.get("migratable"), "migratable settings")
    raw_rooms = migratable.get("rooms")
    if not isinstance(raw_rooms, list):
        raise LegacySettingsApplyViolation("migratable climate rooms are invalid")
    targets = {
        room["legacy_room_id"]: room
        for room in raw_rooms
        if isinstance(room, Mapping) and isinstance(room.get("legacy_room_id"), str)
    }
    mappings = _room_mappings(mappings_value, set(targets))
    home_temperature = migratable.get("home_target_temperature")
    has_climate_change = home_temperature is not None or bool(targets)
    if not has_climate_change:
        return current, []
    contour = current.contour(CLIMATE_CONTOUR_ID)
    if contour is None:
        raise LegacySettingsApplyViolation(
            "native climate contour is not configured",
            code="climate_not_configured",
        )
    native_rooms = {room.room_id: room for room in contour.rooms}
    if any(room_id not in native_rooms for room_id in mappings.values()):
        raise LegacySettingsApplyViolation("room mapping references an unknown native room")

    targets_by_native = {mappings[legacy_id]: target for legacy_id, target in targets.items()}
    rooms = []
    changed: list[str] = []
    for room in contour.rooms:
        target = targets_by_native.get(room.room_id)
        temperature = (
            target.get("target_temperature")
            if target is not None and "target_temperature" in target
            else home_temperature
        )
        humidity = (
            target.get("target_humidity")
            if target is not None and "target_humidity" in target
            else None
        )
        day = _updated_profile(room.day_profile, temperature, humidity)
        night = _updated_profile(room.night_profile, temperature, humidity)
        updated = replace(
            room,
            day_profile=day,
            night_profile=night,
            temporary_override=None,
        )
        rooms.append(updated)
        if updated != room:
            changed.append(room.room_id)
    updated_contour = replace(contour, rooms=tuple(rooms))
    return (
        ContourRegistry(
            contours=tuple(
                updated_contour if item.contour_id == CLIMATE_CONTOUR_ID else item
                for item in current.contours
            )
        ),
        sorted(changed),
    )


def _updated_profile(
    profile: ClimateComfortSettings,
    temperature: object,
    humidity: object,
) -> ClimateComfortSettings:
    return replace(
        profile,
        target_temperature=(
            profile.target_temperature if temperature is None else temperature
        ),
        target_humidity=(profile.target_humidity if humidity is None else humidity),
    )


def _room_mappings(value: object, expected: set[str]) -> dict[str, str]:
    if not isinstance(value, list) or len(value) > 32:
        raise LegacySettingsApplyViolation("room mappings must be a bounded list")
    result: dict[str, str] = {}
    native_ids: set[str] = set()
    for raw in value:
        item = _mapping(raw, "room mapping")
        if set(item) != {"legacy_room_id", "room_id"}:
            raise LegacySettingsApplyViolation("room mapping fields are invalid")
        legacy_id = item.get("legacy_room_id")
        room_id = item.get("room_id")
        if not isinstance(legacy_id, str) or not isinstance(room_id, str):
            raise LegacySettingsApplyViolation("room mapping ids are invalid")
        if legacy_id in result or room_id in native_ids:
            raise LegacySettingsApplyViolation("room mappings must be one-to-one")
        result[legacy_id] = room_id
        native_ids.add(room_id)
    if set(result) != expected:
        raise LegacySettingsApplyViolation("every legacy climate room must be mapped")
    return result


def _settings_changes(
    previous: HausmanHubSettings,
    current: HausmanHubSettings,
) -> list[str]:
    return sorted(
        name
        for name in (
            "climate_reports_enabled",
            "curtain_holidays",
            "light_off_entities",
            "light_on_entities",
            "tv_off_entities",
        )
        if getattr(previous, name) != getattr(current, name)
    )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise LegacySettingsApplyViolation(f"{label} must be an object")
    return value
