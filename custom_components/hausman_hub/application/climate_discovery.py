"""Shared immutable discovery snapshot for the climate setup wizards."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.climate import ClimateDeviceKind, ClimateModelViolation, ClimateRoom


CLIMATE_API_CONTRACT_NAME = "hausman-climate"
CLIMATE_API_CONTRACT_VERSION = 1
MAX_CLIMATE_ROOMS = 128
MAX_CLIMATE_DEVICES = 512
MAX_CLIMATE_STATE_AGE_MS = 5 * 60 * 1000
MAX_FUTURE_SKEW_MS = 60 * 1000
SUPPORTED_BACKEND_COMMAND_TYPES = frozenset(
    {
        "climate.set_hvac_mode",
        "climate.set_temperature",
        "climate.set_fan_mode",
        "climate.turn_off",
        "humidifier.turn_on",
        "humidifier.turn_off",
        "humidifier.set_humidity",
        "trv.set_temperature",
        "switch.turn_on",
        "switch.turn_off",
    }
)


class ClimateImportViolation(ValueError):
    """The source snapshot is unsupported, incomplete, or unsafe."""


@dataclass(frozen=True, slots=True)
class ImportedClimateRoom:
    """One read-only room projection from the existing climate core."""

    room_id: str
    name: str
    temperature: float | None
    humidity: float | None
    target_temperature: float | None
    target_humidity: float | None
    target_strategy: str | None
    mode: str | None
    authority_eligible: bool


@dataclass(frozen=True, slots=True)
class ImportedClimateDevice:
    """A source device candidate that still requires explicit HausmanHub binding."""

    source_id: str
    name: str
    room_id: str
    domain: str
    category: str
    state: str
    available: bool
    command_types: tuple[str, ...]
    suggested_kinds: tuple[ClimateDeviceKind, ...]
    endpoints: tuple = ()
    device_group_id: str | None = None
    device_name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    image_url: str | None = None


@dataclass(frozen=True, slots=True)
class ClimateImportSnapshot:
    """The bounded state accepted from Climate API v1."""

    generated_at: int
    runtime_fresh: bool
    rooms: tuple[ImportedClimateRoom, ...]
    devices: tuple[ImportedClimateDevice, ...]

    def room(self, room_id: str) -> ImportedClimateRoom | None:
        """Return one imported room by stable id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)

    def device(self, source_id: str) -> ImportedClimateDevice | None:
        """Return one imported candidate by source-private id."""

        return next(
            (device for device in self.devices if device.source_id == source_id),
            None,
        )

