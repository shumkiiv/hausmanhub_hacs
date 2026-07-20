"""Describe strict Home Assistant calls for proven climate plans.

The call model is a translation boundary only: it names one approved service,
one validated entity from the private registry, and bounded values.  It can
never execute, carry an arbitrary payload, or reference a source binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from .climate import ClimateDeviceKind
from .climate_observation import ClimateFanMode
from .climate_policy import ClimateFinalDeviceAction
from .contours import ContourMode


CLIMATE_HA_CALL_MODEL_VERSION = 1
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")


class ClimateHaCallViolation(ValueError):
    """A strict Home Assistant call is forged, mixed, or contradictory."""


class ClimateHaService(StrEnum):
    """The complete whitelist of services a climate adapter may name."""

    CLIMATE_SET_HVAC_MODE = "climate.set_hvac_mode"
    CLIMATE_SET_TEMPERATURE = "climate.set_temperature"
    CLIMATE_SET_FAN_MODE = "climate.set_fan_mode"
    HUMIDIFIER_TURN_ON = "humidifier.turn_on"
    HUMIDIFIER_TURN_OFF = "humidifier.turn_off"
    HUMIDIFIER_SET_HUMIDITY = "humidifier.set_humidity"


class ClimateHaHvacMode(StrEnum):
    """The only HVAC modes a climate adapter may request."""

    COOL = "cool"
    HEAT = "heat"
    OFF = "off"


class ClimateHaCallLimit(StrEnum):
    """Bounded reason why a plan is not translated completely."""

    OBSERVE_ONLY = "observe_only"
    HOLD_STATE = "hold_state"
    UNSUPPORTED_ACTION = "unsupported_action"
    MISSING_CONTROL_ENDPOINT = "missing_control_endpoint"
    MISSING_CAPABILITY = "missing_capability"
    NOTHING_TO_TRANSLATE = "nothing_to_translate"
    QUIET_NOT_TRANSLATED = "quiet_not_translated"


_LIMIT_ORDER = tuple(ClimateHaCallLimit)


@dataclass(frozen=True, slots=True)
class ClimateHaServiceCall:
    """One approved service call with bounded values, never executable here."""

    service: ClimateHaService
    entity_id: str
    hvac_mode: ClimateHaHvacMode | None = None
    temperature: float | None = None
    fan_mode: ClimateFanMode | None = None
    humidity: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.service, ClimateHaService):
            raise ClimateHaCallViolation("call service must be approved")
        if not isinstance(self.entity_id, str) or not _ENTITY_ID.fullmatch(
            self.entity_id
        ):
            raise ClimateHaCallViolation("call target must be one HA entity")
        if self.hvac_mode is not None and not isinstance(
            self.hvac_mode,
            ClimateHaHvacMode,
        ):
            raise ClimateHaCallViolation("call hvac mode must be approved")
        if self.temperature is not None and (
            type(self.temperature) not in {int, float}
            or not 10 <= self.temperature <= 35
        ):
            raise ClimateHaCallViolation("call temperature must stay bounded")
        if self.fan_mode is not None and not isinstance(
            self.fan_mode,
            ClimateFanMode,
        ):
            raise ClimateHaCallViolation("call fan mode must be approved")
        if self.humidity is not None and (
            type(self.humidity) is not int or not 0 <= self.humidity <= 100
        ):
            raise ClimateHaCallViolation("call humidity must stay bounded")
        values = {
            ClimateHaService.CLIMATE_SET_HVAC_MODE: (self.hvac_mode is not None, 1),
            ClimateHaService.CLIMATE_SET_TEMPERATURE: (
                self.temperature is not None,
                2,
            ),
            ClimateHaService.CLIMATE_SET_FAN_MODE: (self.fan_mode is not None, 3),
            ClimateHaService.HUMIDIFIER_SET_HUMIDITY: (self.humidity is not None, 4),
            ClimateHaService.HUMIDIFIER_TURN_ON: (True, 0),
            ClimateHaService.HUMIDIFIER_TURN_OFF: (True, 0),
        }
        present, expected = values[self.service]
        fields = (
            self.hvac_mode is not None,
            self.temperature is not None,
            self.fan_mode is not None,
            self.humidity is not None,
        )
        if not present or sum(fields) != (0 if expected == 0 else 1):
            raise ClimateHaCallViolation(
                "call values must exactly match its service"
            )


@dataclass(frozen=True, slots=True)
class ClimateHaDeviceCallPlan:
    """The strict translated calls for one planned device, never a command."""

    device_id: str
    room_id: str
    kind: ClimateDeviceKind
    action: ClimateFinalDeviceAction
    calls: tuple[ClimateHaServiceCall, ...]
    limits: tuple[ClimateHaCallLimit, ...]

    def __post_init__(self) -> None:
        _stable_id(self.device_id, "call plan device id")
        _stable_id(self.room_id, "call plan room id")
        if not isinstance(self.kind, ClimateDeviceKind):
            raise ClimateHaCallViolation("call plan device kind must be approved")
        if not isinstance(self.action, ClimateFinalDeviceAction):
            raise ClimateHaCallViolation("call plan action must be approved")
        if type(self.calls) is not tuple or any(
            not isinstance(call, ClimateHaServiceCall) for call in self.calls
        ):
            raise ClimateHaCallViolation("call plan calls must be immutable")
        if type(self.limits) is not tuple or any(
            not isinstance(limit, ClimateHaCallLimit) for limit in self.limits
        ):
            raise ClimateHaCallViolation("call plan limits must be typed")
        if len(self.limits) != len(set(self.limits)):
            raise ClimateHaCallViolation("call plan limits must be unique")
        if self.limits != tuple(
            limit for limit in _LIMIT_ORDER if limit in self.limits
        ):
            raise ClimateHaCallViolation("call plan limits must use the fixed order")
        if self.calls and ClimateHaCallLimit.MISSING_CONTROL_ENDPOINT in self.limits:
            raise ClimateHaCallViolation("endpointless device cannot retain calls")


@dataclass(frozen=True, slots=True)
class ClimateHaRoomCallPlan:
    """The strict translated calls for one configured room."""

    room_id: str
    devices: tuple[ClimateHaDeviceCallPlan, ...]

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "call plan room id")
        if type(self.devices) is not tuple or any(
            not isinstance(device, ClimateHaDeviceCallPlan) for device in self.devices
        ):
            raise ClimateHaCallViolation("room call plans must be immutable")
        if len(self.devices) != len({device.device_id for device in self.devices}):
            raise ClimateHaCallViolation("room call plan devices must be unique")
        if any(device.room_id != self.room_id for device in self.devices):
            raise ClimateHaCallViolation("room call plans must stay in their room")


@dataclass(frozen=True, slots=True)
class ClimateHaCallPlanSnapshot:
    """Complete strict call translation for one isolated policy snapshot."""

    contour_id: str
    contour_mode: ContourMode
    observed_at: int
    rooms: tuple[ClimateHaRoomCallPlan, ...]
    version: int = CLIMATE_HA_CALL_MODEL_VERSION

    def __post_init__(self) -> None:
        _stable_id(self.contour_id, "call plan contour id")
        if not isinstance(self.contour_mode, ContourMode):
            raise ClimateHaCallViolation("call plan contour mode must be approved")
        if type(self.observed_at) is not int or self.observed_at < 0:
            raise ClimateHaCallViolation("call plan time must be non-negative")
        if type(self.rooms) is not tuple or any(
            not isinstance(room, ClimateHaRoomCallPlan) for room in self.rooms
        ):
            raise ClimateHaCallViolation("call plan rooms must be immutable")
        if len(self.rooms) != len({room.room_id for room in self.rooms}):
            raise ClimateHaCallViolation("call plan room ids must be unique")
        if (
            type(self.version) is not int
            or self.version != CLIMATE_HA_CALL_MODEL_VERSION
        ):
            raise ClimateHaCallViolation("climate HA call model is unsupported")

    @property
    def commands_enabled(self) -> bool:
        """Translation alone can never authorize a physical command."""

        return False

    @property
    def call_count(self) -> int:
        """Return how many strict calls the whole snapshot describes."""

        return sum(len(device.calls) for room in self.rooms for device in room.devices)

    def room(self, room_id: str) -> ClimateHaRoomCallPlan | None:
        """Return one translated room by stable HausmanHub id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)


def _stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ClimateHaCallViolation(f"{label} must be a stable HausmanHub id")
