"""Native Home Assistant discovery for the climate setup wizards (36f1).

Builds the existing ``ClimateImportSnapshot`` shape from a bounded Home
Assistant entity catalog, the version-2 registry, and the native
observation, so the wizard builders can later run without the external
Climate API bridge. No consumer is switched in this sub-step.

Identity rules (locked): a bound registry device keeps its private
``source_id`` and is matched to catalog entities through
``endpoints[].entity_id``; an unbound catalog entity becomes a candidate
whose ``source_id`` is its ``entity_id`` and whose ``room_id`` is the
unassigned sentinel ``""``. Unknown facts stay fail-closed: unavailable,
empty, or absent, never permissive.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.climate import ClimateDevice, ClimateDeviceKind, ClimateRegistry
from ..domain.climate_observation import (
    ClimateDataStatus,
    ClimateObservationSnapshot,
)
from .climate_import import (
    SUPPORTED_BACKEND_COMMAND_TYPES,
    ClimateImportSnapshot,
    ImportedClimateDevice,
    ImportedClimateRoom,
)
from .climate_native_projections import native_device_command_types

CLIMATE_HA_CATALOG_DOMAINS = frozenset({"climate", "humidifier", "sensor"})
CLIMATE_HA_SENSOR_DEVICE_CLASSES = frozenset({"temperature", "humidity"})
UNASSIGNED_CANDIDATE_ROOM = ""

_CLIMATE_FEATURE_TARGET_TEMPERATURE = 1
_CLIMATE_FEATURE_FAN_MODE = 8
_CLIMATE_FEATURE_TURN_OFF = 128


class ClimateNativeSetupViolation(ValueError):
    """The native discovery inputs are unsupported or incomplete."""


@dataclass(frozen=True, slots=True)
class ClimateHaCatalogEntry:
    """One bounded Home Assistant entity candidate for climate discovery."""

    entity_id: str
    domain: str
    state: str
    device_class: str | None
    supported_features: int
    friendly_name: str | None
    available: bool
    last_updated_ms: int


@dataclass(frozen=True, slots=True)
class ClimateHaEntityCatalog:
    """An immutable bounded enumeration of climate-relevant entities."""

    entries: tuple[ClimateHaCatalogEntry, ...]

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or any(
            not isinstance(entry, ClimateHaCatalogEntry) for entry in self.entries
        ):
            raise ClimateNativeSetupViolation("catalog entries must be immutable")
        entity_ids = [entry.entity_id for entry in self.entries]
        if len(set(entity_ids)) != len(entity_ids):
            raise ClimateNativeSetupViolation("catalog entity ids must be unique")

    def entry(self, entity_id: str) -> ClimateHaCatalogEntry | None:
        """Return one catalog entry by exact entity id."""

        return next(
            (entry for entry in self.entries if entry.entity_id == entity_id),
            None,
        )


def build_native_climate_setup_snapshot(
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
    catalog: ClimateHaEntityCatalog,
) -> ClimateImportSnapshot:
    """Build the wizard discovery snapshot from native evidence only."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateNativeSetupViolation("climate registry is unavailable")
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateNativeSetupViolation("native observation is unavailable")
    if not isinstance(catalog, ClimateHaEntityCatalog):
        raise ClimateNativeSetupViolation("native entity catalog is unavailable")

    bound: list[ImportedClimateDevice] = []
    bound_entity_ids: set[str] = set()
    for device in registry.devices:
        entry = _representative_catalog_entry(device, catalog)
        bound_entity_ids.update(
            endpoint.entity_id
            for endpoint in device.endpoints
            if catalog.entry(endpoint.entity_id) is not None
        )
        bound.append(_bound_device(device, entry))
    unbound = [
        _unbound_device(entry)
        for entry in catalog.entries
        if entry.entity_id not in bound_entity_ids
    ]
    return ClimateImportSnapshot(
        generated_at=observation.observed_at,
        runtime_fresh=observation.data_status is ClimateDataStatus.FRESH,
        rooms=tuple(
            _room(registry, observation, room_id) for room_id in _room_ids(registry)
        ),
        devices=tuple(bound) + tuple(unbound),
    )


def _room_ids(registry: ClimateRegistry) -> tuple[str, ...]:
    return tuple(room.room_id for room in registry.rooms)


def _room(
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
    room_id: str,
) -> ImportedClimateRoom:
    configured = registry.room(room_id)
    observed = observation.room(room_id)
    return ImportedClimateRoom(
        room_id=room_id,
        name=(
            configured.name
            if configured is not None
            else (observed.name if observed is not None else room_id)
        ),
        temperature=None if observed is None else observed.temperature,
        humidity=None if observed is None else observed.humidity,
        target_temperature=(
            None if observed is None else observed.observed_target_temperature
        ),
        target_humidity=(
            None if observed is None else observed.observed_target_humidity
        ),
        target_strategy=(
            None if observed is None else observed.observed_target_strategy
        ),
        mode=None if observed is None else observed.mode.value,
        authority_eligible=bool(
            observed is not None and observed.authority_eligible
        ),
    )


def _representative_catalog_entry(
    device: ClimateDevice,
    catalog: ClimateHaEntityCatalog,
) -> ClimateHaCatalogEntry | None:
    for endpoint in device.endpoints:
        entry = catalog.entry(endpoint.entity_id)
        if entry is not None:
            return entry
    return None


def _bound_device(
    device: ClimateDevice,
    entry: ClimateHaCatalogEntry | None,
) -> ImportedClimateDevice:
    return ImportedClimateDevice(
        source_id=device.source_id,
        name=device.name,
        room_id=device.room_id,
        domain="" if entry is None else entry.domain,
        category=(
            "" if entry is None else (entry.device_class or entry.domain)
        ),
        state="" if entry is None else entry.state,
        available=bool(entry is not None and entry.available),
        command_types=native_device_command_types(device),
        suggested_kinds=(device.kind,),
    )


def _unbound_device(entry: ClimateHaCatalogEntry) -> ImportedClimateDevice:
    return ImportedClimateDevice(
        source_id=entry.entity_id,
        name=entry.friendly_name or entry.entity_id,
        room_id=UNASSIGNED_CANDIDATE_ROOM,
        domain=entry.domain,
        category=entry.device_class or entry.domain,
        state=entry.state,
        available=entry.available,
        command_types=_unbound_command_types(entry),
        suggested_kinds=_unbound_suggested_kinds(entry),
    )


def _unbound_command_types(entry: ClimateHaCatalogEntry) -> tuple[str, ...]:
    if entry.domain == "climate":
        commands = ["climate.set_hvac_mode"]
        if entry.supported_features & _CLIMATE_FEATURE_TURN_OFF:
            commands.append("climate.turn_off")
        if entry.supported_features & _CLIMATE_FEATURE_TARGET_TEMPERATURE:
            commands.append("climate.set_temperature")
        if entry.supported_features & _CLIMATE_FEATURE_FAN_MODE:
            commands.append("climate.set_fan_mode")
    elif entry.domain == "humidifier":
        commands = [
            "humidifier.turn_on",
            "humidifier.turn_off",
            "humidifier.set_humidity",
        ]
    else:
        commands = []
    return tuple(
        command for command in commands if command in SUPPORTED_BACKEND_COMMAND_TYPES
    )


def _unbound_suggested_kinds(entry: ClimateHaCatalogEntry) -> tuple[ClimateDeviceKind, ...]:
    if entry.domain == "climate":
        return (ClimateDeviceKind.AIR_CONDITIONER,)
    if entry.domain == "humidifier":
        return (ClimateDeviceKind.HUMIDIFIER,)
    if entry.device_class == "temperature":
        return (ClimateDeviceKind.TEMPERATURE_SENSOR,)
    if entry.device_class == "humidity":
        return (ClimateDeviceKind.HUMIDITY_SENSOR,)
    return ()
