"""Persist bounded facts for direct Wi-Fi climate manual ownership.

The memory stores only stable HausmanHub identifiers and normalized power
phases. It never contains Home Assistant entity ids, services, or payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


CLIMATE_MANUAL_MEMORY_VERSION = 4
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ClimateManualViolation(ValueError):
    """Persisted manual-control memory is unsafe or contradictory."""


class ClimateDirectWifiPhase(StrEnum):
    """Normalized direct Wi-Fi air-conditioner power phase."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class ClimateDirectWifiState:
    """Last observation and last HausmanHub power intent for one device."""

    device_id: str
    room_id: str
    observed_phase: ClimateDirectWifiPhase
    observed_at: int
    commanded_phase: ClimateDirectWifiPhase | None = None
    commanded_at: int | None = None

    def __post_init__(self) -> None:
        _stable_id(self.device_id, "manual-control device id")
        _stable_id(self.room_id, "manual-control room id")
        if not isinstance(self.observed_phase, ClimateDirectWifiPhase):
            raise ClimateManualViolation("observed Wi-Fi phase must be approved")
        _timestamp(self.observed_at, "manual-control observation time")
        if (self.commanded_phase is None) != (self.commanded_at is None):
            raise ClimateManualViolation(
                "commanded Wi-Fi phase and time must be present together"
            )
        if self.commanded_phase is not None and not isinstance(
            self.commanded_phase, ClimateDirectWifiPhase
        ):
            raise ClimateManualViolation("commanded Wi-Fi phase must be approved")
        if self.commanded_at is not None:
            _timestamp(self.commanded_at, "manual-control command time")


@dataclass(frozen=True, slots=True)
class ClimateManualAttribution:
    """Durable public-safe provenance for a manual device exclusion."""

    device_id: str
    reason: str
    source: str
    changed_at: int
    context_id: str | None = None
    operation_id: str | None = None

    def __post_init__(self) -> None:
        _stable_id(self.device_id, "manual attribution device id")
        if self.reason not in {"external_off", "user_excluded"}:
            raise ClimateManualViolation("manual attribution reason is unsupported")
        if not isinstance(self.source, str) or not self.source:
            raise ClimateManualViolation("manual attribution source is invalid")
        _timestamp(self.changed_at, "manual attribution time")


@dataclass(frozen=True, slots=True)
class ClimateManualMemory:
    """Complete restart-safe room ownership memory for one config entry."""

    updated_at: int
    manual_room_ids: tuple[str, ...]
    manual_device_ids: tuple[str, ...]
    devices: tuple[ClimateDirectWifiState, ...]
    attributions: tuple[ClimateManualAttribution, ...] = ()
    # Bounded HA command provenance, retained only long enough to distinguish
    # a delayed state update after a process restart from an external action.
    hausman_context_ids: tuple[str, ...] = ()
    version: int = CLIMATE_MANUAL_MEMORY_VERSION

    def __post_init__(self) -> None:
        _timestamp(self.updated_at, "manual-control memory update time")
        if type(self.manual_room_ids) is not tuple:
            raise ClimateManualViolation("manual room ids must be immutable")
        for room_id in self.manual_room_ids:
            _stable_id(room_id, "manual room id")
        if len(self.manual_room_ids) != len(set(self.manual_room_ids)):
            raise ClimateManualViolation("manual room ids must be unique")
        if type(self.manual_device_ids) is not tuple:
            raise ClimateManualViolation("manual device ids must be immutable")
        for device_id in self.manual_device_ids:
            _stable_id(device_id, "manual device id")
        if len(self.manual_device_ids) != len(set(self.manual_device_ids)):
            raise ClimateManualViolation("manual device ids must be unique")
        if type(self.devices) is not tuple or any(
            not isinstance(device, ClimateDirectWifiState)
            for device in self.devices
        ):
            raise ClimateManualViolation(
                "manual-control devices must be an immutable typed tuple"
            )
        if len(self.devices) != len({device.device_id for device in self.devices}):
            raise ClimateManualViolation("manual-control device ids must be unique")
        if type(self.attributions) is not tuple or any(
            not isinstance(item, ClimateManualAttribution) for item in self.attributions
        ) or len(self.attributions) != len({item.device_id for item in self.attributions}):
            raise ClimateManualViolation("manual-control attributions are invalid")
        if (type(self.hausman_context_ids) is not tuple
                or len(self.hausman_context_ids) > 128
                or any(not isinstance(value, str) or not value or len(value) > 128
                       for value in self.hausman_context_ids)
                or len(self.hausman_context_ids) != len(set(self.hausman_context_ids))):
            raise ClimateManualViolation("Hausman context provenance is invalid")
        if self.version != CLIMATE_MANUAL_MEMORY_VERSION:
            raise ClimateManualViolation(
                "manual-control memory version is unsupported"
            )

    @property
    def commands_enabled(self) -> bool:
        """Manual memory can never authorize a physical command."""

        return False

    def device(self, device_id: str) -> ClimateDirectWifiState | None:
        """Return one remembered direct Wi-Fi device."""

        return next(
            (device for device in self.devices if device.device_id == device_id),
            None,
        )


def empty_climate_manual_memory(*, updated_at: int) -> ClimateManualMemory:
    """Create empty validated manual-control memory."""

    return ClimateManualMemory(
        updated_at=updated_at,
        manual_room_ids=(),
        manual_device_ids=(),
        devices=(),
    )


def _stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ClimateManualViolation(f"{label} must be a stable HausmanHub id")


def _timestamp(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ClimateManualViolation(f"{label} must be a non-negative integer")
