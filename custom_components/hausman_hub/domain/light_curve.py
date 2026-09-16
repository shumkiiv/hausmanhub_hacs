"""Pure day-curve lighting engine for the tambur profile.

The owner-supplied algorithm ("Алгоритм_освещения_тамбура.xlsx") defines the
lighting mode by time of day, sunrise and sunset. Presence never switches the
time mode; motion and presence may only return a reduced brightness:

* ``00:00`` .. ``07:00`` - everything off.
* ``07:00`` .. ``max(sunrise, 09:00)`` - mirror on, independent of presence.
* ``max(sunrise, 09:00)`` .. ``12:00`` - chandelier on at 5%, ramp to 100%,
  colour moves to the day-neutral value.
* ``12:00`` .. ``sunset - 60 min`` - 100% at the neutral colour.
* ``sunset - 60 min`` .. ``22:00`` - brightness fades to 5%, colour warms.
* ``22:00`` .. ``23:00`` - 5% warm.
* ``23:00`` .. ``00:00`` - chandelier off, mirror light on.
* 5 minutes without motion or presence start a 5-minute fade to 5%; presence
  confirmed for 8 seconds fades from the actual current brightness back to
  the maximum of the current time mode over 10 seconds, without changing the
  colour or the time mode.

The engine is pure: it never reads Home Assistant, never sends a command and
returns the desired state plus the fade the caller should apply. All numeric
parameters match the supplied document; the day-neutral colour defaults to the
value already used by the live tambur configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dt_time
from enum import StrEnum

from .room_lighting_ownership import SensorState


class LightCurveViolation(ValueError):
    """Day-curve input is malformed."""


class CurveReason(StrEnum):
    OFF = "off"
    UNKNOWN = "unknown"
    MORNING = "morning"
    RAMP = "ramp"
    DAY = "day"
    EVENING = "evening"
    LATE_EVENING = "late_evening"
    NIGHT = "night"
    ABSENCE = "absence"
    PRESENCE_RETURN = "presence_return"


@dataclass(frozen=True, slots=True)
class LightCurveProfile:
    """Parameters of the day curve; defaults are the supplied document."""

    morning_start: dt_time = dt_time(9, 0)
    day_start: dt_time = dt_time(12, 0)
    evening_end: dt_time = dt_time(22, 0)
    night_start: dt_time = dt_time(23, 0)
    night_end: dt_time = dt_time(0, 0)
    mirror_morning_start: dt_time = dt_time(7, 0)
    evening_lead_minutes: int = 60
    morning_min_percent: int = 5
    day_max_percent: int = 100
    warm_kelvin: int = 2200
    neutral_kelvin: int = 3000
    absence_start_seconds: int = 300
    absence_fade_seconds: int = 300
    presence_confirm_seconds: int = 8
    return_fade_seconds: int = 10
    mode_fade_seconds: int = 20

    def __post_init__(self) -> None:
        for label, value in (
            ("morning start", self.morning_start),
            ("day start", self.day_start),
            ("evening end", self.evening_end),
            ("night start", self.night_start),
            ("night end", self.night_end),
            ("mirror morning start", self.mirror_morning_start),
        ):
            if not isinstance(value, dt_time):
                raise LightCurveViolation(f"day curve {label} is invalid")
        if not (
            0 <= self.morning_min_percent < self.day_max_percent <= 100
        ):
            raise LightCurveViolation("day curve brightness bounds are invalid")
        if not 0 < self.warm_kelvin < self.neutral_kelvin:
            raise LightCurveViolation("day curve colour bounds are invalid")
        for label, value in (
            ("evening lead", self.evening_lead_minutes),
            ("absence start", self.absence_start_seconds),
            ("absence fade", self.absence_fade_seconds),
            ("presence confirmation", self.presence_confirm_seconds),
            ("return fade", self.return_fade_seconds),
            ("mode fade", self.mode_fade_seconds),
        ):
            if type(value) is not int or value < 0:
                raise LightCurveViolation(f"day curve {label} is invalid")


@dataclass(frozen=True, slots=True)
class LightCurveContext:
    """One immutable snapshot for a single day-curve decision.

    ``presence_confirmed`` is true when the current presence has already
    lasted the confirmation window. ``absence_seconds`` is the observed time
    since the last presence; while presence is unconfirmed it carries the
    absence accumulated before this presence started, so a reduced brightness
    is held until the return is confirmed.
    """

    now: dt_time
    sunrise: dt_time
    sunset: dt_time
    presence: SensorState
    chandelier: SensorState
    mirror: SensorState
    presence_confirmed: bool = False
    absence_seconds: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.now, dt_time) or not isinstance(self.sunrise, dt_time):
            raise LightCurveViolation("day curve time input is invalid")
        if not isinstance(self.sunset, dt_time):
            raise LightCurveViolation("day curve time input is invalid")
        if not isinstance(self.presence, SensorState):
            raise LightCurveViolation("day curve presence is invalid")
        if not isinstance(self.chandelier, SensorState):
            raise LightCurveViolation("day curve chandelier state is invalid")
        if not isinstance(self.mirror, SensorState):
            raise LightCurveViolation("day curve mirror state is invalid")
        if type(self.presence_confirmed) is not bool:
            raise LightCurveViolation("day curve presence confirmation is invalid")
        if self.absence_seconds is not None and (
            type(self.absence_seconds) is not int or self.absence_seconds < 0
        ):
            raise LightCurveViolation("day curve absence timer is invalid")


@dataclass(frozen=True, slots=True)
class LightCurveDecision:
    """Desired chandelier and mirror state with the fade to apply."""

    reason: CurveReason
    chandelier_on: bool
    brightness_percent: int | None = None
    color_temperature: int | None = None
    fade_seconds: int = 0
    mirror_on: bool = False
    hold: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "reason": self.reason.value,
            "chandelierOn": self.chandelier_on,
            "brightnessPercent": self.brightness_percent,
            "colorTemperature": self.color_temperature,
            "fadeSeconds": self.fade_seconds,
            "mirrorOn": self.mirror_on,
            "hold": self.hold,
        }


def _minutes(value: dt_time) -> int:
    return value.hour * 60 + value.minute


def _in_window(now: int, start: int, end: int) -> bool:
    """Half-open window that supports one midnight wrap."""

    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _ratio(now: int, start: int, end: int) -> float:
    span = end - start
    if span <= 0 or now <= start:
        return 0.0
    if now >= end:
        return 1.0
    return (now - start) / span


def _mode_at(
    profile: LightCurveProfile, now: int, sunrise: int, sunset: int
) -> tuple[CurveReason, bool, float, int]:
    """Return (reason, on, brightness, kelvin) for the current time mode."""

    morning = max(_minutes(profile.morning_start), sunrise)
    day = max(_minutes(profile.day_start), morning)
    evening_start = sunset - profile.evening_lead_minutes
    evening = _minutes(profile.evening_end)
    night_start = _minutes(profile.night_start)
    night_end = _minutes(profile.night_end)

    if _in_window(now, night_start, night_end):
        return CurveReason.NIGHT, False, 0.0, profile.warm_kelvin
    if now < morning:
        return CurveReason.OFF, False, 0.0, profile.warm_kelvin
    if now < day:
        ratio = _ratio(now, morning, day)
        brightness = profile.morning_min_percent + (
            profile.day_max_percent - profile.morning_min_percent
        ) * ratio
        kelvin = round(
            profile.warm_kelvin
            + (profile.neutral_kelvin - profile.warm_kelvin) * ratio
        )
        return CurveReason.RAMP, True, brightness, kelvin
    if now < evening_start:
        return (
            CurveReason.DAY,
            True,
            float(profile.day_max_percent),
            profile.neutral_kelvin,
        )
    if now < evening:
        ratio = _ratio(now, evening_start, evening)
        brightness = profile.day_max_percent - (
            profile.day_max_percent - profile.morning_min_percent
        ) * ratio
        kelvin = round(
            profile.neutral_kelvin
            + (profile.warm_kelvin - profile.neutral_kelvin) * ratio
        )
        reason = CurveReason.EVENING if now < sunset else CurveReason.LATE_EVENING
        return reason, True, brightness, kelvin
    return CurveReason.LATE_EVENING, True, float(profile.morning_min_percent), profile.warm_kelvin


def evaluate_light_curve(
    profile: LightCurveProfile,
    context: LightCurveContext,
) -> LightCurveDecision:
    """Compute the desired tambur state without any side effect."""

    if not isinstance(profile, LightCurveProfile):
        raise LightCurveViolation("valid day curve profile is required")
    if not isinstance(context, LightCurveContext):
        raise LightCurveViolation("valid day curve context is required")

    now = _minutes(context.now)
    sunrise = _minutes(context.sunrise)
    sunset = _minutes(context.sunset)
    night_start = _minutes(profile.night_start)
    night_end = _minutes(profile.night_end)
    night = _in_window(now, night_start, night_end)

    reason, mode_on, mode_brightness, mode_kelvin = _mode_at(
        profile, now, sunrise, sunset
    )
    morning = max(_minutes(profile.morning_start), sunrise)
    mirror_on = night or _minutes(profile.mirror_morning_start) <= now < morning
    if mirror_on and not night:
        reason = CurveReason.MORNING

    if not mode_on:
        return LightCurveDecision(
            reason=reason,
            chandelier_on=False,
            brightness_percent=None,
            color_temperature=None,
            fade_seconds=profile.mode_fade_seconds,
            mirror_on=mirror_on,
        )

    # Occupancy adjusts brightness, never the time mode. With unknown presence
    # retain an already-on level; a scheduled start uses the minimum, not an
    # invented occupied-room maximum. Colour still follows the time mode.
    if context.presence not in (SensorState.ON, SensorState.OFF):
        return LightCurveDecision(
            reason=CurveReason.UNKNOWN,
            chandelier_on=True,
            brightness_percent=(
                profile.morning_min_percent
                if (context.chandelier is SensorState.OFF
                    or context.mirror is SensorState.ON) else None
            ),
            color_temperature=mode_kelvin,
            fade_seconds=profile.mode_fade_seconds,
            mirror_on=mirror_on,
            hold=(context.chandelier is not SensorState.OFF
                  and context.mirror is not SensorState.ON),
        )

    # Presence never changes the colour or the time mode. Confirmed presence
    # returns the actual current brightness to the mode maximum over the
    # return fade; an unconfirmed presence holds the reduced level instead.
    if context.presence is SensorState.ON and context.presence_confirmed:
        return LightCurveDecision(
            reason=CurveReason.PRESENCE_RETURN,
            chandelier_on=True,
            brightness_percent=round(mode_brightness),
            color_temperature=mode_kelvin,
            fade_seconds=profile.return_fade_seconds,
            mirror_on=mirror_on,
        )

    absence_seconds = context.absence_seconds
    if absence_seconds is not None and absence_seconds >= profile.absence_start_seconds:
        progress = min(
            1.0,
            (absence_seconds - profile.absence_start_seconds)
            / max(1, profile.absence_fade_seconds),
        )
        target = mode_brightness - (
            mode_brightness - profile.morning_min_percent
        ) * progress
        remaining = max(
            1,
            int(
                profile.absence_fade_seconds
                - (absence_seconds - profile.absence_start_seconds)
            ),
        )
        return LightCurveDecision(
            reason=CurveReason.ABSENCE,
            chandelier_on=True,
            brightness_percent=round(target),
            color_temperature=mode_kelvin,
            fade_seconds=remaining if progress < 1.0 else 0,
            mirror_on=mirror_on,
        )

    return LightCurveDecision(
        reason=reason,
        chandelier_on=True,
        brightness_percent=round(mode_brightness),
        color_temperature=mode_kelvin,
        fade_seconds=profile.mode_fade_seconds,
        mirror_on=mirror_on,
    )
