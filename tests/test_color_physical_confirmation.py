"""CCT evidence must never refresh ON/OFF, brightness or manual authority."""
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
    ObservedLightState, PhysicalLightObservation, PhysicalColorObservation,
    physical_color_observed_ms, physical_brightness_observed_ms,
)
from custom_components.hausman_hub.application.power_evidence import trusted_target_state
from custom_components.hausman_hub.application.room_lighting_runtime import RoomLightingRuntime
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction, PlannedCommand

ENTITY = 'light.liustra_tambur'


async def test_fresh_onoff_and_new_native_color_cannot_substitute_cct_report(monkeypatch):
    clock,own,cache,calls,hass,executor,report=command_fixture.harness(monkeypatch)
    cache['light.main']=command_fixture.state('on',at=clock.now_ms()-1000)
    def reader(entity):
        native=cache[entity]; native.entity_id=entity
        onoff=PhysicalLightObservation(entity,'on',clock.now_ms(),'session',7,'ieee',1)
        return ObservedLightState(native,onoff)
    executor._state_reader=reader
    clock.on_sleep=report
    receipt=await executor.execute(hass,PlannedCommand('main',LightAction.SET_COLOR_TEMPERATURE,color_temperature=4000))
    assert receipt.sent and not receipt.confirmed and len(calls)==1


def color_report(clock, **changes):
    return input_fixture.report(clock, channel=3, source='physical_color',
        attribute='lightingColorCtrl.colorTemperature', value=370, **changes)


@pytest.mark.parametrize('bad', [None,'retained','wrong_attribute','switch','bool','low','high','fraction','stale','future','native','gap','registry','bridge','nan'])
def test_color_evidence_does_not_renew_other_properties(bad):
    port, native, clock = input_fixture.make_case(attributes={'color_temp_kelvin':2702,'brightness':64})
    body = color_report(clock)
    if bad == 'wrong_attribute': body['attribute']='genOnOff.onOff'
    if bad == 'switch': body['ieee']=input_fixture.ROLLOUT_BINDINGS[0].ieee
    if bad == 'bool': body['value']=True
    if bad == 'low': body['value']=152
    if bad == 'high': body['value']=501
    if bad == 'fraction': body['value']=370.1
    if bad == 'stale': body['observed_at_ms']-=300001
    if bad == 'future': body['observed_at_ms']+=1
    if bad == 'native': native[ENTITY].attributes['color_temp_kelvin']=4000
    if bad == 'nan': native[ENTITY].attributes['color_temp_kelvin']=float('nan')
    input_fixture.feed(port,body,retained=bad=='retained')
    if bad == 'gap':
        clock['now']+=1
        input_fixture.feed(port,color_report(clock,session=body['session'],sequence=3))
    if bad == 'registry': port._enabled=frozenset()
    if bad == 'bridge': port.invalidate('bridge_boundary')
    state=port.read_state(ENTITY)
    assert (physical_color_observed_ms(state,now_ms=clock['now']) is not None) is (bad is None)
    assert state.observation is None and state.brightness_observation is None
    assert trusted_target_state(state,now_ms=clock['now']) is None
    assert physical_brightness_observed_ms(state,now_ms=clock['now']) is None


@pytest.mark.parametrize('bad',[None,'absent','stale','future','same','wrong_value','wrong_entity','foreign','user','manual_hold','cached','off','turn_on','brightness'])
async def test_cct_needs_new_physical_color_and_exact_causal_reply(monkeypatch,bad):
    clock,own,cache,calls,hass,executor,report=command_fixture.harness(monkeypatch)
    old_onoff=PhysicalLightObservation('light.main','on',clock.now_ms()-400000,'session',1,'ieee',1)
    old_color=PhysicalColorObservation('light.main',250,clock.now_ms()-1000,'session',2,'ieee',1)
    current=[old_color]
    cache['light.main']=command_fixture.state('on',at=clock.now_ms()-1000,attributes={'color_temp_kelvin':4000})
    def reader(entity):
        native=cache[entity]; native.entity_id=entity
        return ObservedLightState(native,old_onoff,None,current[0])
    executor._state_reader=reader
    blocked=[False]
    executor._manual_light_authority=SimpleNamespace(blocks=lambda entity:blocked[0])
    def reply():
        context=own
        if bad=='foreign': context=SimpleNamespace(id='foreign',parent_id=None)
        if bad=='user': context=SimpleNamespace(id=own.id,parent_id=None,user_id='human')
        attrs={'color_temp_kelvin':2702}
        if bad=='cached': attrs['cached']=True
        report(context=context,value='off' if bad=='off' else 'on',attributes=attrs)
        stamp=clock.now_ms()
        if bad=='stale': stamp=command_fixture.BASE_MS-1
        if bad=='future': stamp+=1000
        current[0]=PhysicalColorObservation('light.other' if bad=='wrong_entity' else 'light.main',
            250 if bad=='wrong_value' else 370,stamp,'session',3,'ieee',1)
        if bad=='absent': current[0]=None
        if bad=='same': current[0]=old_color
        if bad=='manual_hold': blocked[0]=True
    clock.on_sleep=reply
    action=LightAction.TURN_ON if bad=='turn_on' else LightAction.SET_BRIGHTNESS if bad=='brightness' else LightAction.SET_COLOR_TEMPERATURE
    result=await executor.execute(hass,PlannedCommand('main',action,color_temperature=2702))
    assert result.confirmed is (bad is None)
    assert result.sent and len(calls)==1
    assert old_onoff.observed_at_ms==command_fixture.BASE_MS-400000


@pytest.mark.parametrize('previous,action,new_color,overflow,expected',[
    ('set_color_temperature',LightAction.SET_COLOR_TEMPERATURE,True,False,False),
    ('turn_on',LightAction.SET_COLOR_TEMPERATURE,True,False,True),
    ('set_brightness',LightAction.SET_COLOR_TEMPERATURE,True,False,True),
    ('set_color_temperature',LightAction.SET_BRIGHTNESS,True,False,True),
    ('set_color_temperature',LightAction.TURN_ON,True,False,True),
    ('set_color_temperature',LightAction.SET_COLOR_TEMPERATURE,False,False,True),
    (None,LightAction.SET_COLOR_TEMPERATURE,True,True,True),
])
def test_color_releases_only_failed_cct(previous,action,new_color,overflow,expected):
    runtime=object.__new__(RoomLightingRuntime)
    anchor=command_fixture.BASE_MS
    native=SimpleNamespace(entity_id=ENTITY,state='on',attributes={'color_temp_kelvin':2702})
    color=PhysicalColorObservation(ENTITY,370,anchor+100 if new_color else anchor,'session',2,'ieee',1)
    view=ObservedLightState(native,None,None,color)
    key=('room','main')
    runtime._dispatch_failures={} if previous is None else {key:(previous,'untrusted:on',anchor)}
    runtime._dispatch_overflow_latch=overflow
    runtime._dispatch_overflow_blocked_after_ms=anchor
    runtime._now_ms=lambda:anchor+100
    runtime._entity_state_object=lambda entity:view
    runtime._target_evidence_revision=lambda entity:'untrusted:on'
    runtime._proven_report_ms=lambda entity:None
    assert runtime._target_attempt_blocked(key,ENTITY,action) is expected
    assert bool(runtime._dispatch_failures) is (previous is not None and expected)


async def test_actual_javascript_color_to_consumer_to_inverted_executor(monkeypatch):
    clock,own,cache,calls,hass,executor,_=command_fixture.harness(monkeypatch)
    attrs={'color_temp_kelvin':2702,'min_color_temp_kelvin':2000,'max_color_temp_kelvin':6535}
    cache[ENTITY]=command_fixture.state('on',at=clock.now_ms()-400000,attributes=attrs)
    cache[ENTITY].entity_id=ENTITY
    port=input_fixture.LightingMqttObservations(cache.get,now_ms=clock.now_ms)
    port._enabled=frozenset({ENTITY}); port._started=True; port.connection_changed(True)
    executor._state_reader=port.read_state
    executor._targets['main']=replace(command_fixture.target(),entity_id=ENTITY,color_temp_inverted=True)
    producer=Path(__file__).resolve().parents[1]/'tools/zigbee2mqtt/hausman_light_reports.mjs'
    script='import {physicalColorTemperature} from '+json.dumps(producer.as_uri())+';\n'+(
        "const r=physicalColorTemperature({type:'readResponse',cluster:'lightingColorCtrl',"
        "device:{ieeeAddr:'0xa4c138784e5cbcd1'},endpoint:{ID:1},data:{colorTemperature:340},"
        "meta:{zclTransactionSequenceNumber:11,rawData:Buffer.from([24,11,1,7,0,0,33,84,1])}},"
        +str(clock.now_ms()+250)+'); console.log(JSON.stringify({...r,session:'
        +json.dumps(str(uuid4()))+',sequence:1}));')
    raw=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,check=True,timeout=10).stdout
    def reply():
        cache[ENTITY]=command_fixture.state('on',context=own,at=clock.now_ms(),attributes={**attrs,'color_temp_kelvin':2941})
        cache[ENTITY].entity_id=ENTITY
        port.receive(input_fixture.REPORT_TOPIC,raw,retained=False)
    clock.on_sleep=reply
    receipt=await executor.execute(hass,PlannedCommand('main',LightAction.SET_COLOR_TEMPERATURE,color_temperature=3200))
    assert receipt.confirmed and len(calls)==1
    assert trusted_target_state(port.read_state(ENTITY),now_ms=clock.now_ms()) is None
    assert port.read_state(ENTITY).brightness_observation is None
