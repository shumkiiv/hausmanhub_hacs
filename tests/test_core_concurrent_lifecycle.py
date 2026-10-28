"""B06 startup serialisation on the real runtime and file-backed branch."""
import asyncio
from types import SimpleNamespace

import pytest

from test_core_lighting_setup import (
    _config_payload, _StartupHass, _startup_runtime, _ConfigService, _SpyExecutor,
    _seed_registered_startup_image, _seed_old_startup_image,
)
from custom_components.hausman_hub import _async_start_room_lighting_with_core
from custom_components.hausman_hub.application.core_manual_input import CoreManualInput
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
import custom_components.hausman_hub.application.room_lighting_core as branch_module
from custom_components.hausman_hub.adapters.core.control_store import FileControlStore


@pytest.mark.asyncio
@pytest.mark.parametrize('registered', [False, True])
async def test_concurrent_helpers_publish_only_one_branch(tmp_path, monkeypatch, registered):
    config = config_from_payload(_config_payload())
    seed = _seed_registered_startup_image if registered else _seed_old_startup_image
    path = seed(tmp_path, config)
    initial_bytes = path.read_bytes() if path.exists() else None
    config_service = _ConfigService(config)
    intake, executor = CoreManualInput(), _SpyExecutor()
    hass = _StartupHass(tmp_path)
    runtime = _startup_runtime(hass, config, intake, executor)
    domain_data, hooks, branches = {}, [], []
    entry = SimpleNamespace(entry_id='concurrent-startup', async_on_unload=hooks.append)
    first_entered, second_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_get = config_service.async_get_config
    reads = []

    async def get(room_id):
        reads.append(room_id)
        if len(reads) == 1:
            first_entered.set()
            await release.wait()
        return await original_get(room_id)

    config_service.async_get_config = get
    original_marker = intake.mark_closed_registration_unavailable
    markers = []

    def mark(**kwargs):
        original_marker(**kwargs)
        markers.append(kwargs)

    monkeypatch.setattr(intake, 'mark_closed_registration_unavailable', mark)
    original_branch = branch_module.RoomLightingCoreBranch

    class TracedBranch(original_branch):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            branches.append(self)

    monkeypatch.setattr(branch_module, 'RoomLightingCoreBranch', TracedBranch)
    lock = asyncio.Lock()
    async def start():
        return await _async_start_room_lighting_with_core(
            hass, entry, room_lighting_service=config_service,
            room_lighting_runtime=runtime, core_manual_input=intake,
            domain_data=domain_data, core_shadow_room_id=config.room_id,
            mutation_lock=lock, power_dependency_resolver=lambda: {},
            expected_closed_image=registered)

    first = asyncio.create_task(start())
    await first_entered.wait()
    async def second_start():
        second_entered.set()
        return await start()
    second = asyncio.create_task(second_start())
    await second_entered.wait()
    assert not first.done() and not second.done()
    assert len(markers) == 1
    release.set()
    try:
        results = await asyncio.gather(first, second)
        assert len(branches) == 1, f'constructed={len(branches)} hooks={len(hooks)} reads={len(reads)}'
        assert results[0] is results[1] is domain_data['core_lighting_branch']
        assert runtime.running and len(hooks) == 4
        assert all(not branch.physical_send_enabled() for branch in branches)
        assert executor.calls == []
        if registered:
            assert intake.command_entity_ids() == frozenset({'light.test_main'})
            assert intake.registration_ready('public-startup-main')
        else:
            assert intake.command_entity_ids() == frozenset()
            assert intake.registration_ready('legacy-neighbour')
        if registered:
            assert path.exists()
            assert config.room_id in FileControlStore(path).load()['closed_registrations']
        else:
            assert (path.read_bytes() if path.exists() else None) == initial_bytes
            if initial_bytes is None: assert FileControlStore(path).load() is None
    finally:
        await runtime.stop()
        for branch in branches:
            branch.on_runtime_stop()


@pytest.fixture
def prepared(tmp_path):
    config = config_from_payload(_config_payload())
    _seed_old_startup_image(tmp_path, config)
    intake, executor = CoreManualInput(), _SpyExecutor()
    hass = _StartupHass(tmp_path)
    runtime = _startup_runtime(hass, config, intake, executor)
    domain_data, hooks = {}, []
    service = _ConfigService(config)
    entry = SimpleNamespace(entry_id='late-concurrent-startup', async_on_unload=hooks.append)
    async def start():
        return await _async_start_room_lighting_with_core(
            hass, entry, room_lighting_service=service, room_lighting_runtime=runtime,
            core_manual_input=intake, domain_data=domain_data,
            core_shadow_room_id=config.room_id, mutation_lock=lock,
            power_dependency_resolver=lambda: {}, expected_closed_image=False)
    lock = asyncio.Lock()
    return SimpleNamespace(config=config,intake=intake,executor=executor,hass=hass,
        runtime=runtime,domain_data=domain_data,hooks=hooks,service=service,start=start)


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['before-start', 'after-running', 'owner-error', 'cancel-waiter'])
async def test_late_join_shares_completion_without_another_marker(prepared, stage):
    p=prepared
    entered, release = asyncio.Event(), asyncio.Event()
    original_start=p.runtime.start
    error=RuntimeError('controlled startup failure')
    async def delayed(*args):
        if stage=='after-running': await original_start(*args)
        entered.set()
        await release.wait()
        if stage=='owner-error': raise error
        if stage!='after-running': await original_start(*args)
    p.runtime.start=delayed
    owner=asyncio.create_task(p.start())
    await entered.wait()
    assert p.intake.registration_ready('legacy-neighbour')
    entered_second=asyncio.Event()
    async def join():
        entered_second.set()
        return await p.start()
    waiter=asyncio.create_task(join())
    await entered_second.wait()
    assert not owner.done() and not waiter.done()
    assert p.intake.registration_ready('legacy-neighbour')
    assert p.intake.command_entity_ids()==frozenset()
    if stage=='cancel-waiter':
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError): await waiter
        assert not owner.done()
    release.set()
    try:
        if stage=='owner-error':
            answers=await asyncio.gather(owner,waiter,return_exceptions=True)
            assert answers==[error,error]
            assert len(p.hooks)==3
        else:
            answer=await owner
            if stage!='cancel-waiter': assert await waiter is answer
            assert len(p.hooks)==4
            assert p.domain_data['core_lighting_branch'] is answer
        assert '_core_lighting_startup_in_flight' not in p.domain_data
        assert p.executor.calls==[]
    finally:
        await p.runtime.stop()


@pytest.mark.asyncio
async def test_owner_cancel_releases_record_without_automatic_restart(prepared):
    p=prepared
    entered, release=asyncio.Event(),asyncio.Event()
    async def pending_get(room_id):
        entered.set()
        await release.wait()
    p.service.async_get_config=pending_get
    owner=asyncio.create_task(p.start())
    await entered.wait()
    waiter_entered=asyncio.Event()
    async def join():
        waiter_entered.set()
        return await p.start()
    waiter=asyncio.create_task(join())
    await waiter_entered.wait()
    owner.cancel()
    with pytest.raises(asyncio.CancelledError): await owner
    with pytest.raises(asyncio.CancelledError): await waiter
    assert '_core_lighting_startup_in_flight' not in p.domain_data
    assert p.hooks==[] and not p.runtime.running
    assert not p.intake.registration_ready('legacy-neighbour')
