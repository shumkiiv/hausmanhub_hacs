"""Only the owner's two rooms, fake radio frames and GET transport."""
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_observations import ROLLOUT_BINDINGS
from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import LightingMqttRefresh, SUPPORTED_ENTITIES
from custom_components.hausman_hub.application.lighting_observation import physical_brightness_observed_ms, physical_color_observed_ms
from custom_components.hausman_hub.application.power_evidence import trusted_target_state
from tests.test_lighting_mqtt_observations import make_case, report, feed

CORRIDOR_POWER = 'switch.0x603d61fffe759363_1'
CORRIDOR = 'light.0xa4c138d69d102803'
SHOWER = ('switch.0x603d61fffe767766_1', 'switch.0xacbac0fffebbe3c4_1', 'switch.0xa4c1385af46163eb')
NEW = (CORRIDOR_POWER, CORRIDOR, *SHOWER)
CHANNELS = ((CORRIDOR_POWER, 'Выключатель малый коридор', 'state_1'),
            (CORRIDOR, 'Люстра малый коридор', 'state'),
            (SHOWER[0], 'Выключатель душевая', 'state_1'),
            (SHOWER[1], 'Выключатель душевая доп свет', 'state_1'),
            (SHOWER[2], 'Подсветка шкафа душевая', 'state'))


def channel(entity):
    return next(i for i, b in enumerate(ROLLOUT_BINDINGS) if b.entity_id == entity)


def test_exact_new_registry_bindings_not_fan_or_power():
    rows = {b.entity_id: (b.ieee, b.endpoint, b.unique_id) for b in ROLLOUT_BINDINGS}
    assert set(rows) == SUPPORTED_ENTITIES | set(NEW)
    assert rows[CORRIDOR_POWER] == ('0x603d61fffe759363', 1, '0x603d61fffe759363_switch_1_zigbee2mqtt')
    assert rows[CORRIDOR] == ('0xa4c138d69d102803', 1, '0xa4c138d69d102803_light_zigbee2mqtt')
    assert rows[SHOWER[0]] == ('0x603d61fffe767766', 1, '0x603d61fffe767766_switch_1_zigbee2mqtt')
    assert rows[SHOWER[1]] == ('0xacbac0fffebbe3c4', 1, '0xacbac0fffebbe3c4_switch_1_zigbee2mqtt')
    assert rows[SHOWER[2]] == ('0xa4c1385af46163eb', 1, '0xa4c1385af46163eb_switch_zigbee2mqtt')


@pytest.mark.parametrize('entity', NEW)
@pytest.mark.parametrize('bad', [None, 'retained', 'registry', 'native', 'endpoint', 'stale', 'gap', 'disconnect'])
def test_only_fresh_bound_physical_channel_is_trusted(entity, bad):
    port, native, clock = make_case()
    assert entity in native, 'new target must be wrapped, never native fallback'
    body = report(clock, channel=channel(entity))
    if bad == 'registry': port._enabled -= {entity}
    if bad == 'native': native[entity].state = 'off'
    if bad == 'endpoint': body['endpoint'] = 2
    if bad == 'stale': body['observed_at_ms'] -= 300_001
    feed(port, body, retained=bad == 'retained')
    if bad == 'gap':
        clock['now'] += 1
        feed(port, report(clock, channel=channel(entity), session=body['session'], sequence=3))
    if bad == 'disconnect': port.connection_changed(False)
    assert trusted_target_state(port.read_state(entity), now_ms=clock['now']) == ('on' if bad is None else None)


@pytest.mark.parametrize('source,attribute,value,attrs,checker', [
    ('physical_brightness', 'genLevelCtrl.currentLevel', 64, {'brightness':64}, physical_brightness_observed_ms),
    ('physical_color', 'lightingColorCtrl.colorTemperature', 300, {'color_temp_kelvin':3333}, physical_color_observed_ms),
])
def test_corridor_properties_do_not_refresh_onoff(source, attribute, value, attrs, checker):
    port, native, clock = make_case(attributes=attrs)
    assert CORRIDOR in native
    feed(port, report(clock, channel=channel(CORRIDOR), source=source, attribute=attribute, value=value))
    view = port.read_state(CORRIDOR)
    assert checker(view, now_ms=clock['now']) == clock['now']
    assert trusted_target_state(view, now_ms=clock['now']) is None


@pytest.mark.parametrize('scope', [frozenset({CORRIDOR_POWER, CORRIDOR}), frozenset(SHOWER)])
@pytest.mark.parametrize('bad', [None, 'manual', 'retained', 'error', 'gap', 'disconnect'])
async def test_exact_room_gets_do_not_write_or_grant_authority(scope, bad):
    port, native, clock = make_case()
    calls, current, session = [], [True], str(uuid4())
    async def publish(topic, payload):
        calls.append((topic, json.loads(payload)))
        entity, _, _ = next(row for row in CHANNELS if (f'zigbee2mqtt/{row[1]}/get', {row[2]:''}) == calls[-1])
        if bad == 'error': raise OSError('offline')
        if bad == 'manual': current[0] = False
        if bad == 'disconnect': port.connection_changed(False)
        clock['now'] += 10
        feed(port, report(clock, channel=channel(entity), session=session,
                         sequence=len(calls) + (1 if bad == 'gap' and len(calls) == 2 else 0)),
             retained=bad == 'retained')
    async def pause(_): current[0] = False
    adapter = LightingMqttRefresh(port, publish, now_ms=lambda:clock['now'], pause=pause)
    assert await adapter(scope, lambda:current[0]) is (bad is None or bad == 'gap' and len(scope) == 1)
    assert len(calls) == len(scope) if bad is None else len(calls) <= len(scope)
    assert all(t.endswith('/get') and list(p.values()) == [''] for t,p in calls)


async def test_partial_mixed_foreign_and_fan_scopes_never_publish():
    port, _, clock = make_case()
    calls = []
    async def publish(*args): calls.append(args)
    adapter = LightingMqttRefresh(port, publish, now_ms=lambda:clock['now'])
    for scope in (frozenset(), frozenset(SHOWER[:2]), frozenset(NEW),
                  frozenset({CORRIDOR,SHOWER[0]}), frozenset({'switch.0xacbac0fffebbe3c4_2'}),
                  SUPPORTED_ENTITIES | {CORRIDOR}):
        assert not await adapter(scope, lambda:True)
    assert calls == []


@pytest.mark.parametrize('entity', tuple(entity for entity in NEW if entity != CORRIDOR_POWER))
async def test_single_off_confirmation_get_for_exact_target_only(entity):
    port, native, clock = make_case('off')
    assert entity in native
    calls = []
    async def publish(topic, payload):
        calls.append((topic,json.loads(payload)))
        clock['now'] += 10
        feed(port,report(clock,channel=channel(entity),value='OFF'))
    adapter = LightingMqttRefresh(port,publish,now_ms=lambda:clock['now'])
    assert await adapter.refresh_confirmation(entity,lambda:True)
    row=next(r for r in CHANNELS if r[0]==entity)
    assert calls == [(f'zigbee2mqtt/{row[1]}/get',{row[2]:''})]


def test_real_producer_exact_endpoints_and_property_scope():
    path=Path(__file__).parents[1]/'tools/zigbee2mqtt/hausman_light_reports.mjs'
    script='''import {physicalOnOff,physicalBrightness,physicalColorTemperature} from %s;
const now=1792680000000;
const frame=(ieee,ep=1)=>({device:{ieeeAddr:ieee},endpoint:{ID:ep},type:'readResponse',cluster:'genOnOff',data:{onOff:1},meta:{zclTransactionSequenceNumber:1,rawData:Buffer.from([1])}});
const ids=['0xa4c138d69d102803','0x603d61fffe767766','0xacbac0fffebbe3c4','0xa4c1385af46163eb'];
const yes=ids.map(id=>!!physicalOnOff(frame(id),now));
const no=ids.map(id=>!!physicalOnOff(frame(id,2),now));
const levels=ids.map(id=>!!physicalBrightness({...frame(id),cluster:'genLevelCtrl',data:{currentLevel:64}},now));
const colors=ids.map(id=>!!physicalColorTemperature({...frame(id),cluster:'lightingColorCtrl',data:{colorTemperature:300}},now));
console.log(JSON.stringify({yes,no,levels,colors}));
''' % json.dumps(path.as_uri())
    result=subprocess.run(['node','--input-type=module','-e',script],capture_output=True,text=True,timeout=5,check=True)
    assert json.loads(result.stdout)=={'yes':[True]*4,'no':[False]*4,'levels':[True,False,False,False],'colors':[True,False,False,False]}
