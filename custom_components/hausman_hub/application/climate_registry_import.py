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
) -> ClimateRegistry:
    """Return a new draft after one explicit fresh candidate selection."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateRegistryImportViolation("registry draft must be validated")
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
            endpoints=_candidate_endpoints(selected_kind, control_entity_id),
        )
        return ClimateRegistry(
            version=registry.version,
            rooms=rooms,
            devices=(*registry.devices, device),
        )
    except ClimateModelViolation as error:
        raise ClimateRegistryImportViolation(str(error)) from error


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
) -> tuple[ClimateEndpoint, ...]:
    if kind not in _ACTIVE_KINDS:
        if control_entity_id is not None and control_entity_id != "":
            raise ClimateRegistryImportViolation(
                "passive import candidate cannot receive a control entity"
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
