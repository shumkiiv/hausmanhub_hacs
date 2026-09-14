"""Pure auxiliary rules for room lighting: exhaust fans and delayed timers.

The room lighting engine owns lights. Some rooms additionally drive a fan that
depends on the room lights and, for the bathroom, on humidity. This module
reproduces that auxiliary behaviour as a deterministic function of observed
state, time band and proven ownership. It never imports Home Assistant, never
reads a state and never sends a command; the runtime applies the returned plan.

The bathroom rules mirror the legacy ``ScenarioControlCoordinator`` controller
one to one so the new engine can run in shadow mode and be compared against the
old writer before any switch:

* ``quiet`` band (before ``day_start``): the fan follows the first light only;
  it stops when the second light is on or when both lights are off.
* ``day`` band: the fan switches on with either light while humidity is at or
  above the threshold; once both lights are off and humidity dropped below the
  threshold, the fan is stopped after ``day_off_seconds``.
* ``night`` band: the fan follows either light and stops as soon as both lights
  are off.

Any unknown light state blocks the stop branch, and an unknown humidity never
counts as dry air: the fan stays on until a fresh reading proves otherwise.
A non-finite reading is malformed input, never evidence: the adapter maps it to
a missing value exactly like the legacy numeric reader did.
Ownership is required for every automatic switch-off; a fan that the system did
not turn on is never switched off by these rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from .room_lighting_ownership import SensorState

BATHROOM_DAY_OFF_TIMER = "bathroom_day_off"


class AuxiliaryViolation(ValueError):
    """Auxiliary rule input is malformed."""


class BathroomBand(StrEnum):
    QUIET = "quiet"
    DAY = "day"
    NIGHT = "night"


class FanAction(StrEnum):
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"


class AuxiliaryTransition(StrEnum):
    """Transition names kept identical to the legacy controller journal."""

    HOLD = "bathroom_hold"
    IDLE = "controller_idle"
    UNKNOWN = "controller_unknown"
    DAY_OFF_PENDING = "bathroom_day_off_pending"


@dataclass(frozen=True, slots=True)
class BathroomTimes:
    """Minute-of-day boundaries of the three bathroom bands."""

    quiet_start_minutes: int
    day_start_minutes: int
    night_start_minutes: int

    def __post_init__(self) -> None:
        for label, value in (
            ("quiet start", self.quiet_start_minutes),
            ("day start", self.day_start_minutes),
            ("night start", self.night_start_minutes),
        ):
            if type(value) is not int or not 0 <= value < 24 * 60:
                raise AuxiliaryViolation(f"bathroom {label} is invalid")
        if not (
            self.quiet_start_minutes
            < self.day_start_minutes
            < self.night_start_minutes
        ):
            raise AuxiliaryViolation("bathroom band boundaries are not ordered")


@dataclass(frozen=True, slots=True)
class BathroomPolicy:
    humidity_threshold: int = 65
    day_off_seconds: int = 1800

    def __post_init__(self) -> None:
        if (
            type(self.humidity_threshold) is not int
            or not 1 <= self.humidity_threshold <= 100
        ):
            raise AuxiliaryViolation("bathroom humidity threshold is invalid")
        if (
            type(self.day_off_seconds) is not int
            or not 1 <= self.day_off_seconds <= 7200
        ):
            raise AuxiliaryViolation("bathroom day-off timer is invalid")


@dataclass(frozen=True, slots=True)
class BathroomObservation:
    """One immutable snapshot used for a single bathroom decision."""

    band: BathroomBand
    lights: tuple[SensorState, ...]
    humidity: float | None
    fan: SensorState
    fan_owned: bool
    allow_activation: bool = True
    dispatch_blocked: bool = False
    pending_timer: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.band, BathroomBand):
            raise AuxiliaryViolation("bathroom band is invalid")
        if len(self.lights) != 2 or any(
            not isinstance(state, SensorState) for state in self.lights
        ):
            raise AuxiliaryViolation("bathroom requires exactly two light states")
        if self.humidity is not None:
            if type(self.humidity) not in {int, float} or not math.isfinite(
                self.humidity
            ):
                # NaN or an infinite reading is never dry and never wet: the
                # caller must pass a missing value instead, matching the
                # legacy numeric state reader.
                raise AuxiliaryViolation("bathroom humidity is invalid")
        if not isinstance(self.fan, SensorState):
            raise AuxiliaryViolation("bathroom fan state is invalid")
        if type(self.fan_owned) is not bool:
            raise AuxiliaryViolation("bathroom fan ownership flag is invalid")
        if type(self.allow_activation) is not bool:
            raise AuxiliaryViolation("bathroom activation flag is invalid")
        if type(self.dispatch_blocked) is not bool:
            raise AuxiliaryViolation("bathroom dispatch block flag is invalid")
        if self.pending_timer is not None and not isinstance(self.pending_timer, str):
            raise AuxiliaryViolation("bathroom pending timer is invalid")


@dataclass(frozen=True, slots=True)
class BathroomDecision:
    """Deterministic outcome of the bathroom exhaust rules."""

    transition: AuxiliaryTransition
    action: FanAction | None = None
    arm_timer_seconds: int | None = None
    clear_timer: bool = False
    blocked: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "transition": self.transition.value,
            "action": self.action.value if self.action is not None else None,
            "armTimerSeconds": self.arm_timer_seconds,
            "clearTimer": self.clear_timer,
            "blocked": self.blocked,
        }


def bathroom_band(now_minutes: int, times: BathroomTimes) -> BathroomBand:
    """Return the current bathroom band for a minute-of-day value."""

    if type(now_minutes) is not int or not 0 <= now_minutes < 24 * 60:
        raise AuxiliaryViolation("current minute is invalid")
    if times.quiet_start_minutes <= now_minutes < times.day_start_minutes:
        return BathroomBand.QUIET
    if times.day_start_minutes <= now_minutes < times.night_start_minutes:
        return BathroomBand.DAY
    return BathroomBand.NIGHT


def evaluate_bathroom_exhaust(
    observation: BathroomObservation,
    policy: BathroomPolicy | None = None,
) -> BathroomDecision:
    """Compute the next bathroom exhaust decision without side effects.

    A ``blocked`` decision means the caller must neither write a journal
    transition nor send a command: the legacy runtime leaves a failed record
    untouched until its evidence changes.
    """

    if not isinstance(observation, BathroomObservation):
        raise AuxiliaryViolation("valid bathroom observation is required")
    policy = policy or BathroomPolicy()

    if observation.dispatch_blocked:
        # A failed command with unchanged evidence must not be retried.
        return BathroomDecision(
            transition=AuxiliaryTransition.HOLD,
            blocked=True,
        )

    light1, light2 = observation.lights
    humidity = observation.humidity
    fan_on = observation.fan is SensorState.ON

    # Keep the legacy operator grouping explicit: day needs humidity, night and
    # quiet do not.
    should_on = (
        observation.band is BathroomBand.DAY
        and humidity is not None
        and humidity >= policy.humidity_threshold
        and SensorState.ON in observation.lights
    ) or (
        observation.band is BathroomBand.NIGHT
        and SensorState.ON in observation.lights
    ) or (
        observation.band is BathroomBand.QUIET
        and light1 is SensorState.ON
        and light2 is SensorState.OFF
    )
    should_off_now = humidity is not None and fan_on and (
        (
            observation.band is BathroomBand.NIGHT
            and light1 is SensorState.OFF
            and light2 is SensorState.OFF
        )
        or (
            observation.band is BathroomBand.QUIET
            and (
                light2 is SensorState.ON
                or (light1 is SensorState.OFF and light2 is SensorState.OFF)
            )
        )
    )

    if should_on:
        action = None
        if observation.fan is SensorState.OFF and observation.allow_activation:
            action = FanAction.TURN_ON
        return BathroomDecision(
            transition=AuxiliaryTransition.HOLD,
            action=action,
            clear_timer=True,
        )

    if light1 not in (SensorState.ON, SensorState.OFF) or light2 not in (
        SensorState.ON,
        SensorState.OFF,
    ):
        return BathroomDecision(transition=AuxiliaryTransition.UNKNOWN)

    if should_off_now and observation.fan_owned:
        return BathroomDecision(
            transition=AuxiliaryTransition.IDLE,
            action=FanAction.TURN_OFF,
            clear_timer=True,
        )

    if (
        observation.band is BathroomBand.DAY
        and light1 is SensorState.OFF
        and light2 is SensorState.OFF
        and fan_on
        and humidity is not None
        and humidity < policy.humidity_threshold
        and observation.fan_owned
    ):
        already_armed = observation.pending_timer == BATHROOM_DAY_OFF_TIMER
        return BathroomDecision(
            transition=AuxiliaryTransition.DAY_OFF_PENDING,
            arm_timer_seconds=None if already_armed else policy.day_off_seconds,
        )

    unknown = fan_on and humidity is None
    return BathroomDecision(
        transition=(
            AuxiliaryTransition.UNKNOWN
            if unknown
            else AuxiliaryTransition.HOLD
        )
    )


def evaluate_bathroom_exhaust_due(
    observation: BathroomObservation,
    *,
    timer_kind: str,
    policy: BathroomPolicy | None = None,
) -> BathroomDecision:
    """Apply the armed ``bathroom_day_off`` timer when it fires.

    Any other timer kind only returns to idle, matching the legacy room
    reconciliation that never switches the fan off for an unrelated timer.
    """

    if not isinstance(observation, BathroomObservation):
        raise AuxiliaryViolation("valid bathroom observation is required")
    policy = policy or BathroomPolicy()
    light1, light2 = observation.lights
    fan_on = observation.fan is SensorState.ON
    if (
        timer_kind == BATHROOM_DAY_OFF_TIMER
        and observation.band is BathroomBand.DAY
        and light1 is SensorState.OFF
        and light2 is SensorState.OFF
        and observation.humidity is not None
        and observation.humidity < policy.humidity_threshold
        and fan_on
        and observation.fan_owned
    ):
        return BathroomDecision(
            transition=AuxiliaryTransition.IDLE,
            action=FanAction.TURN_OFF,
            clear_timer=True,
        )
    return BathroomDecision(
        transition=(
            AuxiliaryTransition.UNKNOWN
            if observation.humidity is None and fan_on
            else AuxiliaryTransition.IDLE
        ),
        clear_timer=True,
    )
