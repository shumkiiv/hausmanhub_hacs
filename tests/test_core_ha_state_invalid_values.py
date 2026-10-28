"""S-02 negatives: real branch sensor route versus adapter-only levels."""
import pytest

from test_core_ha_state_provenance import state
from test_core_local_handoff_matrix import branch_at
from custom_components.hausman_hub.adapters.core.room_lighting import RoomLightingInputAdapter,EntityBinding
from custom_components.hausman_hub.adapters.core.clock import SystemClock
from custom_components.hausman_hub.application.room_lighting_core import RoomLightingCoreBranch,CoreBranchConfig
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.core.models import Quality,LightStateTarget
from test_core_lighting_setup import _config_payload


@pytest.mark.parametrize('old',['on','off'])
@pytest.mark.parametrize('unavailable',['unknown','unavailable'])
def test_real_branch_invalid_presence_replaces_previous_fresh_fact(tmp_path,old,unavailable):
    branch,_,_=branch_at(tmp_path,'presence-invalid')
    branch.activate_shadow()
    branch.admit_manual(resource_id='test_main',request_id='keep-manual',action_id='turn_off',
        target=LightStateTarget(on=False),source='owner',
        expected_control_revision=branch.core_service.record('test_main').control_revision,
        expected_binding_revision=1,receipt_scope='owner')
    manual=branch.core_service.record('test_main').manual
    branch.handle_runtime_event('binary_sensor.test_presence',None,state(old))
    initial=next(fact for fact in branch.resource_view('sensor_presence').facts if fact.property=='occupancy')
    assert initial.quality is Quality.FRESH
    branch.handle_runtime_event('binary_sensor.test_presence',state(old),state(unavailable))
    current=next(fact for fact in branch.resource_view('sensor_presence').facts if fact.property=='occupancy')
    assert branch.core_service.record('test_main').manual==manual
    assert branch.core_service.record('test_main').auto_proof is None and not branch.physical_send_enabled()
    assert current.quality is Quality.INVALID,(old,unavailable,current)


@pytest.mark.parametrize('raw',[True,False,-1,256,128.5,float('nan'),float('inf'),float('-inf'),[],{}])
def test_adapter_only_invalid_level_is_not_fresh_or_an_unhandled_exception(raw):
    adapter=RoomLightingInputAdapter({'light.synthetic':EntityBinding('synthetic','brightness_pct','pct')},
        SystemClock(),'synthetic.epoch')
    observation=adapter.observation('light.synthetic',state('off'),state('on',brightness=raw))
    assert observation is not None and observation.quality is Quality.INVALID,(raw,observation)


@pytest.mark.parametrize('raw,expected',[(0,0),(128,50),(255,100),('128',50),(128.0,50)])
def test_adapter_only_supported_level_controls(raw,expected):
    adapter=RoomLightingInputAdapter({'light.synthetic':EntityBinding('synthetic','brightness_pct','pct')},
        SystemClock(),'synthetic.epoch')
    observation=adapter.observation('light.synthetic',state('off'),state('on',brightness=raw))
    assert observation.quality is Quality.FRESH and observation.value==expected


@pytest.mark.parametrize('raw',[True,False,-1,256,128.5,float('nan'),float('inf'),float('-inf'),[],{},None])
def test_adapter_to_service_explicit_bad_level_replaces_previous_fresh_fact(tmp_path,raw):
    # Adapter-to-service seam only, not the still-broken real HA brightness route.
    branch,_,_=branch_at(tmp_path,'bad-level')
    adapter=RoomLightingInputAdapter({'light.synthetic':EntityBinding('test_main','brightness_pct','pct')},
        SystemClock(),'synthetic.epoch')
    valid=adapter.observation('light.synthetic',state('off'),state('on',brightness=128))
    branch.core_service.ingest_observation(valid)
    assert next(f for f in branch.resource_view('test_main').facts if f.property=='brightness_pct').quality is Quality.FRESH
    invalid=adapter.observation('light.synthetic',state('off'),state('on',brightness=raw))
    branch.core_service.ingest_observation(invalid)
    assert next(f for f in branch.resource_view('test_main').facts if f.property=='brightness_pct').quality is Quality.INVALID
    assert branch.core_service.record('test_main').auto_proof is None
    assert not branch.physical_send_enabled()


def test_missing_level_does_not_invent_a_measurement():
    adapter=RoomLightingInputAdapter({'light.synthetic':EntityBinding('synthetic','brightness_pct','pct')},
        SystemClock(),'synthetic.epoch')
    assert adapter.observation('light.synthetic',state('off'),state('on')) is None


def test_real_dimmable_light_event_delivers_brightness_property(tmp_path):
    config=config_from_payload(_config_payload())
    branch=RoomLightingCoreBranch(branch_config=CoreBranchConfig(room_id=config.room_id,config=config),
        store_path=tmp_path/'dimmable.json')
    branch.activate_shadow()
    branch.handle_runtime_event('light.test_main',state('off',brightness=0),state('on',brightness=128))
    facts=branch.resource_view('test_main').facts
    assert any(fact.property=='brightness_pct' for fact in facts),facts
    assert not branch.physical_send_enabled()
