"""Pure critical sensor fault notification documents.

A critical notification appears when a sensor that takes part in a light or
climate room decision is unavailable, unknown, stale or unhealthy. This module
is pure: it validates one bounded health input and builds the contract document.
It never reads Home Assistant, never changes ownership and never sends a
physical command.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

CRITICAL_SENSOR_NOTIFICATION_CONTRACT_NAME = (
    "hausman-hub-critical-sensor-notification"
)
CRITICAL_SENSOR_NOTIFICATION_CONTRACT_VERSION = 1
CRITICAL_SENSOR_NOTIFICATION_CODE = "critical_sensor_fault"
CRITICAL_SENSOR_NOTIFICATION_CATEGORY = "critical"
CRITICAL_SENSOR_NOTIFICATION_SEVERITY = "critical"
MAX_MESSAGE_LENGTH = 300

_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class CriticalSensorNotificationViolation(ValueError):
    """A critical sensor notification input is malformed or unsafe."""


class CriticalSensorRole(StrEnum):
    """The room decision a participating sensor belongs to."""

    LIGHT = "light"
    CLIMATE = "climate"


class CriticalSensorReason(StrEnum):
    """Why a participating sensor is currently not usable."""

    UNAVAILABLE = "unavailable"
    STALE = "stale"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class CriticalSensorRecoveryAction(StrEnum):
    """The bounded action a client may surface for one fault."""

    CHECK_DEVICE = "check_device"
    WAIT = "wait"
    NONE = "none"


_ROLE_IMPACT = {
    CriticalSensorRole.LIGHT: (
        "Свет комнаты не сможет работать по этому датчику."
    ),
    CriticalSensorRole.CLIMATE: (
        "Климат комнаты не сможет работать по этому датчику."
    ),
}

_REASON_PHRASE = {
    CriticalSensorReason.UNAVAILABLE: "недоступен",
    CriticalSensorReason.UNKNOWN: "не отдаёт состояние",
    CriticalSensorReason.STALE: "передаёт устаревшие показания",
    CriticalSensorReason.UNHEALTHY: "передаёт недостоверные показания",
}

_RECOVERY_ACTION = {
    CriticalSensorReason.UNAVAILABLE: CriticalSensorRecoveryAction.CHECK_DEVICE,
    CriticalSensorReason.STALE: CriticalSensorRecoveryAction.WAIT,
    CriticalSensorReason.UNHEALTHY: CriticalSensorRecoveryAction.CHECK_DEVICE,
    CriticalSensorReason.UNKNOWN: CriticalSensorRecoveryAction.WAIT,
}


def _stable_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _STABLE_ID.fullmatch(value) is None:
        raise CriticalSensorNotificationViolation(
            f"{label} must be a stable HausmanHub id"
        )
    return value


def _text(value: object, label: str, maximum: int = 120) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CriticalSensorNotificationViolation(
            f"{label} must be non-empty text"
        )
    return value


@dataclass(frozen=True, slots=True)
class CriticalSensorHealth:
    """One evaluated participating sensor.

    ``reason`` is ``None`` only for a deliberately confirmed fresh reading.
    A missing, unknown or otherwise unconfirmed sensor must carry its fault
    reason instead, so it can never clear an active notification.
    """

    room_id: str
    room_name: str
    role: CriticalSensorRole
    sensor_id: str
    sensor_name: str
    entity_id: str | None = None
    reason: CriticalSensorReason | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "room_id", _stable_id(self.room_id, "room id"))
        object.__setattr__(self, "room_name", _text(self.room_name, "room name"))
        if not isinstance(self.role, CriticalSensorRole):
            raise CriticalSensorNotificationViolation("sensor role is invalid")
        object.__setattr__(
            self, "sensor_id", _stable_id(self.sensor_id, "sensor id")
        )
        object.__setattr__(
            self, "sensor_name", _text(self.sensor_name, "sensor name")
        )
        if self.entity_id is not None and (
            not isinstance(self.entity_id, str) or not self.entity_id
        ):
            raise CriticalSensorNotificationViolation("entity id is invalid")
        if self.reason is not None and not isinstance(
            self.reason, CriticalSensorReason
        ):
            raise CriticalSensorNotificationViolation("sensor reason is invalid")

    @property
    def key(self) -> tuple[str, str]:
        """One active entry per room and stable sensor id."""

        return (self.room_id, self.sensor_id)

    @property
    def healthy(self) -> bool:
        return self.reason is None


@dataclass(frozen=True, slots=True)
class CriticalSensorNotification:
    """One active critical sensor fault document for a room and sensor."""

    room_id: str
    room_name: str
    role: CriticalSensorRole
    sensor_id: str
    sensor_name: str
    entity_id: str | None
    reason: CriticalSensorReason
    since: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "room_id", _stable_id(self.room_id, "room id"))
        object.__setattr__(self, "room_name", _text(self.room_name, "room name"))
        if not isinstance(self.role, CriticalSensorRole):
            raise CriticalSensorNotificationViolation("sensor role is invalid")
        object.__setattr__(
            self, "sensor_id", _stable_id(self.sensor_id, "sensor id")
        )
        object.__setattr__(
            self, "sensor_name", _text(self.sensor_name, "sensor name")
        )
        if self.entity_id is not None and (
            not isinstance(self.entity_id, str) or not self.entity_id
        ):
            raise CriticalSensorNotificationViolation("entity id is invalid")
        if not isinstance(self.reason, CriticalSensorReason):
            raise CriticalSensorNotificationViolation("sensor reason is invalid")
        if type(self.since) is not int or self.since < 0:
            raise CriticalSensorNotificationViolation("since is invalid")

    @property
    def key(self) -> tuple[str, str]:
        return (self.room_id, self.sensor_id)

    @property
    def message(self) -> str:
        """A plain Russian text naming the room, sensor and decision role."""

        text = (
            f"Комната «{self.room_name}»: {self.sensor_name} "
            f"{_REASON_PHRASE[self.reason]}. {_ROLE_IMPACT[self.role]}"
        )
        if len(text) > MAX_MESSAGE_LENGTH:
            text = text[: MAX_MESSAGE_LENGTH - 1] + "…"
        return text

    @property
    def recovery_action(self) -> CriticalSensorRecoveryAction:
        return _RECOVERY_ACTION[self.reason]

    def to_payload(self) -> dict[str, object]:
        """Build the exact contract document; never a command."""

        return {
            "contract": {
                "name": CRITICAL_SENSOR_NOTIFICATION_CONTRACT_NAME,
                "version": CRITICAL_SENSOR_NOTIFICATION_CONTRACT_VERSION,
            },
            "code": CRITICAL_SENSOR_NOTIFICATION_CODE,
            "category": CRITICAL_SENSOR_NOTIFICATION_CATEGORY,
            "severity": CRITICAL_SENSOR_NOTIFICATION_SEVERITY,
            "roomId": self.room_id,
            "role": self.role.value,
            "sensorId": self.sensor_id,
            "entityId": self.entity_id,
            "reason": self.reason.value,
            "message": self.message,
            "since": self.since,
            "recoveryAction": self.recovery_action.value,
        }


def build_critical_sensor_notification(
    health: CriticalSensorHealth, *, since: int
) -> CriticalSensorNotification:
    """Build one fault notification from an evaluated sensor health."""

    if not isinstance(health, CriticalSensorHealth):
        raise CriticalSensorNotificationViolation(
            "validated critical sensor health is required"
        )
    if health.reason is None:
        raise CriticalSensorNotificationViolation(
            "a fresh sensor does not produce a fault notification"
        )
    return CriticalSensorNotification(
        room_id=health.room_id,
        room_name=health.room_name,
        role=health.role,
        sensor_id=health.sensor_id,
        sensor_name=health.sensor_name,
        entity_id=health.entity_id,
        reason=health.reason,
        since=since,
    )


def build_critical_sensor_notifications(
    healths: tuple[CriticalSensorHealth, ...] | list[CriticalSensorHealth],
    *,
    since: int,
) -> tuple[CriticalSensorNotification, ...]:
    """Build one document per faulted input, skipping confirmed fresh ones."""

    return tuple(
        build_critical_sensor_notification(health, since=since)
        for health in healths
        if health.reason is not None
    )


__all__ = [
    "CRITICAL_SENSOR_NOTIFICATION_CATEGORY",
    "CRITICAL_SENSOR_NOTIFICATION_CODE",
    "CRITICAL_SENSOR_NOTIFICATION_CONTRACT_NAME",
    "CRITICAL_SENSOR_NOTIFICATION_CONTRACT_VERSION",
    "CRITICAL_SENSOR_NOTIFICATION_SEVERITY",
    "MAX_MESSAGE_LENGTH",
    "CriticalSensorHealth",
    "CriticalSensorNotification",
    "CriticalSensorNotificationViolation",
    "CriticalSensorReason",
    "CriticalSensorRecoveryAction",
    "CriticalSensorRole",
    "build_critical_sensor_notification",
    "build_critical_sensor_notifications",
]
