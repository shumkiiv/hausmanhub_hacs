"""Quiet time blocks automatic activation, never authorizes OFF or release."""
from dataclasses import replace
from datetime import time
import pytest

from custom_components.hausman_hub.domain.room_lighting import config_from_payload, RoomLightingViolation
from custom_components.hausman_hub.domain.room_lighting_engine import evaluate_room_lighting, LightAction
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState
from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService
from tests.test_room_lighting_engine import _config, _ctx, _at, _light, _auto, _manual
from tests.test_room_lighting_service import _MemoryStore


def config(quiet={'start':'00:30','end':'07:00'}, **changes):
    payload=_config().to_dict()
    payload['schedule'][0]['when']['anchor']={'kind':'fixed','time':'00:00','offsetMinutes':0}
    payload['quietHours']=quiet
    payload.update(changes)
    return config_from_payload(payload)


@pytest.mark.parametrize('hour,minute,blocked',[(0,29,False),(0,30,True),(3,0,True),(6,59,True),(7,0,False),(23,59,False)])
def test_boundaries_only_suppress_new_automatic_activation(hour,minute,blocked):
    now=_at(hour,minute)
    result=evaluate_room_lighting(config(),_ctx(now,lights=(_light('light_main',SensorState.OFF,now),)))
    assert any(c.action is LightAction.TURN_ON for c in result.commands) is not blocked
    assert not any(c.action is LightAction.TURN_OFF for c in result.commands)
    if blocked: assert any(s.reason.value=='quiet_hours' for s in result.skips)


def test_early_summer_sunrise_cannot_break_quiet_interval():
    payload=config().to_dict()
    payload['schedule'][0]['when']['anchor']={'kind':'sunrise','offsetMinutes':0}
    now=_at(6)
    ctx=replace(_ctx(now,lights=(_light('light_main',SensorState.OFF,now),)),sunrise=time(4,15))
    assert not evaluate_room_lighting(config_from_payload(payload),ctx).commands


@pytest.mark.parametrize('hour,blocked',[(21,False),(22,True),(0,True),(5,True),(6,False),(12,False)])
def test_wrapped_interval(hour,blocked):
    now=_at(hour)
    result=evaluate_room_lighting(config({'start':'22:00','end':'06:00'}),_ctx(now,lights=(_light('light_main',SensorState.OFF,now),)))
    assert bool(result.commands) is not blocked


@pytest.mark.parametrize('bad',[{},True,[],{'start':'24:00','end':'07:00'},{'start':'00:30'},{'start':'07:00','end':'07:00'},{'start':'00:30','end':'07:00','unknown':True}])
def test_invalid_interval_never_silently_becomes_unrestricted(bad):
    with pytest.raises(RoomLightingViolation): config(bad)


def test_roundtrip_and_no_change_without_interval():
    payload=config().to_dict()
    assert payload['quietHours']=={'start':'00:30','end':'07:00'}
    assert config_from_payload(payload).to_dict()==payload
    assert 'quietHours' not in _config().to_dict()
    assert 'quietHours' not in config(None).to_dict()


@pytest.mark.parametrize('manual',[False,True])
def test_existing_on_is_not_forced_off_or_adjusted_by_quiet(manual):
    now=_at(3)
    ctx=_ctx(now,lights=(_light('light_main',SensorState.ON,now-1000,brightness=20),),
             ownership=((_manual if manual else _auto)('light_main',now-1000),))
    assert not evaluate_room_lighting(config(),ctx).commands


def test_explicit_away_off_is_not_blocked_by_quiet():
    now=_at(3)
    ctx=_ctx(now,lights=(_light('light_main',SensorState.ON,now-1000),),away=True)
    payload=config().to_dict();payload['awayBehavior']['mode']='room_off'
    result=evaluate_room_lighting(config_from_payload(payload),ctx)
    assert any(c.action is LightAction.TURN_OFF for c in result.commands)


async def test_old_client_omission_preserves_interval_and_explicit_null_clears():
    store=_MemoryStore();service=RoomLightingService(store)
    saved=await service.async_put_config(config())
    old=saved.to_dict();old.pop('quietHours');old['name']='Старый клиент'
    kept=await service.async_put_config(config_from_payload(old),expected_version=saved.version,preserve_quiet_hours=True)
    assert kept.to_dict()['quietHours']=={'start':'00:30','end':'07:00'}
    cleared=await service.async_put_config(replace(kept,quiet_hours=None),expected_version=kept.version)
    assert 'quietHours' not in cleared.to_dict()


def test_quiet_interval_is_rejected_for_unimplemented_day_curve_path():
    with pytest.raises(RoomLightingViolation):
        config(profile='day_curve')


def test_pending_fan_presence_timer_does_not_bypass_quiet_activation_guard():
    from custom_components.hausman_hub.domain.shower_exhaust import (
        ShowerExhaustObservation, evaluate_shower_exhaust_due, SHOWER_PRESENCE_TIMER,
    )
    observation = ShowerExhaustObservation(
        presence=SensorState.ON, humidity=40, fan=SensorState.OFF,
        fan_owned=False, lights_owned_on=False, allow_activation=False,
    )
    assert evaluate_shower_exhaust_due(observation, timer_kind=SHOWER_PRESENCE_TIMER).action is None


@pytest.mark.parametrize('manual,gap,presence,elapsed,off', [
    (False,False,SensorState.OFF,299,False),
    (False,False,SensorState.OFF,300,True),
    (True,False,SensorState.OFF,600,False),
    (False,True,SensorState.OFF,600,False),
    (False,False,SensorState.UNKNOWN,600,False),
    (False,False,SensorState.ON,600,False),
])
def test_quiet_does_not_remove_owned_absence_off_or_authorize_manual_off(manual,gap,presence,elapsed,off):
    now=_at(3)
    payload=config(timers={'absence_seconds':300,'turn_off_seconds':300}).to_dict()
    payload['devices']['light_targets'][0].update(brightness=False,color_temperature=False)
    payload['schedule'][0]['how'].update(brightness=None,colorTemperature=None)
    cfg=config_from_payload(payload)
    ctx=_ctx(now,presence=presence,presence_at=now-elapsed*1000,
             lights=(_light('light_main',SensorState.ON,now-1000),),
             ownership=((_manual if manual else _auto)('light_main',now-1000),),
             unobserved_since=now-1000 if gap else None)
    result=evaluate_room_lighting(cfg,ctx)
    assert any(c.action is LightAction.TURN_OFF for c in result.commands) is off


@pytest.mark.parametrize('humid',[False,True])
async def test_runtime_fan_activation_blocked_in_quiet_hours(humid):
    from datetime import datetime, timezone
    from tests.test_room_lighting_runtime import _FakeHass, _SpyExecutor, _runtime_with_shadow, _shower_payload
    moment=datetime(2026,9,11,3,tzinfo=timezone.utc)
    hass=_FakeHass()
    for entity,state in [('binary_sensor.demo_presence','on'),('light.demo_main','off'),
                         ('sensor.demo_shower_humidity','70' if humid else '40'),('switch.demo_shower_fan','off')]:
        hass.states.set(entity,state,last_changed=moment)
    payload=_shower_payload(commands_enabled=True)
    payload['quietHours']={'start':'00:30','end':'07:00'}
    executor=_SpyExecutor()
    runtime,_=_runtime_with_shadow(hass,payload,executor=executor,now_ms=lambda:int(moment.timestamp()*1000))
    await runtime.start(hass,'entry')
    try:
        assert not any(c.target_id=='aux_shower_fan' for c in executor.calls)
    finally:
        await runtime.stop()


async def test_fan_timer_crossing_quiet_start_is_rechecked_by_runtime():
    from datetime import datetime, timezone
    from tests.test_room_lighting_runtime import _FakeHass, _SpyExecutor, _runtime_with_shadow, _shower_payload
    moment=datetime(2026,9,11,0,29,tzinfo=timezone.utc)
    clock=[int(moment.timestamp()*1000)]
    hass=_FakeHass()
    for entity,state in [('binary_sensor.demo_presence','on'),('light.demo_main','off'),
                         ('sensor.demo_shower_humidity','40'),('switch.demo_shower_fan','off')]:
        hass.states.set(entity,state,last_changed=moment)
    payload=_shower_payload(commands_enabled=True)
    payload['quietHours']={'start':'00:30','end':'07:00'}
    executor=_SpyExecutor()
    runtime,_=_runtime_with_shadow(hass,payload,executor=executor,now_ms=lambda:clock[0])
    await runtime.start(hass,'entry')
    try:
        clock[0]+=120000
        await runtime.async_process()
        assert not any(c.target_id=='aux_shower_fan' for c in executor.calls)
    finally:
        await runtime.stop()


async def test_persisted_quiet_interval_survives_new_store_and_service():
    import copy
    from custom_components.hausman_hub.application.room_lighting_storage import HomeAssistantRoomLightingStore
    class Durable:
        payload=None
        async def async_load(self): return copy.deepcopy(self.payload)
        async def async_save(self,payload): self.payload=copy.deepcopy(payload)
    durable=Durable()
    store=HomeAssistantRoomLightingStore.__new__(HomeAssistantRoomLightingStore)
    store._store=durable
    saved=await RoomLightingService(store).async_put_config(config())
    restored=HomeAssistantRoomLightingStore.__new__(HomeAssistantRoomLightingStore)
    restored._store=durable
    actual=await RoomLightingService(restored).async_get_config(saved.room_id)
    assert actual.to_dict()==saved.to_dict()


@pytest.mark.parametrize('phase',['decision_flush','pending_target'])
async def test_quiet_start_crossed_during_persistence_blocks_real_dispatch(phase):
    from datetime import datetime, timezone
    from tests.test_room_lighting_runtime import _FakeHass, _SpyExecutor, _runtime_with_shadow, _shower_payload
    moment=datetime(2026,9,11,0,29,59,tzinfo=timezone.utc)
    clock=[int(moment.timestamp()*1000)]
    hass=_FakeHass()
    for entity,state in [('binary_sensor.demo_presence','on'),('light.demo_main','off'),
                         ('sensor.demo_shower_humidity','70'),('switch.demo_shower_fan','off')]:
        hass.states.set(entity,state,last_changed=moment)
    payload=_shower_payload(commands_enabled=True)
    payload['quietHours']={'start':'00:30','end':'07:00'}
    executor=_SpyExecutor()
    runtime,shadow=_runtime_with_shadow(hass,payload,executor=executor,now_ms=lambda:clock[0])
    if phase=='decision_flush':
        original=shadow.async_flush
        async def crossed():
            await original()
            clock[0]=int(moment.timestamp()*1000)+2000
        shadow.async_flush=crossed
    else:
        original=runtime._persist_pending_target_dispatch
        async def crossed(**kwargs):
            result=await original(**kwargs)
            clock[0]=int(moment.timestamp()*1000)+2000
            return result
        runtime._persist_pending_target_dispatch=crossed
    await runtime.start(hass,'entry')
    try:
        late=[c for c in executor.calls if c.target_id=='light_main' or phase=='decision_flush']
        assert late==[]
    finally:
        await runtime.stop()


@pytest.mark.parametrize('phase',['before_pending','after_pending'])
async def test_quiet_power_preparation_is_not_sent_after_boundary(phase):
    from datetime import datetime, timezone
    from tests.test_room_lighting_runtime import _FakeHass, _SpyExecutor, _runtime_with_shadow, _shower_payload
    moment=datetime(2026,9,11,0,29,59,tzinfo=timezone.utc)
    clock=[int(moment.timestamp()*1000)]
    class PowerExecutor(_SpyExecutor):
        power_calls=[]
        async def async_power_on(self,hass,entity_id):
            self.power_calls.append(entity_id)
            return {'confirmed':True,'sent':True,'sendOutcome':'sent'}
    hass=_FakeHass()
    for entity,state in [('binary_sensor.demo_presence','off'),('light.demo_main','off'),
                         ('sensor.demo_shower_humidity','40'),('switch.demo_shower_fan','off'),('switch.demo_power','off')]:
        hass.states.set(entity,state,last_changed=moment)
    payload=_shower_payload(commands_enabled=True)
    payload['quietHours']={'start':'00:30','end':'07:00'}
    executor=PowerExecutor();executor.power_calls=[]
    runtime,_=_runtime_with_shadow(hass,payload,executor=executor,now_ms=lambda:clock[0])
    await runtime.start(hass,'entry')
    try:
        if phase=='before_pending': clock[0]+=2000
        else:
            original=runtime._persist_pending_power_dispatch
            async def crossed(**kwargs):
                result=await original(**kwargs)
                clock[0]+=2000
                return result
            runtime._persist_pending_power_dispatch=crossed
        powered,detail=await runtime._ensure_legacy_room_power_locked(
            'switch.demo_power',at=int(moment.timestamp()*1000),room_id=payload['roomId'],
            operation_id='quiet-power',config_version=1,command_action='turn_on')
        assert not powered
        assert executor.power_calls==[]
        assert detail['reason']=='quiet_hours'
    finally:
        await runtime.stop()


@pytest.mark.parametrize('action,reason,blocked',[
    ('turn_on','presence',True),('set_brightness','schedule',True),
    ('set_color_temperature','lux',True),('turn_off','schedule',False),
    ('turn_off','away',False),('set_brightness','dimming',False),
])
def test_fresh_dispatch_guard_preserves_off_and_absence_dimming(action,reason,blocked):
    from custom_components.hausman_hub.application.room_lighting_runtime import RoomLightingRuntime
    from custom_components.hausman_hub.domain.room_lighting_engine import PlannedCommand,DecisionReason
    runtime=RoomLightingRuntime.__new__(RoomLightingRuntime)
    cfg=config();runtime._configs={cfg.room_id:cfg};runtime._hass=None
    runtime._now_ms=lambda:_at(3)
    command=PlannedCommand('light_main',LightAction(action),reason=DecisionReason(reason))
    assert runtime._quiet_hours_blocks_command(cfg,command) is blocked
