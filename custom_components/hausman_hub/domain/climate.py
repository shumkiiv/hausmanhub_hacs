"""Climate registry model shared by every HASC outer adapter.

The model describes logical devices and their private Home Assistant bindings.
It contains no Home Assistant, HTTP, Node-RED, or storage imports.  Android
payloads are built separately and never expose an endpoint entity identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


REGISTRY_VERSION = 1
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")


class ClimateModelViolation(ValueError):
    """A climate registry value is unsafe or internally inconsistent."""


class ClimateDeviceKind(StrEnum):
    """Logical climate device kinds understood by HASC and Android."""

    AIR_CONDITIONER = "air_conditioner"
    RADIATOR_THERMOSTAT = "radiator_thermostat"
    HUMIDIFIER = "humidifier"
    FLOOR_HEATING = "floor_heating"
    TEMPERATURE_SENSOR = "temperature_sensor"
    HUMIDITY_SENSOR = "humidity_sensor"


class ClimateCapability(StrEnum):
    """Typed user-facing climate abilities; never raw service names."""

    POWER = "power"
    TARGET_TEMPERATURE = "target_temperature"
    TARGET_HUMIDITY = "target_humidity"
    HVAC_MODE = "hvac_mode"
    FAN_MODE = "fan_mode"
    AUTO_MANUAL = "auto_manual"
    TARGET_STRATEGY = "target_strategy"
    COOLDOWN = "cooldown"
    PHYSICAL_FEEDBACK = "physical_feedback"


class ClimateEndpointRole(StrEnum):
    """Private roles through which one logical device is represented in HA."""

    CONTROL = "control"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PHYSICAL_FEEDBACK = "physical_feedback"
    FAN = "fan"
    VALVE_POSITION = "valve_position"
    CHILD_LOCK = "child_lock"
    WATER_LEVEL = "water_level"


class ClimateControlScope(StrEnum):
    """How far HASC may progress for one registered logical device."""

    OBSERVED = "observed"
    CANARY = "canary"
    MANAGED = "managed"


class ClimateControlOwner(StrEnum):
    """The component that owns decisions for a registered device."""

    CLIMATE_CORE = "climate_core"
    MANUAL = "manual"
    OBSERVED = "observed"


_REQUIRED_CAPABILITIES = {
    ClimateDeviceKind.AIR_CONDITIONER: frozenset(
        {ClimateCapability.POWER, ClimateCapability.TARGET_TEMPERATURE}
    ),
    ClimateDeviceKind.RADIATOR_THERMOSTAT: frozenset(
        {ClimateCapability.TARGET_TEMPERATURE}
    ),
    ClimateDeviceKind.HUMIDIFIER: frozenset(
        {ClimateCapability.POWER, ClimateCapability.TARGET_HUMIDITY}
    ),
    ClimateDeviceKind.FLOOR_HEATING: frozenset(
        {ClimateCapability.POWER, ClimateCapability.TARGET_TEMPERATURE}
    ),
    ClimateDeviceKind.TEMPERATURE_SENSOR: frozenset(),
    ClimateDeviceKind.HUMIDITY_SENSOR: frozenset(),
}
_PASSIVE_KINDS = frozenset(
    {
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        ClimateDeviceKind.HUMIDITY_SENSOR,
    }
)


@dataclass(frozen=True, slots=True)
class ClimateRoom:
    """A stable HASC room used by the climate core and Android."""

    room_id: str
    name: str

    def __post_init__(self) -> None:
        _require_stable_id(self.room_id, "room id")
        _require_name(self.name, "room name")


@dataclass(frozen=True, slots=True)
class ClimateEndpoint:
    """One private Home Assistant entity binding for a logical device."""

    role: ClimateEndpointRole
    entity_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, ClimateEndpointRole):
            raise ClimateModelViolation("endpoint role must be approved")
        if not isinstance(self.entity_id, str) or not _ENTITY_ID.fullmatch(
            self.entity_id
        ):
            raise ClimateModelViolation("endpoint must be one Home Assistant entity")


@dataclass(frozen=True, slots=True)
class ClimateDevice:
    """One configured logical climate device with private source bindings."""

    device_id: str
    name: str
    room_id: str
    kind: ClimateDeviceKind
    source_id: str
    control_scope: ClimateControlScope
    control_owner: ClimateControlOwner
    capabilities: tuple[ClimateCapability, ...]
    endpoints: tuple[ClimateEndpoint, ...]

    def __post_init__(self) -> None:
        _require_stable_id(self.device_id, "device id")
        _require_stable_id(self.room_id, "device room id")
        _require_name(self.name, "device name")
        if not isinstance(self.kind, ClimateDeviceKind):
            raise ClimateModelViolation("device kind must be approved")
        if (
            not isinstance(self.source_id, str)
            or not self.source_id
            or self.source_id != self.source_id.strip()
        ):
            raise ClimateModelViolation("device source id is required")
        if len(self.source_id) > 255:
            raise ClimateModelViolation("device source id is too long")
        if not isinstance(self.control_scope, ClimateControlScope):
            raise ClimateModelViolation("device control scope must be approved")
        if not isinstance(self.control_owner, ClimateControlOwner):
            raise ClimateModelViolation("device control owner must be approved")
        _require_unique(self.capabilities, "device capabilities")
        _require_unique((endpoint.role for endpoint in self.endpoints), "endpoint roles")
        if any(not isinstance(value, ClimateCapability) for value in self.capabilities):
            raise ClimateModelViolation("device capability must be approved")
        if any(not isinstance(value, ClimateEndpoint) for value in self.endpoints):
            raise ClimateModelViolation("device endpoint must be approved")
        missing = _REQUIRED_CAPABILITIES[self.kind] - set(self.capabilities)
        if missing:
            raise ClimateModelViolation(
                "device is missing required capabilities: "
                + ", ".join(sorted(value.value for value in missing))
            )
        control_endpoints = [
            endpoint
            for endpoint in self.endpoints
            if endpoint.role is ClimateEndpointRole.CONTROL
        ]
        if self.kind in _PASSIVE_KINDS:
            if control_endpoints:
                raise ClimateModelViolation("passive sensor must not have a control endpoint")
            if self.control_scope is not ClimateControlScope.OBSERVED:
                raise ClimateModelViolation("passive sensor must remain observed")
        elif len(control_endpoints) != 1:
            source_managed = (
                not control_endpoints
                and self.control_owner is ClimateControlOwner.CLIMATE_CORE
                and self.control_scope is ClimateControlScope.MANAGED
            )
            if not source_managed:
                raise ClimateModelViolation("controllable device needs one control endpoint")
        if self.control_scope is ClimateControlScope.OBSERVED:
            if self.control_owner is not ClimateControlOwner.OBSERVED:
                raise ClimateModelViolation("observed device must have observed ownership")
        elif self.control_owner is ClimateControlOwner.OBSERVED:
            raise ClimateModelViolation("controllable scope needs an explicit owner")

    def supports(self, capability: ClimateCapability) -> bool:
        """Return whether the exact typed capability was configured."""

        return capability in self.capabilities

    def endpoint(self, role: ClimateEndpointRole) -> ClimateEndpoint | None:
        """Return one validated private endpoint by role."""

        return next((item for item in self.endpoints if item.role is role), None)


@dataclass(frozen=True, slots=True)
class ClimateRegistry:
    """Complete versioned HASC climate registry."""

    rooms: tuple[ClimateRoom, ...] = ()
    devices: tuple[ClimateDevice, ...] = ()
    version: int = REGISTRY_VERSION

    def __post_init__(self) -> None:
        if self.version != REGISTRY_VERSION:
            raise ClimateModelViolation("unsupported climate registry version")
        _require_unique((room.room_id for room in self.rooms), "room ids")
        _require_unique((device.device_id for device in self.devices), "device ids")
        _require_unique((device.source_id for device in self.devices), "device source ids")
        room_ids = {room.room_id for room in self.rooms}
        missing_rooms = sorted(
            {device.room_id for device in self.devices if device.room_id not in room_ids}
        )
        if missing_rooms:
            raise ClimateModelViolation(
                "devices reference unknown rooms: " + ", ".join(missing_rooms)
            )

    def room(self, room_id: str) -> ClimateRoom | None:
        """Return one configured room by stable HASC id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)

    def device(self, device_id: str) -> ClimateDevice | None:
        """Return one configured device by stable HASC id."""

        return next(
            (device for device in self.devices if device.device_id == device_id),
            None,
        )


def _require_stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ClimateModelViolation(f"{label} must be a stable lowercase id")


def _require_name(value: object, label: str) -> None:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 120:
        raise ClimateModelViolation(f"{label} must be non-empty and at most 120 characters")


def _require_unique(values: object, label: str) -> None:
    items = tuple(values)  # type: ignore[arg-type]
    if len(items) != len(set(items)):
        raise ClimateModelViolation(f"{label} must be unique")
