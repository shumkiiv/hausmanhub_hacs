"""Explicit native Home Assistant bindings for saved climate devices.

The migration path may contain valid logical devices whose old engine owned
their private bindings.  This module lets a local administrator attach those
devices to native Home Assistant entities without recreating the contour and
without ever executing a device command.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json

from ..domain.climate import (
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateRegistry,
)
from .climate_native_setup import (
    ClimateHaCatalogEntry,
    ClimateHaEntityCatalog,
    native_candidate_kinds,
)
from .public_climate_values import public_climate_display_names

CLIMATE_DEVICE_BINDING_OPTIONS_CONTRACT = (
    "hausman-hub-climate-device-binding-options"
)
CLIMATE_DEVICE_BINDING_PREVIEW_CONTRACT = (
    "hausman-hub-climate-device-binding-preview"
)
CLIMATE_DEVICE_BINDING_RECEIPT_CONTRACT = (
    "hausman-hub-climate-device-binding-receipt"
)
CLIMATE_DEVICE_BINDING_CONTRACT_VERSION = 1
MAX_BINDING_CHANGES = 512
JSON_SAFE_INTEGER_MAXIMUM = 9_007_199_254_740_991

_PASSIVE_ROLES = {
    ClimateDeviceKind.TEMPERATURE_SENSOR: ClimateEndpointRole.TEMPERATURE,
    ClimateDeviceKind.HUMIDITY_SENSOR: ClimateEndpointRole.HUMIDITY,
}


class ClimateDeviceBindingViolation(ValueError):
    """One binding request is stale, ambiguous, or incompatible."""

    def __init__(self, message: str, *, code: str = "invalid_binding") -> None:
        super().__init__(message)
        self.code = code


def climate_device_binding_options(
    registry: ClimateRegistry,
    catalog: ClimateHaEntityCatalog,
) -> dict[str, object]:
    """Return bounded, room-aware candidates for every saved climate device."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateDeviceBindingViolation("climate registry is unavailable")
    if not isinstance(catalog, ClimateHaEntityCatalog):
        raise ClimateDeviceBindingViolation("Home Assistant catalog is unavailable")

    room_names = {room.room_id: room.name for room in registry.rooms}
    room_names.update({room.room_id: room.name for room in catalog.rooms})
    kind_names = public_climate_display_names()["device_kinds"]
    bound_entity_owner = {
        endpoint.entity_id: device.device_id
        for device in registry.devices
        for endpoint in device.endpoints
    }
    devices: list[dict[str, object]] = []
    bound_count = 0
    candidate_count = 0
    for device in registry.devices:
        role = _binding_role(device.kind)
        current = device.endpoint(role)
        current_entry = None if current is None else catalog.entry(current.entity_id)
        candidates = [
            _candidate_payload(entry, device.room_id, room_names)
            for entry in catalog.entries
            if device.kind in native_candidate_kinds(entry)
            and bound_entity_owner.get(entry.entity_id, device.device_id)
            == device.device_id
        ]
        candidates.sort(
            key=lambda item: (
                not bool(item["same_room"]),
                not bool(item["available"]),
                str(item["name"]).casefold(),
                str(item["entity_id"]),
            )
        )
        candidate_count += len(candidates)
        if current is not None:
            bound_count += 1
        devices.append(
            {
                "device_id": device.device_id,
                "name": device.name,
                "room_id": device.room_id,
                "room_name": room_names.get(device.room_id, device.room_id),
                "kind": device.kind.value,
                "kind_name": kind_names[device.kind.value],
                "role": role.value,
                "current_entity_id": None if current is None else current.entity_id,
                "current_available": bool(
                    current_entry is not None and current_entry.available
                ),
                "candidates": candidates,
            }
        )
    devices.sort(
        key=lambda item: (
            str(item["room_name"]).casefold(),
            str(item["name"]).casefold(),
            str(item["device_id"]),
        )
    )
    return {
        "contract": {
            "name": CLIMATE_DEVICE_BINDING_OPTIONS_CONTRACT,
            "version": CLIMATE_DEVICE_BINDING_CONTRACT_VERSION,
        },
        "snapshot_revision": _snapshot_revision(registry, catalog),
        "rooms": [
            {
                "id": room_id,
                "name": room_name,
                "devices": [
                    device for device in devices if device["room_id"] == room_id
                ],
            }
            for room_id, room_name in sorted(
                (
                    (room.room_id, room.name)
                    for room in registry.rooms
                    if any(device.room_id == room.room_id for device in registry.devices)
                ),
                key=lambda item: item[1].casefold(),
            )
        ],
        "summary": {
            "device_count": len(devices),
            "bound_count": bound_count,
            "missing_count": len(devices) - bound_count,
            "candidate_count": candidate_count,
        },
    }


def preview_climate_device_bindings(
    registry: ClimateRegistry,
    catalog: ClimateHaEntityCatalog,
    payload: object,
) -> dict[str, object]:
    """Validate one unchanged explicit selection without persisting it."""

    values = _binding_request(payload, require_preview_revision=False)
    expected_snapshot = _snapshot_revision(registry, catalog)
    if values["snapshot_revision"] != expected_snapshot:
        raise ClimateDeviceBindingViolation(
            "Home Assistant entities changed after the wizard was opened",
            code="snapshot_changed",
        )
    selections = _validated_selections(registry, catalog, values["bindings"])
    issues: list[dict[str, object]] = []
    for device, entry in selections:
        if entry.room_id != device.room_id:
            issues.append(
                {
                    "code": "room_mismatch",
                    "device_id": device.device_id,
                    "message": (
                        "Сущность находится в другой комнате Home Assistant. "
                        "Сначала исправьте комнату устройства."
                    ),
                }
            )
        if not entry.available:
            issues.append(
                {
                    "code": "entity_unavailable",
                    "device_id": device.device_id,
                    "message": (
                        "Сущность сейчас недоступна. Дождитесь её появления в сети "
                        "и повторите проверку."
                    ),
                }
            )
    preview_revision = _json_safe_revision(
        {
            "snapshot_revision": expected_snapshot,
            "bindings": [
                {"device_id": device.device_id, "entity_id": entry.entity_id}
                for device, entry in selections
            ],
        }
    )
    return {
        "contract": {
            "name": CLIMATE_DEVICE_BINDING_PREVIEW_CONTRACT,
            "version": CLIMATE_DEVICE_BINDING_CONTRACT_VERSION,
        },
        "snapshot_revision": expected_snapshot,
        "preview_revision": preview_revision,
        "save_allowed": not issues and bool(selections),
        "commands_sent": False,
        "issues": issues,
        "summary": {
            "selected_count": len(selections),
            "ready_count": len(selections) - len({issue["device_id"] for issue in issues}),
        },
    }


def apply_climate_device_bindings(
    registry: ClimateRegistry,
    catalog: ClimateHaEntityCatalog,
    payload: object,
) -> tuple[ClimateRegistry, dict[str, object]]:
    """Return a registry with the checked bindings and a command-free receipt."""

    values = _binding_request(payload, require_preview_revision=True)
    preview = preview_climate_device_bindings(
        registry,
        catalog,
        {
            "snapshot_revision": values["snapshot_revision"],
            "bindings": values["bindings"],
        },
    )
    if values["preview_revision"] != preview["preview_revision"]:
        raise ClimateDeviceBindingViolation(
            "binding preview changed",
            code="preview_changed",
        )
    if preview["save_allowed"] is not True:
        raise ClimateDeviceBindingViolation(
            "binding preview is not ready",
            code="binding_not_ready",
        )
    selections = _validated_selections(registry, catalog, values["bindings"])
    by_device_id = {device.device_id: entry for device, entry in selections}
    updated_devices = tuple(
        _with_binding(device, by_device_id[device.device_id])
        if device.device_id in by_device_id
        else device
        for device in registry.devices
    )
    updated = ClimateRegistry(
        version=registry.version,
        home=registry.home,
        rooms=registry.rooms,
        devices=updated_devices,
    )
    receipt = {
        "contract": {
            "name": CLIMATE_DEVICE_BINDING_RECEIPT_CONTRACT,
            "version": CLIMATE_DEVICE_BINDING_CONTRACT_VERSION,
        },
        "status": "saved",
        "updated_devices": len(selections),
        "commands_sent": False,
        "restart_required": False,
    }
    return updated, receipt


def _candidate_payload(
    entry: ClimateHaCatalogEntry,
    device_room_id: str,
    room_names: dict[str, str],
) -> dict[str, object]:
    return {
        "entity_id": entry.entity_id,
        "name": entry.friendly_name or entry.device_name or entry.entity_id,
        "room_id": entry.room_id,
        "room_name": room_names.get(entry.room_id, "Без комнаты"),
        "same_room": entry.room_id == device_room_id,
        "available": entry.available,
        "device_name": entry.device_name,
        "manufacturer": entry.manufacturer,
        "model": entry.model,
        "image_url": entry.image_url,
    }


def _binding_role(kind: ClimateDeviceKind) -> ClimateEndpointRole:
    return _PASSIVE_ROLES.get(kind, ClimateEndpointRole.CONTROL)


def _validated_selections(
    registry: ClimateRegistry,
    catalog: ClimateHaEntityCatalog,
    values: object,
) -> tuple[tuple[ClimateDevice, ClimateHaCatalogEntry], ...]:
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_BINDING_CHANGES:
        raise ClimateDeviceBindingViolation("binding selection is empty or too large")
    selections: list[tuple[ClimateDevice, ClimateHaCatalogEntry]] = []
    device_ids: set[str] = set()
    entity_ids: set[str] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"device_id", "entity_id"}:
            raise ClimateDeviceBindingViolation("binding selection is invalid")
        device_id = value.get("device_id")
        entity_id = value.get("entity_id")
        if not isinstance(device_id, str) or not isinstance(entity_id, str):
            raise ClimateDeviceBindingViolation("binding identity is invalid")
        if device_id in device_ids:
            raise ClimateDeviceBindingViolation("device binding is repeated")
        if entity_id in entity_ids:
            raise ClimateDeviceBindingViolation("entity binding is repeated")
        device = registry.device(device_id)
        entry = catalog.entry(entity_id)
        if device is None or entry is None:
            raise ClimateDeviceBindingViolation(
                "binding device or entity is unavailable",
                code="snapshot_changed",
            )
        if device.kind not in native_candidate_kinds(entry):
            raise ClimateDeviceBindingViolation("entity type does not match device")
        owner = next(
            (
                other.device_id
                for other in registry.devices
                if other.device_id != device.device_id
                and any(
                    endpoint.entity_id == entity_id
                    for endpoint in other.endpoints
                )
            ),
            None,
        )
        if owner is not None:
            raise ClimateDeviceBindingViolation(
                "entity is already bound to another device"
            )
        device_ids.add(device_id)
        entity_ids.add(entity_id)
        selections.append((device, entry))
    return tuple(selections)


def _with_binding(
    device: ClimateDevice,
    entry: ClimateHaCatalogEntry,
) -> ClimateDevice:
    role = _binding_role(device.kind)
    endpoints = tuple(endpoint for endpoint in device.endpoints if endpoint.role is not role)
    return replace(
        device,
        endpoints=(*endpoints, ClimateEndpoint(role=role, entity_id=entry.entity_id)),
    )


def _binding_request(
    payload: object,
    *,
    require_preview_revision: bool,
) -> dict[str, object]:
    required = {"snapshot_revision", "bindings"}
    if require_preview_revision:
        required.add("preview_revision")
    if not isinstance(payload, dict) or set(payload) != required:
        raise ClimateDeviceBindingViolation("binding request has unknown fields")
    for key in ("snapshot_revision", "preview_revision"):
        if key not in payload:
            continue
        value = payload[key]
        if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAXIMUM:
            raise ClimateDeviceBindingViolation("binding revision is invalid")
    return payload


def _snapshot_revision(
    registry: ClimateRegistry,
    catalog: ClimateHaEntityCatalog,
) -> int:
    return _json_safe_revision(
        {
            "registry": [
                {
                    "id": device.device_id,
                    "room_id": device.room_id,
                    "kind": device.kind.value,
                    "endpoints": [
                        [endpoint.role.value, endpoint.entity_id]
                        for endpoint in device.endpoints
                    ],
                }
                for device in registry.devices
            ],
            "catalog": [
                {
                    "entity_id": entry.entity_id,
                    "room_id": entry.room_id,
                    "available": entry.available,
                    "domain": entry.domain,
                    "device_class": entry.device_class,
                    "hvac_modes": list(entry.hvac_modes),
                    "name": entry.friendly_name,
                    "device_name": entry.device_name,
                    "model": entry.model,
                }
                for entry in catalog.entries
                if native_candidate_kinds(entry)
            ],
        }
    )


def _json_safe_revision(value: object) -> int:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).digest()
    return int.from_bytes(digest[:8], "big") & JSON_SAFE_INTEGER_MAXIMUM
