"""Persistence and shadow reconciliation use cases for the climate registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.climate import (
    ClimateCapability,
    ClimateControlChannel,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateHomeEnvironment,
    MAX_OUTDOOR_TEMPERATURE_SOURCES,
    MAX_ROOM_PRESENCE_ENTITIES,
    ClimateModelViolation,
    ClimateRegistry,
    ClimateRoom,
    LEGACY_REGISTRY_VERSION,
    NATIVE_REGISTRY_VERSION,
    REGISTRY_VERSION,
)
from .climate_discovery import ClimateImportSnapshot


class ClimateRegistryViolation(ValueError):
    """Stored or submitted registry JSON does not match the exact contract."""


@dataclass(frozen=True, slots=True)
class ClimateRegistryReconciliation:
    """Read-only comparison; it never imports or deletes a binding itself."""

    matched_device_ids: tuple[str, ...]
    missing_device_ids: tuple[str, ...]
    room_mismatch_device_ids: tuple[str, ...]
    unregistered_source_ids: tuple[str, ...]

    @property
    def matches(self) -> bool:
        """Return whether every stored and imported device has exact parity."""

        return not (
            self.missing_device_ids
            or self.room_mismatch_device_ids
            or self.unregistered_source_ids
        )


def registry_to_payload(registry: ClimateRegistry) -> dict[str, object]:
    """Serialize only the fixed versioned registry shape."""

    home: dict[str, object] = {
        "outdoor_temperature_entity_id": (
            registry.home.outdoor_temperature_entity_id
        ),
        "presence_entity_id": registry.home.presence_entity_id,
        "central_heating_entity_id": registry.home.central_heating_entity_id,
    }
    if registry.home.outdoor_temperature_entity_ids:
        home["outdoor_temperature_entity_ids"] = list(
            registry.home.outdoor_temperature_entity_ids
        )
    if registry.home.heating_lockout_high != 18.0:
        home["heating_lockout_high"] = registry.home.heating_lockout_high
    if registry.home.heating_lockout_low != 16.0:
        home["heating_lockout_low"] = registry.home.heating_lockout_low
    if registry.home.air_conditioner_minimum_outdoor_temperature != -5.0:
        home["air_conditioner_minimum_outdoor_temperature"] = (
            registry.home.air_conditioner_minimum_outdoor_temperature
        )
    if registry.home.central_heating_temperature_on != 35.0:
        home["central_heating_temperature_on"] = (
            registry.home.central_heating_temperature_on
        )
    if registry.home.central_heating_temperature_off != 30.0:
        home["central_heating_temperature_off"] = (
            registry.home.central_heating_temperature_off
        )
    if registry.home.interseason_enabled:
        home["interseason_enabled"] = True
    if registry.home.interseason_outdoor_max_c != 22.0:
        home["interseason_outdoor_max_c"] = registry.home.interseason_outdoor_max_c
    if registry.home.interseason_cooling_start_gap != 2.0:
        home["interseason_cooling_start_gap"] = (
            registry.home.interseason_cooling_start_gap
        )
    if not registry.home.interseason_window_open_off:
        home["interseason_window_open_off"] = False
    if registry.home.interseason_date_start is not None:
        home["interseason_date_start"] = list(registry.home.interseason_date_start)
    if registry.home.interseason_date_end is not None:
        home["interseason_date_end"] = list(registry.home.interseason_date_end)
    if registry.home.interseason_updated_at is not None:
        home["interseason_updated_at"] = registry.home.interseason_updated_at
    return {
        "version": registry.version,
        "home": home,
        "rooms": [
            _room_payload(room)
            for room in registry.rooms
        ],
        "devices": [
            {
                "id": device.device_id,
                "name": device.name,
                "room_id": device.room_id,
                "kind": device.kind.value,
                "source_id": device.source_id,
                "control_scope": device.control_scope.value,
                "control_owner": device.control_owner.value,
                "capabilities": [value.value for value in device.capabilities],
                "endpoints": [
                    {"role": endpoint.role.value, "entity_id": endpoint.entity_id}
                    for endpoint in device.endpoints
                ],
                "control_channel": (
                    None
                    if device.control_channel is None
                    else device.control_channel.value
                ),
            }
            for device in registry.devices
        ],
    }


def registry_from_payload(payload: object) -> ClimateRegistry:
    """Load an exact persisted/admin registry without permissive coercion."""

    root = _exact_mapping(
        payload,
        {"version", "home", "rooms", "devices"},
        "registry",
    )
    if type(root["version"]) is not int or root["version"] != REGISTRY_VERSION:
        raise ClimateRegistryViolation("unsupported climate registry version")
    home = _exact_mapping(
        root["home"],
        {
            "outdoor_temperature_entity_id",
            "outdoor_temperature_entity_ids",
            "presence_entity_id",
            "central_heating_entity_id",
            "heating_lockout_high",
            "heating_lockout_low",
            "air_conditioner_minimum_outdoor_temperature",
            "central_heating_temperature_on",
            "central_heating_temperature_off",
            "interseason_enabled",
            "interseason_outdoor_max_c",
            "interseason_cooling_start_gap",
            "interseason_window_open_off",
            "interseason_date_start",
            "interseason_date_end",
            "interseason_updated_at",
        },
        "registry home",
        optional={
            "outdoor_temperature_entity_ids",
            "heating_lockout_high",
            "heating_lockout_low",
            "air_conditioner_minimum_outdoor_temperature",
            "central_heating_temperature_on",
            "central_heating_temperature_off",
            "interseason_enabled",
            "interseason_outdoor_max_c",
            "interseason_cooling_start_gap",
            "interseason_window_open_off",
            "interseason_date_start",
            "interseason_date_end",
            "interseason_updated_at",
        },
    )
    rooms = _bounded_list(root["rooms"], "registry rooms", 128)
    devices = _bounded_list(root["devices"], "registry devices", 512)
    try:
        return ClimateRegistry(
            version=root["version"],
            home=ClimateHomeEnvironment(
                outdoor_temperature_entity_id=_optional_entity(
                    home["outdoor_temperature_entity_id"],
                    "outdoor temperature entity",
                ),
                outdoor_temperature_entity_ids=tuple(
                    _required_entity(entity_id, "outdoor temperature entity")
                    for entity_id in _bounded_list(
                        home.get("outdoor_temperature_entity_ids", []),
                        "outdoor temperature entities",
                        MAX_OUTDOOR_TEMPERATURE_SOURCES,
                    )
                ),
                presence_entity_id=_optional_entity(
                    home["presence_entity_id"],
                    "presence entity",
                ),
                central_heating_entity_id=_optional_entity(
                    home["central_heating_entity_id"],
                    "central heating entity",
                ),
                heating_lockout_high=_optional_threshold(
                    home.get("heating_lockout_high"),
                    18.0,
                    "heating lockout high threshold",
                ),
                heating_lockout_low=_optional_threshold(
                    home.get("heating_lockout_low"),
                    16.0,
                    "heating lockout low threshold",
                ),
                air_conditioner_minimum_outdoor_temperature=_optional_threshold(
                    home.get("air_conditioner_minimum_outdoor_temperature"),
                    -5.0,
                    "air conditioner minimum outdoor temperature",
                ),
                central_heating_temperature_on=_optional_threshold(
                    home.get("central_heating_temperature_on"),
                    35.0,
                    "central heating on temperature threshold",
                ),
                central_heating_temperature_off=_optional_threshold(
                    home.get("central_heating_temperature_off"),
                    30.0,
                    "central heating off temperature threshold",
                ),
                interseason_enabled=_optional_flag(
                    home.get("interseason_enabled"),
                    False,
                    "interseason enabled",
                ),
                interseason_outdoor_max_c=_optional_threshold(
                    home.get("interseason_outdoor_max_c"),
                    22.0,
                    "interseason outdoor maximum temperature",
                ),
                interseason_cooling_start_gap=_optional_threshold(
                    home.get("interseason_cooling_start_gap"),
                    2.0,
                    "interseason cooling start gap",
                ),
                interseason_window_open_off=_optional_flag(
                    home.get("interseason_window_open_off"),
                    True,
                    "interseason window-open off",
                ),
                interseason_date_start=_optional_month_day(
                    home.get("interseason_date_start"),
                    "interseason season start date",
                ),
                interseason_date_end=_optional_month_day(
                    home.get("interseason_date_end"),
                    "interseason season end date",
                ),
                interseason_updated_at=_optional_epoch(
                    home.get("interseason_updated_at"),
                    "interseason update time",
                ),
            ),
            rooms=tuple(_room(value, index) for index, value in enumerate(rooms)),
            devices=tuple(
                _device(value, index) for index, value in enumerate(devices)
            ),
        )
    except (ClimateModelViolation, ValueError) as error:
        raise ClimateRegistryViolation(str(error)) from error


def migrate_climate_registry_payload(
    storage_version: int,
    payload: object,
) -> dict[str, object]:
    """Migrate stored version-1 and version-2 registries to the current shape.

    The earlier shapes carry no control channel, so migration makes that field
    absent. Version 1 also lacks native observation bindings, which remain
    absent rather than becoming permissive defaults.
    """

    if storage_version == REGISTRY_VERSION:
        # Home Assistant also calls the migrate hook when only the minor
        # version differs; the exact round trip keeps that path safe.
        return registry_to_payload(registry_from_payload(payload))
    if storage_version == NATIVE_REGISTRY_VERSION:
        root = _exact_mapping(
            payload,
            {"version", "home", "rooms", "devices"},
            "stored registry",
        )
        if root.get("version") != NATIVE_REGISTRY_VERSION:
            raise ClimateRegistryViolation(
                "stored climate registry version does not match storage"
            )
        devices = _bounded_list(root["devices"], "registry devices", 512)
        current_payload = {
            "version": REGISTRY_VERSION,
            "home": root["home"],
            "rooms": root["rooms"],
            "devices": [
                {
                    **_exact_mapping(
                        device,
                        {
                            "id",
                            "name",
                            "room_id",
                            "kind",
                            "source_id",
                            "control_scope",
                            "control_owner",
                            "capabilities",
                            "endpoints",
                        },
                        f"stored device {index}",
                    ),
                    "control_channel": None,
                }
                for index, device in enumerate(devices)
            ],
        }
        return registry_to_payload(registry_from_payload(current_payload))
    if storage_version != LEGACY_REGISTRY_VERSION:
        raise ClimateRegistryViolation("unsupported stored climate registry version")
    root = _exact_mapping(payload, {"version", "rooms", "devices"}, "stored registry")
    if root.get("version") != LEGACY_REGISTRY_VERSION:
        raise ClimateRegistryViolation(
            "stored climate registry version does not match storage"
        )
    rooms = _bounded_list(root["rooms"], "registry rooms", 128)
    devices = _bounded_list(root["devices"], "registry devices", 512)
    try:
        migrated = ClimateRegistry(
            rooms=tuple(
                _legacy_room(value, index) for index, value in enumerate(rooms)
            ),
            devices=tuple(
                _previous_device(value, index)
                for index, value in enumerate(devices)
            ),
        )
    except (ClimateModelViolation, ValueError) as error:
        raise ClimateRegistryViolation(str(error)) from error
    return registry_to_payload(migrated)


def reconcile_climate_registry(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> ClimateRegistryReconciliation:
    """Compare exact private source bindings without modifying either side."""

    imported_by_source = {device.source_id: device for device in snapshot.devices}
    registered_sources = {device.source_id for device in registry.devices}
    matched: list[str] = []
    missing: list[str] = []
    room_mismatch: list[str] = []
    for device in registry.devices:
        imported = imported_by_source.get(device.source_id)
        if imported is None:
            missing.append(device.device_id)
        elif imported.room_id != device.room_id:
            room_mismatch.append(device.device_id)
        else:
            matched.append(device.device_id)
    return ClimateRegistryReconciliation(
        matched_device_ids=tuple(sorted(matched)),
        missing_device_ids=tuple(sorted(missing)),
        room_mismatch_device_ids=tuple(sorted(room_mismatch)),
        unregistered_source_ids=tuple(
            sorted(
                device.source_id
                for device in snapshot.devices
                if device.source_id not in registered_sources
            )
        ),
    )


def _room(value: object, index: int) -> ClimateRoom:
    item = _exact_mapping(
        value,
        {"id", "name", "window_entity_id", "presence_entity_ids"},
        f"room {index}",
        optional={"presence_entity_ids"},
    )
    presence_entity_ids = _bounded_list(
        item.get("presence_entity_ids", []),
        "room presence entities",
        MAX_ROOM_PRESENCE_ENTITIES,
    )
    return ClimateRoom(
        room_id=item["id"],  # type: ignore[arg-type]
        name=item["name"],  # type: ignore[arg-type]
        window_entity_id=_optional_entity(item["window_entity_id"], "window entity"),
        presence_entity_ids=tuple(
            _required_entity(entity_id, "room presence entity")
            for entity_id in presence_entity_ids
        ),
    )


def _legacy_room(value: object, index: int) -> ClimateRoom:
    item = _exact_mapping(value, {"id", "name"}, f"stored room {index}")
    return ClimateRoom(room_id=item["id"], name=item["name"])  # type: ignore[arg-type]


def _optional_entity(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ClimateRegistryViolation(f"{label} must be an entity or unavailable")
    return value


def _required_entity(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ClimateRegistryViolation(f"{label} must be an entity")
    return value


def _room_payload(room: ClimateRoom) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": room.room_id,
        "name": room.name,
        "window_entity_id": room.window_entity_id,
    }
    if room.presence_entity_ids:
        payload["presence_entity_ids"] = list(room.presence_entity_ids)
    return payload


def _optional_threshold(value: object, default: float, label: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClimateRegistryViolation(f"{label} must be numeric")
    return float(value)


def _optional_flag(value: object, default: bool, label: str) -> bool:
    if value is None:
        return default
    if type(value) is not bool:
        raise ClimateRegistryViolation(f"{label} must be boolean")
    return value


def _optional_month_day(value: object, label: str) -> tuple[int, int] | None:
    if value is None:
        return None
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(type(part) is not int for part in value)
    ):
        raise ClimateRegistryViolation(f"{label} must be a month/day pair")
    return (value[0], value[1])


def _optional_epoch(value: object, label: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ClimateRegistryViolation(f"{label} must be a non-negative integer")
    return value


def _device(value: object, index: int) -> ClimateDevice:
    item = _exact_mapping(
        value,
        {
            "id",
            "name",
            "room_id",
            "kind",
            "source_id",
            "control_scope",
            "control_owner",
            "capabilities",
            "endpoints",
            "control_channel",
        },
        f"device {index}",
    )
    capabilities = _bounded_list(item["capabilities"], "device capabilities", 16)
    endpoints = _bounded_list(item["endpoints"], "device endpoints", 16)
    raw_control_channel = item["control_channel"]
    return ClimateDevice(
        device_id=item["id"],  # type: ignore[arg-type]
        name=item["name"],  # type: ignore[arg-type]
        room_id=item["room_id"],  # type: ignore[arg-type]
        kind=ClimateDeviceKind(item["kind"]),
        source_id=item["source_id"],  # type: ignore[arg-type]
        control_scope=ClimateControlScope(item["control_scope"]),
        control_owner=ClimateControlOwner(item["control_owner"]),
        capabilities=tuple(ClimateCapability(value) for value in capabilities),
        endpoints=tuple(_endpoint(value, endpoint_index) for endpoint_index, value in enumerate(endpoints)),
        control_channel=(
            None
            if raw_control_channel is None
            else ClimateControlChannel(raw_control_channel)
        ),
    )


def _previous_device(value: object, index: int) -> ClimateDevice:
    item = _exact_mapping(
        value,
        {
            "id",
            "name",
            "room_id",
            "kind",
            "source_id",
            "control_scope",
            "control_owner",
            "capabilities",
            "endpoints",
        },
        f"stored device {index}",
    )
    return _device({**item, "control_channel": None}, index)


def _endpoint(value: object, index: int) -> ClimateEndpoint:
    item = _exact_mapping(value, {"role", "entity_id"}, f"endpoint {index}")
    return ClimateEndpoint(
        role=ClimateEndpointRole(item["role"]),
        entity_id=item["entity_id"],  # type: ignore[arg-type]
    )


def _exact_mapping(
    value: object,
    keys: set[str],
    label: str,
    optional: set[str] | None = None,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ClimateRegistryViolation(f"{label} must be an object")
    optional_keys = optional or set()
    present = set(value)
    if not (keys - optional_keys) <= present <= keys:
        raise ClimateRegistryViolation(f"{label} must contain only its fixed fields")
    return value


def _bounded_list(value: object, label: str, maximum: int) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ClimateRegistryViolation(f"{label} must be a bounded list")
    return value
