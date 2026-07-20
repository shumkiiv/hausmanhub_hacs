"""Persist only bounded facts needed to restore climate cycle protection.

The memory contains stable HausmanHub identifiers and confirmed transition
times.  It has no Home Assistant entity, source binding, service, transport,
desired state, or command authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


CLIMATE_PROTECTION_MEMORY_VERSION = 1
MAX_CONFIRMED_SHORT_CYCLES = 100
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ClimateProtectionViolation(ValueError):
    """Persisted climate protection memory is unsafe or contradictory."""


class ClimateProtectionPhase(StrEnum):
    """Normalized physical phase used only for transition protection."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class ClimateDeviceProtectionState:
    """Last confirmed transition facts for one logical air conditioner."""

    device_id: str
    room_id: str
    phase: ClimateProtectionPhase
    observed_at: int
    last_started_at: int | None
    last_stopped_at: int | None
    confirmed_short_cycle_count: int

    def __post_init__(self) -> None:
        _stable_id(self.device_id, "protection device id")
        _stable_id(self.room_id, "protection room id")
        if not isinstance(self.phase, ClimateProtectionPhase):
            raise ClimateProtectionViolation("protection phase must be approved")
        _timestamp(self.observed_at, "protection observation time")
        _optional_timestamp(self.last_started_at, "last protected start")
        _optional_timestamp(self.last_stopped_at, "last protected stop")
        if any(
            timestamp is not None and timestamp > self.observed_at
            for timestamp in (self.last_started_at, self.last_stopped_at)
        ):
            raise ClimateProtectionViolation(
                "protected transition cannot be newer than its observation"
            )
        if (
            self.phase is ClimateProtectionPhase.ACTIVE
            and self.last_started_at is None
        ):
            raise ClimateProtectionViolation(
                "active protection state requires a last start"
            )
        if (
            self.phase is ClimateProtectionPhase.INACTIVE
            and self.last_stopped_at is None
        ):
            raise ClimateProtectionViolation(
                "inactive protection state requires a last stop"
            )
        if (
            type(self.confirmed_short_cycle_count) is not int
            or not 0
            <= self.confirmed_short_cycle_count
            <= MAX_CONFIRMED_SHORT_CYCLES
        ):
            raise ClimateProtectionViolation(
                "confirmed short-cycle count must be bounded"
            )


@dataclass(frozen=True, slots=True)
class ClimateProtectionMemory:
    """Complete restart-safe protection memory for one HausmanHub entry."""

    updated_at: int
    devices: tuple[ClimateDeviceProtectionState, ...]
    version: int = CLIMATE_PROTECTION_MEMORY_VERSION

    def __post_init__(self) -> None:
        _timestamp(self.updated_at, "protection memory update time")
        if type(self.devices) is not tuple or any(
            not isinstance(device, ClimateDeviceProtectionState)
            for device in self.devices
        ):
            raise ClimateProtectionViolation(
                "protection devices must be an immutable typed tuple"
            )
        if len(self.devices) != len({device.device_id for device in self.devices}):
            raise ClimateProtectionViolation("protection device ids must be unique")
        if any(device.observed_at > self.updated_at for device in self.devices):
            raise ClimateProtectionViolation(
                "protection device cannot be newer than memory"
            )
        if (
            type(self.version) is not int
            or self.version != CLIMATE_PROTECTION_MEMORY_VERSION
        ):
            raise ClimateProtectionViolation(
                "climate protection memory version is unsupported"
            )

    @property
    def commands_enabled(self) -> bool:
        """Protection memory can never authorize a physical command."""

        return False

    def device(self, device_id: str) -> ClimateDeviceProtectionState | None:
        """Return one remembered device by stable HausmanHub id."""

        return next(
            (device for device in self.devices if device.device_id == device_id),
            None,
        )


def empty_climate_protection_memory(*, updated_at: int) -> ClimateProtectionMemory:
    """Create an explicit empty memory at one validated time."""

    return ClimateProtectionMemory(updated_at=updated_at, devices=())


def _stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ClimateProtectionViolation(f"{label} must be a stable HausmanHub id")


def _timestamp(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ClimateProtectionViolation(f"{label} must be a non-negative integer")


def _optional_timestamp(value: object, label: str) -> None:
    if value is not None:
        _timestamp(value, label)
