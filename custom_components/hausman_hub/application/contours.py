"""Use cases for HausmanHub-owned automatic contour definitions and status."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from ..domain.climate import (
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateRegistry,
)
from ..domain.contours import (
    CLIMATE_TARGET_TEMPERATURE_MAXIMUM,
    CLIMATE_TARGET_TEMPERATURE_MINIMUM,
    CLIMATE_TARGET_TEMPERATURE_STEP,
    CONTOUR_REGISTRY_VERSION,
    ClimateComfortSettings,
    ClimateContourRoom,
    ClimateProfile,
    ClimateSchedule,
    ClimateStrategy,
    ClimateTemporaryOverride,
    ContourDefinition,
    ContourEngine,
    ContourKind,
    ContourMode,
    ContourRegistry,
    ContourViolation,
    climate_contour_room,
    climate_target_temperature,
)
from .climate_import import ClimateImportSnapshot
from .climate_registry_import import (
    ClimateRegistryImportViolation,
    import_managed_climate_selection,
)
from .public_climate_values import (
    public_climate_display_names,
    public_room_mode,
    public_strategy,
)


CLIMATE_CONTOUR_ID = "climate"
CONTOUR_CONTRACT_NAME = "hausman-hub-contours"
CONTOUR_CONTRACT_VERSION = 7
LEGACY_CONTOUR_REGISTRY_VERSION = 1
PROFILE_CONTOUR_REGISTRY_VERSION = 2
SCHEDULE_CONTOUR_REGISTRY_VERSION = 3
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
                {"id", "name", "kind", "mode", "engine", "rooms", "schedule"},
                f"contour {index}",
            )
            schedule = _climate_schedule_from_payload(
                item.get("schedule"),
                f"contour {index} schedule",
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
                        "profiles",
                        "active_profile",
                        "temporary_override",
                    },
                    f"contour {index} room {room_index}",
                )
                rooms.append(
                    climate_contour_room(
                        room_id=room.get("room_id"),
                        device_ids=room.get("device_ids"),
                        profiles=room.get("profiles"),
                        active_profile=room.get("active_profile"),
                        temporary_override=room.get("temporary_override"),
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
                    schedule=schedule,
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
                "schedule": _climate_schedule_payload(contour.schedule),
                "rooms": [
                    {
                        "room_id": room.room_id,
                        "device_ids": list(room.device_ids),
                        **climate_room_profiles(room),
                        "temporary_override": _temporary_override_payload(room),
                    }
                    for room in contour.rooms
                ],
            }
            for contour in registry.contours
        ],
    }


def migrate_contour_registry_payload(
    storage_version: int,
    payload: object,
) -> dict[str, object]:
    """Migrate older room/profile shapes to the explicit schedule shape."""

    if storage_version == CONTOUR_REGISTRY_VERSION:
        return contour_registry_to_payload(contour_registry_from_payload(payload))
    if storage_version not in {
        LEGACY_CONTOUR_REGISTRY_VERSION,
        PROFILE_CONTOUR_REGISTRY_VERSION,
        SCHEDULE_CONTOUR_REGISTRY_VERSION,
    }:
        raise ContourRegistryViolation("unsupported stored contour version")
    root = _mapping(payload, "older contour registry")
    _exact_keys(root, {"version", "contours"}, "older contour registry")
    if root.get("version") != storage_version:
        raise ContourRegistryViolation("stored contour version does not match storage")
    contours: list[ContourDefinition] = []
    try:
        for index, raw in enumerate(_list(root.get("contours"), "older contours")):
            item = _mapping(raw, f"older contour {index}")
            item_keys = {"id", "name", "kind", "mode", "engine", "rooms"}
            if storage_version == SCHEDULE_CONTOUR_REGISTRY_VERSION:
                item_keys.add("schedule")
            _exact_keys(item, item_keys, f"older contour {index}")
            rooms: list[ClimateContourRoom] = []
            for room_index, raw_room in enumerate(
                _list(item.get("rooms"), f"older contour {index} rooms")
            ):
                room = _mapping(
                    raw_room,
                    f"older contour {index} room {room_index}",
                )
                if storage_version == LEGACY_CONTOUR_REGISTRY_VERSION:
                    _exact_keys(
                        room,
                        {
                            "room_id",
                            "device_ids",
                            "target_temperature",
                            "target_humidity",
                            "strategy",
                        },
                        f"older contour {index} room {room_index}",
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
                else:
                    _exact_keys(
                        room,
                        {"room_id", "device_ids", "profiles", "active_profile"},
                        f"older contour {index} room {room_index}",
                    )
                    rooms.append(
                        climate_contour_room(
                            room_id=room.get("room_id"),
                            device_ids=room.get("device_ids"),
                            profiles=room.get("profiles"),
                            active_profile=room.get("active_profile"),
                        )
                    )
            schedule = (
                _climate_schedule_from_payload(
                    item.get("schedule"),
                    f"older contour {index} schedule",
                )
                if storage_version == SCHEDULE_CONTOUR_REGISTRY_VERSION
                else ClimateSchedule()
            )
            contours.append(
                ContourDefinition(
                    contour_id=item.get("id"),  # type: ignore[arg-type]
                    name=item.get("name"),  # type: ignore[arg-type]
                    kind=ContourKind(item.get("kind")),
                    mode=ContourMode(item.get("mode")),
                    engine=ContourEngine(item.get("engine")),
                    rooms=tuple(rooms),
                    schedule=schedule,
                )
            )
        return contour_registry_to_payload(ContourRegistry(contours=tuple(contours)))
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


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
                    if device.control_scope not in {
                        ClimateControlScope.CANARY,
                        ClimateControlScope.MANAGED,
                    }:
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
    source_kinds: object = None,
    name: object,
    mode: object,
    target_temperature: object = None,
    target_humidity: object = None,
    strategy: object = None,
    room_parameters: object = None,
    room_profiles: object = None,
    schedule: object = None,
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
            source_kinds=source_kinds,
        )
        parameters_by_room = _climate_parameters_by_room(
            registry,
            room_parameters=room_parameters,
            target_temperature=target_temperature,
            target_humidity=target_humidity,
            strategy=strategy,
        )
        profiles_by_room = _climate_profiles_by_room(
            registry,
            parameters_by_room=parameters_by_room,
            room_profiles=room_profiles,
        )
        selected_schedule = (
            ClimateSchedule()
            if schedule is None
            else _climate_schedule_from_payload(schedule, "climate schedule")
        )
        if selected_mode is not ContourMode.AUTOMATIC and selected_schedule.enabled:
            selected_schedule = ClimateSchedule(
                enabled=False,
                day_start=selected_schedule.day_start,
                night_start=selected_schedule.night_start,
            )
        assignments = tuple(
            climate_contour_room(
                room_id=room.room_id,
                device_ids=tuple(
                    device.device_id
                    for device in registry.devices
                    if device.room_id == room.room_id
                ),
                profiles=profiles_by_room[room.room_id]["profiles"],
                active_profile=profiles_by_room[room.room_id]["active_profile"],
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
                    schedule=selected_schedule,
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


def climate_room_profiles(room: ClimateContourRoom) -> dict[str, object]:
    """Return one room's exact public day/night profile payload."""

    if not isinstance(room, ClimateContourRoom):
        raise ContourRegistryViolation("climate contour room must be validated")
    return {
        "profiles": {
            ClimateProfile.DAY.value: _comfort_payload(room.day_profile),
            ClimateProfile.NIGHT.value: _comfort_payload(room.night_profile),
        },
        "active_profile": room.active_profile.value,
    }


def with_climate_room_profiles(
    registry: ContourRegistry,
    room_profiles: object,
) -> ContourRegistry:
    """Replace all room profiles atomically without changing their devices."""

    contour = registry.contour(CLIMATE_CONTOUR_ID)
    if contour is None:
        raise ContourRegistryViolation("climate contour is not configured")
    raw = _mapping(room_profiles, "climate room profiles")
    if set(raw) != {room.room_id for room in contour.rooms}:
        raise ContourRegistryViolation(
            "climate room profiles must exactly match contour rooms"
        )
    try:
        rooms = tuple(
            _room_with_profile_payload(room, raw[room.room_id])
            for room in contour.rooms
        )
        schedule = contour.schedule
        if schedule.enabled:
            schedule = replace(schedule, last_applied_profile=None)
        updated = replace(contour, rooms=rooms, schedule=schedule)
        return ContourRegistry(
            contours=tuple(
                updated if item.contour_id == CLIMATE_CONTOUR_ID else item
                for item in registry.contours
            )
        )
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def with_active_climate_profile(
    registry: ContourRegistry,
    profile: object,
    *,
    clear_temporary: bool = True,
) -> ContourRegistry:
    """Select the same day or night profile for every climate room."""

    contour = registry.contour(CLIMATE_CONTOUR_ID)
    if contour is None:
        raise ContourRegistryViolation("climate contour is not configured")
    try:
        selected = ClimateProfile(profile)
        if type(clear_temporary) is not bool:
            raise ContourRegistryViolation(
                "temporary temperature reset flag is invalid"
            )
        updated = replace(
            contour,
            rooms=tuple(
                replace(
                    room,
                    active_profile=selected,
                    temporary_override=(
                        None if clear_temporary else room.temporary_override
                    ),
                )
                for room in contour.rooms
            ),
        )
        return ContourRegistry(
            contours=tuple(
                updated if item.contour_id == CLIMATE_CONTOUR_ID else item
                for item in registry.contours
            )
        )
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def with_climate_schedule(
    registry: ContourRegistry,
    *,
    enabled: object,
    day_start: object,
    night_start: object,
) -> ContourRegistry:
    """Replace only the climate profile schedule after exact validation."""

    contour = registry.contour(CLIMATE_CONTOUR_ID)
    if contour is None:
        raise ContourRegistryViolation("climate contour is not configured")
    try:
        selected_profile = (
            contour.schedule.last_applied_profile
            if enabled is True
            and contour.schedule.enabled
            and day_start == contour.schedule.day_start
            and night_start == contour.schedule.night_start
            else None
        )
        schedule = ClimateSchedule(
            enabled=enabled,  # type: ignore[arg-type]
            day_start=day_start,  # type: ignore[arg-type]
            night_start=night_start,  # type: ignore[arg-type]
            last_applied_profile=selected_profile,
        )
        rooms = (
            contour.rooms
            if selected_profile is not None
            else tuple(
                replace(room, temporary_override=None) for room in contour.rooms
            )
        )
        updated = replace(contour, schedule=schedule, rooms=rooms)
        return ContourRegistry(
            contours=tuple(
                updated if item.contour_id == CLIMATE_CONTOUR_ID else item
                for item in registry.contours
            )
        )
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def _climate_schedule_from_payload(payload: object, label: str) -> ClimateSchedule:
    raw = _mapping(payload, label)
    _exact_keys(
        raw,
        {"enabled", "day_start", "night_start", "last_applied_profile"},
        label,
    )
    try:
        last_applied = raw.get("last_applied_profile")
        return ClimateSchedule(
            enabled=raw.get("enabled"),  # type: ignore[arg-type]
            day_start=raw.get("day_start"),  # type: ignore[arg-type]
            night_start=raw.get("night_start"),  # type: ignore[arg-type]
            last_applied_profile=(
                None if last_applied is None else ClimateProfile(last_applied)
            ),
        )
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def _climate_schedule_payload(schedule: ClimateSchedule) -> dict[str, object]:
    if not isinstance(schedule, ClimateSchedule):
        raise ContourRegistryViolation("climate schedule must be validated")
    return {
        "enabled": schedule.enabled,
        "day_start": schedule.day_start,
        "night_start": schedule.night_start,
        "last_applied_profile": (
            None
            if schedule.last_applied_profile is None
            else schedule.last_applied_profile.value
        ),
    }


def with_applied_climate_schedule_profile(
    registry: ContourRegistry,
    profile: ClimateProfile,
) -> ContourRegistry:
    """Persist the period reservation before its physical command attempt."""

    contour = registry.contour(CLIMATE_CONTOUR_ID)
    if contour is None or not contour.schedule.enabled:
        raise ContourRegistryViolation("climate schedule is not enabled")
    if not isinstance(profile, ClimateProfile):
        raise ContourRegistryViolation("climate schedule profile is invalid")
    try:
        schedule = replace(contour.schedule, last_applied_profile=profile)
        updated = replace(contour, schedule=schedule)
        return ContourRegistry(
            contours=tuple(
                updated if item.contour_id == CLIMATE_CONTOUR_ID else item
                for item in registry.contours
            )
        )
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def with_climate_temporary_temperature(
    registry: ContourRegistry,
    *,
    room_id: object,
    target_temperature: object,
) -> ContourRegistry:
    """Set one room temperature without changing its saved day/night profile."""

    contour = registry.contour(CLIMATE_CONTOUR_ID)
    if (
        contour is None
        or contour.mode is not ContourMode.AUTOMATIC
        or not contour.schedule.enabled
    ):
        raise ContourRegistryViolation(
            "temporary temperature requires an automatic climate schedule"
        )
    if not isinstance(room_id, str):
        raise ContourRegistryViolation("temporary temperature room is invalid")
    try:
        override = ClimateTemporaryOverride(
            target_temperature=climate_target_temperature(target_temperature),
        )
        found = False
        rooms: list[ClimateContourRoom] = []
        for room in contour.rooms:
            if room.room_id == room_id:
                found = True
                rooms.append(replace(room, temporary_override=override))
            else:
                rooms.append(room)
        if not found:
            raise ContourRegistryViolation("temporary temperature room is unknown")
        updated = replace(contour, rooms=tuple(rooms))
        return ContourRegistry(
            contours=tuple(
                updated if item.contour_id == CLIMATE_CONTOUR_ID else item
                for item in registry.contours
            )
        )
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def without_climate_temporary_temperature(
    registry: ContourRegistry,
    *,
    room_id: object,
) -> ContourRegistry:
    """Return one room to its saved active profile before the next boundary."""

    contour = registry.contour(CLIMATE_CONTOUR_ID)
    if (
        contour is None
        or contour.mode is not ContourMode.AUTOMATIC
        or not contour.schedule.enabled
    ):
        raise ContourRegistryViolation(
            "temporary temperature requires an automatic climate schedule"
        )
    if not isinstance(room_id, str):
        raise ContourRegistryViolation("temporary temperature room is invalid")
    found = False
    active = False
    rooms: list[ClimateContourRoom] = []
    for room in contour.rooms:
        if room.room_id == room_id:
            found = True
            active = room.temporary_override is not None
            rooms.append(replace(room, temporary_override=None))
        else:
            rooms.append(room)
    if not found:
        raise ContourRegistryViolation("temporary temperature room is unknown")
    if not active:
        raise ContourRegistryViolation("temporary temperature is not active")
    try:
        updated = replace(contour, rooms=tuple(rooms))
        return ContourRegistry(
            contours=tuple(
                updated if item.contour_id == CLIMATE_CONTOUR_ID else item
                for item in registry.contours
            )
        )
    except (ContourViolation, TypeError, ValueError) as error:
        raise ContourRegistryViolation(str(error)) from error


def _climate_profiles_by_room(
    registry: ClimateRegistry,
    *,
    parameters_by_room: Mapping[str, dict[str, object]],
    room_profiles: object,
) -> dict[str, dict[str, object]]:
    room_ids = {room.room_id for room in registry.rooms}
    if room_profiles is None:
        saved: Mapping[str, Any] = {}
    else:
        saved = _mapping(room_profiles, "saved climate room profiles")
        if not set(saved).issubset(room_ids):
            raise ContourRegistryViolation(
                "saved climate profiles reference an unselected room"
            )
    result: dict[str, dict[str, object]] = {}
    for room_id in room_ids:
        active = parameters_by_room[room_id]
        prior = saved.get(room_id)
        if prior is None:
            result[room_id] = {
                "profiles": {
                    ClimateProfile.DAY.value: dict(active),
                    ClimateProfile.NIGHT.value: dict(active),
                },
                "active_profile": ClimateProfile.DAY.value,
            }
            continue
        normalized = climate_room_profiles(
            _room_with_profile_payload(
                climate_contour_room(
                    room_id=room_id,
                    device_ids=("device",),
                    target_temperature=active["target_temperature"],
                    target_humidity=active["target_humidity"],
                    strategy=active["strategy"],
                ),
                prior,
            )
        )
        selected = str(normalized["active_profile"])
        raw_profiles = normalized["profiles"]
        if not isinstance(raw_profiles, dict):
            raise ContourRegistryViolation("normalized climate profiles are invalid")
        profiles = dict(raw_profiles)
        profiles[selected] = dict(active)
        normalized["profiles"] = profiles
        result[room_id] = normalized
    return result


def _room_with_profile_payload(
    room: ClimateContourRoom,
    payload: object,
) -> ClimateContourRoom:
    raw = _mapping(payload, "climate room profile bundle")
    _exact_keys(raw, {"profiles", "active_profile"}, "climate room profile bundle")
    return climate_contour_room(
        room_id=room.room_id,
        device_ids=room.device_ids,
        profiles=raw.get("profiles"),
        active_profile=raw.get("active_profile"),
        temporary_override=_temporary_override_payload(room),
    )


def _temporary_override_payload(
    room: ClimateContourRoom,
) -> dict[str, object] | None:
    override = room.temporary_override
    if override is None:
        return None
    return {"target_temperature": override.target_temperature}


def _comfort_payload(settings: ClimateComfortSettings) -> dict[str, object]:
    return {
        "target_temperature": settings.target_temperature,
        "target_humidity": settings.target_humidity,
        "strategy": settings.strategy.value,
    }


def _public_comfort_payload(settings: ClimateComfortSettings) -> dict[str, object]:
    return {
        "temperature": settings.target_temperature,
        "humidity": settings.target_humidity,
        "strategy": settings.strategy.value,
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
        schedule = contour.schedule
        if selected_mode is not ContourMode.AUTOMATIC and schedule.enabled:
            schedule = ClimateSchedule(
                enabled=False,
                day_start=schedule.day_start,
                night_start=schedule.night_start,
            )
        rooms = (
            contour.rooms
            if selected_mode is ContourMode.AUTOMATIC
            else tuple(
                replace(room, temporary_override=None) for room in contour.rooms
            )
        )
        updated = replace(contour, mode=selected_mode, schedule=schedule, rooms=rooms)
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
    local_now: datetime | None = None,
) -> dict[str, object]:
    """Project public contour status without private source or entity ids."""

    validate_contour_bindings(contours, climate_registry)
    projection_time = local_now or datetime.now().astimezone()
    return {
        "contract": {
            "name": CONTOUR_CONTRACT_NAME,
            "version": CONTOUR_CONTRACT_VERSION,
        },
        "display_names": public_climate_display_names(
            include_room_data_statuses=False,
        ),
        "contours": [
            _contour_status(
                contour,
                climate_registry,
                snapshot,
                settings_apply_enabled=settings_apply_enabled,
                local_now=projection_time,
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
    local_now: datetime,
) -> dict[str, object]:
    schedule = _public_schedule(contour.schedule, local_now)
    schedule_profile = contour.schedule.last_applied_profile
    schedule_ready = (
        contour.mode is ContourMode.AUTOMATIC
        and contour.schedule.enabled
        and schedule_profile is not None
        and all(room.active_profile is schedule_profile for room in contour.rooms)
    )
    temporary_temperature_available_by_room = {
        room.room_id: (
            schedule_ready
            and settings_apply_enabled
            and snapshot is not None
            and snapshot.runtime_fresh
            and _room_settings_apply_available(room, climate_registry, snapshot)
        )
        for room in contour.rooms
    }
    room_results = [
        _room_status(
            room,
            climate_registry,
            snapshot,
            temporary_temperature_available=(
                temporary_temperature_available_by_room[room.room_id]
            ),
            next_schedule_change_at=schedule["next_change_at"],
        )
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
        "schedule": schedule,
        "rooms": room_results,
        "execution": {
            "owner": contour.engine.value,
            "automatic_active": automatic_active,
            "hausmanhub_direct_commands": False,
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
            "temporary_temperature": {
                "available": any(temporary_temperature_available_by_room.values()),
                "requires_confirmation": True,
                "minimum": CLIMATE_TARGET_TEMPERATURE_MINIMUM,
                "maximum": CLIMATE_TARGET_TEMPERATURE_MAXIMUM,
                "step": CLIMATE_TARGET_TEMPERATURE_STEP,
            },
        },
        "reasons": reasons,
    }


def _room_status(
    assignment: ClimateContourRoom,
    climate_registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot | None,
    *,
    temporary_temperature_available: bool,
    next_schedule_change_at: object,
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
        "comfort_profiles": {
            "active": assignment.active_profile.value,
            ClimateProfile.DAY.value: _public_comfort_payload(
                assignment.day_profile
            ),
            ClimateProfile.NIGHT.value: _public_comfort_payload(
                assignment.night_profile
            ),
        },
        "temporary_temperature": {
            "active": assignment.temporary_override is not None,
            "temperature": (
                None
                if assignment.temporary_override is None
                else assignment.temporary_override.target_temperature
            ),
            "ends": (
                None
                if assignment.temporary_override is None
                else "next_schedule_change"
            ),
            "ends_at": (
                None
                if assignment.temporary_override is None
                else next_schedule_change_at
            ),
            "available": temporary_temperature_available,
        },
        "device_count": len(assignment.device_ids),
        "engine_mode": public_room_mode(
            None if imported_room is None else imported_room.mode
        ),
        "engine_strategy": public_strategy(
            None if imported_room is None else imported_room.target_strategy
        ),
        "authority_ready": authority_ready,
        "targets_in_sync": targets_in_sync,
        "automatic_active": (
            engine_automatic and authority_ready and available and targets_in_sync
        ),
        "reasons": reasons,
    }


def _public_schedule(
    schedule: ClimateSchedule,
    local_now: datetime,
) -> dict[str, object]:
    """Expose the next scheduled profile and its exact local timestamp."""

    next_profile: str | None = None
    next_change_at: str | None = None
    if schedule.enabled:
        profile, transition = schedule.next_transition_after(local_now)
        next_profile = profile.value
        next_change_at = transition.isoformat(timespec="seconds")
    return {
        "enabled": schedule.enabled,
        "day_start": schedule.day_start,
        "night_start": schedule.night_start,
        "next_profile": next_profile,
        "next_change_at": next_change_at,
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
