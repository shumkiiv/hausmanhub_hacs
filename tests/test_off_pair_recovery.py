"""Observed OFF recovery using the real parser, GET adapter and virtual time."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_observations import (
    LightingMqttObservations, ROLLOUT_BINDINGS, REPORT_TOPIC,
)
from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import (
    LightingMqttRefresh, POWER_ENTITY, LIGHT_ENTITY,
)
from test_room_lighting_runtime import (
    _FakeHass, _make_runtime, _config_payload, AWAY_ENTITY_ID,
    RoomLightingHaExecutor, _event, _service_context_factory,
)
import json


async def make_pair(local_time="12:00", stored_level=168, native_level=None, *, initial_read=True):
    moment = datetime.fromisoformat("2026-10-20T" + local_time).replace(tzinfo=ZoneInfo("Asia/Omsk"))
    clock, sequence, session = [int(moment.timestamp() * 1000)], [0], str(uuid4())
    hass = _FakeHass()
    hass.config = SimpleNamespace(time_zone="Asia/Omsk")

    def native(entity, state, level=None):
        hass.states.set(entity, state, {"brightness": level} if entity == LIGHT_ENTITY else {},
                        last_changed=datetime.fromtimestamp(clock[0] / 1000, ZoneInfo("Asia/Omsk")))
        hass.states.get(entity).entity_id = entity

    native(POWER_ENTITY, "on")
    native(LIGHT_ENTITY, "off", native_level)
    native(AWAY_ENTITY_ID, "off")
    native("binary_sensor.demo_presence", "off")
    payload = _config_payload(power_switch_entity=POWER_ENTITY)
    payload["profile"] = "day_curve"
    payload["curveParams"] = {"eveningMaxPercent": 40, "neutralKelvin": 3278,
                              "eveningWarmKelvin": 3003, "presenceConfirmSeconds": 3}
    payload["devices"]["light_targets"][0]["entityId"] = LIGHT_ENTITY
    port = LightingMqttObservations(hass.states.get, now_ms=lambda: clock[0])
    port._enabled = frozenset(b.entity_id for b in ROLLOUT_BINDINGS)
    port._started = True
    port.connection_changed(True)
    calls, entered, gate = [], asyncio.Event(), asyncio.Event()

    def physical(entity, value, brightness=False):
        binding = next(b for b in ROLLOUT_BINDINGS if b.entity_id == entity)
        sequence[0] += 1
        body = dict(schema=1, source="physical_brightness" if brightness else "physical_onoff",
            ieee=binding.ieee, endpoint=binding.endpoint,
            attribute="genLevelCtrl.currentLevel" if brightness else "genOnOff.onOff",
            value=value if brightness else value.upper(), type="attributeReport", transaction=7,
            observed_at_ms=clock[0], session=session, sequence=sequence[0])
        port.receive(REPORT_TOPIC, json.dumps(body), retained=False)

    async def publish(topic, value):
        prop = next(iter(json.loads(value)))
        calls.append((topic, json.loads(value)))
        clock[0] += 10
        entity = POWER_ENTITY if prop == "state_2" else LIGHT_ENTITY
        physical(entity, stored_level if prop == "brightness" else hass.states.get(entity).state,
                 brightness=prop == "brightness")

    adapter = LightingMqttRefresh(port, publish, now_ms=lambda: clock[0])
    runtime = _make_runtime(hass, commands_enabled=True, payload=payload,
        executor=RoomLightingHaExecutor(), now_ms=lambda: clock[0])
    runtime._state_reader, runtime._observation_refresher = port.read_state, adapter
    runtime._state_provider._state_reader = port.read_state

    async def pause(seconds):
        assert seconds == 5
        entered.set()
        await gate.wait()

    runtime._zero_recovery_sleep = pause
    await runtime.start(hass, "entry")
    clock[0] += 1
    native(POWER_ENTITY, "on")
    native(LIGHT_ENTITY, "off", native_level)
    physical(LIGHT_ENTITY, "off")
    physical(LIGHT_ENTITY, stored_level, brightness=True)
    assert hass.states.get(LIGHT_ENTITY).state == "off"
    actual = hass.states.get(LIGHT_ENTITY).attributes["brightness"]
    assert actual is native_level or actual == native_level
    assert port.read_state(LIGHT_ENTITY).observation.value == "off"
    assert port.read_state(LIGHT_ENTITY).brightness_observation.value == stored_level
    initial_task = runtime._zero_initial_read_task
    initial_calls = None
    hass.services.calls.clear()
    if initial_task is not None:
        if not initial_read:
            initial_task.cancel()
        await asyncio.gather(initial_task, return_exceptions=True)
    initial_calls = list(calls)
    initial_services = list(hass.services.calls)
    calls.clear()
    hass.services.calls.clear()

    def transition(entity, value, raw_first):
        clock[0] += 100
        if raw_first:
            physical(entity, value)
        native(entity, value, native_level if entity == LIGHT_ENTITY else None)
        runtime._state_event(SimpleNamespace(data={"entity_id": entity}))
        if not raw_first:
            physical(entity, value)

    async def finish():
        # Yield only; virtual waits must never require real seconds.
        for _ in range(10):
            if entered.is_set():
                break
            await asyncio.sleep(0)
        assert entered.is_set(), "observed OFF->ON did not start recovery"
        clock[0] += 5000
        gate.set()
        await asyncio.gather(*tuple(runtime._zero_recovery_tasks.values()), return_exceptions=True)

    return SimpleNamespace(**locals())


@pytest.mark.parametrize("raw_first", [False, True])
@pytest.mark.parametrize("stored_level", [0, 167, 168])
@pytest.mark.parametrize("local_time,expected", [("12:00", 100), ("21:00", 14), ("23:00", 50)])
async def test_first_fresh_off_on_recovers_native_null_through_real_parser(
    raw_first, stored_level, local_time, expected
):
    case = await make_pair(local_time, stored_level, initial_read=False)
    try:
        case.transition(POWER_ENTITY, "off", raw_first)
        case.transition(POWER_ENTITY, "on", raw_first)
        before = case.runtime._ownership.to_payload()
        assert case.hass.services.calls == []
        await case.finish()
        assert case.hass.services.calls == [("light", "turn_on", {
            "entity_id": LIGHT_ENTITY, "brightness_pct": expected})]
        assert case.runtime._ownership.to_payload() == before
        case.physical(POWER_ENTITY, "on")
        assert len(case.hass.services.calls) == 1
        assert len(case.calls) == 3
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("change", ["service_off", "native_on_off", "physical_on_off", "power_off", "disconnect"])
async def test_new_action_or_observation_loss_cancels_ordinary_off_recovery(change):
    case = await make_pair()
    try:
        case.transition(POWER_ENTITY, "off", False)
        case.transition(POWER_ENTITY, "on", False)
        if change == "service_off":
            case.runtime._service_event(_event("light", "turn_off", LIGHT_ENTITY))
        elif change in {"native_on_off", "physical_on_off"}:
            case.transition(LIGHT_ENTITY, "on", change == "physical_on_off")
            case.transition(LIGHT_ENTITY, "off", change == "physical_on_off")
        elif change == "power_off":
            case.transition(POWER_ENTITY, "off", False)
        else:
            case.port.connection_changed(False)
        case.gate.set()
        await asyncio.gather(*tuple(case.runtime._zero_recovery_tasks.values()), return_exceptions=True)
        assert case.hass.services.calls == []
    finally:
        await case.runtime.stop()


async def test_startup_reads_once_without_creating_recovery_or_claiming_light():
    case = await make_pair()
    try:
        assert len(case.initial_calls) == 3
        assert case.initial_services == []
        assert all(topic.endswith('/get') for topic, _ in case.initial_calls)
        assert not case.runtime._zero_recoveries
        assert not case.runtime._zero_recovery_tasks
        assert case.runtime._zero_initial_read_task is None
        assert case.hass.services.calls == []
        # Startup's GET is not a substitute for the later fresh physical cycle.
        case.transition(POWER_ENTITY, 'off', True)
        case.transition(POWER_ENTITY, 'on', True)
        await case.finish()
        assert len(case.hass.services.calls) == 1
    finally:
        await case.runtime.stop()
    assert not case.port._listeners


@pytest.mark.parametrize('boundary', ['stop', 'power_off', 'epoch', 'config'])
async def test_pending_initial_read_stops_without_device_commands(boundary):
    case = await make_pair(initial_read=False)
    entered, release = asyncio.Event(), asyncio.Event()
    original = case.adapter._publish

    async def blocked_publish(topic, payload):
        entered.set()
        await release.wait()
        await original(topic, payload)

    case.adapter._publish = blocked_publish
    task = asyncio.create_task(case.runtime._async_initial_pair_read(case.runtime._return_intent_epoch))
    case.runtime._zero_initial_read_task = task
    task.add_done_callback(case.runtime._zero_initial_read_done)
    await entered.wait()
    if boundary == 'stop':
        await case.runtime.stop()
    elif boundary == 'power_off':
        case.transition(POWER_ENTITY, 'off', False)
    elif boundary == 'epoch':
        case.runtime._return_intent_epoch += 1
    else:
        from dataclasses import replace
        config = next(iter(case.runtime._configs.values()))
        case.runtime._configs[config.room_id] = replace(config)
    release.set()
    await asyncio.gather(task, return_exceptions=True)
    await case.runtime.stop()
    assert case.hass.services.calls == []
    assert case.runtime._zero_initial_read_task is None
    assert not case.port._listeners
    assert len(case.calls) <= 1


@pytest.mark.parametrize('native_level,raw_level,allowed', [
    (None, 168, True), (255, 254, True), (256, 254, False),
    (-1, 0, False), (float('nan'), 0, False), (float('inf'), 0, False),
    (True, 0, False), (1, 0, True), (168, 0, False),
])
async def test_off_level_bounds_do_not_weaken_general_brightness_evidence(native_level, raw_level, allowed):
    from custom_components.hausman_hub.application.lighting_observation import (
        paired_recovery_brightness_observed_ms, physical_brightness_observed_ms,
    )
    case = await make_pair(stored_level=raw_level, native_level=native_level, initial_read=False)
    try:
        view = case.port.read_state(LIGHT_ENTITY)
        assert (paired_recovery_brightness_observed_ms(view, now_ms=case.clock[0]) is not None) is allowed
        if native_level is None:
            assert physical_brightness_observed_ms(view, now_ms=case.clock[0]) is None
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize('raw_first', [False, True])
@pytest.mark.parametrize('state,level', [('off', 167), ('off', 168), ('on', 0)])
async def test_long_idle_cycle_reads_fresh_dark_state_before_single_recovery(raw_first, state, level):
    case = await make_pair(stored_level=level, initial_read=False)
    try:
        case.native(LIGHT_ENTITY, state, None if state == 'off' else 0)
        case.physical(LIGHT_ENTITY, state)
        case.physical(LIGHT_ENTITY, level, brightness=True)
        case.clock[0] += 300_001
        case.transition(POWER_ENTITY, 'off', raw_first)
        case.transition(POWER_ENTITY, 'on', raw_first)
        before = case.runtime._ownership.to_payload()
        await case.finish()
        assert len(case.calls) == 3  # GET state arrives before GET brightness.
        assert case.hass.services.calls == [('light', 'turn_on', {
            'entity_id': LIGHT_ENTITY, 'brightness_pct': 100})]
        assert case.runtime._ownership.to_payload() == before
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize('raw_first', [False, True])
@pytest.mark.parametrize('missing', ['unknown', 'unavailable'])
@pytest.mark.parametrize('during_get', [False, True])
async def test_unknown_power_burns_cycle_even_if_on_returns_without_new_cycle(raw_first, missing, during_get):
    case = await make_pair(initial_read=False)
    original = case.adapter._publish
    cancelled_at_loss = []

    def lose_then_restore():
        case.native(POWER_ENTITY, missing)
        case.runtime._state_event(SimpleNamespace(data={'entity_id': POWER_ENTITY}))
        cancelled_at_loss.append(not case.runtime._zero_recoveries)
        case.native(POWER_ENTITY, 'on')
        case.runtime._state_event(SimpleNamespace(data={'entity_id': POWER_ENTITY}))

    async def publish(topic, payload):
        lose_then_restore()
        await original(topic, payload)

    try:
        case.transition(POWER_ENTITY, 'off', raw_first)
        case.transition(POWER_ENTITY, 'on', raw_first)
        if during_get:
            case.adapter._publish = publish
            await case.finish()
        else:
            lose_then_restore()
            case.gate.set()
            await asyncio.gather(*tuple(case.runtime._zero_recovery_tasks.values()), return_exceptions=True)
        assert case.hass.services.calls == []
        assert cancelled_at_loss and all(cancelled_at_loss)
        # Recovery is possible only after another fully observed physical cycle.
        case.adapter._publish = original
        case.entered.clear()
        case.gate.clear()
        case.transition(POWER_ENTITY, 'off', raw_first)
        case.transition(POWER_ENTITY, 'on', raw_first)
        await case.finish()
        assert len(case.hass.services.calls) == 1
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize('state,level', [('on', 168), ('unknown', None), ('unavailable', None),
                                        ('off', -1), ('off', 256), ('on', None)])
async def test_stale_bright_on_or_invalid_native_target_never_even_stages_read(state, level):
    case = await make_pair(initial_read=False)
    try:
        case.native(LIGHT_ENTITY, state, level)
        case.clock[0] += 300_001
        case.transition(POWER_ENTITY, 'off', False)
        case.transition(POWER_ENTITY, 'on', False)
        await asyncio.sleep(0)
        assert not case.runtime._zero_recoveries
        assert not case.runtime._zero_recovery_tasks
        assert case.calls == []
        assert case.hass.services.calls == []
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize('change', ['unknown', 'unavailable', 'positive_raw_level'])
async def test_new_target_uncertainty_or_positive_level_cannot_be_undone_during_delay(change):
    case = await make_pair(stored_level=0, native_level=0, initial_read=False)
    try:
        if change == 'positive_raw_level':
            case.native(LIGHT_ENTITY, 'on', 0)
            case.physical(LIGHT_ENTITY, 'on')
            case.physical(LIGHT_ENTITY, 0, brightness=True)
        case.transition(POWER_ENTITY, 'off', False)
        case.transition(POWER_ENTITY, 'on', False)
        if change == 'positive_raw_level':
            case.physical(LIGHT_ENTITY, 42, brightness=True)
            assert not case.runtime._zero_recoveries
            case.physical(LIGHT_ENTITY, 0, brightness=True)
        else:
            case.native(LIGHT_ENTITY, change)
            case.runtime._state_event(SimpleNamespace(data={'entity_id': LIGHT_ENTITY}))
            assert not case.runtime._zero_recoveries
            case.native(LIGHT_ENTITY, 'off', 0)
            case.runtime._state_event(SimpleNamespace(data={'entity_id': LIGHT_ENTITY}))
        case.gate.set()
        await asyncio.gather(*tuple(case.runtime._zero_recovery_tasks.values()), return_exceptions=True)
        assert case.hass.services.calls == []
    finally:
        await case.runtime.stop()


async def test_long_idle_stage_never_sends_without_new_complete_get_proof(monkeypatch):
    from custom_components.hausman_hub.adapters import lighting_mqtt_refresh as module
    case = await make_pair(initial_read=False)
    original = case.adapter._publish

    async def no_brightness_reply(topic, payload):
        if 'brightness' not in json.loads(payload):
            await original(topic, payload)

    try:
        monkeypatch.setattr(module, 'REFRESH_TIMEOUT_SECONDS', 0.001)
        case.adapter._publish = no_brightness_reply
        case.clock[0] += 300_001
        case.transition(POWER_ENTITY, 'off', False)
        case.transition(POWER_ENTITY, 'on', False)
        await case.finish()
        assert case.hass.services.calls == []
        assert not case.runtime._zero_recoveries
    finally:
        await case.runtime.stop()
