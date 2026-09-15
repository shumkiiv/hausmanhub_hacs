"""Tests for the pure shower exhaust fan engine (legacy parity rules)."""

from __future__ import annotations

import pytest

from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState
from custom_components.hausman_hub.domain.shower_exhaust import (
    SHOWER_ABSENCE_TIMER,
    SHOWER_PRESENCE_TIMER,
    ShowerExhaustObservation,
    ShowerExhaustPolicy,
    ShowerExhaustViolation,
    ShowerFanAction,
    ShowerTimer,
    ShowerTransition,
    evaluate_shower_exhaust,
    evaluate_shower_exhaust_due,
)

POLICY = ShowerExhaustPolicy()


def _obs(
    *,
    presence: SensorState = SensorState.OFF,
    humidity: float | None = 40.0,
    fan: SensorState = SensorState.OFF,
    fan_owned: bool = False,
    lights_owned_on: bool = False,
    allow_activation: bool = True,
    pending_timer: str | None = None,
    elapsed_seconds: int | None = None,
) -> ShowerExhaustObservation:
    return ShowerExhaustObservation(
        presence=presence,
        humidity=humidity,
        fan=fan,
        fan_owned=fan_owned,
        lights_owned_on=lights_owned_on,
        allow_activation=allow_activation,
        pending_timer=pending_timer,
        elapsed_seconds=elapsed_seconds,
    )


def test_humid_air_switches_the_fan_on_immediately() -> None:
    decision = evaluate_shower_exhaust(_obs(humidity=60.0), POLICY)
    assert decision.action is ShowerFanAction.TURN_ON
    assert decision.transition is ShowerTransition.HUMIDITY


def test_humid_air_with_fan_on_keeps_the_profile() -> None:
    decision = evaluate_shower_exhaust(
        _obs(presence=SensorState.ON, humidity=60.0, fan=SensorState.ON),
        POLICY,
    )
    assert decision.action is None
    assert decision.transition is ShowerTransition.PROFILE


def test_unknown_presence_never_guesses() -> None:
    decision = evaluate_shower_exhaust(
        _obs(presence=SensorState.UNKNOWN, humidity=40.0), POLICY
    )
    assert decision.transition is ShowerTransition.UNKNOWN
    assert decision.action is None


def test_presence_with_dry_air_arms_the_presence_timer() -> None:
    decision = evaluate_shower_exhaust(
        _obs(presence=SensorState.ON, humidity=40.0, fan=SensorState.OFF), POLICY
    )
    assert decision.transition is ShowerTransition.PROFILE
    assert decision.arm_timer is ShowerTimer.PRESENCE
    assert decision.arm_seconds == 120
    assert decision.clear_timer is True


def test_presence_with_running_fan_does_not_rearm() -> None:
    decision = evaluate_shower_exhaust(
        _obs(presence=SensorState.ON, humidity=40.0, fan=SensorState.ON), POLICY
    )
    assert decision.arm_timer is None
    assert decision.transition is ShowerTransition.PROFILE


def test_absence_arms_the_shortest_relevant_timer() -> None:
    decision = evaluate_shower_exhaust(
        _obs(
            presence=SensorState.OFF,
            humidity=40.0,
            fan=SensorState.ON,
            fan_owned=True,
            lights_owned_on=True,
        ),
        POLICY,
    )
    assert decision.transition is ShowerTransition.ABSENCE_PENDING
    assert decision.arm_timer is ShowerTimer.ABSENCE
    assert decision.arm_seconds == 300


def test_absence_with_unknown_humidity_keeps_the_fan() -> None:
    decision = evaluate_shower_exhaust(
        _obs(
            presence=SensorState.OFF,
            humidity=None,
            fan=SensorState.ON,
            fan_owned=True,
            lights_owned_on=True,
        ),
        POLICY,
    )
    # The lights still schedule their own absence, but the fan is not a
    # candidate to stop while the air is unknown.
    assert decision.arm_timer is not None
    assert decision.transition is ShowerTransition.ABSENCE_PENDING


def test_absence_without_ownership_is_idle() -> None:
    decision = evaluate_shower_exhaust(
        _obs(presence=SensorState.OFF, fan=SensorState.OFF), POLICY
    )
    assert decision.transition is ShowerTransition.IDLE
    assert decision.arm_timer is None


def test_due_presence_timer_runs_the_fan() -> None:
    decision = evaluate_shower_exhaust_due(
        _obs(presence=SensorState.ON, humidity=40.0, fan=SensorState.OFF),
        timer_kind=SHOWER_PRESENCE_TIMER,
        policy=POLICY,
    )
    assert decision.action is ShowerFanAction.TURN_ON
    assert decision.transition is ShowerTransition.OCCUPIED


def test_due_absence_timer_stops_the_dry_owned_fan() -> None:
    decision = evaluate_shower_exhaust_due(
        _obs(
            presence=SensorState.OFF,
            humidity=40.0,
            fan=SensorState.ON,
            fan_owned=True,
            elapsed_seconds=300,
        ),
        timer_kind=SHOWER_ABSENCE_TIMER,
        policy=POLICY,
    )
    assert decision.action is ShowerFanAction.TURN_OFF
    assert decision.transition is ShowerTransition.IDLE


def test_due_absence_timer_waits_before_the_window() -> None:
    decision = evaluate_shower_exhaust_due(
        _obs(
            presence=SensorState.OFF,
            humidity=40.0,
            fan=SensorState.ON,
            fan_owned=True,
            elapsed_seconds=120,
        ),
        timer_kind=SHOWER_ABSENCE_TIMER,
        policy=POLICY,
    )
    assert decision.action is None
    assert decision.arm_seconds == 180


def test_due_absence_never_stops_the_fan_while_humid() -> None:
    decision = evaluate_shower_exhaust_due(
        _obs(
            presence=SensorState.OFF,
            humidity=70.0,
            fan=SensorState.ON,
            fan_owned=True,
            elapsed_seconds=600,
        ),
        timer_kind=SHOWER_ABSENCE_TIMER,
        policy=POLICY,
    )
    assert decision.action is None


def test_validation() -> None:
    with pytest.raises(ShowerExhaustViolation):
        ShowerExhaustPolicy(humidity_threshold=0)
    with pytest.raises(ShowerExhaustViolation):
        ShowerExhaustPolicy(absence_seconds=0)
    with pytest.raises(ShowerExhaustViolation):
        _obs(presence="on")  # type: ignore[arg-type]
    with pytest.raises(ShowerExhaustViolation):
        evaluate_shower_exhaust(object(), POLICY)  # type: ignore[arg-type]


def test_blocked_dispatch_is_a_noop() -> None:
    observation = ShowerExhaustObservation(
        presence=SensorState.ON,
        humidity=70.0,
        fan=SensorState.OFF,
        fan_owned=False,
        lights_owned_on=False,
        dispatch_blocked=True,
    )
    decision = evaluate_shower_exhaust(observation, POLICY)
    assert decision.blocked is True
    assert decision.action is None


def test_disabled_activation_never_turns_the_fan_on() -> None:
    decision = evaluate_shower_exhaust(
        _obs(humidity=70.0, allow_activation=False), POLICY
    )
    assert decision.action is None


def test_due_presence_timer_is_silent_when_presence_is_gone() -> None:
    for presence in (SensorState.OFF, SensorState.UNKNOWN):
        decision = evaluate_shower_exhaust_due(
            _obs(presence=presence, fan=SensorState.OFF),
            timer_kind=SHOWER_PRESENCE_TIMER,
            policy=POLICY,
        )
        assert decision.action is None


def test_due_unknown_timer_kind_is_idle() -> None:
    decision = evaluate_shower_exhaust_due(
        _obs(presence=SensorState.OFF),
        timer_kind="something_else",
        policy=POLICY,
    )
    assert decision.action is None
    assert decision.transition is ShowerTransition.IDLE


def test_unknown_humidity_is_never_counted_as_dry() -> None:
    decision = evaluate_shower_exhaust(
        _obs(presence=SensorState.OFF, humidity=None, fan=SensorState.ON, fan_owned=True),
        POLICY,
    )
    # The lights may still schedule their own absence; the fan must not stop.
    if decision.arm_timer is not None:
        due = evaluate_shower_exhaust_due(
            _obs(
                presence=SensorState.OFF,
                humidity=None,
                fan=SensorState.ON,
                fan_owned=True,
                elapsed_seconds=10_000,
            ),
            timer_kind=SHOWER_ABSENCE_TIMER,
            policy=POLICY,
        )
        assert due.action is None


def test_decision_payload_is_stable() -> None:
    decision = evaluate_shower_exhaust(_obs(humidity=70.0), POLICY)
    assert decision.to_payload() == {
        "transition": "shower_fan_humidity",
        "action": "turn_on",
        "armTimer": None,
        "armSeconds": None,
        "clearTimer": False,
        "blocked": False,
    }
