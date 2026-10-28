"""S-02: HA snapshots are not a physical device report.

All events and the binary capability passport are synthetic. The actual
production branch receives the events and its real closed transport is kept.
"""
from types import SimpleNamespace

import pytest

from test_core_local_handoff_matrix import branch_at
from custom_components.hausman_hub.domain.core.models import Evidence,LightStateTarget,Quality
from custom_components.hausman_hub.adapters.core.room_lighting import RoomLightingInputAdapter,EntityBinding
from custom_components.hausman_hub.adapters.core.clock import SystemClock


def state(value,**attributes):
    return SimpleNamespace(state=value,attributes=attributes)


@pytest.mark.parametrize('case', ['changed','optimistic','retained','reconnect','repeat','attribute-only'])
def test_ordinary_ha_snapshot_is_never_labelled_device_report(tmp_path,case):
    branch,path,config=branch_at(tmp_path,'provenance-'+case)
    branch.activate_shadow()
    before=state('off')
    after=state('on')
    if case=='optimistic': after.attributes['assumed_state']=True
    if case=='retained':
        before=None
        after.attributes['restored']=True
    if case=='reconnect': before=None
    if case=='repeat': before=state('on')
    if case=='attribute-only':
        before=state('on',battery=70)
        after=state('on',battery=71)
    branch.handle_runtime_event('light.test_main',before,after)
    facts=branch.resource_view('test_main').facts
    fact=next(fact for fact in facts if fact.property=='on')
    # Trace the reachable manual path before checking provenance, without
    # inventing any operation confirmation or injecting a trusted Observation.
    receipt=branch.admit_manual(resource_id='test_main',request_id='ha-'+case,
        action_id='turn_on',target=LightStateTarget(on=True),source='owner',
        expected_control_revision=branch.core_service.record('test_main').control_revision,
        expected_binding_revision=config.version,receipt_scope='owner')
    assert branch.core_service.record('test_main').manual is not None
    assert branch.core_service.record('test_main').auto_proof is None
    assert not branch.physical_send_enabled()
    assert fact.evidence is not Evidence.DEVICE_REPORT, (case,fact.quality,receipt.status)
    expected_evidence={'optimistic':Evidence.OPTIMISTIC,'retained':Evidence.RESTORED}.get(case,Evidence.ADAPTER_STATE)
    assert fact.evidence is expected_evidence
    assert fact.quality is (Quality.FRESH if case=='changed' else Quality.STALE)
    if case=='changed': assert receipt.status.name=='NO_OP'
    else: assert receipt.status.name=='ACCEPTED'


@pytest.mark.parametrize('value',['unknown','unavailable'])
@pytest.mark.parametrize('restored',[False,True])
def test_invalid_switching_snapshot_never_proves_off(tmp_path,value,restored):
    branch,_,_=branch_at(tmp_path,'invalid-'+value+str(restored))
    branch.activate_shadow()
    branch.handle_runtime_event('light.test_main',state('on'),state(value,restored=restored))
    fact=next(fact for fact in branch.resource_view('test_main').facts if fact.property=='on')
    assert fact.quality is Quality.INVALID
    assert fact.evidence is not Evidence.DEVICE_REPORT
    receipt=branch.admit_manual(resource_id='test_main',request_id='invalid-off',action_id='turn_off',
        target=LightStateTarget(on=False),source='owner',
        expected_control_revision=branch.core_service.record('test_main').control_revision,
        expected_binding_revision=1,receipt_scope='owner')
    assert receipt.status.name=='ACCEPTED'
    assert not branch.physical_send_enabled() and branch.core_service.record('test_main').auto_proof is None


@pytest.mark.parametrize('property_name,value,attributes,expected',[
    ('occupancy','on',{},True),('brightness_pct','on',{'brightness':128},50)])
def test_non_switching_properties_keep_value_and_adapter_freshness(property_name,value,attributes,expected):
    adapter=RoomLightingInputAdapter({'sensor.test':EntityBinding('synthetic',property_name)},SystemClock(),'synthetic.epoch')
    observation=adapter.observation('sensor.test',None,state(value,**attributes))
    assert observation.value==expected and observation.quality is Quality.FRESH
    assert observation.evidence_kind is Evidence.ADAPTER_STATE
    assert observation.source_sequence is None and observation.binding_revision==0


def test_restored_and_assumed_flags_never_upgrade_evidence():
    adapter=RoomLightingInputAdapter({'light.test':EntityBinding('synthetic','on')},SystemClock(),'synthetic.epoch')
    observation=adapter.observation('light.test',state('off'),state('on',restored=True,assumed_state=True))
    assert observation.evidence_kind is Evidence.RESTORED and observation.quality is Quality.STALE


@pytest.mark.parametrize('property_name,unit,value_type', [
    ('on', 'bool', bool),
    ('occupancy', 'bool', bool),
    ('brightness_pct', 'percent', int),
])
def test_single_observation_removal_is_invalid_not_a_physical_report(property_name, unit, value_type):
    adapter = RoomLightingInputAdapter(
        {'light.test': EntityBinding('synthetic', property_name, unit)},
        SystemClock(), 'synthetic.epoch',
    )
    observation = adapter.observation('light.test', state('on', brightness=128), None)
    assert observation is not None
    assert observation.resource_id == 'synthetic'
    assert observation.property == property_name
    assert observation.unit == unit
    assert type(observation.value) is value_type
    assert observation.quality is Quality.INVALID
    assert observation.evidence_kind is Evidence.ADAPTER_STATE
    assert observation.source_sequence is None
    assert observation.binding_revision == 0
    assert observation.related_operation_id is None


def test_single_observation_unknown_entity_removal_is_ignored():
    adapter = RoomLightingInputAdapter(
        {'light.test': EntityBinding('synthetic', 'on')},
        SystemClock(), 'synthetic.epoch',
    )
    assert adapter.observation('light.not_bound', state('on', brightness=128), None) is None
