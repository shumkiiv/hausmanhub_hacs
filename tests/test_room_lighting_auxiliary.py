"""Unit and parity tests for the pure bathroom exhaust auxiliary engine."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.hausman_hub.domain.room_lighting_auxiliary import (
    BATHROOM_DAY_OFF_TIMER,
    AuxiliaryTransition,
    AuxiliaryViolation,
    BathroomBand,
    BathroomObservation,
    BathroomPolicy,
    BathroomTimes,
    FanAction,
    bathroom_band,
    evaluate_bathroom_exhaust,
    evaluate_bathroom_exhaust_due,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState

TIMES = BathroomTimes(
    quiet_start_minutes=6 * 60,
    day_start_minutes=8 * 60,
    night_start_minutes=22 * 60,
)
POLICY = BathroomPolicy(humidity_threshold=65, day_off_seconds=1800)


def bathroom(
    *,
    band: BathroomBand = BathroomBand.DAY,
    lights: tuple[SensorState, SensorState] = (SensorState.OFF, SensorState.OFF),
    humidity: float | None = 45.0,
    fan: SensorState = SensorState.OFF,
    fan_owned: bool = False,
    allow_activation: bool = True,
    dispatch_blocked: bool = False,
    pending_timer: str | None = None,
) -> BathroomObservation:
    return BathroomObservation(
        band=band,
        lights=lights,
        humidity=humidity,
        fan=fan,
        fan_owned=fan_owned,
        allow_activation=allow_activation,
        dispatch_blocked=dispatch_blocked,
        pending_timer=pending_timer,
    )


@pytest.mark.parametrize(
    ("minute", "expected"),
    (
        (5 * 60 + 59, BathroomBand.NIGHT),
        (6 * 60, BathroomBand.QUIET),
        (7 * 60 + 59, BathroomBand.QUIET),
        (8 * 60, BathroomBand.DAY),
        (21 * 60 + 59, BathroomBand.DAY),
        (22 * 60, BathroomBand.NIGHT),
        (23 * 60 + 30, BathroomBand.NIGHT),
    ),
)
def test_band_boundaries(minute: int, expected: BathroomBand) -> None:
    assert bathroom_band(minute, TIMES) is expected


@pytest.mark.parametrize("bad", (float("nan"), float("inf"), float("-inf")))
def test_non_finite_humidity_is_never_evidence(bad: float) -> None:
    with pytest.raises(AuxiliaryViolation):
        bathroom(humidity=bad)


def test_band_and_policy_validation() -> None:
    with pytest.raises(AuxiliaryViolation):
        BathroomTimes(8 * 60, 6 * 60, 22 * 60)
    with pytest.raises(AuxiliaryViolation):
        BathroomPolicy(humidity_threshold=0)
    with pytest.raises(AuxiliaryViolation):
        BathroomPolicy(day_off_seconds=0)
    with pytest.raises(AuxiliaryViolation):
        bathroom_band(24 * 60, TIMES)
    with pytest.raises(AuxiliaryViolation):
        BathroomObservation(
            band=BathroomBand.DAY,
            lights=(SensorState.OFF,),
            humidity=45.0,
            fan=SensorState.OFF,
            fan_owned=False,
        )


def test_day_band_switches_fan_on_only_with_humidity_and_a_light() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(humidity=65.0, lights=(SensorState.ON, SensorState.OFF)), POLICY
    )
    assert decision.transition is AuxiliaryTransition.HOLD
    assert decision.action is FanAction.TURN_ON
    assert decision.clear_timer is True

    thrifty = evaluate_bathroom_exhaust(
        bathroom(humidity=64.0, lights=(SensorState.ON, SensorState.OFF)), POLICY
    )
    assert thrifty.action is None
    assert thrifty.transition is AuxiliaryTransition.HOLD

    dark = evaluate_bathroom_exhaust(bathroom(humidity=80.0), POLICY)
    assert dark.action is None
    assert dark.transition is AuxiliaryTransition.HOLD


def test_night_band_follows_any_light() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(
            band=BathroomBand.NIGHT,
            humidity=10.0,
            lights=(SensorState.OFF, SensorState.ON),
        ),
        POLICY,
    )
    assert decision.action is FanAction.TURN_ON


def test_quiet_band_follows_first_light_only() -> None:
    on = evaluate_bathroom_exhaust(
        bathroom(
            band=BathroomBand.QUIET,
            lights=(SensorState.ON, SensorState.OFF),
        ),
        POLICY,
    )
    assert on.action is FanAction.TURN_ON

    second = evaluate_bathroom_exhaust(
        bathroom(
            band=BathroomBand.QUIET,
            lights=(SensorState.OFF, SensorState.ON),
            fan=SensorState.OFF,
        ),
        POLICY,
    )
    assert second.action is None
    assert second.transition is AuxiliaryTransition.HOLD


def test_quiet_band_second_light_switches_fan_off() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(
            band=BathroomBand.QUIET,
            humidity=45.0,
            lights=(SensorState.OFF, SensorState.ON),
            fan=SensorState.ON,
            fan_owned=True,
        ),
        POLICY,
    )
    assert decision.transition is AuxiliaryTransition.IDLE
    assert decision.action is FanAction.TURN_OFF


def test_night_absence_switches_owned_fan_off() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(
            band=BathroomBand.NIGHT,
            humidity=45.0,
            lights=(SensorState.OFF, SensorState.OFF),
            fan=SensorState.ON,
            fan_owned=True,
        ),
        POLICY,
    )
    assert decision.action is FanAction.TURN_OFF
    assert decision.transition is AuxiliaryTransition.IDLE


def test_night_absence_without_ownership_keeps_fan() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(
            band=BathroomBand.NIGHT,
            humidity=45.0,
            lights=(SensorState.OFF, SensorState.OFF),
            fan=SensorState.ON,
            fan_owned=False,
        ),
        POLICY,
    )
    assert decision.action is None
    assert decision.transition is AuxiliaryTransition.HOLD


def test_day_dry_off_arms_timer_once() -> None:
    first = evaluate_bathroom_exhaust(
        bathroom(
            humidity=45.0,
            lights=(SensorState.OFF, SensorState.OFF),
            fan=SensorState.ON,
            fan_owned=True,
        ),
        POLICY,
    )
    assert first.transition is AuxiliaryTransition.DAY_OFF_PENDING
    assert first.arm_timer_seconds == 1800
    assert first.action is None

    again = evaluate_bathroom_exhaust(
        bathroom(
            humidity=45.0,
            lights=(SensorState.OFF, SensorState.OFF),
            fan=SensorState.ON,
            fan_owned=True,
            pending_timer=BATHROOM_DAY_OFF_TIMER,
        ),
        POLICY,
    )
    assert again.transition is AuxiliaryTransition.DAY_OFF_PENDING
    assert again.arm_timer_seconds is None


def test_day_dry_off_requires_ownership() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(
            humidity=45.0,
            lights=(SensorState.OFF, SensorState.OFF),
            fan=SensorState.ON,
            fan_owned=False,
        ),
        POLICY,
    )
    assert decision.arm_timer_seconds is None
    assert decision.transition is AuxiliaryTransition.HOLD


def test_unknown_light_blocks_automatic_switch_off() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(
            band=BathroomBand.QUIET,
            humidity=45.0,
            lights=(SensorState.UNKNOWN, SensorState.ON),
            fan=SensorState.ON,
            fan_owned=True,
        ),
        POLICY,
    )
    assert decision.transition is AuxiliaryTransition.UNKNOWN
    assert decision.action is None


def test_unknown_humidity_never_counts_as_dry() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(
            band=BathroomBand.NIGHT,
            humidity=None,
            lights=(SensorState.OFF, SensorState.OFF),
            fan=SensorState.ON,
            fan_owned=True,
        ),
        POLICY,
    )
    assert decision.action is None
    assert decision.transition is AuxiliaryTransition.UNKNOWN


def test_fan_already_on_is_not_commanded_twice() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(humidity=80.0, lights=(SensorState.ON, SensorState.OFF), fan=SensorState.ON),
        POLICY,
    )
    assert decision.action is None
    assert decision.transition is AuxiliaryTransition.HOLD


def test_recovery_disables_activation() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(
            humidity=80.0,
            lights=(SensorState.ON, SensorState.OFF),
            allow_activation=False,
        ),
        POLICY,
    )
    assert decision.action is None


def test_failed_dispatch_blocks_retry() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(dispatch_blocked=True), POLICY
    )
    assert decision.blocked is True
    assert decision.action is None
    assert decision.transition is AuxiliaryTransition.HOLD


def test_due_timer_switches_owned_fan_off() -> None:
    decision = evaluate_bathroom_exhaust_due(
        bathroom(
            humidity=45.0,
            lights=(SensorState.OFF, SensorState.OFF),
            fan=SensorState.ON,
            fan_owned=True,
        ),
        timer_kind=BATHROOM_DAY_OFF_TIMER,
        policy=POLICY,
    )
    assert decision.action is FanAction.TURN_OFF
    assert decision.transition is AuxiliaryTransition.IDLE


def test_due_unrelated_timer_never_switches_off() -> None:
    decision = evaluate_bathroom_exhaust_due(
        bathroom(
            humidity=45.0,
            lights=(SensorState.OFF, SensorState.OFF),
            fan=SensorState.ON,
            fan_owned=True,
        ),
        timer_kind="shower_absence",
        policy=POLICY,
    )
    assert decision.action is None
    assert decision.transition is AuxiliaryTransition.IDLE


def test_decision_payload_is_stable() -> None:
    decision = evaluate_bathroom_exhaust(
        bathroom(humidity=80.0, lights=(SensorState.ON, SensorState.OFF)), POLICY
    )
    assert decision.to_payload() == {
        "transition": "bathroom_hold",
        "action": "turn_on",
        "armTimerSeconds": None,
        "clearTimer": True,
        "blocked": False,
    }


@pytest.mark.asyncio
async def test_engine_matches_legacy_coordinator_bathroom_decisions() -> None:
    from test_scenario_control_rooms import (
        BATHROOM_FAN_TARGET_ID,
        BATHROOM_HUMIDITY_TARGET_ID,
        BATHROOM_LIGHT_TARGET_IDS,
        BATHROOM_SCENARIO_ID,
        make_room_coordinator,
    )

    zone = ZoneInfo("Asia/Omsk")

    def snapshot(coordinator: object) -> BathroomObservation:
        payload = coordinator.payload["bathroom"]  # type: ignore[attr-defined]
        states = coordinator._target_state  # type: ignore[attr-defined]
        lights = tuple(
            _sensor(states(target)) for target in BATHROOM_LIGHT_TARGET_IDS
        )
        humidity_raw = states(BATHROOM_HUMIDITY_TARGET_ID)
        humidity = _humidity(humidity_raw)
        return BathroomObservation(
            band=BathroomBand(_band(coordinator)),
            lights=lights,
            humidity=humidity,
            fan=_sensor(states(BATHROOM_FAN_TARGET_ID)),
            fan_owned=coordinator._is_room_owned(  # type: ignore[attr-defined]
                BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID
            ),
            pending_timer=payload.get("timerKind"),
        )

    def _band(coordinator: object) -> str:
        return coordinator._bathroom_band()  # type: ignore[attr-defined]

    def _sensor(value: object) -> SensorState:
        text = str(value)
        if text in {"on", "off"}:
            return SensorState(text)
        return SensorState.UNKNOWN

    def _humidity(value: object) -> float | None:
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None

    now = [datetime(2026, 9, 7, 12, tzinfo=zone)]
    clock = [0]
    coordinator, service, states, _priority, _store = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={
            BATHROOM_LIGHT_TARGET_IDS[0]: "on",
            BATHROOM_HUMIDITY_TARGET_ID: "65",
        },
    )

    # Day, humid, one light on: both writers turn the fan on and hold.
    decision = evaluate_bathroom_exhaust(snapshot(coordinator), POLICY)
    await coordinator.async_handle_bathroom_change()
    assert _legacy_action(service, BATHROOM_FAN_TARGET_ID) == (
        "turn_on",
        BATHROOM_FAN_TARGET_ID,
    )
    assert decision.action is FanAction.TURN_ON
    assert decision.transition is AuxiliaryTransition.HOLD

    # Day, dry, both lights off, fan owned: both arm the day-off timer.
    states.set(f"test.{BATHROOM_LIGHT_TARGET_IDS[0]}", "off")
    states.set(f"test.{BATHROOM_HUMIDITY_TARGET_ID}", "45")
    decision = evaluate_bathroom_exhaust(snapshot(coordinator), POLICY)
    actions_before = len(service.actions)
    await coordinator.async_handle_bathroom_change()
    assert len(service.actions) == actions_before
    assert coordinator.payload["bathroom"]["transition"] == "bathroom_day_off_pending"
    assert decision.arm_timer_seconds == 1800
    assert decision.transition is AuxiliaryTransition.DAY_OFF_PENDING

    # Timer due: both switch the owned fan off.
    clock[0] = 1_800_000
    decision = evaluate_bathroom_exhaust_due(
        snapshot(coordinator),
        timer_kind=BATHROOM_DAY_OFF_TIMER,
        policy=POLICY,
    )
    await coordinator.async_reconcile_zone_due(BATHROOM_SCENARIO_ID)
    assert _legacy_action(service, BATHROOM_FAN_TARGET_ID) == (
        "turn_off",
        BATHROOM_FAN_TARGET_ID,
    )
    assert decision.action is FanAction.TURN_OFF
    assert decision.transition is AuxiliaryTransition.IDLE


@pytest.mark.asyncio
async def test_engine_matches_legacy_night_quiet_and_unknown_paths() -> None:
    from test_scenario_control_rooms import (
        BATHROOM_FAN_TARGET_ID,
        BATHROOM_HUMIDITY_TARGET_ID,
        BATHROOM_LIGHT_TARGET_IDS,
        make_room_coordinator,
    )

    zone = ZoneInfo("Asia/Omsk")
    light1_id = BATHROOM_LIGHT_TARGET_IDS[0]
    light2_id = BATHROOM_LIGHT_TARGET_IDS[1]

    def _sensor(value: object) -> SensorState:
        raw = str(value)
        return SensorState(raw) if raw in {"on", "off"} else SensorState.UNKNOWN

    def _humidity(value: object) -> float | None:
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None

    def snapshot(coordinator: object) -> BathroomObservation:
        state = coordinator._target_state  # type: ignore[attr-defined]
        return BathroomObservation(
            band=BathroomBand(coordinator._bathroom_band()),  # type: ignore[attr-defined]
            lights=(_sensor(state(light1_id)), _sensor(state(light2_id))),
            humidity=_humidity(state(BATHROOM_HUMIDITY_TARGET_ID)),
            fan=_sensor(state(BATHROOM_FAN_TARGET_ID)),
            fan_owned=coordinator._is_room_owned(  # type: ignore[attr-defined]
                "system-bathroom-exhaust-controller", BATHROOM_FAN_TARGET_ID
            ),
            pending_timer=coordinator.payload["bathroom"].get("timerKind"),  # type: ignore[attr-defined]
        )

    # Night: any light switches the owned fan on, full absence switches it off,
    # and unknown humidity never proves dry air.
    now = [datetime(2026, 9, 7, 23, tzinfo=zone)]
    coordinator, service, states, _priority, _store = await make_room_coordinator(
        now=now,
        clock=[0],
        overrides={light1_id: "on", BATHROOM_HUMIDITY_TARGET_ID: "46"},
    )
    decision = evaluate_bathroom_exhaust(snapshot(coordinator), POLICY)
    await coordinator.async_handle_bathroom_change()
    assert _legacy_action(service, BATHROOM_FAN_TARGET_ID) == (
        "turn_on",
        BATHROOM_FAN_TARGET_ID,
    )
    assert decision.action is FanAction.TURN_ON

    states.set(f"test.{light1_id}", "off")
    decision = evaluate_bathroom_exhaust(snapshot(coordinator), POLICY)
    await coordinator.async_handle_bathroom_change()
    assert _legacy_action(service, BATHROOM_FAN_TARGET_ID) == (
        "turn_off",
        BATHROOM_FAN_TARGET_ID,
    )
    assert decision.action is FanAction.TURN_OFF
    assert decision.transition is AuxiliaryTransition.IDLE

    states.set(f"test.{BATHROOM_HUMIDITY_TARGET_ID}", "unavailable")
    states.set(f"test.{BATHROOM_FAN_TARGET_ID}", "on")
    decision = evaluate_bathroom_exhaust(snapshot(coordinator), POLICY)
    actions_before = len(service.actions)
    await coordinator.async_handle_bathroom_change()
    assert len(service.actions) == actions_before
    assert decision.action is None
    assert decision.transition is AuxiliaryTransition.UNKNOWN

    # Quiet: the first light switches the fan on, the second light and an
    # unknown first light both block the automatic switch-off.
    now = [datetime(2026, 9, 7, 7, tzinfo=zone)]
    coordinator, service, states, _priority, _store = await make_room_coordinator(
        now=now,
        clock=[0],
        overrides={light1_id: "on", BATHROOM_HUMIDITY_TARGET_ID: "45"},
    )
    decision = evaluate_bathroom_exhaust(snapshot(coordinator), POLICY)
    await coordinator.async_handle_bathroom_change()
    assert _legacy_action(service, BATHROOM_FAN_TARGET_ID) == (
        "turn_on",
        BATHROOM_FAN_TARGET_ID,
    )
    assert decision.action is FanAction.TURN_ON

    states.set(f"test.{light1_id}", "off")
    states.set(f"test.{light2_id}", "on")
    decision = evaluate_bathroom_exhaust(snapshot(coordinator), POLICY)
    await coordinator.async_handle_bathroom_change()
    assert _legacy_action(service, BATHROOM_FAN_TARGET_ID) == (
        "turn_off",
        BATHROOM_FAN_TARGET_ID,
    )
    assert decision.action is FanAction.TURN_OFF
    assert decision.transition is AuxiliaryTransition.IDLE

    states.set(f"test.{BATHROOM_FAN_TARGET_ID}", "on")
    states.set(f"test.{light1_id}", "unknown")
    decision = evaluate_bathroom_exhaust(snapshot(coordinator), POLICY)
    actions_before = len(service.actions)
    await coordinator.async_handle_bathroom_change()
    assert len(service.actions) == actions_before
    assert decision.action is None
    assert decision.transition is AuxiliaryTransition.UNKNOWN


def _legacy_action(
    service: object, fan_target_id: str
) -> tuple[str, str] | None:
    actions = [
        (str(entry[2]), str(entry[1]))
        for entry in service.actions  # type: ignore[attr-defined]
        if entry[1] == fan_target_id
    ]
    return actions[-1] if actions else None
