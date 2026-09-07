"""Pure policy primitives shared by the consolidated scenario controllers.

The module deliberately contains no Home Assistant imports.  Controllers can
use these functions to make a deterministic plan from an immutable snapshot.
"""

from __future__ import annotations

from datetime import datetime, time
from enum import StrEnum
from dataclasses import dataclass
from datetime import timedelta


class OccupancyEvidence(StrEnum):
    """Effective occupancy of a zone using motion OR presence semantics."""

    OCCUPIED = "occupied"
    ABSENT = "absent"
    UNKNOWN = "unknown"

    @classmethod
    def from_states(cls, motion: object = None, presence: object = None) -> "OccupancyEvidence":
        states = {str(value).lower() for value in (motion, presence)}
        if "on" in states:
            return cls.OCCUPIED
        if states.intersection({"unknown", "unavailable", "none"}):
            return cls.UNKNOWN
        if states.intersection({"off", "false", "0"}):
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
