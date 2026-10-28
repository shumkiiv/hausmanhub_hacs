"""Closed startup preserves imported manual history, HH-014.b."""
from dataclasses import replace
import asyncio
from types import SimpleNamespace

import pytest

from test_core_lighting_setup import (
    _config_payload, _StartupHass, _startup_runtime, _ConfigService, _SpyExecutor,
    _seed_registered_startup_image, _startup_registration,
    AWAY_ENTITY_ID,
)
from custom_components.hausman_hub import _async_start_room_lighting_with_core
from custom_components.hausman_hub.adapters.core.control_store import FileControlStore
from custom_components.hausman_hub.application.core.command_service import (
    ClosedRegistration, ClosedTargetRegistration, _payload_digest,
)
from custom_components.hausman_hub.application.core_manual_input import (
    CoreManualInput, CoreManualInputError, synthetic_binary_light_profiles,
)
from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig, CoreBranchError, ManualHoldView, RoomLightingCoreBranch,
)
from custom_components.hausman_hub.domain.core.models import LightStateTarget
from custom_components.hausman_hub.domain.room_lighting import config_from_payload


@pytest.mark.parametrize('legacy', ['empty', 'unknown', 'raises', 'newer'])
def test_imported_manual_receipt_and_operation_survive_legacy_snapshot(tmp_path, legacy):
    config = config_from_payload(_config_payload())
    branch_config = CoreBranchConfig(room_id=config.room_id, config=config,
        device_profiles=synthetic_binary_light_profiles(['test_main']))
    source = RoomLightingCoreBranch(branch_config=branch_config, store_path=tmp_path / 'source.json')
    receipt = source.core_service.submit_manual(
        'test_main', 'imported-choice', 'turn_off', LightStateTarget(on=False),
        'physical.owner.choice', source.core_service.record('test_main').control_revision,
        receipt_scope='owner',
    )
    source.on_runtime_stop()
    image = source.core_service._store.load()
    registration = ClosedRegistration(
        branch_id=config.room_id, import_id='preserve-manual', source_identity='file-source',
        source_revision=source.core_service._store.commit_revision,
        source_payload_digest=_payload_digest(image),
        targets=(ClosedTargetRegistration('public-main', 'test_main', 'light.test_main',
            'on', ('turn_on', 'turn_off'), config.version, config.version),),
    )
    path = tmp_path / 'destination.json'
    destination = RoomLightingCoreBranch(branch_config=branch_config, store_path=path)
    destination.core_service.close_dispatch_for_restore()
    destination.import_closed_registration_from(source.core_service, registration,
        expected_commit_revision=destination.core_service._store.commit_revision,
        expected_source_revision=source.core_service._store.commit_revision)
    before = destination.core_service.record('test_main')
    operation = destination.core_service.operation(receipt.operation_id)
    stored_receipt = destination.core_service.receipt('imported-choice', receipt_scope='owner')
    assert before.manual is not None and before.manual.source == 'physical.owner.choice'
    if legacy == 'empty': provider = lambda: {}
    elif legacy == 'unknown': provider = lambda: None
    elif legacy == 'raises':
        def provider(): raise RuntimeError('unavailable legacy snapshot')
    else:
        provider = lambda: {'light.test_main': ManualHoldView(
            entity_id='light.test_main',
            action='turn_on', source='different.legacy',
            proven_at_utc_ms=before.manual.proven_at_utc_ms + 100,
            priority_until_utc_ms=before.manual.priority_until_utc_ms + 3600000,
            evidence_id='newer-legacy',
        )}
        conflicting = provider()['light.test_main']
        assert isinstance(conflicting, ManualHoldView)
        assert conflicting.source != before.manual.source
        assert conflicting.action == 'turn_on'
        assert conflicting.priority_until_utc_ms > before.manual.priority_until_utc_ms
    destination._manual_holds_provider = provider
    destination.activate_shadow()
    destination.process_schedule_tick()
    after = destination.core_service.record('test_main')
    assert after.manual == before.manual
    assert after.authority == before.authority
    assert after.uncertainty_reason == before.uncertainty_reason
    assert destination.core_service.receipt('imported-choice', receipt_scope='owner') == stored_receipt
    assert destination.core_service.operation(receipt.operation_id) == operation
    reread = FileControlStore(path).load()
    assert reread['records']['test_main'].manual == before.manual
    assert destination.physical_send_enabled() is False


def test_stopped_destination_import_cannot_reopen_input_or_change_image(tmp_path):
    config = config_from_payload(_config_payload())
    branch_config = CoreBranchConfig(room_id=config.room_id, config=config)
    source = RoomLightingCoreBranch(branch_config=branch_config, store_path=tmp_path / 'source.json')
    destination = RoomLightingCoreBranch(branch_config=branch_config, store_path=tmp_path / 'destination.json')
    source.on_runtime_stop()
    destination.on_runtime_stop()
    before = destination.core_service._store.load()
    registration = replace(_startup_registration(config),
        source_revision=source.core_service._store.commit_revision,
        source_payload_digest=_payload_digest(source.core_service._store.load()))
    with pytest.raises(CoreBranchError):
        destination.import_closed_registration_from(source.core_service, registration,
            expected_commit_revision=destination.core_service._store.commit_revision,
            expected_source_revision=source.core_service._store.commit_revision)
    assert destination.core_service._store.load() == before
    assert destination.core_service.closed_registrations() == ()
    assert destination.input_closed is True
    assert destination.physical_send_enabled() is False


@pytest.mark.asyncio
async def test_busy_mutation_lock_is_already_reserved_before_first_read(tmp_path):
    from custom_components.hausman_hub.domain.room_lighting_engine import LightAction, PlannedCommand
    config = config_from_payload(_config_payload())
    _seed_registered_startup_image(tmp_path, config)
    intake, executor = CoreManualInput(), _SpyExecutor()
    hass = _StartupHass(tmp_path)
    hass.states.set(AWAY_ENTITY_ID, 'off')
    for sensor in config.devices.sensors:
        if sensor.entity_id:
            hass.states.set(sensor.entity_id, 'off')
    for target in config.devices.light_targets:
        if target.entity_id:
            hass.states.set(target.entity_id, 'off')
    runtime = _startup_runtime(hass, config, intake, executor)
    service = _ConfigService(config)
    lock = asyncio.Lock()
    await lock.acquire()
    calls = []
    real_get = service.async_get_config

    async def record_get(room_id):
        calls.append(room_id)
        return await real_get(room_id)

    service.async_get_config = record_get
    task = asyncio.create_task(_async_start_room_lighting_with_core(
        hass, SimpleNamespace(entry_id='lock-window', async_on_unload=lambda callback: None),
        room_lighting_service=service, room_lighting_runtime=runtime,
        core_manual_input=intake, domain_data={}, core_shadow_room_id=config.room_id,
        mutation_lock=lock, power_dependency_resolver=lambda: {}, expected_closed_image=True))
    try:
        await asyncio.sleep(0)
        assert calls == [] and not task.done()
        assert not intake.registration_ready('unknown-target')
        with pytest.raises(CoreManualInputError): intake.command_entity_ids()
        receipt = await runtime.async_execute_command(config, PlannedCommand('test_main', LightAction.TURN_ON))
        assert receipt['reason'] == 'reserved_command_target'
        assert executor.calls == []
    finally:
        lock.release()
        await task
        await runtime.stop()
