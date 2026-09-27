"""Manual barrier examples F-04, F-05, F-08, F-12 of the FIRST delivery.

The manual path follows the S-04 order: barrier first, then one commit,
then publication and permitted execution. A no_op acceptance still records
the manual hold and bumps generation/revision.
"""

from __future__ import annotations

import pytest

from custom_components.hausman_hub.application.core.command_service import (
    ManualStatus,
    StorageUnavailable,
)
from custom_components.hausman_hub.domain.core.models import (
    Authority,
    OperationPhase,
    REASON_MANUAL_OVERRIDE,
)

from conftest import (
    BASE_TARGET_ON40,
    CONTROL_REVISION_START,
    GENERATION_START,
    MAIN_ENTITY,
    MAIN_RESOURCE,
    MAIN_SCOPE,
    OTHER_ENTITY,
    OTHER_RESOURCE,
    OTHER_SCOPE,
    OWNER_EPISODE,
    OWNER_MODULE,
    OWNER_PROPOSAL,
    SCOPE_START_REVISION,
    TARGET_OFF,
    build_stand,
    owner_proposal,
)


def _queue_auto(stand, proposal_id=OWNER_PROPOSAL):
    stand.modules[OWNER_MODULE].queue_proposal(
        owner_proposal(proposal_id=proposal_id)
    )
    stand.service.process_module_event(OWNER_MODULE, f"ev-{proposal_id}")


def _manual_off(stand, request_id, expected_revision=CONTROL_REVISION_START):
    return stand.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id=request_id,
        action_id=f"manual.{request_id}",
        target=TARGET_OFF,
        source="test.ui",
        expected_control_revision=expected_revision,
    )


def test_f04_manual_cancels_taken_job_before_dispatch(ranked_stand):
    stand = ranked_stand
    _queue_auto(stand)
    stand.clock.advance(1)
    stand.service.take_ready()  # the job is taken, not yet dispatched
    stand.clock.advance(1)  # mono 2: manual OFF arrives
    receipt = _manual_off(stand, "req-f04")
    assert receipt.status is ManualStatus.NO_OP
    assert receipt.control_revision == CONTROL_REVISION_START + 1
    assert receipt.generation == GENERATION_START + 1
    record = stand.service.record(MAIN_RESOURCE)
    assert record.authority is Authority.MANUAL
    assert record.manual is not None
    assert record.manual.priority_until_utc_ms == 2 + 3_600_000
    assert record.manual.minimum_off_until_utc_ms == 2 + 600_000
    assert record.manual.policy_revision == 2
    assert record.auto_proof is None
    # Manual scope group of the room moved to revision 5.
    assert (
        stand.service.scope_revision(MAIN_SCOPE) == SCOPE_START_REVISION + 1
    )
    # Continuing the old job dispatches nothing; it is cancelled.
    stand.clock.advance(1)  # mono 3
    stand.service.run_taken()
    assert stand.service.dispatch_count() == 0
    cancelled = [
        operation
        for operation in stand.service.operations(MAIN_RESOURCE)
        if operation.phase is OperationPhase.CANCELLED
    ]
    assert len(cancelled) == 1
    assert cancelled[0].reason_code == REASON_MANUAL_OVERRIDE


def test_f05_late_report_cannot_overrule_manual_off(ranked_stand):
    stand = ranked_stand
    _queue_auto(stand)
    stand.clock.advance(1)
    stand.service.drain()  # auto ON dispatched at mono 1
    stand.clock.advance(1)  # mono 2: manual OFF
    receipt = _manual_off(stand, "req-f05")
    assert receipt.status is ManualStatus.ACCEPTED
    assert receipt.operation_id is not None
    # Two dispatches of two explicitly different actions.
    assert stand.service.dispatch_count() == 2
    assert stand.transport.calls[0] == (
        "light",
        "turn_on",
        MAIN_ENTITY,
        {"brightness_pct": 40},
    )
    assert stand.transport.calls[1] == ("light", "turn_off", MAIN_ENTITY, {})
    stand.clock.advance(98)  # mono 100: the stale auto report arrives
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
    )
    assert result == "recorded"
    record = stand.service.record(MAIN_RESOURCE)
    assert record.manual is not None
    assert record.auto_proof is None
    superseded = [
        operation
        for operation in stand.service.operations(MAIN_RESOURCE)
        if operation.phase is OperationPhase.SUPERSEDED_AFTER_DISPATCH
    ]
    assert len(superseded) == 1


def test_f08_manual_touches_only_its_own_resource():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(
        ArbitrationMode.RANKED_TARGET,
        module_specs={
            OWNER_MODULE: MAIN_RESOURCE,
            "test.other_module": OTHER_RESOURCE,
        },
    )
    _queue_auto(stand)
    stand.modules["test.other_module"].queue_proposal(
        owner_proposal(
            proposal_id="other.on",
            resource_id=OTHER_RESOURCE,
            module_id="test.other_module",
        )
    )
    stand.service.process_module_event("test.other_module", "ev-other")
    stand.clock.advance(1)
    _manual_off(stand, "req-f08")
    stand.clock.advance(1)
    stand.service.drain()
    # Auto main is not sent; auto other runs with its own rights.
    assert stand.transport.calls == [
        ("light", "turn_on", OTHER_ENTITY, {"brightness_pct": 40})
    ]
    other_record = stand.service.record(OTHER_RESOURCE)
    assert other_record.generation == GENERATION_START
    assert other_record.control_revision == CONTROL_REVISION_START
    assert stand.service.scope_revision(OTHER_SCOPE) == SCOPE_START_REVISION
    main_auto = [
        operation
        for operation in stand.service.operations(MAIN_RESOURCE)
        if operation.module_id == OWNER_MODULE and operation.target != TARGET_OFF
    ]
    assert main_auto[0].phase is OperationPhase.CANCELLED


def test_f12_failed_commit_keeps_auto_closed_and_no_manual_success(
    ranked_stand,
):
    stand = ranked_stand
    _queue_auto(stand)
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable):
        _manual_off(stand, "req-f12")
    # No accepted manual success: the record still shows the old state.
    record = stand.service.record(MAIN_RESOURCE)
    assert record.control_revision == CONTROL_REVISION_START
    assert record.generation == GENERATION_START
    assert record.authority is not Authority.MANUAL
    # Auto stays closed: zero dispatches on the next ticks.
    stand.clock.advance(1)
    stand.service.drain()
    assert stand.service.dispatch_count() == 0
    stand.clock.advance(1)
    stand.service.drain()
    assert stand.service.dispatch_count() == 0
    # After the store recovers, a new manual request is accepted.
    receipt = stand.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id="req-f12-retry",
        action_id="manual.retry",
        target=TARGET_OFF,
        source="test.ui",
        expected_control_revision=CONTROL_REVISION_START,
    )
    assert receipt.status is ManualStatus.NO_OP
    assert receipt.control_revision == CONTROL_REVISION_START + 1
    assert stand.service.dispatch_count() == 0


def test_idem01_replay_conflict_and_revision_rules(ranked_stand):
    from custom_components.hausman_hub.application.core.command_service import (
        IdempotencyConflict,
        RevisionConflict,
    )

    stand = ranked_stand
    first = _manual_off(stand, "req-idem")
    assert first.status is ManualStatus.NO_OP
    # Same request id with the same content: identical receipt, no state
    # change and no new manual hold timer.
    replay = _manual_off(stand, "req-idem")
    assert replay.operation_id == first.operation_id
    assert replay.status is first.status
    assert replay.control_revision == first.control_revision
    assert replay.generation == first.generation
    assert replay.reason_code == first.reason_code
    assert replay.idempotent_replay is True
    record = stand.service.record(MAIN_RESOURCE)
    assert record.control_revision == CONTROL_REVISION_START + 1
    assert record.manual is not None
    # The replay did not restart the hold: the proven time is unchanged.
    assert record.manual.proven_at_utc_ms == 0
    assert record.manual.priority_until_utc_ms == 0 + 3_600_000
    # Same request id with different content: explicit conflict.
    with pytest.raises(IdempotencyConflict):
        stand.service.submit_manual(
            resource_ref=MAIN_RESOURCE,
            request_id="req-idem",
            action_id="manual.other",
            target=BASE_TARGET_ON40,
            source="test.ui",
            expected_control_revision=record.control_revision,
        )
    # A wrong expected revision is refused without any state change.
    with pytest.raises(RevisionConflict):
        _manual_off(stand, "req-other", expected_revision=CONTROL_REVISION_START)


from conftest import BASE_TARGET_ON40  # noqa: E402


def test_idem02_receipt_survives_storage_failure_during_dispatch():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    # Facts: the lamp reports ON40 itself, so a manual OFF must dispatch.
    stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=10,
        properties={"on": True, "brightness_pct": 40},
    )
    # Stand so far made two commits (two initial ingests + this report is
    # the third). Manual acceptance is the next commit, the dispatching
    # record the one after; fail exactly the dispatching record.
    stand.store.fail_at_commit_revisions.add(stand.store.commit_revision + 2)
    with pytest.raises(StorageUnavailable):
        _manual_off(stand, "req-idem2", expected_revision=CONTROL_REVISION_START)
    # The physical call never happened and the acceptance receipt stands.
    assert stand.transport.calls == []
    assert stand.service.dispatch_count() == 0
    record = stand.service.record(MAIN_RESOURCE)
    assert record.authority is Authority.MANUAL
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.FAILED
    assert operation.reason_code == "storage_unavailable_before_send"
    # Replaying the original request returns the same receipt, no re-send.
    replay = _manual_off(stand, "req-idem2", expected_revision=CONTROL_REVISION_START + 1)
    assert replay.idempotent_replay is True
    assert replay.operation_id == operation.operation_id
    assert stand.service.dispatch_count() == 0
    # After the store recovers a new request works normally.
    stand.store.fail_at_commit_revisions.clear()
    second = stand.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id="req-idem2-second",
        action_id="manual.second",
        target=TARGET_OFF,
        source="test.ui",
        expected_control_revision=CONTROL_REVISION_START + 1,
    )
    assert second.status is ManualStatus.ACCEPTED
    assert stand.service.dispatch_count() == 1
