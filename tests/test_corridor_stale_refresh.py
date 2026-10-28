"""Ordinary corridor refreshes physical evidence, not stale authority."""
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import LightingMqttRefresh
from custom_components.hausman_hub.application.device_power_dependencies import DevicePowerDependencyService
from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction, evaluate_room_lighting
from custom_components.hausman_hub.domain.room_lighting_ownership import OwnershipSource
from tests.test_corridor_power_refresh import ROOM, BULB, RELAY, GETS, _channel
from tests.test_lighting_mqtt_observations import make_case, feed, report
from tests.test_room_lighting_force_auto import Store, seed_participants
from tests.test_room_lighting_runtime import (
    _FakeHass, _SpyExecutor, _config_payload, _make_runtime, _dt,
    _service_context_factory, RoomLightingHaExecutor,
)


@asynccontextmanager
async def corridor_case(*, payload=None):
    """Settings exist before ownership. Lower ports only are synthetic."""
    port, native, clock = make_case(attributes={'brightness': 115, 'color_temp_kelvin': 3000})
    hass = _FakeHass()
    hass.states.values.update(native)
    port._native_reader = hass.states.get
    data = deepcopy(payload) if payload is not None else _config_payload(commands_enabled=True)
    if payload is None:
        data.update(roomId=ROOM, version=7, timers={'absence_seconds': 300, 'turn_off_seconds': 300})
        data['devices']['light_targets'][0]['entityId'] = BULB
        data['schedule'][0]['how']['brightness'] = 45
        data['dimming']['targetPercent'] = 5
    sensors = [s for s in data['devices']['sensors'] if s['kind'] in {'presence', 'motion'}]
    lux = [s for s in data['devices']['sensors'] if s['kind'] == 'illuminance']
    for sensor in sensors:
        hass.states.set(sensor['entityId'], 'off', last_changed=_dt(clock['now']))
    for sensor in lux:
        hass.states.set(sensor['entityId'], '316', last_changed=_dt(clock['now']))
    dependency_store = Store()
    dependency_store.payload = {'revision': 1, 'updatedAt': '2026-10-25T12:00:00Z',
        'dependencies': [{'dependentEntityId': BULB, 'powerSourceEntityId': RELAY,
                          'policy': 'auto_turn_on', 'warmupSeconds': 3}]}
    service = DevicePowerDependencyService(dependency_store,
        entity_pair_validator=lambda b, r: (b, r) == (BULB, RELAY),
        electrical_breaker_resolver=lambda _: False)
    await service.async_load()
    authority = ManualLightAuthority(Store())
    await authority.async_load()
    priority, off = await seed_participants(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, payload=data, now_ms=lambda: clock['now'],
        manual_light_authority=authority, light_priority=priority,
        ownership_store=Store(), executor=executor)
    runtime._power_dependency_resolver = lambda: service.mapping
    runtime._state_provider._power_dependency_resolver = runtime._power_dependency_resolver
    runtime._state_reader = port.read_state
    runtime._state_provider._state_reader = port.read_state
    case = SimpleNamespace(runtime=runtime, clock=clock, port=port, native=native,
        hass=hass, authority=authority, priority=priority, off=off, service=service,
        executor=executor, calls=[], hook=None, mode=None, sequence=0, session=str(uuid4()))

    def deliver(entity, **changes):
        case.sequence += 1
        clock['now'] += 10
        body = report(clock, channel=_channel(entity), session=case.session,
                      sequence=case.sequence, value=native[entity].state.upper())
        body.update(changes)
        feed(port, body, retained=case.mode == 'retained')

    async def publish(topic, payload):
        call = (topic, json.loads(payload))
        assert call in GETS
        case.calls.append(call)
        if case.hook is not None:
            hook, case.hook = case.hook, None
            await hook(case)
        entity = RELAY if call == GETS[0] else BULB
        changes = {}
        if case.mode == 'stale': changes['observed_at_ms'] = clock['now'] - 300_001
        if case.mode == 'wrong_endpoint': changes['endpoint'] = 2
        deliver(entity, **changes)

    case.deliver = deliver
    runtime._observation_refresher = LightingMqttRefresh(port, publish,
        now_ms=lambda: clock['now'])
    await runtime.start(hass, 'ordinary-corridor-refresh')
    try:
        for sensor in sensors:
            hass.states.set(sensor['entityId'], 'off', last_changed=_dt(clock['now']))
        case.room = data['roomId']
        result = await runtime.async_force_auto(case.room, reason='test explicit corridor return')
        assert result['outcome'] == 'returned', result
        config = runtime._configs[case.room]
        owner = runtime._control_ownership_for(config)[0]
        assert owner.confirmed and owner.source is OwnershipSource.EXPLICIT_HANDOFF
        case.config, case.target = config, config.devices.light_targets[0]
        case.absent_since = clock['now']
        clock['now'] += 301_001
        for sensor in sensors:
            hass.states.set(sensor['entityId'], 'off', last_changed=_dt(case.absent_since))
            hass.states.get(sensor['entityId']).last_reported = _dt(clock['now'])
        for sensor in lux:
            hass.states.set(sensor['entityId'], '316', last_changed=_dt(clock['now']))
        assert not runtime._target_trust(BULB)[0]
        assert runtime._target_trust(BULB)[1] == 'on'
        assert runtime._control_ownership_for(config)[0].confirmed
        case.calls.clear()
        executor.calls.clear()
        yield case
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_corridor_real_return_then_stale_refresh_reaches_ordinary_dim():
    async with corridor_case() as case:
        assert await case.runtime._refresh_stale_handoff_observations(ROOM)
        assert case.calls == GETS
        context = await case.runtime._build_context(case.config)
        decision = evaluate_room_lighting(case.config, context)
        dim = next(c for c in decision.commands if c.action is LightAction.SET_BRIGHTNESS)
        assert (dim.brightness, dim.fade_seconds) == (5, 20)
        await case.runtime.async_process([ROOM])
        assert any(c.action is LightAction.SET_BRIGHTNESS and c.brightness == 5
                   for c in case.executor.calls)
        assert case.calls == GETS


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['manual_on', 'manual_off', 'config', 'stop', 'gap', 'power'])
async def test_corridor_change_during_real_read_never_dispatches(change):
    async with corridor_case() as case:
        async def intervene(c):
            if change.startswith('manual_'):
                await c.authority.async_capture(BULB, change.removeprefix('manual_'),
                    'service', 'new-owner-gesture', observed_at_ms=c.clock['now'])
            elif change == 'config':
                c.runtime._configs[ROOM] = replace(c.config, version=c.config.version + 1)
            elif change == 'stop':
                await c.runtime.stop()
            elif change == 'gap':
                c.runtime._unobserved_since = c.clock['now']
            elif change == 'power':
                row = c.service.document['dependencies'][0]
                await c.service.async_replace(1, [{**row, 'warmupSeconds': 4}])
        case.hook = intervene
        await case.runtime.async_process([ROOM])
        assert case.calls == GETS[:1]
        assert not case.executor.calls
        if change.startswith('manual_'):
            assert case.authority.record(BULB).fingerprint == 'new-owner-gesture'
            assert case.authority.blocks(BULB)


@pytest.mark.asyncio
async def test_corridor_new_presence_during_read_recalculates_instead_of_dimming():
    async with corridor_case() as case:
        async def present(c):
            sensor = c.config.devices.sensors[0]
            c.hass.states.set(sensor.entity_id, 'on', last_changed=_dt(c.clock['now']))
        case.hook = present
        await case.runtime.async_process([ROOM])
        assert case.calls == GETS
        assert not any(c.action in {LightAction.TURN_OFF, LightAction.SET_BRIGHTNESS}
                       and c.brightness != 45 for c in case.executor.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['retained', 'stale', 'wrong_endpoint'])
async def test_corridor_untrusted_response_cannot_command_or_repeat_each_tick(mode, monkeypatch):
    import custom_components.hausman_hub.adapters.lighting_mqtt_refresh as adapter
    monkeypatch.setattr(adapter, 'REFRESH_TIMEOUT_SECONDS', 0.03)
    async with corridor_case() as case:
        case.mode = mode
        for _ in range(4):
            await case.runtime.async_process([ROOM])
        assert case.calls == GETS[:1]
        assert not case.executor.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['false', 'fake_success', 'error'])
async def test_corridor_failed_refresh_does_not_loop_or_trust_fake_success(outcome):
    async with corridor_case() as case:
        attempts = []
        async def broken(*_):
            attempts.append('read')
            if outcome == 'error': raise OSError('offline')
            return outcome == 'fake_success'
        case.runtime._observation_refresher = broken
        for _ in range(4):
            await case.runtime.async_process([ROOM])
        assert attempts == ['read']
        assert not case.executor.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('condition', ['manual', 'commands_off', 'fresh', 'owner_missing', 'target_off'])
async def test_corridor_ineligible_input_does_not_refresh(condition):
    async with corridor_case() as case:
        if condition == 'manual':
            await case.authority.async_capture(BULB, 'on', 'service', 'before-read')
        elif condition == 'commands_off':
            case.runtime._configs[ROOM] = replace(case.config, commands_enabled=False)
        elif condition == 'fresh':
            case.deliver(RELAY)
            case.deliver(BULB)
        elif condition == 'owner_missing':
            case.runtime._ownership.invalidate_handoffs(ROOM)
        elif condition == 'target_off':
            case.native[BULB].state = 'off'
            case.deliver(BULB)
        assert await case.runtime._refresh_stale_handoff_observations(ROOM)
        assert not case.calls and not case.executor.calls


@pytest.mark.asyncio
async def test_failed_attempt_rearms_only_after_real_report_and_later_expiry():
    async with corridor_case() as case:
        real = case.runtime._observation_refresher
        attempts = []
        async def broken(*_):
            attempts.append('read')
            return False
        case.runtime._observation_refresher = broken
        for _ in range(3):
            assert not await case.runtime._refresh_stale_handoff_observations(ROOM)
        assert attempts == ['read']
        case.deliver(RELAY)
        case.deliver(BULB)
        assert await case.runtime._refresh_stale_handoff_observations(ROOM)
        case.clock['now'] += 301_001
        case.runtime._observation_refresher = real
        assert await case.runtime._refresh_stale_handoff_observations(ROOM)
        assert case.calls == GETS


@pytest.mark.asyncio
async def test_other_generic_room_does_not_gain_physical_refresh():
    data = _config_payload(commands_enabled=True)
    data.update(roomId='other_generic_room')
    data['devices']['light_targets'][0]['entityId'] = BULB
    async with corridor_case(payload=data) as case:
        assert await case.runtime._refresh_stale_handoff_observations(case.room)
        assert not case.calls and not case.executor.calls


@pytest.mark.asyncio
async def test_real_executor_confirms_five_percent_then_off():
    async with corridor_case() as case:
        real = RoomLightingHaExecutor({case.target.id: case.target},
            state_reader=case.port.read_state, manual_light_authority=case.authority,
            now_ms=lambda: case.clock['now'], light_readback_window_seconds=0)
        case.runtime._executor = real
        case.runtime._rebuild_index()
        old_call = case.hass.services.async_call
        async def hardware(domain, service, data, blocking=True, context=None):
            case.clock['now'] += 10
            await old_call(domain, service, data, blocking=blocking, context=context)
            case.native[BULB] = case.hass.states.get(BULB)
            case.native[BULB].entity_id = BULB
            case.deliver(BULB)
            if 'brightness_pct' in data:
                case.deliver(BULB, source='physical_brightness',
                    attribute='genLevelCtrl.currentLevel',
                    value=round(data['brightness_pct'] * 254 / 100))
        case.hass.services.async_call = hardware
        await case.runtime.async_process([ROOM])
        assert case.hass.states.get(BULB).attributes['brightness'] == 13
        owner = case.runtime._control_ownership_for(case.config)[0]
        assert owner.source is OwnershipSource.EXPLICIT_HANDOFF and owner.confirmed
        case.clock['now'] += 20_001
        await case.runtime.async_process([ROOM])
        assert case.hass.states.get(BULB).state == 'off'
        assert [c.action for c in real.calls] == [LightAction.SET_BRIGHTNESS, LightAction.TURN_OFF]
