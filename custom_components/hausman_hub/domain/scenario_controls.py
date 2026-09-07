"""Pure policy primitives shared by the consolidated scenario controllers.

The module deliberately contains no Home Assistant imports.  Controllers can
use these functions to make a deterministic plan from an immutable snapshot.
"""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum
from dataclasses import dataclass
from datetime import timedelta
import math
import re
from typing import Mapping


class OccupancyEvidence(StrEnum):
    """Effective occupancy of a zone using motion OR presence semantics."""

    OCCUPIED = "occupied"
    ABSENT = "absent"
    UNKNOWN = "unknown"

    @classmethod
    def from_states(cls, motion: object = None, presence: object = None) -> "OccupancyEvidence":
        # ``None`` means that this sensor type is not configured in the zone.
        # An explicit HA ``unknown``/``unavailable`` state is different and
        # must remain fail-closed.
        states = {str(value).lower() for value in (motion, presence) if value is not None}
        if not states:
            return cls.UNKNOWN
        if "on" in states:
            return cls.OCCUPIED
        if states and states.issubset({"off", "false", "0"}):
            return cls.ABSENT
        return cls.UNKNOWN


def evening_start(now: datetime, sunset: datetime | None) -> time:
    """Return the local evening boundary, never later than 21:00."""

    cutoff = now.replace(hour=21, minute=0, second=0, microsecond=0)
    if sunset is None:
        return cutoff.time()
    if sunset.tzinfo is not None and now.tzinfo is not None:
        sunset = sunset.astimezone(now.tzinfo)
    if sunset.date() != now.date():
        return cutoff.time()
    return min(sunset.time(), cutoff.time())


def maximum_brightness(
    now: datetime,
    start: datetime,
    deadline: datetime,
    initial: int,
    final: int = 5,
) -> int:
    """Linearly lower the active limit from ``initial`` to ``final``."""

    if deadline <= start or now >= deadline:
        return final
    if now <= start:
        return initial
    fraction = (now - start).total_seconds() / (deadline - start).total_seconds()
    return round(initial + (final - initial) * fraction)


def brightness_after_relative_fade(current: int, *, minimum: int) -> int:
    """Apply a ten percent relative reduction, respecting the active floor."""

    if current <= minimum:
        return minimum
    return max(minimum, round(current * 0.9))


def fade_steps(current: int, target: int) -> tuple[int, ...]:
    """Return one-percent steps from current towards target, inclusive."""

    if target >= current:
        return () if target == current else tuple(range(current + 1, target + 1))
    return tuple(range(current - 1, target - 1, -1))


def brightness_sequence_deadline_ms(
    started_at_ms: int,
    deadline_ms: int,
    start: int,
    target: int,
    current: int,
) -> int | None:
    """Return the absolute due time of the next one-point brightness step."""

    if any(type(value) is not int for value in (
        started_at_ms, deadline_ms, start, target, current
    )):
        raise ValueError("brightness sequence values must be integers")
    if deadline_ms <= started_at_ms:
        raise ValueError("brightness sequence deadline must follow its start")
    if not all(0 <= value <= 100 for value in (start, target, current)):
        raise ValueError("brightness sequence percentages are out of range")
    distance = abs(target - start)
    if distance == 0 or current == target:
        return None
    completed = abs(current - start)
    ordinal = min(distance, completed + 1)
    duration = deadline_ms - started_at_ms
    return started_at_ms + math.ceil(duration * ordinal / distance)


def brightness_sequence_step(
    current: int,
    *,
    start: int,
    target: int,
    started_at_ms: int,
    deadline_ms: int,
    now_ms: int,
) -> tuple[int, float]:
    """Advance at most one point towards the time-bounded brightness target."""

    next_due = brightness_sequence_deadline_ms(
        started_at_ms, deadline_ms, start, target, current
    )
    if next_due is None or now_ms < next_due:
        return current, 0.0
    direction = 1 if target > current else -1
    next_value = current + direction
    if direction > 0:
        next_value = min(next_value, target)
    else:
        next_value = max(next_value, target)
    fraction = min(
        1.0,
        max(0.0, (now_ms - started_at_ms) / (deadline_ms - started_at_ms)),
    )
    ideal = start + (target - start) * fraction
    remainder = abs(ideal - next_value) % 1.0
    return next_value, remainder


def manual_command(event: object, *, supports_temperature: bool) -> dict[str, object] | None:
    """Translate supported upper/hold events into an unconditional manual plan."""

    if str(event).lower() not in {"upper_press", "upper", "hold", "long_press"}:
        return None
    result: dict[str, object] = {"brightness": 100, "manual": True}
    if supports_temperature:
        result["color_temperature"] = 3000
    return result


_COVER_CAPS = {"kitchen": 80, "cabinet": 90}


def cover_cap(zone: object) -> int:
    """Hard ordinary-control opening cap for a named cover zone."""

    return _COVER_CAPS.get(str(zone).lower(), 100)


def clamp_cover_position(zone: object, requested: object) -> int:
    """Clamp every ordinary position command to the zone's hard cap."""

    value = int(requested)
    if value < 0 or value > 100:
        raise ValueError("cover position must be between 0 and 100")
    return min(value, cover_cap(zone))


@dataclass(frozen=True, slots=True)
class ManualLightOwnership:
    """Durable manual priority and the five-minute post-off inhibit."""

    owned: bool = False
    off_inhibit_until: datetime | None = None

    def manual_on(self) -> "ManualLightOwnership":
        return ManualLightOwnership(owned=True)

    def manual_off(self, at: datetime) -> "ManualLightOwnership":
        return ManualLightOwnership(owned=False, off_inhibit_until=at + timedelta(minutes=5))

    def automatic_allowed(self, at: datetime, absent_since: datetime | None = None) -> bool:
        if self.off_inhibit_until is not None and at < self.off_inhibit_until:
            return False
        if not self.owned:
            return True
        return absent_since is not None and at - absent_since >= timedelta(minutes=5)


def absence_confirmed(
    motion: object,
    presence: object,
    *,
    absent_for_seconds: float,
    minimum_seconds: float = 10,
) -> bool:
    """Confirm absence only after both available signals have been off."""

    return (
        OccupancyEvidence.from_states(motion, presence) is OccupancyEvidence.ABSENT
        and absent_for_seconds >= max(10, minimum_seconds)
    )


@dataclass(frozen=True, slots=True)
class LightingPolicy:
    """Editable brightness and colour-temperature settings for one zone."""

    day_minimum: int = 20
    evening_minimum: int = 5
    day_color_temperature: int = 3000
    evening_color_temperature: int = 2200
    ramp_seconds: int = 30
    relative_fade_period_seconds: int = 300

    def __post_init__(self) -> None:
        if not 0 <= self.evening_minimum <= self.day_minimum <= 100:
            raise ValueError("lighting minimums must be ordered percentages")
        if self.ramp_seconds <= 0 or self.relative_fade_period_seconds <= 0:
            raise ValueError("lighting durations must be positive")


def power_readiness(power_state: object, light_state: object) -> str:
    """Classify whether a dependent light may receive a command."""

    power = str(power_state).lower()
    light = str(light_state).lower()
    if power in {"unknown", "unavailable", "none"}:
        return "power_unknown"
    if power != "on":
        return "power_off"
    if light in {"unknown", "unavailable", "none"}:
        return "light_not_ready"
    return "ready"


SCENARIO_CONTROL_DOCUMENT_VERSION = 1
_TARGET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CLOCK_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True, slots=True)
class ScenarioControlPolicy:
    """Server-owned editable bounds shared by managed controllers."""

    absence_confirmation_seconds: int = 10
    presence_rise_seconds: int = 10
    storage_absence_seconds: int = 120
    shower_absence_seconds: int = 300
    shower_fan_presence_seconds: int = 120
    shower_fan_off_seconds: int = 300
    toilet_absence_seconds: int = 480
    toilet_fan_off_seconds: int = 180
    toilet_fan_start: str = "08:30"
    toilet_fan_end: str = "22:30"
    bathroom_humidity_threshold: int = 65
    bathroom_day_off_seconds: int = 1800
    bathroom_quiet_start: str = "06:00"
    bathroom_day_start: str = "08:00"
    bathroom_night_start: str = "22:00"
    office_low_lux_threshold: int = 100
    office_high_lux_threshold: int = 1000
    office_day_low_brightness: int = 40
    office_day_low_kelvin: int = 3000
    office_day_medium_brightness: int = 65
    office_day_medium_kelvin: int = 2500
    office_day_bright_brightness: int = 85
    office_day_bright_kelvin: int = 2200
    office_evening_dark_brightness: int = 25
    office_evening_dark_kelvin: int = 6500
    office_evening_medium_brightness: int = 40
    office_evening_medium_kelvin: int = 5700
    office_evening_bright_brightness: int = 55
    office_evening_bright_kelvin: int = 4800
    office_night_brightness: int = 5
    office_night_kelvin: int = 6500
    office_power_settle_seconds: int = 3
    office_temperature_settle_seconds: int = 1
    manual_release_seconds: int = 300
    manual_off_block_seconds: int = 300
    storage_exhaust_target_id: str | None = None
    storage_exhaust_times: tuple[str, ...] = ("11:00", "20:00")
    storage_exhaust_run_seconds: int = 1800
    small_corridor_lux_threshold: int = 450
    lux_hysteresis: int = 25
    lux_hold_seconds: int = 30
    day_brightness_floor_percent: int = 20
    evening_brightness_floor_percent: int = 5
    night_brightness_cap_percent: int = 5
    relative_fade_percent: int = 10
    relative_fade_period_seconds: int = 300
    brightness_ramp_seconds: int = 30
    neutral_color_temperature_kelvin: int = 3000
    evening_color_temperature_kelvin: int = 2200
    evening_latest: str = "21:00"
    tambur_main_off: str = "23:00"
    small_corridor_main_off: str = "23:30"
    kitchen_cover_cap_percent: int = 80
    cabinet_cover_cap_percent: int = 90


@dataclass(frozen=True, slots=True)
class ScenarioControlDocument:
    """One CAS revision and its complete validated policy."""

    policy_revision: int = 0
    policy: ScenarioControlPolicy = ScenarioControlPolicy()


_POLICY_FIELDS = {
    "absenceConfirmationSeconds": "absence_confirmation_seconds",
    "presenceRiseSeconds": "presence_rise_seconds",
    "storageAbsenceSeconds": "storage_absence_seconds",
    "showerAbsenceSeconds": "shower_absence_seconds",
    "showerFanPresenceSeconds": "shower_fan_presence_seconds",
    "showerFanOffSeconds": "shower_fan_off_seconds",
    "toiletAbsenceSeconds": "toilet_absence_seconds",
    "toiletFanOffSeconds": "toilet_fan_off_seconds",
    "toiletFanStart": "toilet_fan_start",
    "toiletFanEnd": "toilet_fan_end",
    "bathroomHumidityThreshold": "bathroom_humidity_threshold",
    "bathroomDayOffSeconds": "bathroom_day_off_seconds",
    "bathroomQuietStart": "bathroom_quiet_start",
    "bathroomDayStart": "bathroom_day_start",
    "bathroomNightStart": "bathroom_night_start",
    "officeLowLuxThreshold": "office_low_lux_threshold",
    "officeHighLuxThreshold": "office_high_lux_threshold",
    "officeDayLowBrightness": "office_day_low_brightness",
    "officeDayLowKelvin": "office_day_low_kelvin",
    "officeDayMediumBrightness": "office_day_medium_brightness",
    "officeDayMediumKelvin": "office_day_medium_kelvin",
    "officeDayBrightBrightness": "office_day_bright_brightness",
    "officeDayBrightKelvin": "office_day_bright_kelvin",
    "officeEveningDarkBrightness": "office_evening_dark_brightness",
    "officeEveningDarkKelvin": "office_evening_dark_kelvin",
    "officeEveningMediumBrightness": "office_evening_medium_brightness",
    "officeEveningMediumKelvin": "office_evening_medium_kelvin",
    "officeEveningBrightBrightness": "office_evening_bright_brightness",
    "officeEveningBrightKelvin": "office_evening_bright_kelvin",
    "officeNightBrightness": "office_night_brightness",
    "officeNightKelvin": "office_night_kelvin",
    "officePowerSettleSeconds": "office_power_settle_seconds",
    "officeTemperatureSettleSeconds": "office_temperature_settle_seconds",
    "manualReleaseSeconds": "manual_release_seconds",
    "manualOffBlockSeconds": "manual_off_block_seconds",
    "storageExhaustTargetId": "storage_exhaust_target_id",
    "storageExhaustTimes": "storage_exhaust_times",
    "storageExhaustRunSeconds": "storage_exhaust_run_seconds",
    "smallCorridorLuxThreshold": "small_corridor_lux_threshold",
    "luxHysteresis": "lux_hysteresis",
    "luxHoldSeconds": "lux_hold_seconds",
    "dayBrightnessFloorPercent": "day_brightness_floor_percent",
    "eveningBrightnessFloorPercent": "evening_brightness_floor_percent",
    "nightBrightnessCapPercent": "night_brightness_cap_percent",
    "relativeFadePercent": "relative_fade_percent",
    "relativeFadePeriodSeconds": "relative_fade_period_seconds",
    "brightnessRampSeconds": "brightness_ramp_seconds",
    "neutralColorTemperatureKelvin": "neutral_color_temperature_kelvin",
    "eveningColorTemperatureKelvin": "evening_color_temperature_kelvin",
    "eveningLatest": "evening_latest",
    "tamburMainOff": "tambur_main_off",
    "smallCorridorMainOff": "small_corridor_main_off",
    "kitchenCoverCapPercent": "kitchen_cover_cap_percent",
    "cabinetCoverCapPercent": "cabinet_cover_cap_percent",
}
_ROOM_POLICY_FIELDS = frozenset(
    {
        "showerFanPresenceSeconds",
        "showerFanOffSeconds",
        "toiletFanOffSeconds",
        "toiletFanStart",
        "toiletFanEnd",
        "bathroomHumidityThreshold",
        "bathroomDayOffSeconds",
        "bathroomQuietStart",
        "bathroomDayStart",
        "bathroomNightStart",
        "officeLowLuxThreshold",
        "officeHighLuxThreshold",
        "officeDayLowBrightness",
        "officeDayLowKelvin",
        "officeDayMediumBrightness",
        "officeDayMediumKelvin",
        "officeDayBrightBrightness",
        "officeDayBrightKelvin",
        "officeEveningDarkBrightness",
        "officeEveningDarkKelvin",
        "officeEveningMediumBrightness",
        "officeEveningMediumKelvin",
        "officeEveningBrightBrightness",
        "officeEveningBrightKelvin",
        "officeNightBrightness",
        "officeNightKelvin",
        "officePowerSettleSeconds",
        "officeTemperatureSettleSeconds",
    }
)


def validate_scenario_control_policy(policy: ScenarioControlPolicy) -> None:
    """Reject unsafe policy values and inconsistent controller bounds."""

    if not isinstance(policy, ScenarioControlPolicy):
        raise ValueError("a scenario control policy is required")
    integer_fields = {
        name: getattr(policy, field)
        for name, field in _POLICY_FIELDS.items()
        if field not in {
            "storage_exhaust_target_id",
            "storage_exhaust_times",
            "evening_latest",
            "tambur_main_off",
            "small_corridor_main_off",
            "toilet_fan_start",
            "toilet_fan_end",
            "bathroom_quiet_start",
            "bathroom_day_start",
            "bathroom_night_start",
        }
    }
    if any(type(value) is not int for value in integer_fields.values()):
        raise ValueError("scenario control numeric values must be integers")
    if not 10 <= policy.absence_confirmation_seconds <= 60:
        raise ValueError("absence confirmation must be between 10 and 60 seconds")
    if not 0 <= policy.presence_rise_seconds <= 60:
        raise ValueError("presence rise must be between 0 and 60 seconds")
    if not policy.absence_confirmation_seconds <= policy.storage_absence_seconds <= 3600:
        raise ValueError("storage absence must include the confirmation interval")
    if not policy.absence_confirmation_seconds <= policy.shower_absence_seconds <= 3600:
        raise ValueError("shower absence must include the confirmation interval")
    if not 1 <= policy.shower_fan_presence_seconds <= 3600:
        raise ValueError("shower fan presence time is outside its supported range")
    if not policy.absence_confirmation_seconds <= policy.shower_fan_off_seconds <= 3600:
        raise ValueError("shower fan off time must include the confirmation interval")
    if not policy.absence_confirmation_seconds <= policy.toilet_absence_seconds <= 3600:
        raise ValueError("toilet absence must include the confirmation interval")
    if not 1 <= policy.toilet_fan_off_seconds <= 3600:
        raise ValueError("toilet fan off time is outside its supported range")
    if not 1 <= policy.bathroom_humidity_threshold <= 100:
        raise ValueError("bathroom humidity threshold is outside its supported range")
    if not 1 <= policy.bathroom_day_off_seconds <= 7200:
        raise ValueError("bathroom fan off time is outside its supported range")
    if not 1 <= policy.office_low_lux_threshold < policy.office_high_lux_threshold <= 100_000:
        raise ValueError("office lux thresholds are invalid")
    brightness_fields = (
        policy.office_day_low_brightness,
        policy.office_day_medium_brightness,
        policy.office_day_bright_brightness,
        policy.office_evening_dark_brightness,
        policy.office_evening_medium_brightness,
        policy.office_evening_bright_brightness,
        policy.office_night_brightness,
    )
    if any(not 0 <= value <= 100 for value in brightness_fields):
        raise ValueError("office brightness is outside its supported range")
    kelvin_fields = (
        policy.office_day_low_kelvin,
        policy.office_day_medium_kelvin,
        policy.office_day_bright_kelvin,
        policy.office_evening_dark_kelvin,
        policy.office_evening_medium_kelvin,
        policy.office_evening_bright_kelvin,
        policy.office_night_kelvin,
    )
    if any(not 1500 <= value <= 6500 for value in kelvin_fields):
        raise ValueError("office temperature is outside its supported range")
    if not 1 <= policy.office_power_settle_seconds <= 30:
        raise ValueError("office power settle time is outside its supported range")
    if not 1 <= policy.office_temperature_settle_seconds <= 30:
        raise ValueError("office temperature settle time is outside its supported range")
    if not (
        policy.office_day_low_brightness
        <= policy.office_day_medium_brightness
        <= policy.office_day_bright_brightness
    ):
        raise ValueError("office day brightness profiles must be ordered")
    if not (
        policy.office_evening_dark_brightness
        <= policy.office_evening_medium_brightness
        <= policy.office_evening_bright_brightness
    ):
        raise ValueError("office evening brightness profiles must be ordered")
    if not (
        policy.office_day_low_kelvin
        >= policy.office_day_medium_kelvin
        >= policy.office_day_bright_kelvin
    ):
        raise ValueError("office day temperatures must preserve the device inversion")
    if not (
        policy.office_evening_dark_kelvin
        >= policy.office_evening_medium_kelvin
        >= policy.office_evening_bright_kelvin
    ):
        raise ValueError("office evening temperatures must preserve the device inversion")
    if not 0 <= policy.manual_release_seconds <= 3600:
        raise ValueError("manual release is outside its supported range")
    if not 0 <= policy.manual_off_block_seconds <= 3600:
        raise ValueError("manual off block is outside its supported range")
    target_id = policy.storage_exhaust_target_id
    if target_id is not None and (
        not isinstance(target_id, str) or _TARGET_ID.fullmatch(target_id) is None
    ):
        raise ValueError("storage exhaust target id is invalid")
    if (
        not isinstance(policy.storage_exhaust_times, tuple)
        or not 1 <= len(policy.storage_exhaust_times) <= 8
        or len(set(policy.storage_exhaust_times)) != len(policy.storage_exhaust_times)
        or any(_CLOCK_TIME.fullmatch(value) is None for value in policy.storage_exhaust_times)
    ):
        raise ValueError("storage exhaust times are invalid")
    if not 1 <= policy.storage_exhaust_run_seconds <= 7200:
        raise ValueError("storage exhaust run time is outside its supported range")
    if not 1 <= policy.small_corridor_lux_threshold <= 100_000:
        raise ValueError("small corridor lux threshold is outside its supported range")
    if not 0 <= policy.lux_hysteresis < policy.small_corridor_lux_threshold:
        raise ValueError("lux hysteresis must be below the activation threshold")
    if not 0 <= policy.lux_hold_seconds <= 600:
        raise ValueError("lux hold is outside its supported range")
    if not (
        0 <= policy.night_brightness_cap_percent
        <= policy.evening_brightness_floor_percent
        <= policy.day_brightness_floor_percent
        <= 100
    ):
        raise ValueError("brightness floors and caps must be ordered")
    if not 1 <= policy.relative_fade_percent <= 99:
        raise ValueError("relative fade must be between 1 and 99 percent")
    if not 1 <= policy.relative_fade_period_seconds <= 3600:
        raise ValueError("relative fade period is outside its supported range")
    if not 1 <= policy.brightness_ramp_seconds <= 600:
        raise ValueError("brightness ramp is outside its supported range")
    if not 1500 <= policy.evening_color_temperature_kelvin <= 6500:
        raise ValueError("evening color temperature is outside its supported range")
    if not 1500 <= policy.neutral_color_temperature_kelvin <= 6500:
        raise ValueError("neutral color temperature is outside its supported range")
    if policy.evening_color_temperature_kelvin > policy.neutral_color_temperature_kelvin:
        raise ValueError("evening temperature must not exceed neutral temperature")
    for value in (
        policy.evening_latest,
        policy.tambur_main_off,
        policy.small_corridor_main_off,
        policy.toilet_fan_start,
        policy.toilet_fan_end,
        policy.bathroom_quiet_start,
        policy.bathroom_day_start,
        policy.bathroom_night_start,
    ):
        if not isinstance(value, str) or _CLOCK_TIME.fullmatch(value) is None:
            raise ValueError("scenario control schedule time is invalid")
    def to_minutes(value: str) -> int:
        return int(value[:2]) * 60 + int(value[3:])

    if to_minutes(policy.toilet_fan_start) >= to_minutes(policy.toilet_fan_end):
        raise ValueError("toilet fan schedule must be ordered")
    if not (
        to_minutes(policy.bathroom_quiet_start)
        < to_minutes(policy.bathroom_day_start)
        < to_minutes(policy.bathroom_night_start)
    ):
        raise ValueError("bathroom schedule must be ordered")
    if not 1 <= policy.kitchen_cover_cap_percent <= 100:
        raise ValueError("kitchen cover cap is outside its supported range")
    if not 1 <= policy.cabinet_cover_cap_percent <= 100:
        raise ValueError("cabinet cover cap is outside its supported range")


def scenario_control_policy_to_payload(
    policy: ScenarioControlPolicy,
) -> dict[str, object]:
    """Serialize one complete policy after validation."""

    validate_scenario_control_policy(policy)
    payload: dict[str, object] = {}
    for external, field in _POLICY_FIELDS.items():
        value = getattr(policy, field)
        payload[external] = list(value) if isinstance(value, tuple) else value
    return payload


def scenario_control_policy_from_payload(value: object) -> ScenarioControlPolicy:
    """Parse only the exact versioned policy surface."""

    if not isinstance(value, Mapping) or frozenset(value) not in {
        frozenset(_POLICY_FIELDS),
        frozenset(_POLICY_FIELDS) - _ROOM_POLICY_FIELDS,
    }:
        raise ValueError("scenario control policy fields are invalid")
    defaults = ScenarioControlPolicy()
    kwargs = {
        field: value[external] if external in value else getattr(defaults, field)
        for external, field in _POLICY_FIELDS.items()
    }
    exhaust_times = kwargs["storage_exhaust_times"]
    if not isinstance(exhaust_times, list) or not all(
        isinstance(item, str) for item in exhaust_times
    ):
        raise ValueError("storage exhaust times are invalid")
    kwargs["storage_exhaust_times"] = tuple(exhaust_times)
    policy = ScenarioControlPolicy(**kwargs)
    validate_scenario_control_policy(policy)
    return policy


def scenario_control_document_to_payload(
    document: ScenarioControlDocument,
) -> dict[str, object]:
    """Serialize the exact durable CAS document."""

    if not isinstance(document, ScenarioControlDocument):
        raise ValueError("a scenario control document is required")
    if type(document.policy_revision) is not int or not 0 <= document.policy_revision <= 2**31 - 1:
        raise ValueError("policy revision is invalid")
    return {
        "version": SCENARIO_CONTROL_DOCUMENT_VERSION,
        "policyRevision": document.policy_revision,
        "policy": scenario_control_policy_to_payload(document.policy),
    }


def scenario_control_document_from_payload(value: object) -> ScenarioControlDocument:
    """Parse a complete persisted document without permissive defaults."""

    if (
        not isinstance(value, Mapping)
        or set(value) != {"version", "policyRevision", "policy"}
        or value.get("version") != SCENARIO_CONTROL_DOCUMENT_VERSION
        or type(value.get("policyRevision")) is not int
    ):
        raise ValueError("scenario control document is invalid")
    document = ScenarioControlDocument(
        policy_revision=int(value["policyRevision"]),
        policy=scenario_control_policy_from_payload(value["policy"]),
    )
    scenario_control_document_to_payload(document)
    return document


def valid_scenario_control_document_payload(value: object) -> bool:
    """Return whether the safety store payload is exact and complete."""

    try:
        scenario_control_document_from_payload(value)
    except (TypeError, ValueError):
        return False
    return True
