"""Describe one-room ownership promotion and its redacted receipt.

Promotion moves one verified room's devices into HausmanHub management.
The receipt keeps only stable HausmanHub identifiers, bounded statuses, and
counts — never entity ids, source bindings, or backend responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


CLIMATE_OWNERSHIP_MODEL_VERSION = 1
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ClimateOwnershipViolation(ValueError):
    """An ownership decision or receipt is forged or contradictory."""


class ClimateOwnershipStatus(StrEnum):
    """The only outcomes of one room promotion."""

    PROMOTED = "promoted"
    ALREADY_MANAGED = "already_managed"
    DENIED = "denied"
    FAILED = "failed"


class ClimateOwnershipReason(StrEnum):
    """Bounded explanation of one promotion outcome."""

    ROOM_UNKNOWN = "room_unknown"
    MODE_BLOCKED = "mode_blocked"
    CONTOUR_NOT_AUTOMATIC = "contour_not_automatic"
    OBSERVATION_STALE = "observation_stale"
    ROOM_NOT_READY = "room_not_ready"
    ROOM_NOT_VERIFIED = "room_not_verified"
    DEVICE_SCOPE_MIXED = "device_scope_mixed"
    DEVICE_BINDING_MISSING = "device_binding_missing"
    REGISTRY_SAVE_FAILED = "registry_save_failed"


_REASON_ORDER = tuple(ClimateOwnershipReason)


@dataclass(frozen=True, slots=True)
class ClimateOwnershipReceipt:
    """One redacted ownership outcome without private bindings."""

    room_id: str
    observed_at: int
    status: ClimateOwnershipStatus
    reasons: tuple[ClimateOwnershipReason, ...]
    device_count: int
    promoted_count: int
    version: int = CLIMATE_OWNERSHIP_MODEL_VERSION

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "ownership room id")
        if type(self.observed_at) is not int or self.observed_at < 0:
            raise ClimateOwnershipViolation("ownership time must be non-negative")
        if not isinstance(self.status, ClimateOwnershipStatus):
            raise ClimateOwnershipViolation("ownership status must be approved")
        if type(self.reasons) is not tuple or any(
            not isinstance(reason, ClimateOwnershipReason) for reason in self.reasons
        ):
            raise ClimateOwnershipViolation("ownership reasons must be typed")
        if len(self.reasons) != len(set(self.reasons)):
            raise ClimateOwnershipViolation("ownership reasons must be unique")
        if self.reasons != tuple(
            reason for reason in _REASON_ORDER if reason in self.reasons
        ):
            raise ClimateOwnershipViolation("ownership reasons must use fixed order")
        for value, label in (
            (self.device_count, "ownership device count"),
            (self.promoted_count, "ownership promoted count"),
        ):
            if type(value) is not int or value < 0:
                raise ClimateOwnershipViolation(f"{label} must be non-negative")
        if self.promoted_count > self.device_count:
            raise ClimateOwnershipViolation("ownership cannot promote extra devices")
        if (
            type(self.version) is not int
            or self.version != CLIMATE_OWNERSHIP_MODEL_VERSION
        ):
            raise ClimateOwnershipViolation("climate ownership model is unsupported")
        if self.status is ClimateOwnershipStatus.PROMOTED:
            if self.reasons or not self.promoted_count or (
                self.promoted_count != self.device_count
            ):
                raise ClimateOwnershipViolation(
                    "promoted room requires every device promoted cleanly"
                )
        elif self.status is ClimateOwnershipStatus.ALREADY_MANAGED:
            if self.reasons or self.promoted_count:
                raise ClimateOwnershipViolation(
                    "already-managed room retains no reasons"
                )
        elif self.status is ClimateOwnershipStatus.DENIED:
            if (
                not self.reasons
                or ClimateOwnershipReason.REGISTRY_SAVE_FAILED in self.reasons
                or self.promoted_count
            ):
                raise ClimateOwnershipViolation(
                    "denied room requires bounded deny reasons"
                )
        else:
            if self.reasons != (ClimateOwnershipReason.REGISTRY_SAVE_FAILED,) or (
                self.promoted_count
            ):
                raise ClimateOwnershipViolation(
                    "failed room retains only the save failure"
                )


def _stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ClimateOwnershipViolation(f"{label} must be a stable HausmanHub id")
