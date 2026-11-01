"""Automatic corridor lighting requires both motion and outdoor darkness."""

from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path

import pytest

from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightSnapshot, SensorSnapshot, SensorKind, LightAction, DecisionReason, evaluate_curve_room,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    SensorState, OwnershipSnapshot, OwnershipSource,
)
from tests.test_room_lighting_curve_params_runtime import _payload, _context, FULL_CURVE
from tests.test_room_lighting_runtime import _FakeHass, _SpyExecutor, _make_runtime, _NOW_DT, _NOW_MS

CORRIDOR_ENTITY = "binary_sensor.0xf044d3fffebaf11b_occupancy"
ENTRY_ENTITY = "binary_sensor.0xf044d3fffe11964d_occupancy"
OUTDOOR_ENTITY = "sensor.0x00124b003571fd85_illuminance"


def corridor_payload():
    payload = _payload()
    payload["roomId"] = "malyi_koridor"
    payload["profile"] = "day_curve"
    payload["devices"]["power_switch"] = None
    payload["devices"]["light_targets"] = payload["devices"]["light_targets"][:1]
    payload["devices"]["sensors"] = [
        {"id": "corridor_motion", "name": "Коридор", "kind": "motion", "entityId": CORRIDOR_ENTITY},
        {"id": "entry_motion", "name": "Тамбур", "kind": "motion", "entityId": ENTRY_ENTITY},
        {"id": "outdoor", "name": "Улица", "kind": "illuminance", "entityId": OUTDOOR_ENTITY},
    ]
    payload["illumination"] = {"sensor": OUTDOOR_ENTITY, "minLux": 0,
                               "maxLux": 100000, "thresholds": [], "failClosed": True}
    payload["behaviors"] = {"onlyWhenDark": True}
    return payload


def corridor_config():
    return config_from_payload(corridor_payload())


def test_bright_outdoor_light_turns_off_automatically_owned_corridor():
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=501),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now - 2000, brightness=30),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert any(command.action is LightAction.TURN_OFF and command.reason is DecisionReason.LUX
               for command in decision.commands)


@pytest.mark.parametrize("lux,age,ownership", [
    (400, 0, OwnershipSource.AUTO),
    (501, 301_000, OwnershipSource.AUTO),
    (None, 0, OwnershipSource.AUTO),
    (501, 0, OwnershipSource.MANUAL),
    (501, 0, None),
])
def test_bright_off_requires_fresh_reading_over_threshold_and_auto_ownership(lux, age, ownership):
    base = _context(12)
    records = () if ownership is None else (
        OwnershipSnapshot("light_demo_main", ownership, True, base.now - 1000),
    )
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now - age, lux=lux),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
       ownership=records)
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_OFF for command in decision.commands)


async def test_bright_off_rechecked_before_dispatch_after_manual_action_or_lux_drop():
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=501),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now - 2000, brightness=30),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    config = corridor_config()
    command = next(c for c in evaluate_curve_room(config, context, presence=SensorState.ON,
        presence_confirmed=True, absence_seconds=0).commands if c.action is LightAction.TURN_OFF)
    runtime = _make_runtime(_FakeHass(), payload=corridor_payload())
    manual = replace(context, ownership=(
        OwnershipSnapshot("light_demo_main", OwnershipSource.MANUAL, True, base.now),))
    dark = replace(context, sensors=(context.sensors[0], replace(context.sensors[1], lux=399)))
    try:
        assert runtime._post_pending_curve_profile_block_reason(config, command, manual) is not None
        assert runtime._post_pending_curve_profile_block_reason(config, command, dark) is not None
    finally:
        await runtime.stop()


def test_bright_off_does_not_assume_auto_ownership_across_observation_gap():
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=501),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 2000),),
       unobserved_since=base.now - 1000)
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_OFF for command in decision.commands)


def test_bright_off_preserves_later_unattributed_on_state():
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=501),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_OFF for command in decision.commands)


def test_bright_off_preserves_profile_when_another_source_was_manually_lit():
    payload = corridor_payload()
    main = payload["devices"]["light_targets"][0]
    payload["devices"]["light_targets"].append({**main, "id": "other", "entityId": "light.other"})
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=501),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),
               LightSnapshot("other", SensorState.ON, base.now, brightness=30)),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),
                  OwnershipSnapshot("other", OwnershipSource.MANUAL, True, base.now - 1000)))
    decision = evaluate_curve_room(config_from_payload(payload), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_OFF for command in decision.commands)


@pytest.mark.parametrize("state,lux", [(SensorState.UNKNOWN, 501),
    (SensorState.UNAVAILABLE, 501), (SensorState.ON, float("nan")),
    (SensorState.ON, float("inf"))])
def test_bright_off_requires_a_valid_illuminance_state(state, lux):
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, state, base.now, lux=lux),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_OFF for command in decision.commands)


def test_bright_corridor_policy_does_not_change_another_room():
    payload = corridor_payload()
    payload["roomId"] = "another_room"
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=501),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    decision = evaluate_curve_room(config_from_payload(payload), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_OFF for command in decision.commands)


@pytest.mark.parametrize("lux", [400.0, 500.0, None])
def test_motion_does_not_turn_on_when_outdoor_darkness_is_not_proven(lux):
    config = corridor_config()
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("entry_motion", SensorKind.MOTION, SensorState.OFF, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=lux),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(config, context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_ON for command in decision.commands)


@pytest.mark.parametrize("motion_id", ["corridor_motion", "entry_motion"])
def test_either_motion_turns_on_below_400_lux(motion_id):
    config = corridor_config()
    base = _context(12)
    sensors = tuple(
        SensorSnapshot(name, SensorKind.MOTION,
                       SensorState.ON if name == motion_id else SensorState.OFF, base.now)
        for name in ("corridor_motion", "entry_motion")
    ) + (SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=399),)
    context = replace(base, sensors=sensors,
                      lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(config, context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert any(command.action is LightAction.TURN_ON for command in decision.commands)


def test_darkness_alone_does_not_turn_on_without_motion():
    config = corridor_config()
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.OFF, base.now),
        SensorSnapshot("entry_motion", SensorKind.MOTION, SensorState.OFF, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=10),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(config, context, presence=SensorState.OFF,
                                   presence_confirmed=False, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_ON for command in decision.commands)


def test_stale_outdoor_reading_does_not_permit_auto_on():
    config = corridor_config()
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("entry_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON,
                       base.now - 301_000, lux=10),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(config, context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_ON for command in decision.commands)


@pytest.mark.parametrize("motion_id", [CORRIDOR_ENTITY, ENTRY_ENTITY])
async def test_runtime_uses_each_motion_sensor_without_a_presence_sensor(motion_id):
    payload = corridor_payload()
    hass = _FakeHass()
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set(CORRIDOR_ENTITY, "on" if motion_id == CORRIDOR_ENTITY else "off", last_changed=_NOW_DT)
    hass.states.set(ENTRY_ENTITY, "on" if motion_id == ENTRY_ENTITY else "off", last_changed=_NOW_DT)
    hass.states.set(OUTDOOR_ENTITY, "100", last_changed=_NOW_DT)
    runtime = _make_runtime(hass, payload=payload, now_ms=lambda: _NOW_MS)
    await runtime.start(hass, "entry")
    try:
        await runtime.async_process(("malyi_koridor",))
        entries = runtime._shadow.journal_payload()["entries"]
        assert any(command["action"] == "turn_on" for entry in entries for command in entry["commands"])
    finally:
        await runtime.stop()


@pytest.mark.parametrize("lux,motion", [(400, SensorState.ON), (100, SensorState.OFF)])
def test_pending_auto_on_is_cancelled_if_lux_or_motion_changes(lux, motion):
    config = corridor_config()
    base = _context(12)
    original = replace(base, sensors=(
        SensorSnapshot("entry_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=100),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    command = next(command for command in evaluate_curve_room(
        config, original, presence=SensorState.ON, presence_confirmed=True,
        absence_seconds=0).commands if command.action is LightAction.TURN_ON)
    changed = replace(original, sensors=(
        SensorSnapshot("entry_motion", SensorKind.MOTION, motion, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=lux),
    ))
    runtime = _make_runtime(_FakeHass(), payload=corridor_payload())
    assert runtime._post_pending_curve_profile_block_reason(config, command, changed) == "superseded"


async def test_motion_and_darkness_reach_the_executor():
    payload = corridor_payload()
    payload["commandsEnabled"] = True
    hass = _FakeHass()
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set(CORRIDOR_ENTITY, "on", last_changed=_NOW_DT)
    hass.states.set(ENTRY_ENTITY, "off", last_changed=_NOW_DT)
    hass.states.set(OUTDOOR_ENTITY, "100", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, payload=payload, executor=executor, now_ms=lambda: _NOW_MS)
    await runtime.start(hass, "entry")
    try:
        await runtime.async_process(("malyi_koridor",))
        assert any(command.action is LightAction.TURN_ON for command in executor.calls), repr(runtime._shadow.journal_payload())
    finally:
        await runtime.stop()


async def test_bright_outdoor_light_reaches_executor_for_auto_owned_corridor():
    payload = corridor_payload()
    payload["commandsEnabled"] = True
    hass = _FakeHass()
    hass.states.set("light.demo_main", "on", {"brightness": 100}, last_changed=_NOW_DT)
    hass.states.set(CORRIDOR_ENTITY, "on", last_changed=_NOW_DT)
    hass.states.set(ENTRY_ENTITY, "off", last_changed=_NOW_DT)
    hass.states.set(OUTDOOR_ENTITY, "501", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, payload=payload, executor=executor, now_ms=lambda: _NOW_MS)
    await runtime.start(hass, "entry")
    try:
        runtime._ownership.record_auto("malyi_koridor", "light_demo_main", _NOW_MS, confirmed=True)
        await runtime.async_process(("malyi_koridor",))
        assert any(command.action is LightAction.TURN_OFF and command.reason is DecisionReason.LUX
                   for command in executor.calls), repr(runtime._shadow.journal_payload())
    finally:
        await runtime.stop()


def test_motion_does_not_change_a_manually_owned_light():
    config = corridor_config()
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=20),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.MANUAL, True, base.now),))
    decision = evaluate_curve_room(config, context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert decision.commands == ()


def test_missing_darkness_setting_cannot_enable_unrestricted_corridor():
    payload = corridor_payload()
    payload["behaviors"]["onlyWhenDark"] = False
    config = config_from_payload(payload)
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=500),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(config, context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_ON for command in decision.commands)


def test_an_unconfigured_motion_cannot_trigger_the_corridor():
    config = corridor_config()
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("other_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.OFF, base.now),
        SensorSnapshot("entry_motion", SensorKind.MOTION, SensorState.OFF, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=50),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(config, context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_ON for command in decision.commands)


def test_wrong_pair_of_configured_motion_sensors_cannot_enable_corridor():
    payload = corridor_payload()
    payload["devices"]["sensors"][0]["entityId"] = "binary_sensor.other_a"
    payload["devices"]["sensors"][1]["entityId"] = "binary_sensor.other_b"
    config = config_from_payload(payload)
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=50),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(config, context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_ON for command in decision.commands)


def test_other_illuminance_sensor_cannot_enable_corridor():
    payload = corridor_payload()
    payload["devices"]["sensors"][2]["entityId"] = "sensor.other_illuminance"
    payload["illumination"]["sensor"] = "sensor.other_illuminance"
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=50),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(config_from_payload(payload), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_ON for command in decision.commands)


@pytest.mark.parametrize("hour", [0, 8.999, 23.5, 23.999])
def test_night_interval_never_turns_on_corridor(hour):
    base = _context(hour)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=50),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_ON for command in decision.commands)


def test_at_nine_motion_may_turn_corridor_on():
    base = _context(9)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=50),
    ), lights=(LightSnapshot("light_demo_main", SensorState.OFF, base.now),))
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert any(command.action is LightAction.TURN_ON for command in decision.commands)


def test_night_switches_off_automatically_owned_light():
    base = _context(23.5)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=50),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert any(command.action is LightAction.TURN_OFF for command in decision.commands)


def test_night_overrides_curve_that_waits_for_absence():
    payload = corridor_payload()
    payload["curveParams"] = deepcopy(FULL_CURVE)
    base = _context(23.5)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=50),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    decision = evaluate_curve_room(config_from_payload(payload), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert any(command.action is LightAction.TURN_OFF for command in decision.commands)


@pytest.mark.parametrize("ownership", [(), (OwnershipSnapshot("light_demo_main", OwnershipSource.MANUAL, True, 1),)])
def test_night_preserves_manually_or_unattributed_light(ownership):
    base = _context(23.5)
    context = replace(base, lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
                      ownership=ownership)
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(command.action is LightAction.TURN_OFF for command in decision.commands)


def test_scheduled_night_off_survives_runtime_recheck():
    base = _context(23.5)
    context = replace(base, lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
                      ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    config = corridor_config()
    decision = evaluate_curve_room(config, context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    command = next(c for c in decision.commands if c.action is LightAction.TURN_OFF)
    runtime = _make_runtime(_FakeHass(), payload=corridor_payload())
    assert runtime._post_pending_curve_profile_block_reason(config, command, context) is None


def test_no_brightness_increase_without_motion():
    base = _context(12)
    context = replace(base, sensors=(
        SensorSnapshot("corridor_motion", SensorKind.MOTION, SensorState.OFF, base.now),
        SensorSnapshot("entry_motion", SensorKind.MOTION, SensorState.OFF, base.now),
        SensorSnapshot("outdoor", SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=50),
    ), lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=10),),
       ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    decision = evaluate_curve_room(corridor_config(), context, presence=SensorState.ON,
                                   presence_confirmed=True, absence_seconds=0)
    assert not any(c.action is LightAction.SET_BRIGHTNESS and c.brightness > 10 for c in decision.commands)


def test_night_off_does_not_require_sun_data_in_runtime_recheck():
    base = _context(23.5, sun_healthy=False)
    context = replace(base, lights=(LightSnapshot("light_demo_main", SensorState.ON, base.now, brightness=30),),
                      ownership=(OwnershipSnapshot("light_demo_main", OwnershipSource.AUTO, True, base.now - 1000),))
    config = corridor_config()
    command = next(c for c in evaluate_curve_room(config, context, presence=SensorState.ON,
                   presence_confirmed=True, absence_seconds=0).commands if c.action is LightAction.TURN_OFF)
    runtime = _make_runtime(_FakeHass(), payload=corridor_payload())
    assert runtime._post_pending_curve_profile_block_reason(config, command, context) is None


@pytest.mark.parametrize("hour", [9, 10, 12, 17, 20, 21, 22, 23])
def test_archived_tambur_curve_matches_prepared_corridor(hour):
    archive = Path(__file__).resolve().parents[2] / "live-realtime-20261026T145719Z-r2"
    tambur = json.loads(next(archive.glob("config-tambur-*.json")).read_text())
    corridor = json.loads(next(archive.glob("config-malyi_koridor-*.json")).read_text())
    motion = next(item for item in corridor["devices"]["sensors"] if item["kind"] == "motion")
    vestibule_motion = next(item for item in tambur["devices"]["sensors"] if item["id"] == "motion_tambur")
    street = next(item for item in tambur["devices"]["sensors"] if item["id"] == "lux_street")
    corridor["devices"]["sensors"] = [motion, vestibule_motion, street]
    for field in ("profile", "curveParams", "morningBoundary", "dimming", "timers", "illumination"):
        corridor[field] = deepcopy(tambur[field])
    corridor["behaviors"] = {**tambur["behaviors"], "onlyWhenDark": True}
    tconfig, cconfig = config_from_payload(tambur), config_from_payload(corridor)
    base = _context(hour)
    sensors = (
        SensorSnapshot(motion["id"], SensorKind.MOTION, SensorState.ON, base.now),
        SensorSnapshot(vestibule_motion["id"], SensorKind.MOTION, SensorState.OFF, base.now),
        SensorSnapshot(street["id"], SensorKind.ILLUMINANCE, SensorState.ON, base.now, lux=100),
    )
    def planned(config, target):
        context = replace(base, sensors=sensors,
                          lights=(LightSnapshot(target, SensorState.OFF, base.now),))
        result = evaluate_curve_room(config, context, presence=SensorState.ON,
                                     presence_confirmed=True, absence_seconds=0)
        return [(c.action, c.brightness, c.color_temperature, c.fade_seconds)
                for c in result.commands if c.target_id == target]
    assert planned(tconfig, "chandelier") == planned(cconfig, "corridor_main")
