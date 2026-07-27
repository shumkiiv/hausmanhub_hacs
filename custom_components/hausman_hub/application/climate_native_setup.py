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

import re
from dataclasses import dataclass

from ..domain.climate import (
    ClimateDevice,
    ClimateDeviceKind,
    ClimateModelViolation,
    ClimateRegistry,
    ClimateRoom,
)
from ..domain.climate_observation import (
    ClimateDataStatus,
    ClimateObservationSnapshot,
)
from .climate_discovery import (
    MAX_CLIMATE_DEVICES,
    MAX_CLIMATE_ROOMS,
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
_TRV_MARKERS = (
    "термоголовка",
    "терморегулятор",
    "thermostatic radiator valve",
    "trvzb",
)
_TRV_TOKEN = re.compile(r"\btrv\b", re.IGNORECASE)


class ClimateNativeSetupViolation(ValueError):
    """The native discovery inputs are unsupported or incomplete."""


@dataclass(frozen=True, slots=True)
class ClimateHaCatalogRoom:
    """One stable logical room projected from a Home Assistant area."""

    room_id: str
    name: str

    def __post_init__(self) -> None:
        try:
            ClimateRoom(room_id=self.room_id, name=self.name)
        except ClimateModelViolation as error:
            raise ClimateNativeSetupViolation(
                "catalog room must have a stable id and bounded name"
            ) from error


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
    room_id: str = UNASSIGNED_CANDIDATE_ROOM
    entity_category: str | None = None
    device_group_id: str | None = None
    device_name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    image_url: str | None = None
    hvac_modes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClimateHaEntityCatalog:
    """An immutable bounded enumeration of climate-relevant entities."""

    entries: tuple[ClimateHaCatalogEntry, ...]
    rooms: tuple[ClimateHaCatalogRoom, ...] = ()

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or any(
            not isinstance(entry, ClimateHaCatalogEntry) for entry in self.entries
        ):
            raise ClimateNativeSetupViolation("catalog entries must be immutable")
        if type(self.rooms) is not tuple or any(
            not isinstance(room, ClimateHaCatalogRoom) for room in self.rooms
        ):
            raise ClimateNativeSetupViolation("catalog rooms must be immutable")
        if len(self.entries) > MAX_CLIMATE_DEVICES:
            raise ClimateNativeSetupViolation("catalog has too many entities")
        if len(self.rooms) > MAX_CLIMATE_ROOMS:
            raise ClimateNativeSetupViolation("catalog has too many rooms")
        entity_ids = [entry.entity_id for entry in self.entries]
        if len(set(entity_ids)) != len(entity_ids):
            raise ClimateNativeSetupViolation("catalog entity ids must be unique")
        room_ids = [room.room_id for room in self.rooms]
        if len(set(room_ids)) != len(room_ids):
            raise ClimateNativeSetupViolation("catalog room ids must be unique")
        known_rooms = set(room_ids)
        if any(
            not isinstance(entry.room_id, str)
            or (entry.room_id and entry.room_id not in known_rooms)
            for entry in self.entries
        ):
            raise ClimateNativeSetupViolation(
                "catalog entity room must reference a catalog room"
            )

    def entry(self, entity_id: str) -> ClimateHaCatalogEntry | None:
        """Return one catalog entry by exact entity id."""

        return next(
            (entry for entry in self.entries if entry.entity_id == entity_id),
            None,
        )

    def room(self, room_id: str) -> ClimateHaCatalogRoom | None:
        """Return one catalog room by its stable public id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)


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
            _room(registry, observation, catalog, room_id)
            for room_id in _room_ids(registry, catalog)
        ),
        devices=tuple(bound) + tuple(unbound),
    )


def _room_ids(
    registry: ClimateRegistry,
    catalog: ClimateHaEntityCatalog,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [room.room_id for room in registry.rooms]
            + [room.room_id for room in catalog.rooms]
        )
    )


def _room(
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
    catalog: ClimateHaEntityCatalog,
    room_id: str,
) -> ImportedClimateRoom:
    configured = registry.room(room_id)
    observed = observation.room(room_id)
    discovered = catalog.room(room_id)
    return ImportedClimateRoom(
        room_id=room_id,
        name=(
            configured.name
            if configured is not None
            else (
                discovered.name
                if discovered is not None
                else (observed.name if observed is not None else room_id)
            )
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
        endpoints=device.endpoints,
        device_group_id=None if entry is None else entry.device_group_id,
        device_name=None if entry is None else entry.device_name,
        manufacturer=None if entry is None else entry.manufacturer,
        model=None if entry is None else entry.model,
        image_url=None if entry is None else entry.image_url,
    )


def _unbound_device(entry: ClimateHaCatalogEntry) -> ImportedClimateDevice:
    return ImportedClimateDevice(
        source_id=entry.entity_id,
        name=entry.friendly_name or entry.entity_id,
        room_id=entry.room_id,
        domain=entry.domain,
        category=entry.device_class or entry.domain,
        state=entry.state,
        available=entry.available,
        command_types=_unbound_command_types(entry),
        suggested_kinds=_unbound_suggested_kinds(entry),
        device_group_id=entry.device_group_id,
        device_name=entry.device_name,
        manufacturer=entry.manufacturer,
        model=entry.model,
        image_url=entry.image_url,
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
        if entry.hvac_modes:
            if "cool" in entry.hvac_modes:
                return (ClimateDeviceKind.AIR_CONDITIONER,)
            if "heat" in entry.hvac_modes:
                return (ClimateDeviceKind.RADIATOR_THERMOSTAT,)
            if _is_trv(entry):
                return (ClimateDeviceKind.RADIATOR_THERMOSTAT,)
            return ()
        if _is_trv(entry):
            return (ClimateDeviceKind.RADIATOR_THERMOSTAT,)
        return (ClimateDeviceKind.AIR_CONDITIONER,)
    if entry.domain == "humidifier":
        return (ClimateDeviceKind.HUMIDIFIER,)
    if entry.device_class == "temperature":
        return (ClimateDeviceKind.TEMPERATURE_SENSOR,)
    if entry.device_class == "humidity":
        return (ClimateDeviceKind.HUMIDITY_SENSOR,)
    return ()


def _is_trv(entry: ClimateHaCatalogEntry) -> bool:
    values = (
        entry.friendly_name,
        entry.device_name,
        entry.model,
    )
    text = " ".join(value for value in values if value is not None).casefold()
    return any(marker in text for marker in _TRV_MARKERS) or bool(_TRV_TOKEN.search(text))
