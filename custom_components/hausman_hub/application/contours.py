"""Use cases for HASC-owned automatic contour definitions and status."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..domain.climate import (
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateRegistry,
)
from ..domain.contours import (
    CONTOUR_REGISTRY_VERSION,
    ClimateContourRoom,
    ContourDefinition,
    ContourEngine,
    ContourKind,
    ContourMode,
    ContourRegistry,
    ContourViolation,
    climate_contour_room,
)
from .climate_import import ClimateImportSnapshot
from .climate_registry_import import (
    ClimateRegistryImportViolation,
    import_managed_climate_selection,
)


CLIMATE_CONTOUR_ID = "climate"
CONTOUR_CONTRACT_NAME = "hausman-hasc-contours"
CONTOUR_CONTRACT_VERSION = 2
_ACTIVE_KINDS = frozenset(
    {
        ClimateDeviceKind.AIR_CONDITIONER,
        ClimateDeviceKind.RADIATOR_THERMOSTAT,
        ClimateDeviceKind.HUMIDIFIER,
        ClimateDeviceKind.FLOOR_HEATING,
    }
)
_CLIMATE_ROOM_PARAMETER_FIELDS = {
    "target_temperature",
    "target_humidity",
    "strategy",
}


class ContourRegistryViolation(ValueError):
    """A contour payload or its device binding is unsupported."""


def contour_registry_from_payload(payload: object) -> ContourRegistry:
    """Decode one exact persisted contour registry."""

    root = _mapping(payload, "contour registry")
    _exact_keys(root, {"version", "contours"}, "contour registry")
    if root.get("version") != CONTOUR_REGISTRY_VERSION:
        raise ContourRegistryViolation("unsupported contour registry version")
    raw_contours = _list(root.get("contours"), "contours")
    contours: list[ContourDefinition] = []
    try:
        for index, raw in enumerate(raw_contours):
            item = _mapping(raw, f"contour {index}")
            _exact_keys(
                item,
                {"id", "name", "kind", "mode", "engine", "rooms"},
                f"contour {index}",
            )
            raw_rooms = _list(item.get("rooms"), f"contour {index} rooms")
            rooms: list[ClimateContourRoom] = []
            for room_index, raw_room in enumerate(raw_rooms):
                room = _mapping(raw_room, f"contour {index} room {room_index}")
                _exact_keys(
                    room,
                    {
                        "room_id",
                        "device_ids",
                        "target_temperature",
                        "target_humidity",
                        "strategy",
                    },
                    f"contour {index} room {room_index}",
                )
                rooms.append(
                    climate_contour_room(
                        room_id=room.get("room_id"),
                        device_ids=room.get("device_ids"),
                        target_temperature=room.get("target_temperature"),
                        target_humidity=room.get("target_humidity"),
                        strategy=room.get("strategy"),
                    )
                )
            contours.append(
                ContourDefinition(
                    contour_id=item.get("id"),  # type: ignore[arg-type]
                    name=item.get("name"),  # type: ignore[arg-type]
                    kind=ContourKind(item.get("kind")),
                    mode=ContourMode(item.get("mode")),
                    engine=ContourEngine(item.get("engine")),
                    rooms=tuple(rooms),
                )
            )
        return ContourRegistry(contours=tuple(contours))
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def contour_registry_to_payload(registry: ContourRegistry) -> dict[str, object]:
    """Encode the exact persisted contour registry shape."""

    if not isinstance(registry, ContourRegistry):
        raise ContourRegistryViolation("contour registry must be validated")
    return {
        "version": registry.version,
        "contours": [
            {
                "id": contour.contour_id,
                "name": contour.name,
                "kind": contour.kind.value,
                "mode": contour.mode.value,
                "engine": contour.engine.value,
                "rooms": [
                    {
                        "room_id": room.room_id,
                        "device_ids": list(room.device_ids),
                        "target_temperature": room.target_temperature,
                        "target_humidity": room.target_humidity,
                        "strategy": room.strategy.value,
                    }
                    for room in contour.rooms
                ],
            }
            for contour in registry.contours
        ],
    }


def validate_contour_bindings(
    contours: ContourRegistry,
    climate_registry: ClimateRegistry,
) -> None:
    """Require every public contour room/device to exist in the private registry."""

    if not isinstance(contours, ContourRegistry) or not isinstance(
        climate_registry, ClimateRegistry
    ):
        raise ContourRegistryViolation("contour bindings need validated registries")
    for contour in contours.contours:
        for assignment in contour.rooms:
            if climate_registry.room(assignment.room_id) is None:
                raise ContourRegistryViolation("contour references an unknown room")
            active_devices = 0
            for device_id in assignment.device_ids:
                device = climate_registry.device(device_id)
                if device is None or device.room_id != assignment.room_id:
                    raise ContourRegistryViolation(
                        "contour device is missing or assigned to another room"
                    )
                if device.kind in _ACTIVE_KINDS:
                    active_devices += 1
                    if device.control_scope is not ClimateControlScope.MANAGED:
                        raise ContourRegistryViolation(
                            "automatic contour devices must be managed by their engine"
                        )
            if active_devices == 0:
                raise ContourRegistryViolation(
                    "climate contour room needs a controllable device"
                )


def build_climate_contour_setup(
    snapshot: ClimateImportSnapshot,
    *,
    room_ids: object,
    source_ids: object,
    name: object,
    mode: object,
    target_temperature: object = None,
    target_humidity: object = None,
    strategy: object = None,
    room_parameters: object = None,
) -> tuple[ClimateRegistry, ContourRegistry]:
    """Create one climate contour from explicit existing-engine selections.

    ``room_parameters`` is the normal multi-room input.  The three shared
    values remain accepted for stored tests and older internal callers, but
    both input styles can never be mixed.
    """

    if not isinstance(name, str):
        raise ContourRegistryViolation("contour name is required")
    try:
        selected_mode = ContourMode(mode)
    except (TypeError, ValueError) as error:
        raise ContourRegistryViolation("contour mode must be approved") from error
    if selected_mode is ContourMode.DISABLED:
        raise ContourRegistryViolation("new climate contour cannot start disabled")
    try:
        registry = import_managed_climate_selection(
            snapshot,
            room_ids=room_ids,
            source_ids=source_ids,
        )
        parameters_by_room = _climate_parameters_by_room(
            registry,
            room_parameters=room_parameters,
            target_temperature=target_temperature,
            target_humidity=target_humidity,
            strategy=strategy,
        )
        assignments = tuple(
            climate_contour_room(
                room_id=room.room_id,
                device_ids=tuple(
                    device.device_id
                    for device in registry.devices
                    if device.room_id == room.room_id
                ),
                **parameters_by_room[room.room_id],
            )
            for room in registry.rooms
        )
        contours = ContourRegistry(
            contours=(
                ContourDefinition(
                    contour_id=CLIMATE_CONTOUR_ID,
                    name=name,
                    kind=ContourKind.CLIMATE,
                    mode=selected_mode,
                    engine=ContourEngine.EXISTING_CLIMATE_CORE,
                    rooms=assignments,
                ),
            )
        )
        validate_contour_bindings(contours, registry)
        return registry, contours
    except (ClimateRegistryImportViolation, ContourViolation, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def climate_room_parameters(payload: object) -> dict[str, object]:
    """Validate and normalize one room's three visible comfort parameters."""

    values = _mapping(payload, "climate room parameters")
    _exact_keys(
        values,
        _CLIMATE_ROOM_PARAMETER_FIELDS,
        "climate room parameters",
    )
    try:
        validated = climate_contour_room(
            room_id="room",
            device_ids=("device",),
            target_temperature=values.get("target_temperature"),
            target_humidity=values.get("target_humidity"),
            strategy=values.get("strategy"),
        )
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error
    return {
        "target_temperature": validated.target_temperature,
        "target_humidity": validated.target_humidity,
        "strategy": validated.strategy.value,
    }


def _climate_parameters_by_room(
    registry: ClimateRegistry,
    *,
    room_parameters: object,
    target_temperature: object,
    target_humidity: object,
    strategy: object,
) -> dict[str, dict[str, object]]:
    room_ids = {room.room_id for room in registry.rooms}
    if room_parameters is None:
        shared = climate_room_parameters(
            {
                "target_temperature": target_temperature,
                "target_humidity": target_humidity,
                "strategy": strategy,
            }
        )
        return {room_id: dict(shared) for room_id in room_ids}
    if any(
        value is not None
        for value in (target_temperature, target_humidity, strategy)
    ):
        raise ContourRegistryViolation(
            "shared and per-room climate parameters cannot be mixed"
        )
    raw = _mapping(room_parameters, "climate room parameter map")
    if set(raw) != room_ids:
        raise ContourRegistryViolation(
            "climate room parameters must exactly match selected rooms"
        )
    return {
        room_id: climate_room_parameters(raw[room_id])
        for room_id in room_ids
    }


def with_climate_contour_mode(
    registry: ContourRegistry,
    mode: object,
) -> ContourRegistry:
    """Return the same registry with only the climate lifecycle changed."""

    contour = registry.contour(CLIMATE_CONTOUR_ID)
    if contour is None:
        raise ContourRegistryViolation("climate contour is not configured")
    try:
        selected_mode = ContourMode(mode)
        updated = replace(contour, mode=selected_mode)
        return ContourRegistry(
            contours=tuple(
                updated if item.contour_id == CLIMATE_CONTOUR_ID else item
                for item in registry.contours
            )
        )
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def contour_snapshot(
    contours: ContourRegistry,
    climate_registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot | None,
    *,
    settings_apply_enabled: bool = False,
) -> dict[str, object]:
    """Project public contour status without private source or entity ids."""

    validate_contour_bindings(contours, climate_registry)
    return {
        "contract": {
            "name": CONTOUR_CONTRACT_NAME,
            "version": CONTOUR_CONTRACT_VERSION,
        },
        "contours": [
            _contour_status(
                contour,
                climate_registry,
                snapshot,
                settings_apply_enabled=settings_apply_enabled,
            )
            for contour in contours.contours
        ],
    }


def _contour_status(
    contour: ContourDefinition,
    climate_registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot | None,
    *,
    settings_apply_enabled: bool,
) -> dict[str, object]:
    room_results = [
        _room_status(room, climate_registry, snapshot)
        for room in contour.rooms
    ]
    reasons = list(
        dict.fromkeys(
            reason
            for room in room_results
            for reason in room["reasons"]  # type: ignore[index]
            if isinstance(reason, str)
        )
    )
    automatic_active = bool(room_results) and all(
        room["automatic_active"] is True for room in room_results
    )
    if contour.mode is ContourMode.DISABLED:
        status = "disabled"
        automatic_active = False
    elif snapshot is None:
        status = "unavailable"
    elif not snapshot.runtime_fresh:
        status = "stale"
    elif contour.mode is ContourMode.AUTOMATIC and not automatic_active:
        status = "attention"
    elif any(room["status"] != "ready" for room in room_results):
        status = "attention"
    else:
        status = "ready"
    settings_apply_available = (
        settings_apply_enabled
        and contour.mode is ContourMode.AUTOMATIC
        and snapshot is not None
        and snapshot.runtime_fresh
        and all(
            _room_settings_apply_available(room, climate_registry, snapshot)
            for room in contour.rooms
        )
    )
    return {
        "id": contour.contour_id,
        "name": contour.name,
        "kind": contour.kind.value,
        "mode": contour.mode.value,
        "status": status,
        "engine": {
            "name": "hausman-climate",
            "version": 1,
        },
        "rooms": room_results,
        "execution": {
            "owner": contour.engine.value,
            "automatic_active": automatic_active,
            "hasc_direct_commands": False,
            "settings_apply": {
                "available": settings_apply_available,
                "requires_confirmation": True,
                "parameters": {
                    "temperature": True,
                    "strategy": True,
                    "automatic_mode": True,
                    "humidity": False,
                },
            },
        },
        "reasons": reasons,
    }


def _room_status(
    assignment: ClimateContourRoom,
    climate_registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot | None,
) -> dict[str, object]:
    room = climate_registry.room(assignment.room_id)
    imported_room = None if snapshot is None else snapshot.room(assignment.room_id)
    devices = [
        climate_registry.device(device_id) for device_id in assignment.device_ids
    ]
    imported_devices = [
        None if snapshot is None or device is None else snapshot.device(device.source_id)
        for device in devices
    ]
    reasons: list[str] = []
    if imported_room is None:
        reasons.append("room_state_unavailable")
    if snapshot is not None and not snapshot.runtime_fresh:
        reasons.append("state_stale")
    if any(device is None or not device.available for device in imported_devices):
        reasons.append("device_unavailable")
    engine_automatic = (
        imported_room is not None
        and imported_room.mode in {"auto", "forced_auto_only"}
    )
    if imported_room is not None and not engine_automatic:
        reasons.append("engine_not_automatic")
    authority_ready = imported_room is not None and imported_room.authority_eligible
    if imported_room is not None and not authority_ready:
        reasons.append("authority_not_ready")
    temperature_matches = _same_number(
        None if imported_room is None else imported_room.target_temperature,
        assignment.target_temperature,
    )
    humidity_matches = _optional_same_number(
        None if imported_room is None else imported_room.target_humidity,
        assignment.target_humidity,
    )
    strategy_matches = (
        imported_room is not None
        and imported_room.target_strategy is not None
        and imported_room.target_strategy == assignment.strategy.value
    )
    if imported_room is not None and not temperature_matches:
        reasons.append("target_temperature_differs")
    if imported_room is not None and humidity_matches is False:
        reasons.append("target_humidity_differs")
    if imported_room is not None and imported_room.target_strategy is None:
        reasons.append("target_strategy_unavailable")
    elif imported_room is not None and not strategy_matches:
        reasons.append("target_strategy_differs")
    available = (
        imported_room is not None
        and snapshot is not None
        and snapshot.runtime_fresh
        and not any(reason in {"device_unavailable"} for reason in reasons)
    )
    targets_in_sync = temperature_matches and (
        humidity_matches is True or humidity_matches is None
    ) and strategy_matches
    return {
        "id": assignment.room_id,
        "name": None if room is None else room.name,
        "status": "ready" if available else "unavailable",
        "current": {
            "temperature": (
                None if imported_room is None else imported_room.temperature
            ),
            "humidity": None if imported_room is None else imported_room.humidity,
        },
        "targets": {
            "temperature": assignment.target_temperature,
            "humidity": assignment.target_humidity,
            "strategy": assignment.strategy.value,
        },
        "device_count": len(assignment.device_ids),
        "engine_mode": None if imported_room is None else imported_room.mode,
        "engine_strategy": (
            None if imported_room is None else imported_room.target_strategy
        ),
        "authority_ready": authority_ready,
        "targets_in_sync": targets_in_sync,
        "automatic_active": (
            engine_automatic and authority_ready and available and targets_in_sync
        ),
        "reasons": reasons,
    }


def _room_settings_apply_available(
    assignment: ClimateContourRoom,
    climate_registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> bool:
    """Mirror the typed room-command capability gate without exposing bindings."""

    room = snapshot.room(assignment.room_id)
    if room is None:
        return False
    controlled_air_conditioners = []
    for device_id in assignment.device_ids:
        device = climate_registry.device(device_id)
        imported = None if device is None else snapshot.device(device.source_id)
        if (
            device is None
            or imported is None
            or imported.room_id != assignment.room_id
            or not imported.available
        ):
            return False
        if (
            device.kind is ClimateDeviceKind.AIR_CONDITIONER
            and device.control_owner is ClimateControlOwner.CLIMATE_CORE
            and device.control_scope is ClimateControlScope.MANAGED
            and "climate.set_temperature" in imported.command_types
        ):
            controlled_air_conditioners.append(device)
    return len(controlled_air_conditioners) == 1


def _same_number(left: object, right: object) -> bool:
    return (
        not isinstance(left, bool)
        and isinstance(left, (int, float))
        and not isinstance(right, bool)
        and isinstance(right, (int, float))
        and abs(float(left) - float(right)) < 0.01
    )


def _optional_same_number(left: object, right: object) -> bool | None:
    if left is None:
        return None
    return _same_number(left, right)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContourRegistryViolation(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ContourRegistryViolation(f"{label} must be a list")
    return value


def _exact_keys(values: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(values) != expected:
        raise ContourRegistryViolation(f"{label} has unsupported or missing fields")
