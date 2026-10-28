"""Existing closed operations, not a production owner-switch coordinator.

Real file persistence and real common manual intake; no device evidence is
invented and no physical command can leave the closed transport.
"""
import asyncio
from dataclasses import replace

import pytest

from test_core_lighting_setup import _config_payload
from custom_components.hausman_hub.adapters.core.control_store import FileControlStore
from custom_components.hausman_hub.application.core.command_service import (
    ClosedRegistration, ClosedTargetRegistration, ClosedRegistrationConflict, _payload_digest,
)
from custom_components.hausman_hub.application.core_manual_input import (
    CoreManualInput, CoreManualInputError, CoreManualTargetBinding,
    synthetic_binary_light_profiles,
)
from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig, CoreBranchError, RoomLightingCoreBranch,
)
from custom_components.hausman_hub.domain.room_lighting import config_from_payload


RESOURCE, TARGET, ENTITY = 'test_main', 'public-main', 'light.test_main'


def branch_at(tmp_path, name):
    config = config_from_payload(_config_payload())
    path = tmp_path / (name + '.json')
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(room_id=config.room_id, config=config,
            device_profiles=synthetic_binary_light_profiles([RESOURCE])),
        store_path=path,
    )
    assert not branch.physical_send_enabled()
    return branch, path, config


def intake_for(branch):
    intake = CoreManualInput()
    intake.register_transferred_target(branch, CoreManualTargetBinding(
        target_id=TARGET, resource_id=RESOURCE, entity_id=ENTITY,
        room_id=branch.room_id,
    ))
    return intake


def envelope(branch, request_id):
    return dict(version=1, requestId=request_id,
        expectedControlRevision=branch.core_service.record(RESOURCE).control_revision,
        expectedBindingRevision=branch.core_service.spec(RESOURCE).binding_revision)


async def submit(intake, branch, request_id, *, control=None):
    return await intake.async_submit(
        target_id=TARGET, action_id='turn_off',
        core_control=control if control is not None else envelope(branch, request_id),
        principal='owner', correlation_id=request_id, request_id=request_id,
        full=True,
    )


def closed_registration(source, config, import_id='local-matrix'):
    image = source.core_service._store.load()
    return ClosedRegistration(
        branch_id=config.room_id, import_id=import_id, source_identity='local-file',
        source_revision=source.core_service._store.commit_revision,
        source_payload_digest=_payload_digest(image),
        targets=(ClosedTargetRegistration(TARGET, RESOURCE, ENTITY, 'on',
            ('turn_on', 'turn_off'), config.version, config.version),),
    )


def import_latest(source, destination, config, *, registration=None):
    registration = registration or closed_registration(source, config)
    destination.core_service.close_dispatch_for_restore()
    destination.import_closed_registration_from(source.core_service, registration,
        expected_commit_revision=destination.core_service._store.commit_revision,
        expected_source_revision=source.core_service._store.commit_revision)
    assert not destination.physical_send_enabled()
    return registration


def assert_history(source, destination, source_path, destination_path):
    old, new = FileControlStore(source_path).load(), FileControlStore(destination_path).load()
    for key in ('records', 'operations', 'receipts', 'receipt_times',
                'receipt_fingerprints', 'confirm_keys', 'episode_locks',
                'scope_revisions', 'config_revisions'):
        assert new[key] == old[key]
    assert destination.core_service.record(RESOURCE).manual == source.core_service.record(RESOURCE).manual
    assert not source.physical_send_enabled() and not destination.physical_send_enabled()


@pytest.mark.asyncio
async def test_manual_before_close_is_in_latest_real_file_image(tmp_path):
    source, source_path, config = branch_at(tmp_path, 'source')
    intake = intake_for(source)
    answer = await submit(intake, source, 'before-close')
    assert answer['accepted'] is True and answer['confirmed'] is False
    source.on_runtime_stop()
    destination, destination_path, _ = branch_at(tmp_path, 'destination')
    import_latest(source, destination, config)
    assert_history(source, destination, source_path, destination_path)
    assert destination.core_service.record(RESOURCE).manual is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('close_first', [False, True])
async def test_captured_admission_has_two_deterministic_orders(tmp_path, monkeypatch, close_first):
    source, source_path, config = branch_at(tmp_path, 'source')
    intake = intake_for(source)
    loop = asyncio.get_running_loop()
    captured, arrived = [], asyncio.Event()
    future = loop.create_future()

    def capture(executor, callback, *args):
        assert executor is None and not args
        captured.append(callback)
        arrived.set()
        return future

    # Only the documented executor boundary is delayed. The real admission
    # callback still rechecks the real branch lock and input-closed flag.
    monkeypatch.setattr(loop, 'run_in_executor', capture)
    task = asyncio.create_task(submit(intake, source, 'captured'))
    await arrived.wait()
    assert len(captured) == 1
    if close_first:
        source.on_runtime_stop()
        before = source_path.read_bytes()
    try:
        future.set_result(captured[0]())
    except Exception as exc:
        future.set_exception(exc)
    if close_first:
        with pytest.raises(CoreManualInputError) as error:
            await task
        assert error.value.code == 'core_unavailable'
        assert source_path.read_bytes() == before
    else:
        assert (await task)['accepted'] is True
        source.on_runtime_stop()
    destination, destination_path, _ = branch_at(tmp_path, 'destination')
    import_latest(source, destination, config)
    assert_history(source, destination, source_path, destination_path)
    assert (destination.core_service.record(RESOURCE).manual is None) == close_first
    assert not intake.registration_ready(TARGET)
    assert intake.command_entity_ids() == frozenset({ENTITY})


@pytest.mark.asyncio
@pytest.mark.parametrize('window', ['unknown', 'projection'])
async def test_absent_branch_window_is_refusal_not_accepted_or_legacy(tmp_path, window):
    source, _, config = branch_at(tmp_path, 'source')
    source.on_runtime_stop()
    intake = CoreManualInput()
    if window == 'unknown':
        intake.mark_closed_registration_unavailable(branch_id=config.room_id)
        with pytest.raises(CoreManualInputError):
            intake.command_entity_ids()
    else:
        intake.install_closed_registration_projection(closed_registration(source, config))
        assert intake.command_entity_ids() == frozenset({ENTITY})
    assert not intake.registration_ready(TARGET)
    with pytest.raises(CoreManualInputError):
        await submit(intake, source, 'window-command')
    assert source.core_service.record(RESOURCE).manual is None


@pytest.mark.asyncio
async def test_new_destination_only_and_replay_preserves_receipt(tmp_path):
    source, source_path, config = branch_at(tmp_path, 'source')
    intake = intake_for(source)
    request = envelope(source, 'replay')
    original_answer = await submit(intake, source, 'replay', control=request)
    durable_receipt = source.core_service.receipt('replay', receipt_scope='owner')
    source.on_runtime_stop()
    source_bytes = source_path.read_bytes()
    destination, destination_path, _ = branch_at(tmp_path, 'destination')
    registration = import_latest(source, destination, config)
    intake.install_closed_registration_projection(registration, branch=destination)
    previous = destination.core_service._store.load()
    replay_answer = await submit(intake, destination, 'replay', control=request)
    for field in ('contract', 'correlationId', 'requestId', 'targetId', 'actionId',
                  'targetType', 'accepted', 'confirmed', 'status', 'statusName',
                  'commandSource', 'reasonCode', 'dryRun', 'coreOperation'):
        assert replay_answer[field] == original_answer[field]
    assert replay_answer['commandSent'] is False
    assert destination.core_service.receipt('replay', receipt_scope='owner') == durable_receipt
    assert replay_answer['coreOperation']['operationId'] == durable_receipt.operation_id
    assert destination.core_service._store.load() == previous
    assert (await submit(intake, destination, 'after-attach'))['accepted'] is True
    assert source_path.read_bytes() == source_bytes
    assert FileControlStore(destination_path).load() != previous
    assert source.input_closed and not destination.physical_send_enabled()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['import', 'attach'])
async def test_import_or_attach_failure_keeps_reservation_and_refusal(tmp_path, failure):
    source, _, config = branch_at(tmp_path, 'source')
    source.on_runtime_stop()
    registration = closed_registration(source, config)
    intake = CoreManualInput()
    intake.install_closed_registration_projection(registration)
    destination, path, _ = branch_at(tmp_path, 'destination')
    if failure == 'import':
        destination.core_service.close_dispatch_for_restore()
        before = path.read_bytes() if path.exists() else None
        revision = destination.core_service._store.commit_revision
        with pytest.raises(ClosedRegistrationConflict):
            import_latest(source, destination, config,
                registration=replace(registration, source_payload_digest='f' * 64))
        assert (path.read_bytes() if path.exists() else None) == before
        assert destination.core_service._store.commit_revision == revision
    else:
        import_latest(source, destination, config)
        with pytest.raises(CoreManualInputError):
            intake.install_closed_registration_projection(
                replace(registration, import_id='changed-generation'), branch=destination)
    assert not intake.registration_ready(TARGET)
    assert intake.command_entity_ids() == frozenset({ENTITY})
    with pytest.raises(CoreManualInputError):
        await submit(intake, destination, 'failed-window')
    assert not destination.physical_send_enabled()


@pytest.mark.asyncio
async def test_late_manual_uses_current_image_and_old_stopped_receiver_refuses(tmp_path):
    source, source_path, config = branch_at(tmp_path, 'source')
    intake = intake_for(source)
    await submit(intake, source, 'early')
    source.on_runtime_stop()
    destination, destination_path, _ = branch_at(tmp_path, 'destination')
    registration = import_latest(source, destination, config)
    intake.install_closed_registration_projection(registration, branch=destination)
    await submit(intake, destination, 'late')
    destination.on_runtime_stop()
    latest = closed_registration(destination, config, import_id='fresh-rollback-recipient')
    source_before = source_path.read_bytes()
    with pytest.raises(CoreBranchError):
        import_latest(destination, source, config, registration=latest)
    assert source_path.read_bytes() == source_before
    recipient, recipient_path, _ = branch_at(tmp_path, 'fresh-recipient')
    import_latest(destination, recipient, config, registration=latest)
    assert_history(destination, recipient, destination_path, recipient_path)
    assert recipient.core_service.receipt('late', receipt_scope='owner') is not None
    assert recipient.core_service.receipt('early', receipt_scope='owner') is not None
