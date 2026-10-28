"""Non-topology config edits must not permanently poison the authority graph."""
from dataclasses import replace
import pytest

from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from tests.test_room_lighting_service import _MemoryStore
from tests.test_room_lighting_engine import _config


@pytest.mark.parametrize('change',['name','quiet','commands','reorder','target_id'])
async def test_same_physical_room_scope_never_invalidates(change):
    store=_MemoryStore();calls=[]
    service=RoomLightingService(store,topology_invalidator=lambda:calls.append('closed'))
    saved=await service.async_put_config(_config())
    calls.clear();payload=saved.to_dict()
    if change=='name': payload['name']='Новое имя'
    elif change=='quiet': payload['quietHours']={'start':'00:30','end':'07:00'}
    elif change=='commands': payload['commandsEnabled']=not payload.get('commandsEnabled',False)
    elif change=='reorder': payload['devices']['light_targets'].reverse()
    else:
        payload['devices']['light_targets'][1]['id']='renamed_mirror'
    await service.async_put_config(config_from_payload(payload),expected_version=saved.version)
    assert calls==[]


@pytest.mark.parametrize('change',['replace','remove','add'])
async def test_changed_physical_scope_still_invalidates_before_save(change):
    store=_MemoryStore();calls=[]
    service=RoomLightingService(store,topology_invalidator=lambda:calls.append('closed'))
    saved=await service.async_put_config(_config());calls.clear()
    payload=saved.to_dict();targets=payload['devices']['light_targets']
    if change=='replace': targets[1]['entityId']='light.new_mirror'
    elif change=='remove': targets.pop()
    else: targets.append({**targets[1],'id':'third','entityId':'light.third'})
    original=store.async_upsert
    async def upsert(config):
        assert calls==['closed']
        return await original(config)
    store.async_upsert=upsert
    await service.async_put_config(config_from_payload(payload),expected_version=saved.version)
    assert calls==['closed']


async def test_creation_and_deletion_remain_conservative():
    store=_MemoryStore();calls=[]
    service=RoomLightingService(store,topology_invalidator=lambda:calls.append('closed'))
    config=await service.async_put_config(_config());assert calls==['closed']
    assert await service.async_delete_config(config.room_id) is True
    assert calls==['closed','closed']


async def test_actual_authority_graph_and_existing_manual_hold_survive_quiet_put():
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
    from tests.test_manual_light_authority import _Store
    authority=ManualLightAuthority(_Store())
    await authority.async_load()
    config=_config();store=_MemoryStore();store.configs[config.room_id]=config
    graph=RelatedLightSources.from_configs((config,));authority.set_related_sources(graph)
    assert await authority.async_capture('light.demo_main','on','manual','before-quiet')
    record=authority.record('light.demo_main')
    service=RoomLightingService(store,topology_invalidator=authority.invalidate_related_sources)
    payload=config.to_dict();payload['quietHours']={'start':'00:30','end':'07:00'}
    saved=await service.async_put_config(config_from_payload(payload),expected_version=config.version)
    authority.set_related_sources(RelatedLightSources.from_configs((saved,)))
    assert authority._related_sources==graph and authority._related_sources.ready
    assert authority.record('light.demo_main')==record
    assert authority.blocks('light.demo_main')


@pytest.mark.parametrize('failure',[False,True])
async def test_topology_save_failure_never_opens_graph(failure):
    store=_MemoryStore();calls=[]
    service=RoomLightingService(store,topology_invalidator=lambda:calls.append('closed'))
    saved=await service.async_put_config(_config());calls.clear()
    payload=saved.to_dict();payload['devices']['light_targets'][1]['entityId']='light.replaced'
    async def reject(config):
        assert calls==['closed']
        if failure: raise OSError('synthetic save failed')
        return False
    store.async_upsert=reject
    with pytest.raises((OSError,RuntimeError)):
        await service.async_put_config(config_from_payload(payload),expected_version=saved.version)
    assert calls==['closed'] and store.configs[saved.room_id]==saved
