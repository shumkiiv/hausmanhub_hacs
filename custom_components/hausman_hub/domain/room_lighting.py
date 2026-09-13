"""Immutable room lighting configuration model.

This module is pure domain logic. It validates and serializes the full room
lighting model defined by the contracts package but never touches Home
Assistant services, entities or any command executor.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, tzinfo
from enum import StrEnum
import re
from typing import Any


ROOM_LIGHTING_CONFIG_NAME = "hausman-hub-room-lighting-config"
ROOM_LIGHTING_CONFIG_VERSION = 1
ROOM_LIGHTING_STORAGE_VERSION = 1
MAX_SCHEDULE_ENTRIES = 64
MAX_SWITCH_BINDINGS = 64
MAX_LIGHT_TARGETS = 64
MAX_SENSORS = 32
MAX_WIRELESS_SWITCHES = 16
MIN_BRIGHTNESS = 0
MAX_BRIGHTNESS = 100
MIN_KELVIN = 2000
MAX_KELVIN = 6500
MIN_LUX = 0
MAX_LUX = 100_000
MANUAL_PRIORITY = "manual_above_auto"

_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_TIME_OF_DAY = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_BINARY_SENSOR = re.compile(r"^binary_sensor\.[a-z0-9_]+$")
_LUX_SENSOR = re.compile(r"^sensor\.[a-z0-9_]+$")
_LIGHT_ENTITY = re.compile(r"^light\.[a-z0-9_]+$")
_SWITCH_ENTITY = re.compile(r"^switch\.[a-z0-9_]+$")
_WIRELESS_ENTITY = re.compile(r"^(?:sensor|event|binary_sensor)\.[a-z0-9_]+$")
_HA_DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DEVICE_TRIGGER_SUBTYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

_DAY_PRESETS = frozenset({"weekdays", "weekend", "all"})
_WEEKDAYS = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})
_PRESS_TYPES = frozenset({"single", "double", "long", "hold", "up", "down"})
_BUTTONS = frozenset({"left", "right", "up", "down"})


class RoomLightingViolation(ValueError):
    """A room lighting document is malformed or outside safe bounds."""


class SensorKind(StrEnum):
    PRESENCE = "presence"
    MOTION = "motion"
    ILLUMINANCE = "illuminance"


class LightKind(StrEnum):
    LIGHT = "light"
    SWITCH = "switch"


class LightRole(StrEnum):
    MAIN = "main"
    ACCENT = "accent"
    NIGHT = "night"
    MIRROR = "mirror"
    OTHER = "other"


class Button(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


class PressType(StrEnum):
    SINGLE = "single"
    DOUBLE = "double"
    LONG = "long"
    HOLD = "hold"
    UP = "up"
    DOWN = "down"


class AnchorKind(StrEnum):
    FIXED = "fixed"
    SUNRISE = "sunrise"
    SUNSET = "sunset"


class ScheduleMode(StrEnum):
    ON_PRESENCE = "on_presence"
    ALWAYS = "always"
    NIGHT_LIGHT = "night_light"
    OFF = "off"


class SwitchAction(StrEnum):
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    TOGGLE = "toggle"
    SET_MAX = "set_max"


class ReleaseMode(StrEnum):
    TIMER_AND_ABSENCE = "timer_and_absence"
    TIMER_ONLY = "timer_only"
    ABSENCE_ONLY = "absence_only"


class AwayMode(StrEnum):
    ROOM_OFF = "room_off"
    NONE = "none"


class RestoreMode(StrEnum):
    BY_CURRENT_CONDITIONS = "by_current_conditions"
    NONE = "none"


def _enum(value: object, enum_type: type[StrEnum], label: str) -> Any:
    try:
        return enum_type(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise RoomLightingViolation(f"{label} is invalid") from None


def _stable_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _STABLE_ID.fullmatch(value) is None:
        raise RoomLightingViolation(f"{label} must be a stable HausmanHub id")
    return value


def _text(value: object, label: str, maximum: int = 120) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise RoomLightingViolation(f"{label} must be non-empty text")
    return value


def _flag(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise RoomLightingViolation(f"{label} must be boolean")
    return value


def _integer(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise RoomLightingViolation(
            f"{label} must be an integer within {minimum}..{maximum}"
        )
    return value


def _optional_brightness(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label, minimum=MIN_BRIGHTNESS, maximum=MAX_BRIGHTNESS)


def _optional_kelvin(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label, minimum=MIN_KELVIN, maximum=MAX_KELVIN)


def _number(value: object, label: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RoomLightingViolation(f"{label} must be a number")
    if not minimum <= float(value) <= maximum:
        raise RoomLightingViolation(f"{label} is outside the safe range")
    return float(value)


def _days_of_week(value: object, label: str) -> str | tuple[str, ...]:
    if isinstance(value, str):
        if value not in _DAY_PRESETS:
            raise RoomLightingViolation(f"{label} preset is invalid")
        return value
    if isinstance(value, (list, tuple)):
        items = tuple(value)
        if (
            not items
            or len(items) != len(set(items))
            or any(item not in _WEEKDAYS for item in items)
        ):
            raise RoomLightingViolation(f"{label} day list is invalid")
        return items
    raise RoomLightingViolation(f"{label} must be a preset or a day list")


def _targets_tuple(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise RoomLightingViolation(f"{label} must be a list")
    items = tuple(value)
    if len(items) != len(set(items)):
        raise RoomLightingViolation(f"{label} must not repeat")
    for item in items:
        _stable_id(item, label)
    return items


def _roles_tuple(value: object, label: str) -> tuple[LightRole, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise RoomLightingViolation(f"{label} must be a list")
    items = tuple(value)
    if len(items) != len(set(items)):
        raise RoomLightingViolation(f"{label} must not repeat")
    return tuple(_enum(item, LightRole, label) for item in items)


@dataclass(frozen=True, slots=True)
class Calibration:
    offset: float = 0
    multiplier: float = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "offset",
            _number(self.offset, "calibration offset", minimum=-10000, maximum=10000),
        )
        object.__setattr__(
            self,
            "multiplier",
            _number(self.multiplier, "calibration multiplier", minimum=0.0001, maximum=100),
        )


@dataclass(frozen=True, slots=True)
class RoomSensor:
    id: str
    name: str
    kind: SensorKind
    entity_id: str | None = None
    auto_adopt_override: bool | None = None

    def __post_init__(self) -> None:
        _stable_id(self.id, "sensor id")
        _text(self.name, "sensor name")
        object.__setattr__(self, "kind", _enum(self.kind, SensorKind, "sensor kind"))
        if self.entity_id is not None:
            if not isinstance(self.entity_id, str):
                raise RoomLightingViolation("sensor entity id must be text")
            pattern = _LUX_SENSOR if self.kind is SensorKind.ILLUMINANCE else _BINARY_SENSOR
            if pattern.fullmatch(self.entity_id) is None:
                raise RoomLightingViolation(
                    "sensor entity id does not match its sensor kind"
                )
        if self.auto_adopt_override is not None:
            _flag(self.auto_adopt_override, "sensor auto adopt override")


@dataclass(frozen=True, slots=True)
class LightTarget:
    id: str
    name: str
    kind: LightKind
    brightness: bool
    color_temperature: bool
    entity_id: str | None = None
    role: LightRole | None = None
    group_id: str | None = None
    auto_adopt_override: bool | None = None
    color_temp_inverted: bool = False
    auto_control: bool = True

    def __post_init__(self) -> None:
        _stable_id(self.id, "light target id")
        _text(self.name, "light target name")
        object.__setattr__(self, "kind", _enum(self.kind, LightKind, "light target kind"))
        _flag(self.brightness, "light target brightness flag")
        _flag(self.color_temperature, "light target colour temperature flag")
        _flag(self.color_temp_inverted, "light target colour temperature inversion flag")
        _flag(self.auto_control, "light target automatic control flag")
        if self.kind is LightKind.SWITCH and (self.brightness or self.color_temperature):
            raise RoomLightingViolation(
                "an unregulated switch target cannot expose brightness or colour temperature"
            )
        if self.entity_id is not None:
            pattern = _LIGHT_ENTITY if self.kind is LightKind.LIGHT else _SWITCH_ENTITY
            if not isinstance(self.entity_id, str) or pattern.fullmatch(self.entity_id) is None:
                raise RoomLightingViolation(
                    "light target entity id does not match its kind"
                )
        if self.role is not None:
            object.__setattr__(self, "role", _enum(self.role, LightRole, "light target role"))
        if self.group_id is not None:
            _stable_id(self.group_id, "light target group id")
        if self.auto_adopt_override is not None:
            _flag(self.auto_adopt_override, "light target auto adopt override")


@dataclass(frozen=True, slots=True)
class PowerSwitch:
    id: str
    name: str
    entity_id: str | None = None
    auto_adopt_override: bool | None = None

    def __post_init__(self) -> None:
        _stable_id(self.id, "power switch id")
        _text(self.name, "power switch name")
        if self.entity_id is not None and (
            not isinstance(self.entity_id, str)
            or _SWITCH_ENTITY.fullmatch(self.entity_id) is None
        ):
            raise RoomLightingViolation("power switch entity id is invalid")
        if self.auto_adopt_override is not None:
            _flag(self.auto_adopt_override, "power switch auto adopt override")


@dataclass(frozen=True, slots=True)
class WirelessSwitch:
    id: str
    name: str
    buttons: tuple[Button, ...]
    press_types: tuple[PressType, ...]
    entity_id: str | None = None
    device_id: str | None = None
    trigger_subtypes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _stable_id(self.id, "wireless switch id")
        _text(self.name, "wireless switch name")
        if self.entity_id is not None and (
            not isinstance(self.entity_id, str)
            or _WIRELESS_ENTITY.fullmatch(self.entity_id) is None
        ):
            raise RoomLightingViolation("wireless switch entity id is invalid")
        if self.device_id is not None and (
            not isinstance(self.device_id, str)
            or _HA_DEVICE_ID.fullmatch(self.device_id) is None
        ):
            raise RoomLightingViolation("wireless switch device id is invalid")
        if not isinstance(self.buttons, tuple) or not self.buttons:
            raise RoomLightingViolation("wireless switch must declare confirmed buttons")
        normalized_buttons = tuple(_enum(button, Button, "wireless switch button") for button in self.buttons)
        if len(normalized_buttons) != len(set(normalized_buttons)):
            raise RoomLightingViolation("wireless switch buttons are invalid")
        object.__setattr__(self, "buttons", normalized_buttons)
        if not isinstance(self.press_types, tuple) or not self.press_types:
            raise RoomLightingViolation(
                "wireless switch must declare confirmed press types"
            )
        normalized_presses = tuple(
            _enum(press, PressType, "wireless switch press type") for press in self.press_types
        )
        if len(normalized_presses) != len(set(normalized_presses)):
            raise RoomLightingViolation("wireless switch press types are invalid")
        object.__setattr__(self, "press_types", normalized_presses)
        if not isinstance(self.trigger_subtypes, tuple):
            raise RoomLightingViolation("wireless switch trigger subtypes are invalid")
        normalized_subtypes: list[str] = []
        for subtype in self.trigger_subtypes:
            if (
                not isinstance(subtype, str)
                or _DEVICE_TRIGGER_SUBTYPE.fullmatch(subtype) is None
            ):
                raise RoomLightingViolation(
                    "wireless switch trigger subtype is invalid"
                )
            normalized_subtypes.append(subtype)
        if len(normalized_subtypes) != len(set(normalized_subtypes)):
            raise RoomLightingViolation("wireless switch trigger subtypes are invalid")
        if len(normalized_subtypes) > 16:
            raise RoomLightingViolation("wireless switch trigger subtypes exceed the bound")
        object.__setattr__(self, "trigger_subtypes", tuple(normalized_subtypes))


@dataclass(frozen=True, slots=True)
class Devices:
    sensors: tuple[RoomSensor, ...] = ()
    light_targets: tuple[LightTarget, ...] = ()
    power_switch: PowerSwitch | None = None
    wireless_switches: tuple[WirelessSwitch, ...] = ()
    select_all: bool = False

    def __post_init__(self) -> None:
        for values, maximum, label in (
            (self.sensors, MAX_SENSORS, "sensors"),
            (self.light_targets, MAX_LIGHT_TARGETS, "light targets"),
            (self.wireless_switches, MAX_WIRELESS_SWITCHES, "wireless switches"),
        ):
            if not isinstance(values, tuple) or len(values) > maximum:
                raise RoomLightingViolation(f"{label} exceed the safe bound")
        _flag(self.select_all, "select all flag")
        identifiers: list[str] = [item.id for item in self.sensors]
        identifiers.extend(item.id for item in self.light_targets)
        identifiers.extend(item.id for item in self.wireless_switches)
        if self.power_switch is not None:
            identifiers.append(self.power_switch.id)
        if len(identifiers) != len(set(identifiers)):
            raise RoomLightingViolation("device ids must be unique inside one room")

    @property
    def light_target_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.light_targets)

    @property
    def group_ids(self) -> frozenset[str]:
        return frozenset(
            item.group_id for item in self.light_targets if item.group_id is not None
        )

    @property
    def roles(self) -> frozenset[LightRole]:
        return frozenset(item.role for item in self.light_targets if item.role is not None)

    @property
    def wireless_switch_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.wireless_switches)

    @property
    def illuminance_entity_ids(self) -> frozenset[str]:
        return frozenset(
            item.entity_id
            for item in self.sensors
            if item.kind is SensorKind.ILLUMINANCE and item.entity_id is not None
        )

    def target(self, target_id: str) -> LightTarget | None:
        for item in self.light_targets:
            if item.id == target_id:
                return item
        return None

    def wireless_switch(self, switch_id: str) -> WirelessSwitch | None:
        for item in self.wireless_switches:
            if item.id == switch_id:
                return item
        return None


@dataclass(frozen=True, slots=True)
class Anchor:
    kind: AnchorKind
    time: str | None = None
    offset_minutes: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(self.kind, AnchorKind, "schedule anchor kind"))
        if self.time is not None and (
            not isinstance(self.time, str) or _TIME_OF_DAY.fullmatch(self.time) is None
        ):
            raise RoomLightingViolation("schedule anchor time must be HH:MM")
        if self.kind is AnchorKind.FIXED and self.time is None:
            raise RoomLightingViolation("a fixed schedule anchor requires a time")
        _integer(
            self.offset_minutes,
            "schedule anchor offset minutes",
            minimum=-1440,
            maximum=1440,
        )


@dataclass(frozen=True, slots=True)
class ScheduleWhen:
    days_of_week: str | tuple[str, ...]
    anchor: Anchor
    holiday: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "days_of_week", _days_of_week(self.days_of_week, "schedule days of week")
        )
        if not isinstance(self.anchor, Anchor):
            raise RoomLightingViolation("schedule when requires an anchor")
        _flag(self.holiday, "schedule holiday flag")


@dataclass(frozen=True, slots=True)
class Targets:
    light_targets: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    roles: tuple[LightRole, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "light_targets", _targets_tuple(self.light_targets, "target light id")
        )
        object.__setattr__(
            self, "group_ids", _targets_tuple(self.group_ids, "target group id")
        )
        object.__setattr__(self, "roles", _roles_tuple(self.roles, "target role"))


@dataclass(frozen=True, slots=True)
class ScheduleHow:
    brightness: int | None
    color_temperature: int | None
    fade: bool = True
    mode: ScheduleMode = ScheduleMode.ON_PRESENCE
    min_on_seconds: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "brightness",
            _optional_brightness(self.brightness, "schedule brightness"),
        )
        object.__setattr__(
            self,
            "color_temperature",
            _optional_kelvin(self.color_temperature, "schedule colour temperature"),
        )
        _flag(self.fade, "schedule fade flag")
        object.__setattr__(
            self, "mode", _enum(self.mode, ScheduleMode, "schedule mode")
        )
        _integer(
            self.min_on_seconds,
            "schedule minimum on seconds",
            minimum=0,
            maximum=86400,
        )


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    id: str
    when: ScheduleWhen
    how: ScheduleHow
    targets: Targets = field(default_factory=Targets)
    title: str | None = None

    def __post_init__(self) -> None:
        _stable_id(self.id, "schedule entry id")
        if self.title is not None:
            _text(self.title, "schedule entry title", maximum=60)
        if not isinstance(self.when, ScheduleWhen):
            raise RoomLightingViolation("schedule entry requires a when block")
        if not isinstance(self.how, ScheduleHow):
            raise RoomLightingViolation("schedule entry requires a how block")
        if not isinstance(self.targets, Targets):
            raise RoomLightingViolation("schedule entry requires a targets block")


@dataclass(frozen=True, slots=True)
class SwitchBinding:
    switch_id: str
    action: SwitchAction
    targets: Targets = field(default_factory=Targets)
    button: Button | None = None
    press_type: PressType | None = None
    trigger_subtype: str | None = None

    def __post_init__(self) -> None:
        _stable_id(self.switch_id, "binding switch id")
        object.__setattr__(self, "action", _enum(self.action, SwitchAction, "binding action"))
        if not isinstance(self.targets, Targets):
            raise RoomLightingViolation("binding requires a targets block")
        if self.button is not None:
            object.__setattr__(
                self, "button", _enum(self.button, Button, "binding button")
            )
        if self.press_type is not None:
            object.__setattr__(
                self, "press_type", _enum(self.press_type, PressType, "binding press type")
            )
        if self.trigger_subtype is not None and (
            not isinstance(self.trigger_subtype, str)
            or _DEVICE_TRIGGER_SUBTYPE.fullmatch(self.trigger_subtype) is None
        ):
            raise RoomLightingViolation("binding trigger subtype is invalid")
        if self.trigger_subtype is not None:
            if self.button is not None or self.press_type is not None:
                raise RoomLightingViolation(
                    "binding cannot combine a trigger subtype with button/pressType"
                )
        elif self.button is None or self.press_type is None:
            raise RoomLightingViolation(
                "binding requires either button+pressType or a trigger subtype"
            )


@dataclass(frozen=True, slots=True)
class IlluminationThreshold:
    lux: float
    brightness: int | None = None
    color_temperature: int | None = None
    modifier: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "lux",
            _number(self.lux, "illumination threshold lux", minimum=MIN_LUX, maximum=MAX_LUX),
        )
        object.__setattr__(
            self,
            "brightness",
            _optional_brightness(self.brightness, "illumination threshold brightness"),
        )
        object.__setattr__(
            self,
            "color_temperature",
            _optional_kelvin(self.color_temperature, "illumination threshold colour temperature"),
        )
        if self.modifier is not None:
            _number(self.modifier, "illumination threshold modifier", minimum=-100, maximum=100)


@dataclass(frozen=True, slots=True)
class Illumination:
    sensor: str
    calibration: Calibration = field(default_factory=Calibration)
    hysteresis: float = 5
    min_lux: float = 0
    max_lux: float = MAX_LUX
    thresholds: tuple[IlluminationThreshold, ...] = ()
    fail_closed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.sensor, str) or _LUX_SENSOR.fullmatch(self.sensor) is None:
            raise RoomLightingViolation("illumination sensor must be a sensor entity id")
        if not isinstance(self.calibration, Calibration):
            raise RoomLightingViolation("illumination calibration is invalid")
        object.__setattr__(
            self,
            "hysteresis",
            _number(self.hysteresis, "illumination hysteresis", minimum=0, maximum=10000),
        )
        object.__setattr__(
            self,
            "min_lux",
            _number(self.min_lux, "illumination minimum lux", minimum=MIN_LUX, maximum=MAX_LUX),
        )
        object.__setattr__(
            self,
            "max_lux",
            _number(self.max_lux, "illumination maximum lux", minimum=MIN_LUX, maximum=MAX_LUX),
        )
        if float(self.min_lux) > float(self.max_lux):
            raise RoomLightingViolation("illumination minimum lux exceeds maximum lux")
        if not isinstance(self.thresholds, tuple) or len(self.thresholds) > 16:
            raise RoomLightingViolation("illumination thresholds exceed the safe bound")
        if self.fail_closed is not True:
            raise RoomLightingViolation(
                "illumination must fail closed when lux is unknown, unavailable or stale"
            )


@dataclass(frozen=True, slots=True)
class Dimming:
    enabled: bool = True
    on_absence: bool = True
    fade_seconds: int = 5
    target_percent: int = 0

    def __post_init__(self) -> None:
        _flag(self.enabled, "dimming enabled flag")
        _flag(self.on_absence, "dimming on-absence flag")
        _integer(self.fade_seconds, "dimming fade seconds", minimum=0, maximum=3600)
        _integer(
            self.target_percent,
            "dimming target percent",
            minimum=MIN_BRIGHTNESS,
            maximum=MAX_BRIGHTNESS,
        )


@dataclass(frozen=True, slots=True)
class ManualOffProtection:
    enabled: bool = True
    minimum_interval_seconds: int = 600
    release_mode: ReleaseMode = ReleaseMode.TIMER_AND_ABSENCE
    stable_absence_seconds: int = 30
    priority: str = MANUAL_PRIORITY

    def __post_init__(self) -> None:
        _flag(self.enabled, "manual protection enabled flag")
        _integer(
            self.minimum_interval_seconds,
            "manual protection minimum interval",
            minimum=15,
            maximum=86400,
        )
        object.__setattr__(
            self,
            "release_mode",
            _enum(self.release_mode, ReleaseMode, "manual protection release mode"),
        )
        _integer(
            self.stable_absence_seconds,
            "manual protection stable absence",
            minimum=5,
            maximum=600,
        )
        if self.priority != MANUAL_PRIORITY:
            raise RoomLightingViolation(
                "manual protection priority is fixed to manual_above_auto"
            )


@dataclass(frozen=True, slots=True)
class AwayReturn:
    restore: RestoreMode = RestoreMode.BY_CURRENT_CONDITIONS

    def __post_init__(self) -> None:
        object.__setattr__(self, "restore", _enum(self.restore, RestoreMode, "away restore mode"))


@dataclass(frozen=True, slots=True)
class AwayBehavior:
    mode: AwayMode
    return_: AwayReturn | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum(self.mode, AwayMode, "away mode"))
        if self.return_ is not None and not isinstance(self.return_, AwayReturn):
            raise RoomLightingViolation("away return block is invalid")


@dataclass(frozen=True, slots=True)
class Timers:
    absence_seconds: int = 30
    turn_off_seconds: int = 300

    def __post_init__(self) -> None:
        _integer(self.absence_seconds, "absence seconds", minimum=0, maximum=86400)
        _integer(self.turn_off_seconds, "turn off seconds", minimum=0, maximum=86400)


@dataclass(frozen=True, slots=True)
class Behaviors:
    only_when_dark: bool = False
    respect_manual_off: bool = True
    restore_ownership_after_restart: bool = True

    def __post_init__(self) -> None:
        _flag(self.only_when_dark, "only-when-dark flag")
        if self.respect_manual_off is not True:
            raise RoomLightingViolation(
                "respect manual off is a fixed policy and cannot be disabled"
            )
        _flag(self.restore_ownership_after_restart, "restore ownership flag")


@dataclass(frozen=True, slots=True)
class Overrides:
    default_brightness: int | None = None
    default_color_temperature: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "default_brightness",
            _optional_brightness(self.default_brightness, "override brightness"),
        )
        object.__setattr__(
            self,
            "default_color_temperature",
            _optional_kelvin(
                self.default_color_temperature, "override colour temperature"
            ),
        )


@dataclass(frozen=True, slots=True)
class RoomLightingConfig:
    """One complete, validated room lighting configuration document."""

    room_id: str
    name: str
    version: int
    devices: Devices
    schedule: tuple[ScheduleEntry, ...]
    switch_bindings: tuple[SwitchBinding, ...]
    dimming: Dimming
    manual_off_protection: ManualOffProtection
    away_behavior: AwayBehavior
    auto_adopt: bool
    updated_at: int
    commands_enabled: bool = False
    illumination: Illumination | None = None
    timers: Timers | None = None
    behaviors: Behaviors | None = None
    template_id: str | None = None
    overrides: Overrides = field(default_factory=Overrides)

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "room id")
        _text(self.name, "room name")
        _integer(self.version, "config version", minimum=1, maximum=9_007_199_254_740_991)
        if not isinstance(self.devices, Devices):
            raise RoomLightingViolation("room lighting config requires devices")
        for values, maximum, label in (
            (self.schedule, MAX_SCHEDULE_ENTRIES, "schedule"),
            (self.switch_bindings, MAX_SWITCH_BINDINGS, "switch bindings"),
        ):
            if not isinstance(values, tuple) or len(values) > maximum:
                raise RoomLightingViolation(f"{label} exceed the safe bound")
        if not isinstance(self.dimming, Dimming):
            raise RoomLightingViolation("room lighting config requires dimming")
        if not isinstance(self.manual_off_protection, ManualOffProtection):
            raise RoomLightingViolation("room lighting config requires manual off protection")
        if not isinstance(self.away_behavior, AwayBehavior):
            raise RoomLightingViolation("room lighting config requires away behaviour")
        _flag(self.auto_adopt, "auto adopt flag")
        _integer(self.updated_at, "updated at", minimum=0, maximum=9_007_199_254_740_991)
        _flag(self.commands_enabled, "commands enabled flag")
        if self.illumination is not None and not isinstance(self.illumination, Illumination):
            raise RoomLightingViolation("illumination block is invalid")
        if self.timers is not None and not isinstance(self.timers, Timers):
            raise RoomLightingViolation("timers block is invalid")
        if self.behaviors is not None and not isinstance(self.behaviors, Behaviors):
            raise RoomLightingViolation("behaviours block is invalid")
        if self.template_id is not None:
            _stable_id(self.template_id, "template id")
        if not isinstance(self.overrides, Overrides):
            raise RoomLightingViolation("overrides block is invalid")
        violations = self._reference_violations()
        if violations:
            raise RoomLightingViolation("; ".join(violations))

    def _reference_violations(self) -> tuple[str, ...]:
        violations: list[str] = []
        target_ids = self.devices.light_target_ids
        group_ids = self.devices.group_ids
        roles = self.devices.roles

        def check_targets(targets: Targets, label: str) -> None:
            for target_id in targets.light_targets:
                if target_id not in target_ids:
                    violations.append(f"{label} references an unknown light target: {target_id}")
            for group_id in targets.group_ids:
                if group_id not in group_ids:
                    violations.append(f"{label} references an unknown group: {group_id}")
            for role in targets.roles:
                if role not in roles:
                    violations.append(f"{label} references an unused role: {role.value}")

        for entry in self.schedule:
            check_targets(entry.targets, f"schedule entry {entry.id}")
            if entry.how.brightness is not None:
                for target_id in entry.targets.light_targets:
                    target = self.devices.target(target_id)
                    if target is not None and not target.brightness:
                        violations.append(
                            f"schedule entry {entry.id} sets brightness on an "
                            f"unregulated target: {target_id}"
                        )
            if entry.how.color_temperature is not None:
                for target_id in entry.targets.light_targets:
                    target = self.devices.target(target_id)
                    if target is not None and not target.color_temperature:
                        violations.append(
                            f"schedule entry {entry.id} sets colour temperature on an "
                            f"unregulated target: {target_id}"
                        )

        for binding in self.switch_bindings:
            wireless = self.devices.wireless_switch(binding.switch_id)
            if wireless is None:
                violations.append(
                    f"switch binding references an unknown wireless switch: {binding.switch_id}"
                )
            else:
                if binding.trigger_subtype is not None and (
                    binding.trigger_subtype not in wireless.trigger_subtypes
                ):
                    violations.append(
                        "switch binding uses an unconfirmed trigger subtype: "
                        f"{binding.trigger_subtype}"
                    )
                if binding.button is not None and binding.button not in wireless.buttons:
                    violations.append(
                        f"switch binding uses an unconfirmed button: {binding.button.value}"
                    )
                if (
                    binding.press_type is not None
                    and binding.press_type not in wireless.press_types
                ):
                    violations.append(
                        f"switch binding uses an unconfirmed press type: {binding.press_type.value}"
                    )
            check_targets(binding.targets, f"switch binding {binding.switch_id}")

        if self.illumination is not None:
            illuminance = self.devices.illuminance_entity_ids
            if illuminance and self.illumination.sensor not in illuminance:
                violations.append(
                    "illumination sensor is not selected as an illuminance sensor"
                )
        return tuple(violations)

    def effective_auto_adopt(self, override: bool | None) -> bool:
        """Resolve one device-level auto-adopt override against the room default."""

        return self.auto_adopt if override is None else override

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract": {
                "name": ROOM_LIGHTING_CONFIG_NAME,
                "version": ROOM_LIGHTING_CONFIG_VERSION,
            },
            "roomId": self.room_id,
            "name": self.name,
            "version": self.version,
            "devices": _devices_to_payload(self.devices),
            "schedule": [_schedule_to_payload(entry) for entry in self.schedule],
            "switchBindings": [_binding_to_payload(item) for item in self.switch_bindings],
            "dimming": {
                "enabled": self.dimming.enabled,
                "onAbsence": self.dimming.on_absence,
                "fadeSeconds": self.dimming.fade_seconds,
                "targetPercent": self.dimming.target_percent,
            },
            "manualOffProtection": {
                "enabled": self.manual_off_protection.enabled,
                "minimumIntervalSeconds": self.manual_off_protection.minimum_interval_seconds,
                "releaseMode": self.manual_off_protection.release_mode.value,
                "stableAbsenceSeconds": self.manual_off_protection.stable_absence_seconds,
                "priority": self.manual_off_protection.priority,
            },
            "awayBehavior": _away_to_payload(self.away_behavior),
            "autoAdopt": self.auto_adopt,
            "commandsEnabled": self.commands_enabled,
            "updatedAt": self.updated_at,
            "templateId": self.template_id,
            "overrides": {
                "default_brightness": self.overrides.default_brightness,
                "default_color_temperature": self.overrides.default_color_temperature,
            },
        }
        if self.illumination is not None:
            payload["illumination"] = _illumination_to_payload(self.illumination)
        if self.timers is not None:
            payload["timers"] = {
                "absence_seconds": self.timers.absence_seconds,
                "turn_off_seconds": self.timers.turn_off_seconds,
            }
        if self.behaviors is not None:
            payload["behaviors"] = {
                "onlyWhenDark": self.behaviors.only_when_dark,
                "respectManualOff": self.behaviors.respect_manual_off,
                "restoreOwnershipAfterRestart": self.behaviors.restore_ownership_after_restart,
            }
        return payload


def _device_entity(payload: dict[str, object], entity_id: str | None) -> dict[str, object]:
    """Add entityId only when present; the contract rejects an explicit null."""

    if entity_id is not None:
        payload["entityId"] = entity_id
    return payload


def _devices_to_payload(devices: Devices) -> dict[str, object]:
    return {
        "sensors": [
            _device_entity(
                {
                    "id": item.id,
                    "name": item.name,
                    "kind": item.kind.value,
                    "autoAdoptOverride": item.auto_adopt_override,
                },
                item.entity_id,
            )
            for item in devices.sensors
        ],
        "light_targets": [
            _device_entity(
                {
                    "id": item.id,
                    "name": item.name,
                    "kind": item.kind.value,
                    "role": item.role.value if item.role is not None else None,
                    "groupId": item.group_id,
                    "brightness": item.brightness,
                    "color_temperature": item.color_temperature,
                    "colorTempInverted": item.color_temp_inverted,
                    "autoControl": item.auto_control,
                    "autoAdoptOverride": item.auto_adopt_override,
                },
                item.entity_id,
            )
            for item in devices.light_targets
        ],
        "power_switch": (
            None
            if devices.power_switch is None
            else _device_entity(
                {
                    "id": devices.power_switch.id,
                    "name": devices.power_switch.name,
                    "autoAdoptOverride": devices.power_switch.auto_adopt_override,
                },
                devices.power_switch.entity_id,
            )
        ),
        "wireless_switches": [
            _wireless_switch_to_payload(item)
            for item in devices.wireless_switches
        ],
        "selectAll": devices.select_all,
    }


def _wireless_switch_to_payload(item: WirelessSwitch) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": item.id,
        "name": item.name,
        "buttons": [button.value for button in item.buttons],
        "pressTypes": [press.value for press in item.press_types],
    }
    if item.entity_id is not None:
        payload["entityId"] = item.entity_id
    if item.device_id is not None:
        payload["deviceId"] = item.device_id
    if item.trigger_subtypes:
        payload["triggerSubtypes"] = list(item.trigger_subtypes)
    return payload


def _targets_to_payload(targets: Targets) -> dict[str, object]:
    return {
        "lightTargets": list(targets.light_targets),
        "groupIds": list(targets.group_ids),
        "roles": [role.value for role in targets.roles],
    }


def _schedule_to_payload(entry: ScheduleEntry) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": entry.id,
        "when": {
            "daysOfWeek": (
                entry.when.days_of_week
                if isinstance(entry.when.days_of_week, str)
                else list(entry.when.days_of_week)
            ),
            "holiday": entry.when.holiday,
            "anchor": {
                "kind": entry.when.anchor.kind.value,
                "offsetMinutes": entry.when.anchor.offset_minutes,
            },
        },
        "targets": _targets_to_payload(entry.targets),
        "how": {
            "brightness": entry.how.brightness,
            "colorTemperature": entry.how.color_temperature,
            "fade": entry.how.fade,
            "mode": entry.how.mode.value,
            "minOnSeconds": entry.how.min_on_seconds,
        },
    }
    if entry.when.anchor.time is not None:
        payload["when"]["anchor"]["time"] = entry.when.anchor.time  # type: ignore[index]
    if entry.title is not None:
        payload["title"] = entry.title
    return payload


def _binding_to_payload(binding: SwitchBinding) -> dict[str, object]:
    payload: dict[str, object] = {
        "switchId": binding.switch_id,
        "action": binding.action.value,
        "targets": _targets_to_payload(binding.targets),
    }
    if binding.button is not None:
        payload["button"] = binding.button.value
    if binding.press_type is not None:
        payload["pressType"] = binding.press_type.value
    if binding.trigger_subtype is not None:
        payload["triggerSubtype"] = binding.trigger_subtype
    return payload


def _illumination_to_payload(illumination: Illumination) -> dict[str, object]:
    return {
        "sensor": illumination.sensor,
        "calibration": {
            "offset": illumination.calibration.offset,
            "multiplier": illumination.calibration.multiplier,
        },
        "hysteresis": illumination.hysteresis,
        "minLux": illumination.min_lux,
        "maxLux": illumination.max_lux,
        "thresholds": [
            {
                "lux": item.lux,
                "brightness": item.brightness,
                "colorTemperature": item.color_temperature,
                "modifier": item.modifier,
            }
            for item in illumination.thresholds
        ],
        "failClosed": illumination.fail_closed,
    }


def _away_to_payload(away: AwayBehavior) -> dict[str, object]:
    payload: dict[str, object] = {"mode": away.mode.value}
    if away.return_ is not None:
        payload["return"] = {"restore": away.return_.restore.value}
    return payload


def _require(payload: dict[str, object], key: str, label: str) -> object:
    if key not in payload:
        raise RoomLightingViolation(f"{label} is missing")
    return payload[key]


def _as_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RoomLightingViolation(f"{label} must be an object")
    return value


def _as_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise RoomLightingViolation(f"{label} must be a list")
    return value


def _sensor_from_payload(payload: object) -> RoomSensor:
    data = _as_dict(payload, "sensor")
    return RoomSensor(
        id=_require(data, "id", "sensor id"),
        name=_require(data, "name", "sensor name"),
        kind=_require(data, "kind", "sensor kind"),
        entity_id=data.get("entityId"),
        auto_adopt_override=data.get("autoAdoptOverride"),
    )


def _light_target_from_payload(payload: object) -> LightTarget:
    data = _as_dict(payload, "light target")
    return LightTarget(
        id=_require(data, "id", "light target id"),
        name=_require(data, "name", "light target name"),
        kind=_require(data, "kind", "light target kind"),
        entity_id=data.get("entityId"),
        role=data.get("role"),
        group_id=data.get("groupId"),
        brightness=_require(data, "brightness", "light target brightness"),
        color_temperature=_require(
            data, "color_temperature", "light target colour temperature"
        ),
        auto_adopt_override=data.get("autoAdoptOverride"),
        color_temp_inverted=data.get("colorTempInverted", False),
        auto_control=data.get("autoControl", True),
    )


def _power_switch_from_payload(payload: object) -> PowerSwitch | None:
    if payload is None:
        return None
    data = _as_dict(payload, "power switch")
    return PowerSwitch(
        id=_require(data, "id", "power switch id"),
        name=_require(data, "name", "power switch name"),
        entity_id=data.get("entityId"),
        auto_adopt_override=data.get("autoAdoptOverride"),
    )


def _wireless_switch_from_payload(payload: object) -> WirelessSwitch:
    data = _as_dict(payload, "wireless switch")
    buttons = _as_list(_require(data, "buttons", "wireless switch buttons"), "wireless switch buttons")
    presses = _as_list(
        _require(data, "pressTypes", "wireless switch press types"),
        "wireless switch press types",
    )
    trigger_subtypes = data.get("triggerSubtypes")
    if trigger_subtypes is None:
        trigger_subtypes = []
    if not isinstance(trigger_subtypes, (list, tuple)):
        raise RoomLightingViolation("wireless switch trigger subtypes must be a list")
    return WirelessSwitch(
        id=_require(data, "id", "wireless switch id"),
        name=_require(data, "name", "wireless switch name"),
        entity_id=data.get("entityId"),
        device_id=data.get("deviceId"),
        trigger_subtypes=tuple(trigger_subtypes),
        buttons=tuple(_enum(item, Button, "wireless switch button") for item in buttons),
        press_types=tuple(_enum(item, PressType, "wireless switch press type") for item in presses),
    )


def _devices_from_payload(payload: object) -> Devices:
    data = _as_dict(payload, "devices")
    sensors = _as_list(data.get("sensors", []), "sensors")
    targets = _as_list(data.get("light_targets", []), "light targets")
    wireless = _as_list(data.get("wireless_switches", []), "wireless switches")
    return Devices(
        sensors=tuple(_sensor_from_payload(item) for item in sensors),
        light_targets=tuple(_light_target_from_payload(item) for item in targets),
        power_switch=_power_switch_from_payload(data.get("power_switch")),
        wireless_switches=tuple(_wireless_switch_from_payload(item) for item in wireless),
        select_all=data.get("selectAll", False),
    )


def _anchor_from_payload(payload: object) -> Anchor:
    data = _as_dict(payload, "schedule anchor")
    return Anchor(
        kind=_require(data, "kind", "schedule anchor kind"),
        time=data.get("time"),
        offset_minutes=data.get("offsetMinutes", 0),
    )


def _targets_from_payload(payload: object) -> Targets:
    if payload is None:
        return Targets()
    data = _as_dict(payload, "targets")
    return Targets(
        light_targets=tuple(data.get("lightTargets", [])),
        group_ids=tuple(data.get("groupIds", [])),
        roles=tuple(data.get("roles", [])),
    )


def _schedule_from_payload(payload: object) -> ScheduleEntry:
    data = _as_dict(payload, "schedule entry")
    when = _as_dict(_require(data, "when", "schedule when"), "schedule when")
    how = _as_dict(_require(data, "how", "schedule how"), "schedule how")
    return ScheduleEntry(
        id=_require(data, "id", "schedule entry id"),
        title=data.get("title"),
        when=ScheduleWhen(
            days_of_week=_require(when, "daysOfWeek", "schedule days of week"),
            holiday=when.get("holiday", False),
            anchor=_anchor_from_payload(_require(when, "anchor", "schedule anchor")),
        ),
        targets=_targets_from_payload(data.get("targets")),
        how=ScheduleHow(
            brightness=how.get("brightness"),
            color_temperature=how.get("colorTemperature"),
            fade=how.get("fade", True),
            mode=how.get("mode", ScheduleMode.ON_PRESENCE),
            min_on_seconds=how.get("minOnSeconds", 0),
        ),
    )


def _binding_from_payload(payload: object) -> SwitchBinding:
    data = _as_dict(payload, "switch binding")
    return SwitchBinding(
        switch_id=_require(data, "switchId", "binding switch id"),
        button=data.get("button"),
        press_type=data.get("pressType"),
        trigger_subtype=data.get("triggerSubtype"),
        action=_require(data, "action", "binding action"),
        targets=_targets_from_payload(data.get("targets")),
    )


def _threshold_from_payload(payload: object) -> IlluminationThreshold:
    data = _as_dict(payload, "illumination threshold")
    return IlluminationThreshold(
        lux=_require(data, "lux", "illumination threshold lux"),
        brightness=data.get("brightness"),
        color_temperature=data.get("colorTemperature"),
        modifier=data.get("modifier"),
    )


def _illumination_from_payload(payload: object) -> Illumination:
    data = _as_dict(payload, "illumination")
    calibration = _as_dict(data.get("calibration", {}), "illumination calibration")
    thresholds = _as_list(data.get("thresholds", []), "illumination thresholds")
    return Illumination(
        sensor=_require(data, "sensor", "illumination sensor"),
        calibration=Calibration(
            offset=calibration.get("offset", 0),
            multiplier=calibration.get("multiplier", 1),
        ),
        hysteresis=data.get("hysteresis", 5),
        min_lux=_require(data, "minLux", "illumination minimum lux"),
        max_lux=_require(data, "maxLux", "illumination maximum lux"),
        thresholds=tuple(_threshold_from_payload(item) for item in thresholds),
        fail_closed=_require(data, "failClosed", "illumination fail closed"),
    )


def _dimming_from_payload(payload: object) -> Dimming:
    data = _as_dict(payload, "dimming")
    return Dimming(
        enabled=data.get("enabled", True),
        on_absence=data.get("onAbsence", True),
        fade_seconds=data.get("fadeSeconds", 5),
        target_percent=data.get("targetPercent", 0),
    )


def _manual_off_from_payload(payload: object) -> ManualOffProtection:
    data = _as_dict(payload, "manual off protection")
    return ManualOffProtection(
        enabled=data.get("enabled", True),
        minimum_interval_seconds=data.get("minimumIntervalSeconds", 600),
        release_mode=data.get("releaseMode", ReleaseMode.TIMER_AND_ABSENCE),
        stable_absence_seconds=data.get("stableAbsenceSeconds", 30),
        priority=data.get("priority", MANUAL_PRIORITY),
    )


def _away_from_payload(payload: object) -> AwayBehavior:
    data = _as_dict(payload, "away behaviour")
    returned = data.get("return")
    return_ = None
    if returned is not None:
        return_data = _as_dict(returned, "away return")
        return_ = AwayReturn(restore=return_data.get("restore", RestoreMode.BY_CURRENT_CONDITIONS))
    return AwayBehavior(
        mode=_require(data, "mode", "away mode"),
        return_=return_,
    )


def _timers_from_payload(payload: object) -> Timers:
    data = _as_dict(payload, "timers")
    return Timers(
        absence_seconds=data.get("absence_seconds", 30),
        turn_off_seconds=data.get("turn_off_seconds", 300),
    )


def _behaviors_from_payload(payload: object) -> Behaviors:
    data = _as_dict(payload, "behaviours")
    return Behaviors(
        only_when_dark=data.get("onlyWhenDark", False),
        respect_manual_off=data.get("respectManualOff", True),
        restore_ownership_after_restart=data.get("restoreOwnershipAfterRestart", True),
    )


def _overrides_from_payload(payload: object) -> Overrides:
    if payload is None:
        return Overrides()
    data = _as_dict(payload, "overrides")
    return Overrides(
        default_brightness=data.get("default_brightness"),
        default_color_temperature=data.get("default_color_temperature"),
    )


def room_lighting_config_from_payload(payload: object) -> RoomLightingConfig:
    """Decode a strict storage document, ignoring unknown optional fields."""

    data = _as_dict(payload, "room lighting config")
    schedule = _as_list(_require(data, "schedule", "schedule"), "schedule")
    bindings = _as_list(
        _require(data, "switchBindings", "switch bindings"), "switch bindings"
    )
    illumination = data.get("illumination")
    timers = data.get("timers")
    behaviors = data.get("behaviors")
    return RoomLightingConfig(
        room_id=_require(data, "roomId", "room id"),
        name=_require(data, "name", "room name"),
        version=_require(data, "version", "config version"),
        devices=_devices_from_payload(_require(data, "devices", "devices")),
        schedule=tuple(_schedule_from_payload(item) for item in schedule),
        switch_bindings=tuple(_binding_from_payload(item) for item in bindings),
        dimming=_dimming_from_payload(_require(data, "dimming", "dimming")),
        manual_off_protection=_manual_off_from_payload(
            _require(data, "manualOffProtection", "manual off protection")
        ),
        away_behavior=_away_from_payload(_require(data, "awayBehavior", "away behaviour")),
        auto_adopt=_require(data, "autoAdopt", "auto adopt"),
        updated_at=_require(data, "updatedAt", "updated at"),
        commands_enabled=data.get("commandsEnabled", False),
        illumination=None if illumination is None else _illumination_from_payload(illumination),
        timers=None if timers is None else _timers_from_payload(timers),
        behaviors=None if behaviors is None else _behaviors_from_payload(behaviors),
        template_id=data.get("templateId"),
        overrides=_overrides_from_payload(data.get("overrides")),
    )


def room_lighting_violations(payload: object) -> tuple[str, ...]:
    """Return readable validation messages without raising."""

    if isinstance(payload, RoomLightingConfig):
        try:
            config = room_lighting_config_from_payload(payload.to_dict())
        except RoomLightingViolation as error:
            return (str(error),)
        return config._reference_violations()
    try:
        room_lighting_config_from_payload(payload)
    except RoomLightingViolation as error:
        return (str(error),)
    return ()


def config_to_payload(config: RoomLightingConfig) -> dict[str, object]:
    """Encode the exact contract payload for one configuration."""

    if not isinstance(config, RoomLightingConfig):
        raise RoomLightingViolation("room lighting config is required")
    return config.to_dict()


def config_entity_ids(config: RoomLightingConfig) -> tuple[str, ...]:
    """Return the control entity ids referenced by one room configuration.

    Only control entities are returned: light targets, the power switch and
    wireless switches. Sensors are intentionally excluded, because a shared
    presence, motion or illuminance sensor is legitimate shared infrastructure,
    while a control entity must belong to a single room.
    """

    if not isinstance(config, RoomLightingConfig):
        raise RoomLightingViolation("room lighting config is required")
    entities: list[str] = []
    for target in config.devices.light_targets:
        if target.entity_id is not None:
            entities.append(target.entity_id)
    power = config.devices.power_switch
    if power is not None and power.entity_id is not None:
        entities.append(power.entity_id)
    for switch in config.devices.wireless_switches:
        if switch.entity_id is not None:
            entities.append(switch.entity_id)
    return tuple(entities)


def room_lighting_entity_collisions(
    configs: Iterable[RoomLightingConfig],
) -> tuple[str, ...]:
    """Return readable messages for control entity ids shared by different rooms.

    Only control entities are checked: a light target, power switch or wireless
    switch must belong to a single room, because the runtime indexes it
    one-to-one. Shared sensors are allowed.
    """

    owners: dict[str, str] = {}
    collisions: list[str] = []
    for config in configs:
        if not isinstance(config, RoomLightingConfig):
            raise RoomLightingViolation("room lighting config is required")
        for entity_id in config_entity_ids(config):
            owner = owners.get(entity_id)
            if owner is not None and owner != config.room_id:
                collisions.append(
                    f"entity {entity_id} is used by rooms {owner} and {config.room_id}"
                )
            else:
                owners[entity_id] = config.room_id
    return tuple(collisions)


def config_from_payload(payload: object) -> RoomLightingConfig:
    """Decode and validate one configuration payload."""

    return room_lighting_config_from_payload(payload)


def resolve_anchor_time(
    anchor: Anchor,
    *,
    day: date,
    sunrise: dt_time,
    sunset: dt_time,
    home_timezone: tzinfo,
) -> datetime:
    """Resolve one dynamic anchor to an absolute aware datetime.

    The home timezone and the actual sunrise/sunset times are passed in, so the
    resolver stays a pure function and never reads the global environment.
    """

    if not isinstance(anchor, Anchor):
        raise RoomLightingViolation("schedule anchor is required")
    if anchor.kind is AnchorKind.FIXED:
        if anchor.time is None:
            raise RoomLightingViolation("a fixed schedule anchor requires a time")
        hour, minute = (int(part) for part in anchor.time.split(":"))
        base = datetime.combine(day, dt_time(hour=hour, minute=minute))
    elif anchor.kind is AnchorKind.SUNRISE:
        base = datetime.combine(day, sunrise)
    else:
        base = datetime.combine(day, sunset)
    return base.replace(tzinfo=home_timezone) + timedelta(minutes=anchor.offset_minutes)
