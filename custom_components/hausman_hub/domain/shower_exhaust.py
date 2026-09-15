"""Pure shower exhaust engine (presence and humidity driven fan).

The legacy shower controller is a presence-and-humidity fan, not a light-led
one:

* humidity above the threshold switches the fan on immediately, even while the
  presence state is still unknown;
* while somebody is present and the air is dry, a presence timer runs; when it
  expires with the person still present, the fan switches on anyway so a
  shower is ventilated;
* once presence is gone, an owned fan stops only after its stable-absence
  window, and only while humidity is known and at or below the threshold.

The room lights keep their own day/evening/night profiles; this engine only
decides the fan. It is pure: no Home Assistant import, no command, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .room_lighting_ownership import SensorState

SHOWER_PRESENCE_TIMER = "shower_presence"
SHOWER_ABSENCE_TIMER = "shower_absence"


class ShowerExhaustViolation(ValueError):
    """Shower exhaust input is malformed."""


class ShowerFanAction(StrEnum):
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"


class ShowerTimer(StrEnum):
    PRESENCE = SHOWER_PRESENCE_TIMER
    ABSENCE = SHOWER_ABSENCE_TIMER


class ShowerTransition(StrEnum):
    """Transition names kept identical to the legacy controller journal."""

    PROFILE = "shower_profile"
    HUMIDITY = "shower_fan_humidity"
    OCCUPIED = "occupied_hold"
    ABSENCE_PENDING = "shower_absence_pending"
    IDLE = "controller_idle"
    UNKNOWN = "controller_unknown"


@dataclass(frozen=True, slots=True)
class ShowerExhaustPolicy:
    humidity_threshold: int = 55
    presence_run_seconds: int = 120
    absence_seconds: int = 300
    fan_off_seconds: int = 300

    def __post_init__(self) -> None:
        if type(self.humidity_threshold) is not int or not 1 <= self.humidity_threshold <= 100:
            raise ShowerExhaustViolation("shower humidity threshold is invalid")
        for label, value in (
            ("presence run", self.presence_run_seconds),
            ("absence", self.absence_seconds),
            ("fan off", self.fan_off_seconds),
        ):
            if type(value) is not int or not 1 <= value <= 3600:
                raise ShowerExhaustViolation(f"shower {label} timer is invalid")


@dataclass(frozen=True, slots=True)
class ShowerExhaustObservation:
    presence: SensorState
    humidity: float | None
    fan: SensorState
    fan_owned: bool
    lights_owned_on: bool
    allow_activation: bool = True
    dispatch_blocked: bool = False
    pending_timer: str | None = None
    elapsed_seconds: int | None = None

    def __post_init__(self) -> None:
        for label, state in (
            ("presence", self.presence),
            ("fan", self.fan),
        ):
            if not isinstance(state, SensorState):
                raise ShowerExhaustViolation(f"shower {label} state is invalid")
        if self.humidity is not None and type(self.humidity) not in {int, float}:
            raise ShowerExhaustViolation("shower humidity is invalid")
        for label, value in (
            ("fan ownership", self.fan_owned),
            ("light ownership", self.lights_owned_on),
            ("activation", self.allow_activation),
            ("dispatch block", self.dispatch_blocked),
        ):
            if type(value) is not bool:
                raise ShowerExhaustViolation(f"shower {label} flag is invalid")
        if self.pending_timer is not None and not isinstance(self.pending_timer, str):
            raise ShowerExhaustViolation("shower pending timer is invalid")
        if self.elapsed_seconds is not None and (
            type(self.elapsed_seconds) is not int or self.elapsed_seconds < 0
        ):
            raise ShowerExhaustViolation("shower elapsed timer is invalid")


@dataclass(frozen=True, slots=True)
class ShowerExhaustDecision:
    transition: ShowerTransition
    action: ShowerFanAction | None = None
    arm_timer: ShowerTimer | None = None
    arm_seconds: int | None = None
    clear_timer: bool = False
    blocked: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "transition": self.transition.value,
            "action": self.action.value if self.action is not None else None,
            "armTimer": self.arm_timer.value if self.arm_timer is not None else None,
            "armSeconds": self.arm_seconds,
            "clearTimer": self.clear_timer,
            "blocked": self.blocked,
        }


def _humid(observation: ShowerExhaustObservation, policy: ShowerExhaustPolicy) -> bool:
    return (
        observation.humidity is not None
        and observation.humidity > policy.humidity_threshold
    )


def evaluate_shower_exhaust(
    observation: ShowerExhaustObservation,
    policy: ShowerExhaustPolicy | None = None,
) -> ShowerExhaustDecision:
    """Compute the next shower fan decision without side effects."""

    if not isinstance(observation, ShowerExhaustObservation):
        raise ShowerExhaustViolation("valid shower observation is required")
    policy = policy or ShowerExhaustPolicy()

    if observation.dispatch_blocked:
        return ShowerExhaustDecision(
            transition=ShowerTransition.IDLE, blocked=True
        )

    humid = _humid(observation, policy)
    fan_off = observation.fan is SensorState.OFF
    if humid and fan_off and observation.allow_activation:
        return ShowerExhaustDecision(
            transition=ShowerTransition.HUMIDITY,
            action=ShowerFanAction.TURN_ON,
        )

    if observation.presence not in (SensorState.ON, SensorState.OFF):
        return ShowerExhaustDecision(transition=ShowerTransition.UNKNOWN)

    if observation.presence is SensorState.ON:
        if humid:
            return ShowerExhaustDecision(
                transition=ShowerTransition.PROFILE, clear_timer=True
            )
        if fan_off and observation.allow_activation:
            return ShowerExhaustDecision(
                transition=ShowerTransition.PROFILE,
                arm_timer=ShowerTimer.PRESENCE,
                arm_seconds=policy.presence_run_seconds,
                clear_timer=True,
            )
        return ShowerExhaustDecision(
            transition=ShowerTransition.PROFILE, clear_timer=True
        )

    # Presence is off. A dry, owned fan may stop after its own window; the
    # lights keep their own absence handling in the room engine.
    fan_can_stop = (
        observation.fan is SensorState.ON
        and observation.humidity is not None
        and observation.humidity <= policy.humidity_threshold
        and observation.fan_owned
    )
    if not observation.lights_owned_on and not fan_can_stop:
        return ShowerExhaustDecision(
            transition=(
                ShowerTransition.UNKNOWN
                if observation.fan is SensorState.ON and observation.humidity is None
                else ShowerTransition.IDLE
            )
        )
    seconds = min(
        policy.absence_seconds if observation.lights_owned_on else 3601,
        policy.fan_off_seconds if fan_can_stop else 3601,
    )
    return ShowerExhaustDecision(
        transition=ShowerTransition.ABSENCE_PENDING,
        arm_timer=ShowerTimer.ABSENCE,
        arm_seconds=seconds,
    )


def evaluate_shower_exhaust_due(
    observation: ShowerExhaustObservation,
    *,
    timer_kind: str,
    policy: ShowerExhaustPolicy | None = None,
) -> ShowerExhaustDecision:
    """Apply an armed shower timer when it fires."""

    if not isinstance(observation, ShowerExhaustObservation):
        raise ShowerExhaustViolation("valid shower observation is required")
    policy = policy or ShowerExhaustPolicy()

    if timer_kind == SHOWER_PRESENCE_TIMER:
        if observation.presence is SensorState.ON and observation.fan is SensorState.OFF:
            return ShowerExhaustDecision(
                transition=ShowerTransition.OCCUPIED,
                action=ShowerFanAction.TURN_ON,
            )
        return ShowerExhaustDecision(transition=ShowerTransition.OCCUPIED)

    if timer_kind != SHOWER_ABSENCE_TIMER or observation.presence is not SensorState.OFF:
        return ShowerExhaustDecision(
            transition=(
                ShowerTransition.UNKNOWN
                if observation.presence not in (SensorState.ON, SensorState.OFF)
                else ShowerTransition.IDLE
            )
        )

    elapsed = observation.elapsed_seconds or 0
    fan_pending = (
        observation.fan is SensorState.ON
        and observation.humidity is not None
        and observation.humidity <= policy.humidity_threshold
        and observation.fan_owned
    )
    if fan_pending and elapsed >= policy.fan_off_seconds:
        return ShowerExhaustDecision(
            transition=ShowerTransition.IDLE,
            action=ShowerFanAction.TURN_OFF,
            clear_timer=True,
        )
    if fan_pending:
        return ShowerExhaustDecision(
            transition=ShowerTransition.ABSENCE_PENDING,
            arm_timer=ShowerTimer.ABSENCE,
            arm_seconds=max(0, policy.fan_off_seconds - elapsed),
        )
    return ShowerExhaustDecision(
        transition=(
            ShowerTransition.UNKNOWN
            if observation.fan is SensorState.ON and observation.humidity is None
            else ShowerTransition.IDLE
        ),
        clear_timer=True,
    )
