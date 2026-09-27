"""Recovery examples of the second delivery: D-01..D-13 and D-15.

Every scenario restarts into a brand-new service built from the durable
file image only; nothing is carried over from the previous instance in
memory. The transport call counter decides whether a repeat happened.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from custom_components.hausman_hub.application.core.command_service import (
    CapacityExceeded,
    ManualStatus,
    StorageUnavailable,
)
from custom_components.hausman_hub.domain.core.models import (
    Authority,
    OperationPhase,
    ProposalKind,
    REASON_CONFIRMATION_TIMEOUT,
    REASON_OWNERSHIP_UNPROVEN,
)
from custom_components.hausman_hub.domain.core.recovery import (
    evaluate_clock_trust,
)

from conftest import (
    BASE_TARGET_ON40,
    MAIN_ENTITY,
    MAIN_RESOURCE,
    OTHER_RESOURCE,
    OWNER_EPISODE,
    OWNER_MODULE,
    OWNER_PROPOSAL,
    TARGET_OFF,
    build_file_stand,
    owner_proposal,
    restore_file_stand,
)


def _auto_on40_dispatched(stand):
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()


def _manual(stand, request_id, target=TARGET_OFF, expected=12, action=None):
    return stand.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id=request_id,
        action_id=action or f"manual.{request_id}",
        target=target,
        source="test.ui",
        expected_control_revision=expected,
    )


def test_d01_failed_acceptance_neither_accepts_nor_replays(bootstrap=None):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    # A pending auto target exists before the failed manual acceptance.
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable):
        _manual(stand, "req-d01")
    assert stand.transport.calls == []
    record = stand.service.record(MAIN_RESOURCE)
    assert record.authority is Authority.AUTOMATIC_ELIGIBLE
    # Restart: the pending automatic work is cancelled by recovery and the
    # undemonstrated intervention never opens the automatic rights.
    restarted = restore_file_stand(stand)
    restarted.clock.advance(1)
    restarted.service.drain()
    assert restarted.transport.calls == []
    record = restarted.service.record(MAIN_RESOURCE)
    assert record.generation == 7
    assert record.authority is Authority.AUTOMATIC_ELIGIBLE
    cancelled = restarted.service.operations(MAIN_RESOURCE)[-1]
    assert cancelled.phase is OperationPhase.CANCELLED


def tmp_path_fixture():
    import tempfile

    return tempfile.mkdtemp(prefix="hh014b-d01-")


def test_d02_accepted_manual_survives_restart_with_its_receipt():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    receipt = _manual(stand, "req-d02")
    assert receipt.status is ManualStatus.NO_OP
    restarted = restore_file_stand(stand)
    record = restarted.service.record(MAIN_RESOURCE)
    assert record.control_revision == 13
    assert record.generation == 8
    assert record.authority is Authority.MANUAL
    # The same request returns the same durable receipt, not a new
    # acceptance.
    replay = _manual(restarted, "req-d02", expected=13)
    assert replay.idempotent_replay is True
    assert replay.operation_id == receipt.operation_id
    assert restarted.transport.calls == []


def test_d03_pending_work_is_cancelled_not_replayed():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    restarted = restore_file_stand(stand)
    restarted.clock.advance(1)
    restarted.service.drain()
    assert restarted.transport.calls == []
    operation = restarted.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.CANCELLED
    assert operation.reason_code == "restart_pending_cancelled"


def test_d04_durable_dispatching_without_call_is_unknown():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    # Stage one only: the dispatching record is durable, the call is not.
    stand.service.advance_timeouts()
    stand.service.take_ready()
    stand.service.run_taken()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.DISPATCHING
    restarted = restore_file_stand(stand)
    operation = restarted.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    assert operation.reason_code == "restart_uncertain"
    assert (
        restarted.service.episode_lock_reason(MAIN_RESOURCE, OWNER_EPISODE)
        == "restart_uncertain"
    )
    restarted.clock.advance(1)
    restarted.service.drain()
    assert restarted.transport.calls == []


def test_d05_call_without_durable_next_stage_is_unknown():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    # The transport call happens, but the following stage commit fails.
    stand.transport.on_call = lambda: setattr(
        stand.store, "fail_next_commit", True
    )
    with pytest.raises(StorageUnavailable):
        stand.service.drain()
    assert len(stand.transport.calls) == 1
    restarted = restore_file_stand(stand)
    operation = restarted.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    assert operation.reason_code == "restart_uncertain"
    # One call before the restart, zero after it: no replay.
    restarted.clock.advance(1)
    restarted.service.drain()
    assert restarted.transport.calls == []


def test_d06_unproven_confirmation_is_not_restored_as_ownership():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()
    # The confirmation transition fails to become durable.
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable):
        stand.ingest(
            MAIN_RESOURCE,
            sequence=2,
            mono_ms=100,
            properties={"on": True, "brightness_pct": 40},
        )
    restarted = restore_file_stand(stand)
    operation = restarted.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    assert restarted.service.record(MAIN_RESOURCE).auto_proof is None
    # A replayed old report (retained evidence) cannot confirm it either.
    restarted.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
        evidence="retained",
    )
    restarted.clock.advance(1)
    restarted.service.drain()
    assert restarted.service.record(MAIN_RESOURCE).auto_proof is None


def test_d09_restored_manual_terms_and_absence_restart():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.clock.advance(2)
    receipt = _manual(stand, "req-d09", expected=12)
    assert receipt.status is ManualStatus.NO_OP
    restarted = restore_file_stand(stand)
    record = restarted.service.record(MAIN_RESOURCE)
    assert record.manual is not None
    # Absolute UTC terms survive exactly: the hour was not restarted.
    assert record.manual.proven_at_utc_ms == 2
    assert record.manual.priority_until_utc_ms == 2 + 3_600_000
    assert record.manual.minimum_off_until_utc_ms == 2 + 600_000
    assert record.manual.policy_revision == 2
    # Let the clock run to both boundaries: expiry itself never commands.
    restarted.clock.utc_ms = 2 + 3_600_000
    restarted.service.drain()
    assert restarted.transport.calls == []
    restarted.clock.utc_ms = 2 + 3_600_000 + 600_000
    restarted.service.drain()
    assert restarted.transport.calls == []


def test_d10_file_store_keeps_the_receipt_across_dispatch_failure():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=10,
        properties={"on": True, "brightness_pct": 40},
    )
    stand.store.fail_at_commit_revisions.add(stand.store.commit_revision + 2)
    with pytest.raises(StorageUnavailable):
        _manual(stand, "req-d10")
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.FAILED
    assert stand.transport.calls == []
    restarted = restore_file_stand(stand)
    replay = _manual(restarted, "req-d10", expected=13)
    assert replay.idempotent_replay is True
    assert replay.operation_id == operation.operation_id
    assert restarted.service.dispatch_count() == 0
    # A genuinely new manual request works after the store recovers and
    # fresh observations arrive (K-10: fresh facts first).
    restarted.ingest(
        MAIN_RESOURCE,
        sequence=3,
        mono_ms=5,
        properties={"on": True, "brightness_pct": 40},
    )
    fresh = _manual(restarted, "req-d10-new", expected=13)
    assert fresh.status is ManualStatus.ACCEPTED
    assert restarted.service.dispatch_count() == 1
    assert restarted.transport.calls[-1] == (
        "light",
        "turn_off",
        MAIN_ENTITY,
        {},
    )


def test_d11_clock_uncertainty_never_extends_rights():
    # A backwards calendar jump beyond 2000 ms against the monotonic
    # clock makes the clock untrusted.
    trust = evaluate_clock_trust(
        platform_sync_confirmed=True,
        previous_utc_ms=10_000,
        previous_mono_ms=10_000,
        current_utc_ms=7_000,
        current_mono_ms=10_001,
    )
    assert trust.trusted is False
    # 2000 ms of divergence is inside the limit, 2001 is outside.
    inside = evaluate_clock_trust(
        True, 10_000, 10_000, 12_000, 10_000
    )
    outside = evaluate_clock_trust(
        True, 10_000, 10_000, 12_001, 10_000
    )
    assert inside.trusted is True
    assert outside.trusted is False
    # A restart without a confirmed platform sync cannot open the
    # automatic rights.
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    restarted = restore_file_stand(stand, platform_sync=False)
    assert restarted.service.record(MAIN_RESOURCE).uncertainty_reason == (
        "clock_uncertain"
    )
    stand2 = restarted
    stand2.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand2.service.process_module_event(OWNER_MODULE, "ev-1")
    stand2.clock.advance(1)
    stand2.service.drain()
    assert stand2.transport.calls == []


def test_d12_storage_outage_closes_dependent_records():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    # ENOSPC during the acceptance write: no acceptance, no send.
    stand.store.injector.faults["tmp_write"] = OSError(
        28, "No space left on device"
    )
    with pytest.raises(OSError):
        _manual(stand, "req-d12")
    assert stand.transport.calls == []
    # While the outage lasts, dependent writes of the other resource are
    # closed honestly as well.
    stand.store.fail_forever = True
    with pytest.raises(StorageUnavailable):
        stand.ingest(
            OTHER_RESOURCE,
            sequence=2,
            mono_ms=10,
            properties={"on": True},
        )
    # After recovery the store works again.
    stand.store.fail_forever = False
    stand.ingest(
        OTHER_RESOURCE,
        sequence=2,
        mono_ms=10,
        properties={"on": True},
    )
    assert stand.service.record(OTHER_RESOURCE).control_revision == 12


def test_d13_capacity_refuses_before_acceptance():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )
    from custom_components.hausman_hub.application.core.command_service import (
        RECEIPT_WINDOW_MS,
        ManualReceipt,
        ManualStatus,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    # Seed a full receipt window directly through a durable commit.
    store = stand.store
    image = stand.service._build_image()
    for index in range(2048):
        request_id = f"seed-{index}"
        image["receipts"][request_id] = ManualReceipt(
            request_id=request_id,
            status=ManualStatus.NO_OP,
            reason_code="no_op",
            operation_id=None,
            control_revision=12,
            generation=7,
        )
        image["receipt_times"][request_id] = stand.clock.now_utc_ms()
        image["receipt_fingerprints"][request_id] = [
            MAIN_RESOURCE,
            f"a-{index}",
            None,
        ]
    store.commit(store.commit_revision, image)
    restarted = restore_file_stand(stand)
    with pytest.raises(CapacityExceeded):
        _manual(restarted, "req-over", expected=12)
    # After the window passes, expired receipts are pruned and the
    # request is accepted again.
    restarted.clock.utc_ms += RECEIPT_WINDOW_MS + 1
    restarted.ingest(
        MAIN_RESOURCE, sequence=3, mono_ms=5, properties={"on": False}
    )
    receipt = _manual(restarted, "req-over", expected=12)
    assert receipt.status is ManualStatus.NO_OP
    # The pruned original request of another id cannot be replayed any
    # more: its revision is stale.
    with pytest.raises(Exception):
        _manual(restarted, "seed-0", expected=12)


def test_d15_barrier_set_by_failed_manual_blocks_the_old_call():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    # Stage one: the dispatching record is durable, the call pending.
    stand.service.advance_timeouts()
    stand.service.take_ready()
    stand.service.run_taken()
    # A new manual intent arrives; its own record fails to commit.
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable):
        _manual(stand, "req-d15")
    # The durable old dispatch must not overrule the newer deny barrier:
    # zero transport calls, and the in-flight automatic operation is
    # withdrawn the moment the newer intent arrives.
    stand.service.run_dispatching()
    assert stand.transport.calls == []
    auto_operation = [
        op
        for op in stand.service.operations(MAIN_RESOURCE)
        if op.module_id == OWNER_MODULE
    ][-1]
    assert auto_operation.dispatch_started_at_mono_ms is None
    assert auto_operation.phase in (
        OperationPhase.CANCELLED,
        OperationPhase.SUPERSEDED_AFTER_DISPATCH,
    )
