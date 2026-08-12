"""Restart-safe memory preventing repeated identical climate commands."""

from __future__ import annotations

from dataclasses import dataclass
import re


CLIMATE_COMMAND_GUARD_VERSION = 1
MAX_GUARDED_CLIMATE_DEVICES = 512
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
_SCHEDULE_SLOT = re.compile(r"^\d{4}-\d{2}-\d{2}T(?:10|22):00$")


class ClimateCommandGuardViolation(ValueError):
    """Persisted command guard state is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class ClimateGuardedCommand:
    """One already attempted desired state for a logical climate device."""

    device_id: str
    fingerprint: str
    attempted_at: int

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or _STABLE_ID.fullmatch(
            self.device_id
        ) is None:
            raise ClimateCommandGuardViolation("guarded device id is invalid")
        if not isinstance(self.fingerprint, str) or _FINGERPRINT.fullmatch(
            self.fingerprint
        ) is None:
            raise ClimateCommandGuardViolation("command fingerprint is invalid")
        if type(self.attempted_at) is not int or self.attempted_at < 0:
            raise ClimateCommandGuardViolation("command attempt time is invalid")


@dataclass(frozen=True, slots=True)
class ClimateCommandGuardMemory:
    """Complete restart-safe duplicate suppression state for one entry."""

    updated_at: int
    commands: tuple[ClimateGuardedCommand, ...]
    last_scheduled_slot: str | None = None
    version: int = CLIMATE_COMMAND_GUARD_VERSION

    def __post_init__(self) -> None:
        if type(self.updated_at) is not int or self.updated_at < 0:
            raise ClimateCommandGuardViolation("command guard update time is invalid")
        if type(self.commands) is not tuple or any(
            not isinstance(command, ClimateGuardedCommand)
            for command in self.commands
        ):
            raise ClimateCommandGuardViolation("guarded commands must be immutable")
        if len(self.commands) > MAX_GUARDED_CLIMATE_DEVICES:
            raise ClimateCommandGuardViolation("too many guarded climate devices")
        if len(self.commands) != len(
            {command.device_id for command in self.commands}
        ):
            raise ClimateCommandGuardViolation("guarded device ids must be unique")
        if any(command.attempted_at > self.updated_at for command in self.commands):
            raise ClimateCommandGuardViolation("guarded command is newer than memory")
        if self.last_scheduled_slot is not None and _SCHEDULE_SLOT.fullmatch(
            self.last_scheduled_slot
        ) is None:
            raise ClimateCommandGuardViolation("scheduled synchronization slot is invalid")
        if self.version != CLIMATE_COMMAND_GUARD_VERSION:
            raise ClimateCommandGuardViolation("command guard version is unsupported")

    def command(self, device_id: str) -> ClimateGuardedCommand | None:
        """Return the latest attempted desired state for one logical device."""

        return next(
            (command for command in self.commands if command.device_id == device_id),
            None,
        )
