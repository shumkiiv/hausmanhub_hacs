"""Explicitly add one read-only Climate API candidate to a registry draft."""

from __future__ import annotations

from ..domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateModelViolation,
    ClimateRegistry,
    ClimateRoom,
)
from .climate_import import ClimateImportSnapshot, ImportedClimateDevice


class ClimateRegistryImportViolation(ValueError):
    """A selected import candidate cannot safely enter the explicit draft."""


_ACTIVE_KINDS = frozenset(
    {
        ClimateDeviceKind.AIR_CONDITIONER,
        ClimateDeviceKind.RADIATOR_THERMOSTAT,
        ClimateDeviceKind.HUMIDIFIER,
        ClimateDeviceKind.FLOOR_HEATING,
    }
)
_CONTROL_DOMAINS = {
    ClimateDeviceKind.AIR_CONDITIONER: frozenset({"climate"}),
    ClimateDeviceKind.RADIATOR_THERMOSTAT: frozenset({"climate"}),
    ClimateDeviceKind.HUMIDIFIER: frozenset({"humidifier"}),
    ClimateDeviceKind.FLOOR_HEATING: frozenset({"climate", "switch"}),
}


def add_import_candidate_to_registry(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    *,
    source_id: object,
    device_id: object,
    device_name: object,
    kind: object,
    control_scope: object,
    control_owner: object,
    control_entity_id: object = None,
    source_engine_binding: bool = False,
) -> ClimateRegistry:
    """Return a new draft after one explicit fresh candidate selection."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateRegistryImportViolation("registry draft must be validated")
    if type(source_engine_binding) is not bool:
        raise ClimateRegistryImportViolation("source-engine binding must be boolean")
    if not isinstance(snapshot, ClimateImportSnapshot) or not snapshot.runtime_fresh:
        raise ClimateRegistryImportViolation("import snapshot must be fresh")
    if not isinstance(source_id, str):
        raise ClimateRegistryImportViolation("import candidate is invalid")
    imported = snapshot.device(source_id)
    if imported is None:
        raise ClimateRegistryImportViolation("import candidate is unavailable")
    if any(device.source_id == imported.source_id for device in registry.devices):
        raise ClimateRegistryImportViolation("import candidate is already registered")
    if any(device.device_id == device_id for device in registry.devices):
        raise ClimateRegistryImportViolation("public device id is already registered")

    try:
        selected_kind = ClimateDeviceKind(kind)
        selected_scope = ClimateControlScope(control_scope)
        selected_owner = ClimateControlOwner(control_owner)
    except (TypeError, ValueError) as error:
        raise ClimateRegistryImportViolation("import selection is unsupported") from error
    if selected_kind not in imported.suggested_kinds:
        raise ClimateRegistryImportViolation("device kind was not suggested by import")

    room = snapshot.room(imported.room_id)
    if room is None:
        raise ClimateRegistryImportViolation("import candidate room is unavailable")
    rooms = registry.rooms
    if registry.room(room.room_id) is None:
        try:
            rooms = (*rooms, ClimateRoom(room_id=room.room_id, name=room.name))
        except ClimateModelViolation as error:
            raise ClimateRegistryImportViolation("imported room is invalid") from error

    try:
        device = ClimateDevice(
            device_id=device_id,  # type: ignore[arg-type]
            name=device_name,  # type: ignore[arg-type]
            room_id=imported.room_id,
            kind=selected_kind,
            source_id=imported.source_id,
            control_scope=selected_scope,
            control_owner=selected_owner,
            capabilities=_candidate_capabilities(imported, selected_kind),
            endpoints=_candidate_endpoints(
                selected_kind,
                control_entity_id,
                source_engine_binding=source_engine_binding,
            ),
        )
        return ClimateRegistry(
            version=registry.version,
            rooms=rooms,
            devices=(*registry.devices, device),
        )
    except ClimateModelViolation as error:
        raise ClimateRegistryImportViolation(str(error)) from error


def import_managed_climate_selection(
    snapshot: ClimateImportSnapshot,
    *,
    room_ids: object,
    source_ids: object,
    source_kinds: object = None,
) -> ClimateRegistry:
    """Build an exact registry from devices already owned by climate-core.

    The existing engine already owns the private device bindings. HASC therefore
    needs only the selected source candidate, inferred kind, and stable public
    HASC id; it does not ask the owner to bind the same Home Assistant entity a
    second time.
    """

    if not isinstance(snapshot, ClimateImportSnapshot) or not snapshot.runtime_fresh:
        raise ClimateRegistryImportViolation("import snapshot must be fresh")
    if not isinstance(room_ids, (list, tuple)) or any(
        not isinstance(value, str) for value in room_ids
    ):
        raise ClimateRegistryImportViolation("selected rooms must be strings")
    if not isinstance(source_ids, (list, tuple)) or any(
        not isinstance(value, str) for value in source_ids
    ):
        raise ClimateRegistryImportViolation("selected devices must be strings")
    selected_rooms = tuple(dict.fromkeys(room_ids))
    selected_sources = tuple(dict.fromkeys(source_ids))
    if not selected_rooms or len(selected_rooms) != len(room_ids):
        raise ClimateRegistryImportViolation("selected rooms must be non-empty and unique")
    if not selected_sources or len(selected_sources) != len(source_ids):
        raise ClimateRegistryImportViolation("selected devices must be non-empty and unique")
    if source_kinds is None:
        selected_kinds: dict[str, ClimateDeviceKind] = {}
    elif not isinstance(source_kinds, dict) or set(source_kinds) != set(
        selected_sources
    ):
        raise ClimateRegistryImportViolation(
            "selected device kinds must exactly match selected devices"
        )
    else:
        try:
            selected_kinds = {
                source_id: ClimateDeviceKind(source_kinds[source_id])
                for source_id in selected_sources
            }
        except (TypeError, ValueError) as error:
            raise ClimateRegistryImportViolation(
                "selected device kind is unsupported"
            ) from error

    room_set = set(selected_rooms)
    rooms: list[ClimateRoom] = []
    for room_id in selected_rooms:
        imported_room = snapshot.room(room_id)
        if imported_room is None:
            raise ClimateRegistryImportViolation("selected room is unavailable")
        rooms.append(ClimateRoom(room_id=imported_room.room_id, name=imported_room.name))
    registry = ClimateRegistry(rooms=tuple(rooms))

    candidates: list[ImportedClimateDevice] = []
    for source_id in selected_sources:
        candidate = snapshot.device(source_id)
        if (
            candidate is None
            or candidate.room_id not in room_set
            or not candidate.suggested_kinds
        ):
            raise ClimateRegistryImportViolation(
                "selected device is unavailable or outside selected rooms"
            )
        candidates.append(candidate)
    candidates.sort(key=lambda item: (item.room_id, item.name.casefold(), item.source_id))

    id_counts: dict[tuple[str, ClimateDeviceKind], int] = {}
    used_device_ids: set[str] = set()
    for candidate in candidates:
        kind = selected_kinds.get(candidate.source_id, candidate.suggested_kinds[0])
        if kind not in candidate.suggested_kinds:
            raise ClimateRegistryImportViolation(
                "selected device kind was not suggested by import"
            )
        count_key = (candidate.room_id, kind)
        id_counts[count_key] = id_counts.get(count_key, 0) + 1
        ordinal = id_counts[count_key]
        while True:
            ordinal_suffix = "" if ordinal == 1 else f"_{ordinal}"
            suffix = f"_{kind.value}{ordinal_suffix}"
            prefix_length = 64 - len(suffix)
            if prefix_length < 1:
                raise ClimateRegistryImportViolation(
                    "selected devices cannot receive unique public ids"
                )
            device_id = f"{candidate.room_id[:prefix_length]}{suffix}"
            if device_id not in used_device_ids:
                used_device_ids.add(device_id)
                id_counts[count_key] = ordinal
                break
            ordinal += 1
        passive = kind in {
            ClimateDeviceKind.TEMPERATURE_SENSOR,
            ClimateDeviceKind.HUMIDITY_SENSOR,
        }
        registry = add_import_candidate_to_registry(
            registry,
            snapshot,
            source_id=candidate.source_id,
            device_id=device_id,
            device_name=candidate.name,
            kind=kind,
            control_scope=(
                ClimateControlScope.OBSERVED
                if passive
                else ClimateControlScope.MANAGED
            ),
            control_owner=(
                ClimateControlOwner.OBSERVED
                if passive
                else ClimateControlOwner.CLIMATE_CORE
            ),
            source_engine_binding=not passive,
        )
    return registry


def candidate_control_domain(kind: object) -> str | tuple[str, ...] | None:
    """Return the exact native entity-selector domain for one suggested kind."""

    try:
        selected_kind = ClimateDeviceKind(kind)
    except (TypeError, ValueError) as error:
        raise ClimateRegistryImportViolation("device kind is unsupported") from error
    domains = _CONTROL_DOMAINS.get(selected_kind)
    if domains is None:
        return None
    values = tuple(sorted(domains))
    return values[0] if len(values) == 1 else values


def import_candidate_is_unchanged(
    previous: ClimateImportSnapshot,
    current: ClimateImportSnapshot,
    source_id: object,
) -> bool:
    """Ignore live readings but reject any selected binding/capability drift."""

    if (
        not isinstance(previous, ClimateImportSnapshot)
        or not isinstance(current, ClimateImportSnapshot)
        or current.runtime_fresh is not True
        or not isinstance(source_id, str)
    ):
        return False
    before = previous.device(source_id)
    after = current.device(source_id)
    if before is None or after is None:
        return False
    before_room = previous.room(before.room_id)
    after_room = current.room(after.room_id)
    if before_room is None or after_room is None:
        return False
    return (
        (
            before.source_id,
            before.name,
            before.room_id,
            before.domain,
            before.category,
            before.command_types,
            before.suggested_kinds,
        )
        == (
            after.source_id,
            after.name,
            after.room_id,
            after.domain,
            after.category,
            after.command_types,
            after.suggested_kinds,
        )
        and (before_room.room_id, before_room.name)
        == (after_room.room_id, after_room.name)
    )


def _candidate_capabilities(
    imported: ImportedClimateDevice,
    kind: ClimateDeviceKind,
) -> tuple[ClimateCapability, ...]:
    commands = set(imported.command_types)
    values: list[ClimateCapability] = []

    def include(capability: ClimateCapability, condition: bool) -> None:
        if condition:
            values.append(capability)

    if kind is ClimateDeviceKind.AIR_CONDITIONER:
        include(
            ClimateCapability.POWER,
            {"climate.set_hvac_mode", "climate.turn_off"}.issubset(commands),
        )
        include(
            ClimateCapability.TARGET_TEMPERATURE,
            "climate.set_temperature" in commands,
        )
        include(ClimateCapability.HVAC_MODE, "climate.set_hvac_mode" in commands)
        include(ClimateCapability.FAN_MODE, "climate.set_fan_mode" in commands)
    elif kind is ClimateDeviceKind.RADIATOR_THERMOSTAT:
        include(
            ClimateCapability.TARGET_TEMPERATURE,
            bool({"trv.set_temperature", "climate.set_temperature"} & commands),
        )
    elif kind is ClimateDeviceKind.HUMIDIFIER:
        include(
            ClimateCapability.POWER,
            {"humidifier.turn_on", "humidifier.turn_off"}.issubset(commands),
        )
        include(
            ClimateCapability.TARGET_HUMIDITY,
            "humidifier.set_humidity" in commands,
        )
    elif kind is ClimateDeviceKind.FLOOR_HEATING:
        include(
            ClimateCapability.POWER,
            {"switch.turn_on", "switch.turn_off"}.issubset(commands)
            or {"climate.set_hvac_mode", "climate.turn_off"}.issubset(commands),
        )
        include(
            ClimateCapability.TARGET_TEMPERATURE,
            "climate.set_temperature" in commands,
        )
        include(ClimateCapability.HVAC_MODE, "climate.set_hvac_mode" in commands)
    return tuple(values)


def _candidate_endpoints(
    kind: ClimateDeviceKind,
    control_entity_id: object,
    *,
    source_engine_binding: bool = False,
) -> tuple[ClimateEndpoint, ...]:
    if kind not in _ACTIVE_KINDS:
        if control_entity_id is not None and control_entity_id != "":
            raise ClimateRegistryImportViolation(
                "passive import candidate cannot receive a control entity"
            )
        return ()
    if source_engine_binding:
        if control_entity_id not in {None, ""}:
            raise ClimateRegistryImportViolation(
                "source-engine binding must not accept a Home Assistant entity"
            )
        return ()
    if not isinstance(control_entity_id, str):
        raise ClimateRegistryImportViolation(
            "controllable import candidate needs a control entity"
        )
    domain = control_entity_id.partition(".")[0]
    if domain not in _CONTROL_DOMAINS[kind]:
        raise ClimateRegistryImportViolation(
            "control entity domain does not match the device kind"
        )
    return (
        ClimateEndpoint(
            role=ClimateEndpointRole.CONTROL,
            entity_id=control_entity_id,
        ),
    )
