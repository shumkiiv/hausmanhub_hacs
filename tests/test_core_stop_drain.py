"""B06: real file-backed branch, controlled runtime event shutdown order."""
import asyncio
from contextlib import suppress
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_core_lighting_setup import (
    _start_with_image, _seed_registered_startup_image, _config_payload,
    _StartupHass, _startup_runtime, _ConfigService, _SpyExecutor,
    RoomLightingRuntime,RoomLightingShadowService,_MemoryShadowStore,_NOW_MS,AWAY_ENTITY_ID,
)
from custom_components.hausman_hub import _async_start_room_lighting_with_core
from custom_components.hausman_hub.application.core_manual_input import CoreManualInput,synthetic_binary_light_profiles
from custom_components.hausman_hub.adapters.core.control_store import FileControlStore
from custom_components.hausman_hub.domain.core.models import LightStateTarget
from custom_components.hausman_hub.application.room_lighting_core import CoreBranchConfig,RoomLightingCoreBranch
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from test_core_local_handoff_matrix import closed_registration


def sensor_event():
    return SimpleNamespace(data=dict(entity_id='binary_sensor.test_presence',
        old_state=SimpleNamespace(state='off',attributes={}),
        new_state=SimpleNamespace(state='on',attributes={})))


async def started(tmp_path):
    expected={}
    def writer(directory,config):
        source=RoomLightingCoreBranch(branch_config=CoreBranchConfig(room_id=config.room_id,
            config=config,device_profiles=synthetic_binary_light_profiles(['test_main'])),
            store_path=directory/'manual-source.json')
        receipt=source.admit_manual(resource_id='test_main',request_id='stop-drain-manual',
            action_id='turn_off',target=LightStateTarget(on=False,brightness_pct=None,color_temperature_k=None),
            source='owner',expected_control_revision=source.core_service.record('test_main').control_revision,
            expected_binding_revision=config.version,receipt_scope='owner')
        expected['receipt']=receipt
        expected['manual']=source.core_service.record('test_main').manual
        source.on_runtime_stop()
        path=directory/f'hausman_hub.core_shadow_{config.room_id}.json'
        destination=RoomLightingCoreBranch(branch_config=CoreBranchConfig(room_id=config.room_id,config=config),store_path=path)
        registration=closed_registration(source,config)
        registration=replace(registration,targets=tuple(replace(target,target_id='public-startup-main') for target in registration.targets))
        destination.core_service.close_dispatch_for_restore()
        destination.import_closed_registration_from(source.core_service,registration,
            expected_commit_revision=destination.core_service._store.commit_revision,
            expected_source_revision=source.core_service._store.commit_revision)
        assert not source.physical_send_enabled() and not destination.physical_send_enabled()
        return path
    result=await _start_with_image(tmp_path,expected=True,image_writer=writer)
    branch=result[-1]
    assert expected['manual'] is not None
    assert branch.core_service.record('test_main').manual==expected['manual']
    assert branch.core_service.receipt('stop-drain-manual',receipt_scope='owner')==expected['receipt']
    return result


@pytest.mark.asyncio
async def test_async_stop_waits_for_real_branch_worker_and_has_no_late_file_writes(tmp_path):
    config,hass,intake,executor,runtime,hooks,branch=await started(tmp_path)
    original_manual=branch.core_service.record('test_main').manual
    loop=asyncio.get_running_loop()
    entered,stop_entered=asyncio.Event(),asyncio.Event()
    release=threading.Event()
    calls=[]
    class Port:
        def __call__(self,*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3),'test worker was not released'
            calls.append(args[0])
            branch(*args)
        def on_runtime_stop(self): branch.on_runtime_stop()
    runtime.set_core_input_port(Port())
    runtime._state_event(sensor_event())
    await asyncio.wait_for(entered.wait(),1)
    async def stop():
        stop_entered.set()
        await runtime.stop()
    task=asyncio.create_task(stop())
    try:
        await stop_entered.wait()
        assert not task.done() and runtime.running
        release.set()
        await asyncio.wait_for(task,2)
        path=tmp_path/f'hausman_hub.core_shadow_{config.room_id}.json'
        durable=path.read_bytes()
        assert not runtime.running and calls==['binary_sensor.test_presence']
        assert not any(not task.done() for task in runtime._core_input_tasks)
        assert branch.core_service.record('test_main').manual==original_manual
        runtime._state_event(sensor_event())
        await asyncio.gather(*hass.tasks,return_exceptions=True)
        assert path.read_bytes()==durable
        assert FileControlStore(path).load()['records']['test_main'].manual==original_manual
        assert not intake.registration_ready('public-startup-main')
        assert executor.calls==[] and not branch.physical_send_enabled()
    finally:
        release.set()
        await runtime.stop()


@pytest.mark.asyncio
async def test_sync_unload_drops_captured_not_started_input_then_explicitly_drains(tmp_path):
    config,hass,intake,executor,runtime,hooks,branch=await started(tmp_path)
    pending=[]
    original_create=hass.async_create_task
    def defer(coroutine):
        pending.append(coroutine)
        return None
    hass.async_create_task=defer
    runtime._state_event(sensor_event())
    assert pending
    runtime.close_and_drain_core_input()
    runtime.cancel()
    path=tmp_path/f'hausman_hub.core_shadow_{config.room_id}.json'
    closed=path.read_bytes()
    hass.async_create_task=original_create
    try:
        await asyncio.gather(*(asyncio.create_task(item) for item in pending))
        await runtime.stop()
        await asyncio.gather(*hass.tasks,return_exceptions=True)
        assert path.read_bytes()==closed
        assert not any(not task.done() for task in runtime._core_input_tasks)
        assert not intake.registration_ready('public-startup-main')
        assert executor.calls==[] and not branch.physical_send_enabled()
    finally:
        for item in pending:
            with suppress(RuntimeError): item.close()
        await runtime.stop()


@pytest.mark.asyncio
async def test_old_runtime_callback_cannot_enter_reloaded_branch(tmp_path):
    config,hass,intake,executor,runtime,hooks,branch=await started(tmp_path)
    original_manual=branch.core_service.record('test_main').manual
    stale_callback=runtime._state_event
    await runtime.stop()
    new_intake=CoreManualInput()
    new_executor=_SpyExecutor()
    new_runtime=_startup_runtime(hass,config,new_intake,new_executor)
    new_hooks=[]
    new_branch=await _async_start_room_lighting_with_core(hass,
        SimpleNamespace(entry_id='reloaded',async_on_unload=new_hooks.append),
        room_lighting_service=_ConfigService(config),room_lighting_runtime=new_runtime,
        core_manual_input=new_intake,domain_data={},core_shadow_room_id=config.room_id,
        mutation_lock=asyncio.Lock(),power_dependency_resolver=lambda:{},expected_closed_image=True)
    try:
        path=tmp_path/f'hausman_hub.core_shadow_{config.room_id}.json'
        before=path.read_bytes()
        stale_callback(sensor_event())
        await asyncio.gather(*hass.tasks,return_exceptions=True)
        assert path.read_bytes()==before
        assert new_branch is not branch and new_runtime.running and not runtime.running
        assert new_branch.core_service.record('test_main').manual==original_manual
        assert new_intake.registration_ready('public-startup-main')
        assert not intake.registration_ready('public-startup-main')
        assert executor.calls==new_executor.calls==[]
    finally:
        await new_runtime.stop()


@pytest.mark.asyncio
async def test_real_subscription_callbacks_are_unsubscribed_once_and_stale_callbacks_drop(tmp_path):
    config=config_from_payload(_config_payload())
    path=_seed_registered_startup_image(tmp_path,config)
    hass,intake,executor=_StartupHass(tmp_path),CoreManualInput(),_SpyExecutor()
    callbacks,unsubscribed=[],[]
    def subscribe(kind,callback):
        index=len(callbacks)
        callbacks.append((kind,callback))
        def unsubscribe(): unsubscribed.append(index)
        return unsubscribe
    runtime=RoomLightingRuntime(hass,_ConfigService(config),
        RoomLightingShadowService(_MemoryShadowStore()),executor=executor,now_ms=lambda:_NOW_MS,
        track_state_changes=lambda hass,entities,callback:subscribe(tuple(entities),callback),
        track_interval=lambda hass,callback,interval:subscribe('clock',callback),
        listen_bus=lambda hass,kind,callback:subscribe(kind,callback),
        reserved_entity_ids_provider=intake.command_entity_ids,away_entity_id=AWAY_ENTITY_ID)
    hooks=[]
    branch=await _async_start_room_lighting_with_core(hass,
        SimpleNamespace(entry_id='subscriptions',async_on_unload=hooks.append),
        room_lighting_service=_ConfigService(config),room_lighting_runtime=runtime,
        core_manual_input=intake,domain_data={},core_shadow_room_id=config.room_id,
        mutation_lock=asyncio.Lock(),power_dependency_resolver=lambda:{},expected_closed_image=True)
    await runtime.stop()
    assert callbacks and sorted(unsubscribed)==list(range(len(callbacks)))
    durable=path.read_bytes()
    for kind,callback in callbacks:
        if isinstance(kind,tuple) and 'binary_sensor.test_presence' in kind:
            callback(sensor_event())
    await runtime.stop()
    await asyncio.gather(*hass.tasks,return_exceptions=True)
    assert sorted(unsubscribed)==list(range(len(callbacks)))
    assert path.read_bytes()==durable and executor.calls==[]
    assert not branch.physical_send_enabled()
