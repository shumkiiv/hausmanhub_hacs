"""Exhaustive shadow parity: the pure bathroom engine vs the legacy controller.

The migration rule is "reproduce the legacy controller one to one, prove it,
then switch the single writer". This harness enumerates the whole decision
space (band x two lights x humidity x fan x ownership) and asserts that the
pure engine and the legacy ``ScenarioControlCoordinator`` agree on every case:
the same physical action, and for action-free cases the same transition.

The legacy journal reports ``light_action`` while a command is being executed,
so transition equality is only asserted when neither side commands; the engine
still reports its intended transition, which is asserted in that case.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.hausman_hub.domain.room_lighting_auxiliary import (
    AuxiliaryTransition,
    BathroomBand,
    BathroomObservation,
    BathroomPolicy,
    FanAction,
    evaluate_bathroom_exhaust,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState

ZONE = ZoneInfo("Asia/Omsk")
POLICY = BathroomPolicy(humidity_threshold=65, day_off_seconds=1800)

BAND_HOURS = {
    BathroomBand.QUIET: 7,
    BathroomBand.DAY: 12,
    BathroomBand.NIGHT: 23,
}
RAW_STATE = {
    SensorState.ON: "on",
    SensorState.OFF: "off",
    SensorState.UNKNOWN: "unknown",
}
LEGACY_TRANSITION = {
    "bathroom_hold": AuxiliaryTransition.HOLD,
    "controller_idle": AuxiliaryTransition.IDLE,
    "controller_unknown": AuxiliaryTransition.UNKNOWN,
    "bathroom_day_off_pending": AuxiliaryTransition.DAY_OFF_PENDING,
}
LEGACY_ACTION = {
    "turn_on": FanAction.TURN_ON,
    "turn_off": FanAction.TURN_OFF,
    None: None,
}

SENSOR_STATES = (SensorState.ON, SensorState.OFF, SensorState.UNKNOWN)
FAN_STATES = (SensorState.ON, SensorState.OFF, SensorState.UNKNOWN)
HUMIDITIES = (None, 40.0, 68.0)


def _humidity_raw(humidity: float | None) -> str:
    return "unavailable" if humidity is None else str(humidity)


def _case_label(
    band: BathroomBand,
    light1: SensorState,
    light2: SensorState,
    humidity: float | None,
    fan: SensorState,
    owned: bool,
) -> str:
    return (
        f"band={band.value} lights=({light1.value},{light2.value}) "
        f"humidity={humidity} fan={fan.value} owned={owned}"
    )


async def _run_legacy_case(
    *,
    band: BathroomBand,
    light1: SensorState,
    light2: SensorState,
    humidity: float | None,
    fan: SensorState,
    owned: bool,
    allow_activation: bool,
) -> tuple[str | None, str]:
    """Return (legacy fan action, legacy journal transition) for one case."""

    from test_scenario_control_rooms import (
        BATHROOM_FAN_TARGET_ID,
        BATHROOM_HUMIDITY_TARGET_ID,
        BATHROOM_LIGHT_TARGET_IDS,
        make_room_coordinator,
    )

    light1_id, light2_id = BATHROOM_LIGHT_TARGET_IDS
    hour = BAND_HOURS[band]
    clock = [0]

    if owned and fan is SensorState.ON:
        # Ownership only exists after a confirmed automatic switch-on: create
        # it through the real legacy path, then move to the case frame.
        now = [datetime(2026, 9, 7, hour, tzinfo=ZONE)]
        coordinator, service, states, _priority, _store = await make_room_coordinator(
            now=now,
            clock=clock,
            overrides={
                light1_id: "on",
                light2_id: "off",
                BATHROOM_HUMIDITY_TARGET_ID: "68",
            },
        )
        await coordinator.async_handle_bathroom_change()
        assert service.actions and service.actions[-1][2] == "turn_on"
        states.set(f"test.{light1_id}", RAW_STATE[light1])
        states.set(f"test.{light2_id}", RAW_STATE[light2])
        states.set(f"test.{BATHROOM_HUMIDITY_TARGET_ID}", _humidity_raw(humidity))
    else:
        now = [datetime(2026, 9, 7, hour, tzinfo=ZONE)]
        coordinator, service, states, _priority, _store = await make_room_coordinator(
            now=now,
            clock=clock,
            overrides={
                light1_id: RAW_STATE[light1],
                light2_id: RAW_STATE[light2],
                BATHROOM_HUMIDITY_TARGET_ID: _humidity_raw(humidity),
                BATHROOM_FAN_TARGET_ID: RAW_STATE[fan],
            },
        )

    before = len(service.actions)
    await coordinator.async_handle_bathroom_change(allow_activation=allow_activation)
    new_actions = service.actions[before:]
    action = str(new_actions[-1][2]) if new_actions else None
    transition = str(coordinator.payload["bathroom"]["transition"])
    return action, transition


def _engine_case(
    *,
    band: BathroomBand,
    light1: SensorState,
    light2: SensorState,
    humidity: float | None,
    fan: SensorState,
    owned: bool,
    allow_activation: bool,
):
    observation = BathroomObservation(
        band=band,
        lights=(light1, light2),
        humidity=humidity,
        fan=fan,
        fan_owned=owned,
        allow_activation=allow_activation,
    )
    return evaluate_bathroom_exhaust(observation, POLICY)


@pytest.mark.asyncio
async def test_exhaustive_bathroom_shadow_parity_with_legacy() -> None:
    checked = 0
    for band in (BathroomBand.QUIET, BathroomBand.DAY, BathroomBand.NIGHT):
        for light1 in SENSOR_STATES:
            for light2 in SENSOR_STATES:
                for humidity in HUMIDITIES:
                    for fan in FAN_STATES:
                        ownership_choices = (False, True) if fan is SensorState.ON else (False,)
                        for owned in ownership_choices:
                            case = _case_label(
                                band, light1, light2, humidity, fan, owned
                            )
                            legacy_action, legacy_transition = await _run_legacy_case(
                                band=band,
                                light1=light1,
                                light2=light2,
                                humidity=humidity,
                                fan=fan,
                                owned=owned,
                                allow_activation=True,
                            )
                            decision = _engine_case(
                                band=band,
                                light1=light1,
                                light2=light2,
                                humidity=humidity,
                                fan=fan,
                                owned=owned,
                                allow_activation=True,
                            )
                            assert decision.action is LEGACY_ACTION[legacy_action], (
                                f"action mismatch: {case}; legacy={legacy_action}; "
                                f"engine={decision.action}"
                            )
                            if legacy_action is None:
                                assert (
                                    decision.transition is LEGACY_TRANSITION[legacy_transition]
                                ), (
                                    f"transition mismatch: {case}; "
                                    f"legacy={legacy_transition}; engine={decision.transition}"
                                )
                            elif legacy_action == "turn_on":
                                assert decision.transition is AuxiliaryTransition.HOLD, case
                            else:
                                assert decision.transition is AuxiliaryTransition.IDLE, case
                            checked += 1
    # 3 bands x 3 x 3 lights x 3 humidities x (2 owned-on + off + unknown fans)
    assert checked == 3 * 3 * 3 * 3 * 4


@pytest.mark.asyncio
async def test_parity_holds_with_activation_disabled() -> None:
    """Recovery runs must not turn the fan on in either implementation."""

    for light1 in (SensorState.ON, SensorState.OFF):
        for humidity in (None, 40.0, 68.0):
            legacy_action, legacy_transition = await _run_legacy_case(
                band=BathroomBand.DAY,
                light1=light1,
                light2=SensorState.OFF,
                humidity=humidity,
                fan=SensorState.OFF,
                owned=False,
                allow_activation=False,
            )
            decision = _engine_case(
                band=BathroomBand.DAY,
                light1=light1,
                light2=SensorState.OFF,
                humidity=humidity,
                fan=SensorState.OFF,
                owned=False,
                allow_activation=False,
            )
            assert legacy_action is None
            assert decision.action is None
            assert decision.transition is LEGACY_TRANSITION[legacy_transition]
