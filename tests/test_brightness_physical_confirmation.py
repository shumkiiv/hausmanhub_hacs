"""Real level evidence, independent from ON/OFF and manual ownership."""

from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path
import json
import subprocess
from uuid import uuid4

import pytest

import test_light_command_readback as command_fixture
import test_lighting_mqtt_observations as input_fixture
from custom_components.hausman_hub.application.lighting_observation import (
    ObservedLightState, PhysicalLightObservation, PhysicalBrightnessObservation,
    physical_brightness_observed_ms,
)
from custom_components.hausman_hub.application.power_evidence import trusted_target_state
from custom_components.hausman_hub.application.room_lighting_runtime import RoomLightingRuntime
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction, PlannedCommand


ENTITY = "light.liustra_tambur"


def level_report(clock, **changes):
    return input_fixture.report(clock, channel=3, source="physical_brightness",
        attribute="genLevelCtrl.currentLevel", value=64, **changes)


def test_physical_level_is_independent_and_never_renews_onoff():
    port, native, clock = input_fixture.make_case(attributes={"brightness": 64})
    body = level_report(clock)
    before = dict(vars(native[ENTITY]))
    input_fixture.feed(port, body)
    view = port.read_state(ENTITY)
    assert view.observation is None
    assert physical_brightness_observed_ms(view, now_ms=clock["now"]) == body["observed_at_ms"]
    assert trusted_target_state(view, now_ms=clock["now"]) is None
    assert vars(native[ENTITY]) == before


@pytest.mark.parametrize("bad", ["retained", "wrong_attribute", "switch", "boolean", "255", "negative", "stale", "future", "wrong_native", "gap", "bridge", "registry"])
def test_bad_level_evidence_fails_closed(bad):
    port, native, clock = input_fixture.make_case(attributes={"brightness": 64})
    body = level_report(clock)
    retained = False
    if bad == "retained": retained = True
    if bad == "wrong_attribute": body["attribute"] = "genOnOff.onOff"
    if bad == "switch": body.update(ieee=input_fixture.ROLLOUT_BINDINGS[0].ieee)
    if bad == "boolean": body["value"] = True
    if bad == "255": body["value"] = 255
    if bad == "negative": body["value"] = -1
    if bad == "stale": body["observed_at_ms"] -= 300_001
    if bad == "future": body["observed_at_ms"] += 1
    if bad == "wrong_native": native[ENTITY].attributes = {"brightness": 20}
    input_fixture.feed(port, body, retained=retained)
    if bad == "gap":
        clock["now"] += 1
        input_fixture.feed(port, level_report(clock, session=body["session"], sequence=3))
    if bad == "bridge": port.invalidate("bridge_boundary")
    if bad == "registry": port._enabled = frozenset()
    assert physical_brightness_observed_ms(port.read_state(ENTITY), now_ms=clock["now"]) is None


def test_interleaved_level_and_onoff_share_sequence_but_not_freshness():
    port, native, clock = input_fixture.make_case(attributes={"brightness": 64})
    first = input_fixture.report(clock, channel=3)
    input_fixture.feed(port, first)
    clock["now"] += 300_001
    level = level_report(clock, session=first["session"], sequence=2)
    input_fixture.feed(port, level)
    view = port.read_state(ENTITY)
    assert view.observation.observed_at_ms == first["observed_at_ms"]
    assert trusted_target_state(view, now_ms=clock["now"]) is None
    assert physical_brightness_observed_ms(view, now_ms=clock["now"]) == clock["now"]
    clock["now"] += 100
    input_fixture.feed(port, level)
    assert view.brightness_observation.observed_at_ms == level["observed_at_ms"]
    port.connection_changed(False)
    assert port.read_state(ENTITY).brightness_observation is None


@pytest.mark.parametrize("bad", [None, "absent", "stale", "future", "same", "wrong_value", "wrong_entity", "foreign", "user", "manual_hold", "cached", "off", "turn_on", "color"])
async def test_brightness_requires_new_physical_level_and_exact_causal_native_reply(monkeypatch, bad):
    clock, own, cache, calls, hass, executor, report = command_fixture.harness(monkeypatch)
    old_onoff = PhysicalLightObservation("light.main", "on", clock.now_ms() - 400_000, "session", 1, "ieee", 1)
    old_level = PhysicalBrightnessObservation("light.main", 64, clock.now_ms() - 1000, "session", 2, "ieee", 1)
    current = [old_level]
    cache["light.main"] = command_fixture.state("on", at=clock.now_ms()-1000, attributes={"brightness":64})

    def reader(entity):
        native = cache[entity]
        native.entity_id = entity
        return ObservedLightState(native, old_onoff, current[0])

    executor._state_reader = reader
    blocked = [False]
    executor._manual_light_authority = SimpleNamespace(blocks=lambda entity: blocked[0])

    def reply():
        context = own
        if bad == "foreign": context = SimpleNamespace(id="foreign", parent_id=None)
        if bad == "user": context = SimpleNamespace(id=own.id, parent_id=None, user_id="human")
        attrs = {"brightness":64}
        if bad == "cached": attrs["cached"] = True
        report(context=context, value="off" if bad == "off" else "on", attributes=attrs)
        stamp = clock.now_ms()
        if bad == "stale": stamp = command_fixture.BASE_MS - 1
        if bad == "future": stamp += 1000
        current[0] = PhysicalBrightnessObservation(
            "light.other" if bad == "wrong_entity" else "light.main",
            25 if bad == "wrong_value" else 64, stamp, "session", 3, "ieee", 1,
        )
        if bad == "absent": current[0] = None
        if bad == "same": current[0] = old_level
        if bad == "manual_hold": blocked[0] = True

    clock.on_sleep = reply
    action = LightAction.TURN_ON if bad == "turn_on" else LightAction.SET_COLOR_TEMPERATURE if bad == "color" else LightAction.SET_BRIGHTNESS
    result = await executor.execute(hass, PlannedCommand("main", action, brightness=25))
    assert result.confirmed is (bad is None)
    assert result.sent and len(calls) == 1
    assert old_onoff.observed_at_ms == command_fixture.BASE_MS - 400_000


@pytest.mark.parametrize("previous,action,new_level,overflow,expected", [
    ("set_brightness", LightAction.SET_BRIGHTNESS, True, False, False),
    ("turn_on", LightAction.SET_BRIGHTNESS, True, False, True),
    ("set_brightness", LightAction.TURN_ON, True, False, True),
    ("set_brightness", LightAction.SET_BRIGHTNESS, False, False, True),
    (None, LightAction.SET_BRIGHTNESS, True, True, True),
])
def test_level_releases_only_failed_brightness_not_failed_on_or_overflow(previous, action, new_level, overflow, expected):
    runtime = object.__new__(RoomLightingRuntime)
    anchor = command_fixture.BASE_MS
    native = SimpleNamespace(entity_id=ENTITY, state="on", attributes={"brightness":64})
    level = PhysicalBrightnessObservation(ENTITY,64,anchor+100 if new_level else anchor,"session",2,"ieee",1)
    view = ObservedLightState(native, None, level)
    key = ("room", "main")
    runtime._dispatch_failures = {} if previous is None else {key:(previous,"untrusted:on",anchor)}
    runtime._dispatch_overflow_latch = overflow
    runtime._dispatch_overflow_blocked_after_ms = anchor
    runtime._now_ms = lambda: anchor+100
    runtime._entity_state_object = lambda entity: view
    runtime._target_evidence_revision = lambda entity: "untrusted:on"
    runtime._proven_report_ms = lambda entity: None
    assert runtime._target_attempt_blocked(key, ENTITY, action) is expected
    assert bool(runtime._dispatch_failures) is (previous is not None and expected)


async def test_real_javascript_frame_to_consumer_to_command_confirmation(monkeypatch):
    """The actual producer schema must reach the actual executor unchanged."""
    clock, own, cache, calls, hass, executor, _ = command_fixture.harness(monkeypatch)
    cache[ENTITY] = command_fixture.state("on", at=clock.now_ms()-400_000, attributes={"brightness":84})
    cache[ENTITY].entity_id = ENTITY
    port = input_fixture.LightingMqttObservations(cache.get, now_ms=clock.now_ms)
    port._enabled = frozenset({ENTITY})
    port._started = True
    port.connection_changed(True)
    executor._state_reader = port.read_state
    executor._targets["main"] = replace(command_fixture.target(), entity_id=ENTITY)
    stamp = clock.now_ms()+250
    producer = Path(__file__).resolve().parents[1]/"tools/zigbee2mqtt/hausman_light_reports.mjs"
    script = "import {physicalBrightness} from " + json.dumps(producer.as_uri()) + ";" + "\n" + (
        "const r=physicalBrightness({type:'readResponse',cluster:'genLevelCtrl',"
        "device:{ieeeAddr:'0xa4c138784e5cbcd1'},endpoint:{ID:1},data:{currentLevel:64},"
        "meta:{zclTransactionSequenceNumber:11,rawData:Buffer.from([24,11,1,0,0,0,32,64])}},"
        +str(stamp)+"); console.log(JSON.stringify({...r,session:"
        +json.dumps(str(uuid4()))+",sequence:1}));"
    )
    raw = subprocess.run(["node","--input-type=module","-e",script],capture_output=True,text=True,check=True,timeout=10).stdout

    def reply():
        cache[ENTITY] = command_fixture.state("on", context=own, at=clock.now_ms(), attributes={"brightness":64})
        cache[ENTITY].entity_id = ENTITY
        port.receive(input_fixture.REPORT_TOPIC, raw, retained=False)

    clock.on_sleep = reply
    receipt = await executor.execute(hass, PlannedCommand("main",LightAction.SET_BRIGHTNESS,brightness=25))
    assert receipt.confirmed and len(calls)==1
    assert trusted_target_state(port.read_state(ENTITY),now_ms=clock.now_ms()) is None
