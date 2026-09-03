"""Immutable policy types for manual light-off protection."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENTITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_POLICY_KEYS = frozenset(
    {
        "enabled",
        "minimumIntervalSeconds",
        "releaseMode",
        "stableAbsenceSeconds",
        "extendOnRepeatedManualOff",
        "noSensorFallback",
        "protectedScope",
        "allowManualRelease",
    }
)


class ManualLightOffProtectionViolation(ValueError):
    """The protected lighting policy is not safe to use."""


class ReleaseMode(StrEnum):
    TIMER_AND_ABSENCE = "timer_and_absence"
    TIMER_ONLY = "timer_only"
    ABSENCE_ONLY = "absence_only"


class NoSensorFallback(StrEnum):
    TIMER_ONLY = "timer_only"
    MANUAL_RELEASE = "manual_release"


class ProtectedScope(StrEnum):
    SOURCE = "source"
    PROFILE = "profile"


class EffectivePolicySource(StrEnum):
    GLOBAL = "global"
    ROOM = "room"
    PROFILE = "profile"


class ProtectionState(StrEnum):
    ACTIVE = "active"
    READY_TO_RELEASE = "ready_to_release"
    RELEASED = "released"
    CANCELLED_BY_MANUAL_ON = "cancelled_by_manual_on"


@dataclass(frozen=True, slots=True)
class ScenarioCommandAttribution:
    """Provenance for a transition caused by Hausman automation."""

    source: str
    attribution_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source:
            raise ManualLightOffProtectionViolation("attribution source is invalid")
        _entity(self.attribution_id, "attribution id")


@dataclass(frozen=True, slots=True)
class LightProtectionDecision:
    """A guard result that does not itself issue a physical command."""

    allowed: bool
    reason: str | None = None
    protection_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise ManualLightOffProtectionViolation("decision allowed is invalid")


@dataclass(frozen=True, slots=True)
class ManualOffPolicy:
    enabled: bool
    minimum_interval_seconds: int
    release_mode: ReleaseMode
    stable_absence_seconds: int
    extend_on_repeated_manual_off: bool
    no_sensor_fallback: NoSensorFallback
    protected_scope: ProtectedScope
    allow_manual_release: bool

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ManualLightOffProtectionViolation("enabled must be a boolean")
        _bounded_int(self.minimum_interval_seconds, 30, 86400, "minimum interval")
        if not isinstance(self.release_mode, ReleaseMode):
            raise ManualLightOffProtectionViolation("release mode is invalid")
        _bounded_int(self.stable_absence_seconds, 5, 600, "stable absence")
        if type(self.extend_on_repeated_manual_off) is not bool:
            raise ManualLightOffProtectionViolation("repeated-off policy must be a boolean")
        if not isinstance(self.no_sensor_fallback, NoSensorFallback):
            raise ManualLightOffProtectionViolation("no-sensor fallback is invalid")
        if not isinstance(self.protected_scope, ProtectedScope):
            raise ManualLightOffProtectionViolation("protected scope is invalid")
        if type(self.allow_manual_release) is not bool:
            raise ManualLightOffProtectionViolation("manual-release policy must be a boolean")

    def as_wire(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "minimumIntervalSeconds": self.minimum_interval_seconds,
            "releaseMode": self.release_mode.value,
            "stableAbsenceSeconds": self.stable_absence_seconds,
            "extendOnRepeatedManualOff": self.extend_on_repeated_manual_off,
            "noSensorFallback": self.no_sensor_fallback.value,
            "protectedScope": self.protected_scope.value,
            "allowManualRelease": self.allow_manual_release,
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_wire(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class LightProfile:
    room_id: str
    profile_id: str
    light_ids: tuple[str, ...]
    presence_sensor_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _stable(self.room_id, "room id")
        _stable(self.profile_id, "profile id")
        object.__setattr__(self, "light_ids", tuple(self.light_ids))
        object.__setattr__(self, "presence_sensor_ids", tuple(self.presence_sensor_ids))
        if not 1 <= len(self.light_ids) <= 32 or len(set(self.light_ids)) != len(self.light_ids):
            raise ManualLightOffProtectionViolation("light ids are invalid")
        if len(self.presence_sensor_ids) > 8 or len(set(self.presence_sensor_ids)) != len(self.presence_sensor_ids):
            raise ManualLightOffProtectionViolation("presence sensor ids are invalid")
        for entity_id in (*self.light_ids, *self.presence_sensor_ids):
            _entity(entity_id, "entity id")

    def as_wire(self) -> dict[str, object]:
        return {
            "roomId": self.room_id,
            "profileId": self.profile_id,
            "lightIds": list(self.light_ids),
            "presenceSensorIds": list(self.presence_sensor_ids),
        }


def resolve_manual_off_policy(
    settings: Mapping[str, object], room_id: str, profile_id: str
) -> ManualOffPolicy:
    """Resolve global, room, then profile fields without mutating input."""

    return resolve_manual_off_policy_with_source(settings, room_id, profile_id)[0]


def resolve_manual_off_policy_with_source(
    settings: Mapping[str, object], room_id: str, profile_id: str
) -> tuple[ManualOffPolicy, EffectivePolicySource]:
    _stable(room_id, "room id")
    # Resolution is intentionally useful for legacy catalog identifiers too;
    # persisted profiles are validated strictly by ``parse_settings``.
    if not isinstance(profile_id, str) or not profile_id:
        raise ManualLightOffProtectionViolation("profile id is invalid")
    parsed = parse_settings(settings)
    effective = parsed.global_policy.as_wire()
    source = EffectivePolicySource.GLOBAL
    room = parsed.room_overrides.get(room_id)
    if room is not None:
        effective.update(room)
        source = EffectivePolicySource.ROOM
    profile = parsed.profile_overrides.get(profile_id)
    if profile is not None:
        effective.update(profile)
        source = EffectivePolicySource.PROFILE
    return _policy_from_wire(effective, require_all=True), source


@dataclass(frozen=True, slots=True)
class ManualLightOffProtectionSettings:
    global_policy: ManualOffPolicy
    room_overrides: Mapping[str, Mapping[str, object]]
    profile_overrides: Mapping[str, Mapping[str, object]]
    profiles: tuple[LightProfile, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "room_overrides", MappingProxyType(dict(self.room_overrides)))
        object.__setattr__(self, "profile_overrides", MappingProxyType(dict(self.profile_overrides)))
        object.__setattr__(self, "profiles", tuple(self.profiles))

    def as_wire(self) -> dict[str, object]:
        return {
            "globalPolicy": self.global_policy.as_wire(),
            "roomOverrides": {key: dict(value) for key, value in self.room_overrides.items()},
            "profileOverrides": {key: dict(value) for key, value in self.profile_overrides.items()},
            "profiles": [profile.as_wire() for profile in self.profiles],
        }


def parse_settings(value: Mapping[str, object]) -> ManualLightOffProtectionSettings:
    if not isinstance(value, Mapping) or set(value) != {
        "globalPolicy", "roomOverrides", "profileOverrides", "profiles"
    }:
        raise ManualLightOffProtectionViolation("settings shape is invalid")
    global_policy = _policy_from_wire(value["globalPolicy"], require_all=True)
    room_overrides = _overrides(value["roomOverrides"], "room override")
    profile_overrides = _overrides(value["profileOverrides"], "profile override")
    profiles_raw = value["profiles"]
    if not isinstance(profiles_raw, list) or len(profiles_raw) > 64:
        raise ManualLightOffProtectionViolation("profiles are invalid")
    profiles = tuple(_profile(item) for item in profiles_raw)
    if len({profile.profile_id for profile in profiles}) != len(profiles):
        raise ManualLightOffProtectionViolation("profiles have duplicate ids")
    return ManualLightOffProtectionSettings(
        global_policy, room_overrides, profile_overrides, profiles
    )


def _profile(value: object) -> LightProfile:
    if not isinstance(value, Mapping) or set(value) != {
        "roomId", "profileId", "lightIds", "presenceSensorIds"
    }:
        raise ManualLightOffProtectionViolation("profile shape is invalid")
    lights = value["lightIds"]
    sensors = value["presenceSensorIds"]
    if not isinstance(lights, list) or not isinstance(sensors, list):
        raise ManualLightOffProtectionViolation("profile ids are invalid")
    return LightProfile(str(value["roomId"]), str(value["profileId"]), tuple(lights), tuple(sensors))


def _overrides(value: object, label: str) -> Mapping[str, Mapping[str, object]]:
    if not isinstance(value, Mapping) or len(value) > 64:
        raise ManualLightOffProtectionViolation(f"{label}s are invalid")
    result: dict[str, Mapping[str, object]] = {}
    for key, override in value.items():
        _stable(key, label)
        if not isinstance(override, Mapping) or not override or not set(override) <= _POLICY_KEYS:
            raise ManualLightOffProtectionViolation(f"{label} is invalid")
        _policy_from_wire({**_default_wire(), **dict(override)}, require_all=True)
        result[key] = MappingProxyType(dict(override))
    return result


def _policy_from_wire(value: object, *, require_all: bool) -> ManualOffPolicy:
    if not isinstance(value, Mapping) or (require_all and set(value) != _POLICY_KEYS):
        raise ManualLightOffProtectionViolation("policy shape is invalid")
    try:
        return ManualOffPolicy(
            enabled=value["enabled"],
            minimum_interval_seconds=value["minimumIntervalSeconds"],
            release_mode=ReleaseMode(value["releaseMode"]),
            stable_absence_seconds=value["stableAbsenceSeconds"],
            extend_on_repeated_manual_off=value["extendOnRepeatedManualOff"],
            no_sensor_fallback=NoSensorFallback(value["noSensorFallback"]),
            protected_scope=ProtectedScope(value["protectedScope"]),
            allow_manual_release=value["allowManualRelease"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ManualLightOffProtectionViolation("policy is invalid") from error


def _default_wire() -> dict[str, object]:
    return {
        "enabled": True,
        "minimumIntervalSeconds": 600,
        "releaseMode": "timer_and_absence",
        "stableAbsenceSeconds": 30,
        "extendOnRepeatedManualOff": True,
        "noSensorFallback": "timer_only",
        "protectedScope": "profile",
        "allowManualRelease": True,
    }


def _bounded_int(value: object, lower: int, upper: int, label: str) -> None:
    if type(value) is not int or not lower <= value <= upper:
        raise ManualLightOffProtectionViolation(f"{label} is invalid")


def _stable(value: object, label: str) -> None:
    if not isinstance(value, str) or _STABLE_ID.fullmatch(value) is None:
        raise ManualLightOffProtectionViolation(f"{label} is invalid")


def _entity(value: object, label: str) -> None:
    if not isinstance(value, str) or _ENTITY_ID.fullmatch(value) is None:
        raise ManualLightOffProtectionViolation(f"{label} is invalid")
