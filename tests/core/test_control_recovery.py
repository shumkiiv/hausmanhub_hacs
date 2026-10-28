"""Recovery examples of the second delivery: D-01..D-13 and D-15.

Every scenario restarts into a brand-new service built from the durable
file image only; nothing is carried over from the previous instance in
memory. The transport call counter decides whether a repeat happened.
"""

from __future__ import annotations

from decimal import Decimal
from dataclasses import replace
from pathlib import Path

import pytest

from custom_components.hausman_hub.application.core.command_service import (
    ClosedRegistration,
    ClosedTargetRegistration,
    CapacityExceeded,
    CoreCommandService,
    ManualStatus,
    ModuleEntry,
    ModuleManifest,
    Operation,
    StorageUnavailable,
    _payload_digest,
)
from custom_components.hausman_hub.domain.core.models import (
    Authority,
    ModuleMode,
    OperationPhase,
    Origin,
    ProposalKind,
    REASON_CONFIRMATION_TIMEOUT,
)
from custom_components.hausman_hub.domain.core.recovery import (
    evaluate_clock_trust,
)


def test_payload_digest_is_typed_and_canonical_without_codec_io():
    """Closed-import source identity ignores codec-only ordering, never types."""
    first = {
        "records": {"b": 2, "a": 1},
        "receipt_fingerprints": {"id": ("turn_on", Decimal("1.0"))},
        "scope_revisions": {"scope": {"one", "two"}},
    }
    reordered = {
        "scope_revisions": {"scope": {"two", "one"}},
        "receipt_fingerprints": {"id": ["turn_on", Decimal("1.0")]},
        "records": {"a": 1, "b": 2},
    }
    assert _payload_digest(first) == _payload_digest(reordered)
    assert _payload_digest({"value": "1"}) != _payload_digest({"value": 1})
    assert _payload_digest({"value": False}) != _payload_digest({"value": 0})
    assert _payload_digest({"v": {"a", "b"}}) != _payload_digest({"v": ["a", "b"]})

from custom_components.hausman_hub.adapters.core.control_store import (
    FAULT_POINTS,
    FileControlStore,
    StoreImageError,
)
from conftest import (
    BASE_TARGET_ON40,
    MAIN_ENTITY,
    MAIN_RESOURCE,
    MAIN_SCOPE,
    BINDING_REVISION_START,
    CONFIG_REVISION_START,
    Counter,
    FaultInjector,
    FakeClock,
    FakeTransport,
    OTHER_RESOURCE,
    OWNER_EPISODE,
    OWNER_MODULE,
    OWNER_PROPOSAL,
    TARGET_OFF,
    build_file_stand,
    owner_proposal,
    restore_file_stand,
)


def _restore_file_stand_closed(stand):
    """Restore the durable image directly into the only allowed mode.

    This intentionally does not alter the generic fixture's ACTIVE default:
    a stopped room must prove its own OFF/SHADOW recovery explicitly.
    """

    from custom_components.hausman_hub.adapters.core.light import LightDeviceAdapter
    from custom_components.hausman_hub.domain.core.models import (
        ProposalKind,
        StabilityProfile,
    )
    from conftest import light_profile, module_spec_resource

    clock = FakeClock(boot_id="test.closed_restore")
    clock.utc_ms = stand.clock.utc_ms
    transport = FakeTransport()
    store = FileControlStore(stand.store_path)
    counter = Counter()
    adapter = LightDeviceAdapter(
        profiles_by_resource={rid: light_profile() for rid in stand.specs},
        binding_revisions={
            rid: BINDING_REVISION_START for rid in stand.specs
        },
        boot_id=clock.boot_id(),
        id_factory=counter.next,
    )
    entries = {
        module_id: ModuleEntry(
            manifest=ModuleManifest(
                module_id=module_id,
                api_version=1,
                config_schema_version=1,
                resource_roles=("light",),
                allowed_proposal_kinds=(
                    ProposalKind.TARGET,
                    ProposalKind.REQUIRE_ON,
                ),
                observation_subscriptions=(module_spec_resource(stand, module_id),),
                policy_priority=50,
            ),
            instance=module,
        )
        for module_id, module in stand.modules.items()
    }
    return CoreCommandService.restore(
        clock=clock,
        store=store,
        adapter=adapter,
        transport=transport,
        specs=stand.specs,
        bindings=stand.bindings,
        modules=entries,
        stability_profiles={
            "default": StabilityProfile("default", 0, 0, 0, 0)
        },
        config_revisions={rid: CONFIG_REVISION_START for rid in stand.specs},
        scope_revisions={MAIN_SCOPE: 4, "test.other_scope": 4},
        id_factory=counter.next,
        clock_trust=evaluate_clock_trust(True, None, None, clock.utc_ms, 0),
        core_mode=ModuleMode.SHADOW,
    ), transport


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


def test_s11_closed_restore_cancels_unsent_and_keeps_dispatched_unknown():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path_fixture(), ArbitrationMode.RANKED_TARGET)
    _auto_on40_dispatched(stand)
    before_calls = len(stand.transport.calls)
    stand.service.close_dispatch_for_restore()

    record = stand.service.record(MAIN_RESOURCE)
    assert record.generation == 8
    assert record.control_revision == 13
    assert record.authority is Authority.UNCERTAIN
    assert record.auto_proof is None
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    assert operation.reason_code == "closed_for_restore_unknown"
    stand.service.drain()
    assert len(stand.transport.calls) == before_calls

    restored, transport = _restore_file_stand_closed(stand)
    assert restored._core_mode is ModuleMode.SHADOW
    restored.drain()
    assert transport.calls == []
    assert restored.operations(MAIN_RESOURCE)[-1].phase is OperationPhase.UNKNOWN


def test_s11_closed_instance_keeps_late_manual_but_never_dispatches():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path_fixture(), ArbitrationMode.RANKED_TARGET)
    stand.service.close_dispatch_for_restore()
    receipt = _manual(
        stand,
        "closed-manual",
        target=BASE_TARGET_ON40,
        expected=13,
    )
    record = stand.service.record(MAIN_RESOURCE)
    assert receipt.status is ManualStatus.ACCEPTED
    assert record.manual is not None
    assert record.manual.priority_until_utc_ms == 3_600_000
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.CANCELLED
    assert operation.reason_code == "dispatch_closed_for_restore"
    assert stand.transport.calls == []


@pytest.mark.parametrize(
    ("phase", "expected_phase", "expected_reason"),
    (
        (OperationPhase.QUEUED, OperationPhase.CANCELLED, "closed_for_restore"),
        (OperationPhase.TAKEN, OperationPhase.CANCELLED, "closed_for_restore"),
        (
            OperationPhase.DISPATCHING,
            OperationPhase.UNKNOWN,
            "closed_for_restore_unknown",
        ),
        (
            OperationPhase.OBSERVING,
            OperationPhase.UNKNOWN,
            "closed_for_restore_unknown",
        ),
        (OperationPhase.UNKNOWN, OperationPhase.UNKNOWN, None),
    ),
)
def test_s11_close_classifies_each_recoverable_operation_phase(
    phase, expected_phase, expected_reason
):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path_fixture(), ArbitrationMode.RANKED_TARGET)
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "closed-phase")
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    # The prior durable image can be interrupted at any lifecycle boundary.
    # This fixture only selects that persisted boundary; the classification is
    # production code in the close transition.
    operation.phase = phase
    stand.service.close_dispatch_for_restore()
    closed = stand.service.operations(MAIN_RESOURCE)[-1]
    assert closed.phase is expected_phase
    assert closed.reason_code == expected_reason
    assert stand.transport.calls == []


@pytest.mark.parametrize("fault_point", FAULT_POINTS)
def test_s11_close_fault_never_reopens_old_dispatch(fault_point):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path_fixture(), ArbitrationMode.RANKED_TARGET)
    _auto_on40_dispatched(stand)
    before_calls = len(stand.transport.calls)
    stand.store.injector.faults[fault_point] = OSError(fault_point)
    with pytest.raises((StorageUnavailable, OSError)):
        stand.service.close_dispatch_for_restore()
    # A failed atomic replacement may retain either complete image, but the
    # old process itself has already lost every path to a physical send.
    stand.service.drain()
    assert len(stand.transport.calls) == before_calls


def test_s11_close_retries_a_prewrite_failure_once_without_a_second_generation():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path_fixture(), ArbitrationMode.RANKED_TARGET)
    before_calls = len(stand.transport.calls)
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable):
        stand.service.close_dispatch_for_restore()

    unchanged = stand.service.record(MAIN_RESOURCE)
    assert (unchanged.generation, unchanged.control_revision) == (7, 12)
    stand.service.close_dispatch_for_restore()
    closed = stand.service.record(MAIN_RESOURCE)
    assert (closed.generation, closed.control_revision) == (8, 13)
    assert stand.store.commit_revision == 3
    stand.service.close_dispatch_for_restore()
    assert stand.store.commit_revision == 3
    assert len(stand.transport.calls) == before_calls


@pytest.mark.parametrize("fault_point", FAULT_POINTS)
def test_s11_close_reconciles_each_atomic_write_boundary(fault_point):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path_fixture(), ArbitrationMode.RANKED_TARGET)
    before_calls = len(stand.transport.calls)
    stand.store.injector.faults[fault_point] = OSError(fault_point)
    with pytest.raises(StorageUnavailable):
        stand.service.close_dispatch_for_restore()

    stand.service.close_dispatch_for_restore()
    closed = stand.service.record(MAIN_RESOURCE)
    assert (closed.generation, closed.control_revision) == (8, 13)
    durable = stand.store.load()
    assert durable["records"][MAIN_RESOURCE] == closed
    revision_after_retry = stand.store.commit_revision
    stand.service.close_dispatch_for_restore()
    assert stand.store.commit_revision == revision_after_retry
    assert len(stand.transport.calls) == before_calls


def test_s11_close_retries_an_ambiguous_reconciliation_without_new_rights():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path_fixture(), ArbitrationMode.RANKED_TARGET)
    # dir_fsync fails after replace: the transition may already be in the
    # file, but the first caller has no durability confirmation.
    stand.store.injector.faults["dir_fsync"] = OSError("first")
    with pytest.raises(StorageUnavailable):
        stand.service.close_dispatch_for_restore()
    # The reconciliation write can itself be interrupted.  It stays visible
    # and a later retry must preserve the same generation/control revision.
    stand.store.injector.faults["dir_fsync"] = OSError("second")
    with pytest.raises(StorageUnavailable):
        stand.service.close_dispatch_for_restore()
    with pytest.raises(StorageUnavailable):
        _manual(stand, "blocked-until-durable-close")

    stand.service.close_dispatch_for_restore()
    closed = stand.service.record(MAIN_RESOURCE)
    assert (closed.generation, closed.control_revision) == (8, 13)
    assert stand.store.load()["records"][MAIN_RESOURCE] == closed
    assert stand.transport.calls == []


@pytest.mark.parametrize("fault_point", ("dir_fsync", "reread"))
def test_s11_close_recommits_episode_lock_after_replace_without_new_generation(
    fault_point,
):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    _auto_on40_dispatched(stand)
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.OBSERVING
    stand.clock.advance(10_000)
    stand.service.advance_timeouts()
    assert stand.service.episode_lock_reason(
        MAIN_RESOURCE, OWNER_EPISODE
    ) == REASON_CONFIRMATION_TIMEOUT
    calls_before_close = len(stand.transport.calls)

    # Both failures occur after replace.  The persisted typed reload has a
    # tuple episode-lock key, so Case B must recommit the captured encodable
    # intended image instead of the decoded mapping.
    stand.store.injector.faults[fault_point] = OSError("first")
    with pytest.raises(StorageUnavailable):
        stand.service.close_dispatch_for_restore()
    durable_after_first = stand.store.load()
    assert any(
        isinstance(key, tuple)
        for key in durable_after_first["episode_locks"]
    )
    assert durable_after_first["records"][MAIN_RESOURCE].generation == 8
    assert durable_after_first["records"][MAIN_RESOURCE].control_revision == 13

    stand.store.injector.faults[fault_point] = OSError("second")
    with pytest.raises(StorageUnavailable):
        stand.service.close_dispatch_for_restore()
    stand.service.close_dispatch_for_restore()
    closed = stand.service.record(MAIN_RESOURCE)
    assert (closed.generation, closed.control_revision) == (8, 13)
    assert stand.service._same_close_payload(
        stand.store.load(), durable_after_first
    )
    assert len(stand.transport.calls) == calls_before_close


def test_s11_close_refuses_a_newer_manual_successor_without_overwriting_it():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path_fixture(), ArbitrationMode.RANKED_TARGET)
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable):
        stand.service.close_dispatch_for_restore()

    # A separate fresh coordinator accepts a manual successor from the last
    # proven durable base.  The old closed instance must not write its stale
    # intended snapshot over that newer manual right.
    successor = restore_file_stand(stand)
    receipt = _manual(successor, "newer-manual", expected=12)
    with pytest.raises(StorageUnavailable, match="newer image"):
        stand.service.close_dispatch_for_restore()
    durable = stand.store.load()
    assert durable["records"][MAIN_RESOURCE].manual is not None
    assert durable["receipts"]["newer-manual"] == receipt
    assert stand.transport.calls == []


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
    # The manual scope revision of the room is part of the same
    # acceptance image (SEC-S04-04).
    assert restarted.service.scope_revision(MAIN_SCOPE) == 5
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
    from custom_components.hausman_hub.application.core.command_service import (
        StorageUnavailable as SU,
    )

    with pytest.raises(SU):
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
    # Recovery goes through an explicit reload of the whole image: a new
    # store instance re-syncs with the durable truth.
    restarted = restore_file_stand(stand)
    restarted.ingest(
        OTHER_RESOURCE,
        sequence=2,
        mono_ms=10,
        properties={"on": True},
    )
    record = restarted.service.record(OTHER_RESOURCE)
    assert record.control_revision == 12
    assert record.generation == 7


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


def test_sec_s04_10_no_id_collision_after_restart():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    receipt = _manual(stand, "req-alpha")
    assert receipt.status is ManualStatus.NO_OP
    original_operations = [
        operation.operation_id
        for operation in stand.service.operations(MAIN_RESOURCE)
    ]
    # Two more operations appear after the restart; their ids must not
    # collide with the restored ones even though the counter restarted.
    restarted = restore_file_stand(stand)
    receipt_b = _manual(restarted, "req-beta", expected=13)
    assert receipt_b.status is ManualStatus.ACCEPTED
    restarted.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=10,
        properties={"on": True, "brightness_pct": 40},
    )
    receipt_c = _manual(restarted, "req-gamma", expected=14)
    assert receipt_c.status is ManualStatus.ACCEPTED
    ids = [
        operation.operation_id
        for operation in restarted.service.operations(MAIN_RESOURCE)
    ]
    assert len(ids) == len(set(ids)), "operation ids collided after restart"
    for operation_id in original_operations:
        assert operation_id in ids
    # The receipt of the restored acceptance still points at its own
    # operation, not at a silently replaced one.
    restored_receipt = restarted.service.receipt("req-alpha")
    assert restored_receipt is not None
    assert restored_receipt.operation_id in ids


def test_sec_s04_11_restored_operation_never_confirms_after_restart():
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
    # Durable dispatching without a call, then restart.
    stand.service.advance_timeouts()
    stand.service.take_ready()
    stand.service.run_taken()
    restarted = restore_file_stand(stand)
    operation = restarted.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    # A fresh, well-typed device report arrives in the new process.
    result = restarted.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=5,
        properties={"on": True, "brightness_pct": 40},
    )
    # A restored operation is never confirmed across the restart
    # (SEC-S04-11): its boundary data belongs to a dead process.
    assert result == "recorded"
    assert operation.phase is OperationPhase.UNKNOWN
    assert restarted.service.record(MAIN_RESOURCE).auto_proof is None


def test_sec_s04_03_restarted_ownership_stays_closed():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )
    from custom_components.hausman_hub.application.core.command_service import (
        ManualStatus,
    )
    from conftest import auto_on40_confirmed_anyon

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.ANY_ON,
        module_specs={
            OWNER_MODULE: MAIN_RESOURCE,
            "test.module_a": MAIN_RESOURCE,
            "test.module_b": MAIN_RESOURCE,
        },
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    auto_on40_confirmed_anyon(stand)
    assert stand.service.record(MAIN_RESOURCE).auto_proof is not None
    # Restart with fully trusted clocks: the recorded proof stays as
    # history but does not act until continuity is re-established.
    restarted = restore_file_stand(stand)
    record = restarted.service.record(MAIN_RESOURCE)
    assert record.auto_proof is not None
    assert record.uncertainty_reason == "restart_continuity_unproven"
    # A fresh auto proposal cannot dispatch on the old proof.
    restarted.modules["test.module_a"].queue_proposal(
        owner_proposal(
            proposal_id="a",
            module_id="test.module_a",
            kind=ProposalKind.REQUIRE_ON,
        )
    )
    restarted.service.process_module_event("test.module_a", "ev-a")
    restarted.clock.advance(1)
    restarted.service.drain()
    assert restarted.transport.calls == []
    # Manual commands are not affected by the continuity closure.
    manual_receipt = restarted.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id="req-manual-after-restart",
        action_id="manual.off",
        target=TARGET_OFF,
        source="test.ui",
        expected_control_revision=12,
    )
    assert manual_receipt.status is ManualStatus.ACCEPTED


def test_sec_s04_11b_durable_observing_with_timestamp_never_confirms():
    """Durable OBSERVING with a real dispatch timestamp: after a restart
    (new boot) a fresh device report must not turn it into a late
    confirmation (SEC-S04-11 residual, non-retained evidence)."""

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
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.OBSERVING
    assert operation.dispatch_started_at_mono_ms is not None
    restarted = restore_file_stand(stand)
    operation = restarted.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    # A fresh, well-typed report of the new process must not confirm the
    # restored operation.
    result = restarted.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=5,
        properties={"on": True, "brightness_pct": 40},
    )
    assert result == "recorded"
    assert operation.phase is OperationPhase.UNKNOWN
    assert restarted.service.record(MAIN_RESOURCE).auto_proof is None


def test_sec_s04_02_missing_boot_id_stays_fail_closed():
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
    # Persist an image whose boot_id is absent: provenance is kept per
    # operation, so the missing id stays fail-closed (SEC-S04-11).
    import json

    image = stand.service._build_image()
    image.pop("boot_id", None)
    stand.store.commit(stand.store.commit_revision, image)
    restarted = restore_file_stand(stand)
    restarted.clock.advance(1)
    restarted.service.drain()
    assert restarted.transport.calls == []
    operation = restarted.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.CANCELLED


def test_sec_s04_08_queued_occupies_capacity():
    from custom_components.hausman_hub.application.core.command_service import (
        CapacityExceeded,
        Operation,
        OperationPhase,
        Origin,
    )
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        module_specs={
            OWNER_MODULE: MAIN_RESOURCE,
            "test.other_module": OTHER_RESOURCE,
        },
    )
    # Seed 1023 unresolved outcomes of counted phases into the image.
    image = stand.service._build_image()
    for index in range(1023):
        image["operations"][f"seed-{index}"] = Operation(
            operation_id=f"seed-{index}",
            resource_id=OTHER_RESOURCE,
            decision_id=f"seeddec-{index}",
            origin=Origin.AUTOMATION,
            target=BASE_TARGET_ON40,
            generation=7,
            control_revision=12,
            config_revision=2,
            binding_revision=3,
            scope_revisions={},
            created_at_mono_ms=0,
            phase=OperationPhase.UNKNOWN,
            reason_code="seed",
        )
    stand.store.commit(stand.store.commit_revision, image)
    restarted = restore_file_stand(stand)
    # One queued target of the other resource is accepted (1023+1=1024).
    restarted.modules["test.other_module"].queue_proposal(
        owner_proposal(
            proposal_id="other.q1",
            resource_id=OTHER_RESOURCE,
            module_id="test.other_module",
        )
    )
    restarted.service.process_module_event("test.other_module", "ev-q1")
    # A queued target of ANOTHER resource would exceed the limit and is
    # refused before acceptance; replacing a same-resource pending target
    # does not consume extra capacity.
    restarted.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    with pytest.raises(CapacityExceeded):
        restarted.service.process_module_event(OWNER_MODULE, "ev-main-over")
    # The accepted queued target is still there, nothing was deleted.
    assert restarted.service.record(OTHER_RESOURCE).control_revision == 12


def test_sec_s04_02_exists_error_closes_existing_store(tmp_path, monkeypatch):
    import pathlib

    from custom_components.hausman_hub.application.core.command_service import (
        StorageUnavailable,
    )
    from test_control_store import STORE_FILE, make_state

    path = tmp_path / STORE_FILE
    store = FileControlStore(path)
    store.commit(0, make_state(control_revision=12, commit_revision=0))

    def broken_exists(self):
        raise OSError("exists failed")

    monkeypatch.setattr(pathlib.Path, "exists", broken_exists)
    with pytest.raises(StoreImageError):
        store.load()
    assert store.healthy() is False
    # Until an explicit reload succeeds, the closed store refuses writes.
    with pytest.raises(StorageUnavailable):
        store.commit(store.commit_revision, make_state(control_revision=13, commit_revision=0))
    monkeypatch.undo()
    store.load()
    assert store.healthy() is True


def test_sec_s04_08_manual_replacement_of_own_pending_at_full_capacity():
    """At full capacity a manual command replacing the resource's own
    pending automatic target must be accepted: it frees the slot it
    occupies (SEC-S04-08 residual, audit scenario)."""

    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    # Seed 1023 unresolved outcomes on the other resource.
    image = stand.service._build_image()
    for index in range(1023):
        image["operations"][f"seed-{index}"] = Operation(
            operation_id=f"seed-{index}",
            resource_id=OTHER_RESOURCE,
            decision_id=f"seeddec-{index}",
            origin=Origin.AUTOMATION,
            target=BASE_TARGET_ON40,
            generation=7,
            control_revision=12,
            config_revision=2,
            binding_revision=3,
            scope_revisions={},
            created_at_mono_ms=0,
            phase=OperationPhase.UNKNOWN,
            reason_code="seed",
        )
    stand.store.commit(stand.store.commit_revision, image)
    restarted = restore_file_stand(stand)
    # One pending automatic target of main (not dispatched yet).
    restarted.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    restarted.service.process_module_event(OWNER_MODULE, "ev-1")
    unresolved = restarted.service._count_unresolved_operations()
    assert unresolved == 1024
    # The manual command replaces that pending target: freed 1, added 1 —
    # the future composition stays within the limit.
    receipt = _manual(restarted, "req-replace", expected=12)
    assert receipt.status is ManualStatus.ACCEPTED
    assert restarted.service.dispatch_count() == 0
    auto_operation = [
        operation
        for operation in restarted.service.operations(MAIN_RESOURCE)
        if operation.module_id == OWNER_MODULE
    ][-1]
    assert auto_operation.phase is OperationPhase.CANCELLED
    assert restarted.transport.calls == []


def test_sec_s04_08_no_op_manual_at_full_capacity_adds_nothing():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    image = stand.service._build_image()
    # Fill the store to the exact limit with counted UNKNOWN outcomes.
    for index in range(1024):
        image["operations"][f"seed-{index}"] = Operation(
            operation_id=f"seed-{index}",
            resource_id=OTHER_RESOURCE,
            decision_id=f"seeddec-{index}",
            origin=Origin.AUTOMATION,
            target=TARGET_OFF,
            generation=7,
            control_revision=12,
            config_revision=2,
            binding_revision=3,
            scope_revisions={},
            created_at_mono_ms=0,
            phase=OperationPhase.UNKNOWN,
            reason_code="seed",
        )
    stand.store.commit(stand.store.commit_revision, image)
    restarted = restore_file_stand(stand)
    assert restarted.service._count_unresolved_operations() == 1024
    # A NO_OP manual adds no unresolved operation: accepted exactly at
    # the limit (SEC-S04-08-T boundary).
    restarted.ingest(
        MAIN_RESOURCE, sequence=2, mono_ms=5, properties={"on": False}
    )
    receipt = _manual(restarted, "req-noop", expected=12)
    assert receipt.status is ManualStatus.NO_OP
    assert restarted.service._count_unresolved_operations() == 1024
    # A dispatched manual would exceed it and is refused.
    with pytest.raises(CapacityExceeded):
        restarted.service.submit_manual(
            resource_ref=MAIN_RESOURCE,
            request_id="req-over",
            action_id="manual.on40",
            target=BASE_TARGET_ON40,
            source="test.ui",
            expected_control_revision=13,
        )


def test_sec_s04_11c_provenance_survives_image_recommit():
    """The per-operation provenance markers persist through restore's own
    commit and a later reload: a second restart still marks the same
    operations as restored (SEC-S04-11 residual serialization)."""

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
    first_restarted = restore_file_stand(stand)
    assert first_restarted.service._restored_operation_ids
    # The markers are durable: the stored image carries them and a
    # reload (and its own re-commit) keeps them — this discriminates a
    # real roundtrip from a set recomputed from operations.
    stored = first_restarted.store.load()
    assert stored["restored_operation_ids"]
    second_restarted = restore_file_stand(first_restarted)
    stored_twice = second_restarted.store.load()
    assert stored_twice["restored_operation_ids"] == stored[
        "restored_operation_ids"
    ]
    assert (
        second_restarted.service._restored_operation_ids
        == first_restarted.service._restored_operation_ids
    )


def test_sec_s04_08_manual_replacement_at_full_capacity_future_composition():
    """At full capacity a manual command replacing the resource's own
    pending automatic target must be accepted: it frees the slot it
    occupies (SEC-S04-08 residual, future composition)."""

    from custom_components.hausman_hub.application.core.command_service import (
        ManualStatus,
        Operation as ServiceOperation,
        OperationPhase as ServicePhase,
        Origin as ServiceOrigin,
    )
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
        LightStateTarget as LightTarget,
    )
    from decimal import Decimal

    stand = build_file_stand(
        tmp_path_fixture(),
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    # Seed 1023 unresolved outcomes on the other resource.
    image = stand.service._build_image()
    for index in range(1023):
        image["operations"][f"seed-{index}"] = ServiceOperation(
            operation_id=f"seed-{index}",
            resource_id=OTHER_RESOURCE,
            decision_id=f"seeddec-{index}",
            origin=Origin.AUTOMATION,
            target=TARGET_OFF,
            generation=7,
            control_revision=12,
            config_revision=2,
            binding_revision=3,
            scope_revisions={},
            created_at_mono_ms=0,
            phase=ServicePhase.UNKNOWN,
            reason_code="seed",
        )
    stand.store.commit(stand.store.commit_revision, image)
    restarted = restore_file_stand(stand)
    # One pending automatic target of main (not dispatched yet): 1024.
    restarted.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    restarted.service.process_module_event(OWNER_MODULE, "ev-1")
    assert restarted.service._count_unresolved_operations() == 1024
    # Manual OFF for the same resource: frees the pending slot (1023),
    # adds its own dispatched op (1024) — within the limit.
    receipt = restarted.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id="req-d15",
        action_id="manual.d15",
        target=TARGET_OFF,
        source="test.ui",
        expected_control_revision=12,
    )
    assert receipt.status is ManualStatus.ACCEPTED
    # Count may be 1023 or 1024 depending on whether the manual dispatch
    # produced a live operation; the key assertion is ACCEPTED, not
    # CapacityExceeded.
    # The old automatic target was cancelled.
    cancelled = [
        op for op in restarted.service.operations(MAIN_RESOURCE)
        if op.phase is ServicePhase.CANCELLED
    ]
    assert len(cancelled) >= 1


def test_closed_import_copies_a_complete_real_file_image_once(tmp_path):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    source = build_file_stand(tmp_path / "source", ArbitrationMode.RANKED_TARGET)
    source.service.close_dispatch_for_restore()
    destination = build_file_stand(
        tmp_path / "destination", ArbitrationMode.RANKED_TARGET
    )
    destination.service.close_dispatch_for_restore()
    registration = ClosedRegistration(
        branch_id="synthetic.room",
        import_id="closed-import-1",
        source_identity="source-file-image",
        source_revision=source.store.commit_revision,
        source_payload_digest=_payload_digest(source.store.load()),
        targets=(
            ClosedTargetRegistration(
                target_id="light.main", resource_id=MAIN_RESOURCE,
                entity_id=MAIN_ENTITY, property_name="on",
                allowed_actions=("turn_on", "turn_off"),
                binding_revision=BINDING_REVISION_START,
                config_revision=CONFIG_REVISION_START,
            ),
            ClosedTargetRegistration(
                target_id="light.other", resource_id=OTHER_RESOURCE,
                entity_id="light.test_other", property_name="on",
                allowed_actions=("turn_on", "turn_off"),
                binding_revision=BINDING_REVISION_START,
                config_revision=CONFIG_REVISION_START,
            ),
        ),
    )
    destination.service.import_closed_registration_from(
        source.service, registration,
        expected_commit_revision=destination.store.commit_revision,
        expected_source_revision=source.store.commit_revision,
    )
    assert destination.service.closed_registrations() == (registration,)
    assert destination.service.record(MAIN_RESOURCE) == source.service.record(
        MAIN_RESOURCE
    )
    commit_revision = destination.store.commit_revision
    destination.service.import_closed_registration_from(
        source.service, registration,
        expected_commit_revision=commit_revision,
        expected_source_revision=source.store.commit_revision,
    )
    assert destination.store.commit_revision == commit_revision
    # A later durable manual decision in the destination cannot be erased by
    # replaying the old source snapshot under the same import identity.
    later = destination.service.submit_manual(
        resource_ref=MAIN_RESOURCE, request_id="destination-later",
        action_id="destination.manual", target=TARGET_OFF,
        source="test.destination",
        expected_control_revision=destination.service.record(
            MAIN_RESOURCE
        ).control_revision,
    )
    assert later.status in (ManualStatus.ACCEPTED, ManualStatus.NO_OP)
    after_later = destination.service.record(MAIN_RESOURCE)
    destination.service.import_closed_registration_from(
        source.service, registration,
        expected_commit_revision=destination.store.commit_revision,
        expected_source_revision=source.store.commit_revision,
    )
    assert destination.service.record(MAIN_RESOURCE) == after_later


@pytest.mark.parametrize("target", (TARGET_OFF, BASE_TARGET_ON40))
def test_first_closed_import_refuses_destination_manual_history(tmp_path, target):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode
    from custom_components.hausman_hub.application.core.command_service import (
        ClosedRegistrationConflict,
    )

    source = build_file_stand(tmp_path / "source", ArbitrationMode.RANKED_TARGET)
    source.service.close_dispatch_for_restore()
    destination = build_file_stand(tmp_path / "destination", ArbitrationMode.RANKED_TARGET)
    destination.service.submit_manual(
        resource_ref=MAIN_RESOURCE, request_id="new-human",
        action_id="new-human-action", target=target, source="test.destination",
        expected_control_revision=12,
    )
    destination.service.close_dispatch_for_restore()
    registration = ClosedRegistration(
        "synthetic.room", "first-import", "source-file",
        source.store.commit_revision,
        (
            ClosedTargetRegistration("light.main", MAIN_RESOURCE, MAIN_ENTITY,
                "on", ("turn_on", "turn_off"), BINDING_REVISION_START,
                CONFIG_REVISION_START),
            ClosedTargetRegistration("light.other", OTHER_RESOURCE,
                "light.test_other", "on", ("turn_on", "turn_off"),
                BINDING_REVISION_START, CONFIG_REVISION_START),
        ),
            _payload_digest(source.store.load()),
    )
    before = destination.service.record(MAIN_RESOURCE)
    revision = destination.store.commit_revision
    calls_before = list(destination.transport.calls)
    with pytest.raises(ClosedRegistrationConflict, match="not pristine"):
        destination.service.import_closed_registration_from(
            source.service, registration, expected_commit_revision=revision,
            expected_source_revision=source.store.commit_revision,
        )
    assert destination.service.record(MAIN_RESOURCE) == before
    assert destination.store.commit_revision == revision
    assert destination.transport.calls == calls_before


@pytest.mark.parametrize("fault_point", FAULT_POINTS)
def test_closed_import_fault_keeps_registration_and_rights_as_one_image(
    tmp_path, fault_point
):
    """Every file interruption leaves the old image or the whole new one."""

    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    source = build_file_stand(tmp_path / "source", ArbitrationMode.RANKED_TARGET)
    source.service.submit_manual(
        resource_ref=MAIN_RESOURCE, request_id="source-manual",
        action_id="source.manual", target=TARGET_OFF, source="test.source",
        expected_control_revision=12,
    )
    source.service.close_dispatch_for_restore()
    destination = build_file_stand(
        tmp_path / "destination", ArbitrationMode.RANKED_TARGET
    )
    destination.service.close_dispatch_for_restore()
    registration = ClosedRegistration(
        branch_id="synthetic.room", import_id="fault-import",
        source_identity="source-file-image",
        source_revision=source.store.commit_revision,
            source_payload_digest=_payload_digest(source.store.load()),
        targets=(
            ClosedTargetRegistration("light.main", MAIN_RESOURCE, MAIN_ENTITY,
                "on", ("turn_on", "turn_off"), BINDING_REVISION_START,
                CONFIG_REVISION_START),
            ClosedTargetRegistration("light.other", OTHER_RESOURCE,
                "light.test_other", "on", ("turn_on", "turn_off"),
                BINDING_REVISION_START, CONFIG_REVISION_START),
        ),
    )
    injector = FaultInjector()
    injector.faults[fault_point] = OSError(fault_point, "injected")
    destination.store._fault_injector = injector
    before = destination.service.record(MAIN_RESOURCE)
    with pytest.raises(StorageUnavailable):
        destination.service.import_closed_registration_from(
            source.service, registration,
            expected_commit_revision=destination.store.commit_revision,
            expected_source_revision=source.store.commit_revision,
        )
    reread = FileControlStore(destination.store_path).load()
    assert reread is not None
    imported = reread["closed_registrations"]
    durable_record = reread["records"][MAIN_RESOURCE]
    # A replace may have succeeded before the reported failure.  In both
    # cases routing registration and the manual record share one image.
    if imported:
        assert imported == {"synthetic.room": registration}
        assert durable_record == source.service.record(MAIN_RESOURCE)
    else:
        assert durable_record == before
    assert destination.transport.calls == []


def _closed_import_pair(tmp_path):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    source = build_file_stand(tmp_path / "source", ArbitrationMode.RANKED_TARGET)
    destination = build_file_stand(tmp_path / "destination", ArbitrationMode.RANKED_TARGET)
    source.service.close_dispatch_for_restore()
    destination.service.close_dispatch_for_restore()
    registration = ClosedRegistration(
        "synthetic.room", "negative-import", "source-file",
        source.store.commit_revision,
        tuple(ClosedTargetRegistration(target, resource, entity, "on",
              ("turn_on", "turn_off"), BINDING_REVISION_START, CONFIG_REVISION_START)
              for target, resource, entity in (("light.main", MAIN_RESOURCE, MAIN_ENTITY),
                  ("light.other", OTHER_RESOURCE, "light.test_other"))),
        _payload_digest(source.store.load()),
    )
    return source, destination, registration


@pytest.mark.parametrize("case", (
    "destination-revision", "source-revision", "registration-revision", "digest",
    "source-manual-moved", "partial-scope", "foreign-scope", "empty-scope",
    "duplicate-target", "duplicate-resource", "duplicate-entity", "binding-revision",
    "config-revision", "targets-list", "actions-list", "bool-binding",
    "bool-config", "bool-source", "bool-expected-source", "bool-expected-destination",
))
def test_closed_import_invalid_proof_never_changes_destination(tmp_path, case):
    from custom_components.hausman_hub.application.core.command_service import ClosedRegistrationConflict

    source, destination, registration = _closed_import_pair(tmp_path)
    source_revision = source.store.commit_revision
    revision = destination.store.commit_revision
    scope = None
    if case == "destination-revision": revision += 1
    if case == "source-revision": source_revision += 1
    if case == "registration-revision": registration = replace(registration, source_revision=source_revision + 1)
    if case == "digest": registration = replace(registration, source_payload_digest="0" * 64)
    if case == "source-manual-moved":
        _manual(source, "new-source-manual", expected=13)
        source_revision = source.store.commit_revision
        registration = replace(registration, source_revision=source_revision)
    if case == "partial-scope": scope = frozenset({MAIN_RESOURCE})
    if case == "foreign-scope": scope = frozenset({"foreign"})
    if case == "empty-scope": scope = frozenset()
    if case in {"duplicate-target", "duplicate-resource", "duplicate-entity"}:
        field = {"duplicate-target": "target_id", "duplicate-resource": "resource_id",
                 "duplicate-entity": "entity_id"}[case]
        registration = replace(registration, targets=(registration.targets[0],
            replace(registration.targets[1], **{field: getattr(registration.targets[0], field)})))
    if case in {"binding-revision", "config-revision", "bool-binding", "bool-config", "actions-list"}:
        field, value = {"binding-revision": ("binding_revision", 999),
            "config-revision": ("config_revision", 999), "bool-binding": ("binding_revision", True),
            "bool-config": ("config_revision", True), "actions-list": ("allowed_actions", ["turn_on"])}[case]
        registration = replace(registration, targets=(replace(registration.targets[0], **{field: value}),
                                                       registration.targets[1]))
    if case == "targets-list": registration = replace(registration, targets=list(registration.targets))
    if case == "bool-source": registration = replace(registration, source_revision=True)
    if case == "bool-expected-source": source_revision = True
    if case == "bool-expected-destination": revision = True
    before = Path(destination.store_path).read_bytes()
    record = destination.service.record(MAIN_RESOURCE)
    actual_revision = destination.store.commit_revision
    with pytest.raises(ClosedRegistrationConflict):
        destination.service.import_closed_registration_from(source.service, registration,
            expected_commit_revision=revision, expected_source_revision=source_revision,
            controlled_resource_ids=scope)
    assert Path(destination.store_path).read_bytes() == before
    assert destination.store.commit_revision == actual_revision
    assert destination.service.record(MAIN_RESOURCE) == record
    assert destination.service.closed_registrations() == ()
    assert destination.transport.calls == []


def test_closed_import_keeps_nonempty_seeded_unknown_history_without_replay(tmp_path):
    """Synthetic persisted uncertainty, not proof of a physical dispatch."""
    source, destination, registration = _closed_import_pair(tmp_path)
    receipt = _manual(source, "seeded-unknown-manual", target=BASE_TARGET_ON40, expected=13)
    image = source.store.load()
    operation = image["operations"][receipt.operation_id]
    image["operations"][receipt.operation_id] = replace(
        operation, phase=OperationPhase.UNKNOWN, reason_code="synthetic_persisted_unknown")
    source.store.commit(source.store.commit_revision, image)
    # Reconstruct memory from the same durable image before any import proof.
    restored_source, source_transport = _restore_file_stand_closed(source)
    restored_source.close_dispatch_for_restore()
    current = FileControlStore(source.store_path).load()
    unknown_ids = {key for key, value in current["operations"].items()
                   if value.phase is OperationPhase.UNKNOWN}
    assert receipt.operation_id in unknown_ids
    assert current["operations"][receipt.operation_id].reason_code == "synthetic_persisted_unknown"
    registration = replace(registration, source_revision=current["commit_revision"],
                           source_payload_digest=_payload_digest(current))
    destination.service.import_closed_registration_from(restored_source, registration,
        expected_commit_revision=destination.store.commit_revision,
        expected_source_revision=current["commit_revision"])
    imported = FileControlStore(destination.store_path).load()
    for field in ("records", "operations", "receipts", "receipt_times",
                  "receipt_fingerprints", "confirm_keys", "episode_locks",
                  "scope_revisions", "config_revisions"):
        assert imported[field] == current[field]
    assert imported["operations"][receipt.operation_id].phase is OperationPhase.UNKNOWN
    assert imported["receipts"]["seeded-unknown-manual"] == receipt
    restored_destination, destination_transport = _restore_file_stand_closed(destination)
    restored_destination.drain()
    reread = FileControlStore(destination.store_path).load()
    assert reread["operations"][receipt.operation_id] == current["operations"][receipt.operation_id]
    assert restored_destination.record(MAIN_RESOURCE).manual == current["records"][MAIN_RESOURCE].manual
    assert source_transport.calls == destination_transport.calls == []
    assert source.transport.calls == destination.transport.calls == []


def test_closed_import_copies_manual_receipts_and_unknown_without_replay(tmp_path):
    source, destination, registration = _closed_import_pair(tmp_path)
    receipt = _manual(source, "source-late-manual", target=BASE_TARGET_ON40, expected=13)
    durable_operation = source.store.load()["operations"][receipt.operation_id]
    memory_operation = source.service.operations(MAIN_RESOURCE)[-1]
    assert durable_operation == memory_operation
    assert durable_operation.phase is OperationPhase.CANCELLED
    assert durable_operation.reason_code == "dispatch_closed_for_restore"
    registration = replace(registration, source_revision=source.store.commit_revision,
                           source_payload_digest=_payload_digest(source.store.load()))
    destination.service.import_closed_registration_from(source.service, registration,
        expected_commit_revision=destination.store.commit_revision,
        expected_source_revision=source.store.commit_revision)
    image = destination.store.load()
    assert image["records"][MAIN_RESOURCE] == source.service.record(MAIN_RESOURCE)
    assert image["receipts"]["source-late-manual"] == receipt
    assert image["operations"] == source.store.load()["operations"]
    restored, transport = _restore_file_stand_closed(destination)
    restored.drain()
    assert restored.closed_registrations() == (registration,)
    assert restored.record(MAIN_RESOURCE).manual == source.service.record(MAIN_RESOURCE).manual
    assert transport.calls == destination.transport.calls == source.transport.calls == []


@pytest.mark.parametrize("fault_point", FAULT_POINTS)
def test_closed_late_manual_failed_acceptance_never_sends_or_claims_success(tmp_path, fault_point):
    source, _, _ = _closed_import_pair(tmp_path)
    source.store.injector.faults[fault_point] = OSError(fault_point)
    with pytest.raises(StorageUnavailable):
        _manual(source, "failed-late-manual", target=BASE_TARGET_ON40, expected=13)
    assert "failed-late-manual" not in source.service._receipts
    assert source.service._dispatch_closed_for_restore is True
    source.service.drain()
    assert source.transport.calls == []


@pytest.mark.parametrize("resource", (MAIN_RESOURCE, OTHER_RESOURCE))
@pytest.mark.parametrize("difference", ("entity", "binding", "config", "manual-scope"))
def test_closed_import_rejects_consistent_but_incompatible_source(tmp_path, resource, difference):
    from custom_components.hausman_hub.application.core.command_service import (
        ClosedRegistrationConflict, _canonical_source_image,
    )
    source, destination, registration = _closed_import_pair(tmp_path)
    service = source.service
    if difference == "entity": service._bindings[resource] = "light.foreign_source"
    if difference == "binding":
        service._specs[resource] = replace(service._specs[resource], binding_revision=99)
        service._records[resource] = replace(service._records[resource], binding_revision=99)
    if difference == "config": service._config_revisions[resource] = 99
    if difference == "manual-scope":
        service._specs[resource] = replace(service._specs[resource], manual_scopes=("foreign.scope",))
        service._scope_revisions["foreign.scope"] = 9
    service._commit_state()
    durable = source.store.load()
    assert _canonical_source_image(durable) == _canonical_source_image(service._build_image())
    # Only MAIN is registered. OTHER still belongs to the transferred image.
    registration = replace(registration, targets=registration.targets[:1],
        source_revision=source.store.commit_revision, source_payload_digest=_payload_digest(durable))
    before = Path(destination.store_path).read_bytes()
    revision = destination.store.commit_revision
    record = destination.service.record(MAIN_RESOURCE)
    with pytest.raises(ClosedRegistrationConflict):
        destination.service.import_closed_registration_from(service, registration,
            expected_commit_revision=revision, expected_source_revision=source.store.commit_revision,
            controlled_resource_ids=frozenset({MAIN_RESOURCE}))
    assert Path(destination.store_path).read_bytes() == before
    assert destination.store.commit_revision == revision
    assert destination.service.record(MAIN_RESOURCE) == record
    assert destination.service.closed_registrations() == ()
    assert destination.transport.calls == source.transport.calls == []


@pytest.mark.parametrize("difference", ("owner_module", "adapter_id", "capability_profile_id", "power_dependencies"))
def test_closed_import_rejects_other_source_spec_semantics(tmp_path, difference):
    from custom_components.hausman_hub.application.core.command_service import (
        ClosedRegistrationConflict, _canonical_source_image,
    )
    source, destination, registration = _closed_import_pair(tmp_path)
    value = (MAIN_RESOURCE,) if difference == "power_dependencies" else "foreign.value"
    source.service._specs[OTHER_RESOURCE] = replace(source.service._specs[OTHER_RESOURCE], **{difference: value})
    durable = source.store.load()
    assert _canonical_source_image(durable) == _canonical_source_image(source.service._build_image())
    registration = replace(registration, targets=registration.targets[:1])
    before = Path(destination.store_path).read_bytes()
    revision = destination.store.commit_revision
    with pytest.raises(ClosedRegistrationConflict):
        destination.service.import_closed_registration_from(source.service, registration,
            expected_commit_revision=revision, expected_source_revision=source.store.commit_revision,
            controlled_resource_ids=frozenset({MAIN_RESOURCE}))
    assert Path(destination.store_path).read_bytes() == before
    assert destination.store.commit_revision == revision
    assert destination.transport.calls == source.transport.calls == []


@pytest.mark.parametrize("difference", ("record-binding", "record-id", "scope-keys", "missing-declared-scope"))
def test_closed_import_rejects_inconsistent_source_record_or_scope(tmp_path, difference):
    from custom_components.hausman_hub.application.core.command_service import (
        ClosedRegistrationConflict, _canonical_source_image,
    )
    source, destination, registration = _closed_import_pair(tmp_path)
    if difference == "record-binding":
        source.service._records[OTHER_RESOURCE] = replace(source.service._records[OTHER_RESOURCE], binding_revision=99)
    if difference == "record-id":
        source.service._records[OTHER_RESOURCE] = replace(source.service._records[OTHER_RESOURCE], resource_id="foreign.record")
    if difference == "scope-keys": source.service._scope_revisions["foreign.scope"] = 9
    if difference == "missing-declared-scope":
        source.service._scope_revisions.pop(MAIN_SCOPE)
        destination.service._scope_revisions.pop(MAIN_SCOPE)
        destination.service._commit_state()
    source.service._commit_state()
    durable = source.store.load()
    assert _canonical_source_image(durable) == _canonical_source_image(source.service._build_image())
    registration = replace(registration, source_revision=source.store.commit_revision,
                           source_payload_digest=_payload_digest(durable))
    before = Path(destination.store_path).read_bytes()
    revision = destination.store.commit_revision
    with pytest.raises(ClosedRegistrationConflict):
        destination.service.import_closed_registration_from(source.service, registration,
            expected_commit_revision=revision, expected_source_revision=source.store.commit_revision)
    assert Path(destination.store_path).read_bytes() == before
    assert destination.store.commit_revision == revision
    assert destination.transport.calls == source.transport.calls == []


@pytest.mark.parametrize("unbound_passive", (False, True))
def test_closed_import_partial_registration_keeps_complete_new_manual_history(tmp_path, unbound_passive):
    source, destination, registration = _closed_import_pair(tmp_path)
    if unbound_passive:
        source.service._bindings.pop(OTHER_RESOURCE)
        destination.service._bindings.pop(OTHER_RESOURCE)
    _manual(source, "subset-late-manual", target=BASE_TARGET_ON40, expected=13)
    registration = replace(registration, targets=registration.targets[:1],
        source_revision=source.store.commit_revision, source_payload_digest=_payload_digest(source.store.load()))
    assert source.service._scope_revisions != destination.service._scope_revisions
    destination.service.import_closed_registration_from(source.service, registration,
        expected_commit_revision=destination.store.commit_revision,
        expected_source_revision=source.store.commit_revision,
        controlled_resource_ids=frozenset({MAIN_RESOURCE}))
    assert destination.service.record(MAIN_RESOURCE).manual == source.service.record(MAIN_RESOURCE).manual
    assert destination.service._scope_revisions == source.service._scope_revisions
    assert set(destination.store.load()["records"]) == set(source.store.load()["records"])
    assert destination.transport.calls == source.transport.calls == []


def test_closed_import_rejects_revision_advanced_by_second_real_store(tmp_path):
    from custom_components.hausman_hub.application.core.command_service import (
        ClosedRegistrationConflict, _canonical_source_image,
    )
    source, destination, registration = _closed_import_pair(tmp_path)
    expected_source = source.store.commit_revision
    other_store = FileControlStore(source.store_path)
    same_payload = other_store.load()
    other_store.commit(expected_source, same_payload)
    durable = other_store.load()
    assert durable['commit_revision'] == expected_source + 1
    assert source.store.commit_revision == expected_source
    registration = replace(registration, source_payload_digest=_payload_digest(durable))
    before_bytes = Path(destination.store_path).read_bytes()
    before_image = _canonical_source_image(destination.store.load())
    before_memory = _canonical_source_image(destination.service._build_image())
    before_revision = destination.store.commit_revision
    with pytest.raises(ClosedRegistrationConflict):
        destination.service.import_closed_registration_from(source.service, registration,
            expected_commit_revision=before_revision, expected_source_revision=expected_source)
    assert Path(destination.store_path).read_bytes() == before_bytes
    assert destination.store.commit_revision == before_revision
    assert _canonical_source_image(destination.store.load()) == before_image
    assert _canonical_source_image(destination.service._build_image()) == before_memory
    assert destination.service.closed_registrations() == ()
    assert destination.transport.calls == source.transport.calls == []


def _manual_denial_stand(tmp_path):
    """Real file and closed recovery, with no observation after admission."""
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path, ArbitrationMode.RANKED_TARGET)
    stand.service, stand.transport = _restore_file_stand_closed(stand)
    stand.store = stand.service._store
    stand.clock = stand.service._clock
    return stand


def _submit_denied_manual(stand, request_id="durable-denial"):
    return _manual(
        stand, request_id, target=BASE_TARGET_ON40,
        expected=stand.service.record(MAIN_RESOURCE).control_revision,
    )


def _assert_manual_image_preserved(stand, image):
    memory = stand.service._build_image()
    for section in (
        "records", "receipts", "receipt_times",
        "scope_revisions",
    ):
        assert image[section] == memory[section]
    assert {key: tuple(value) for key, value in image["receipt_fingerprints"].items()} == {
        key: tuple(value) for key, value in memory["receipt_fingerprints"].items()
    }
    record = image["records"][MAIN_RESOURCE]
    assert record.authority is Authority.MANUAL
    assert record.manual is not None
    assert record.auto_proof is None
    assert stand.transport.calls == []


def test_manual_denial_is_durable_immediately_without_a_later_event(tmp_path):
    stand = _manual_denial_stand(tmp_path)
    receipt = _submit_denied_manual(stand)
    image = FileControlStore(stand.store_path).load()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.operation_id == receipt.operation_id
    assert operation.phase is OperationPhase.CANCELLED
    assert operation.reason_code == "core_not_active"
    assert image["operations"][receipt.operation_id] == operation
    assert operation.origin is Origin.MANUAL
    assert operation.target == BASE_TARGET_ON40
    assert operation.dispatch_started_at_mono_ms is None
    _assert_manual_image_preserved(stand, image)


def test_manual_denial_restart_and_replay_keep_the_same_terminal_operation(tmp_path):
    stand = _manual_denial_stand(tmp_path)
    receipt = _submit_denied_manual(stand)
    before = FileControlStore(stand.store_path).load()
    restored = restore_file_stand(stand)
    operation = restored.service.operations(MAIN_RESOURCE)[-1]
    assert operation.operation_id == receipt.operation_id
    assert operation.phase is OperationPhase.CANCELLED
    assert operation.reason_code == "core_not_active"
    replay = _manual(restored, "durable-denial", target=BASE_TARGET_ON40)
    assert replay == replace(receipt, idempotent_replay=True)
    assert len(restored.service.operations(MAIN_RESOURCE)) == 1
    after = FileControlStore(stand.store_path).load()
    for section in ("receipts", "receipt_times", "receipt_fingerprints"):
        assert after[section] == before[section]
    assert after["records"][MAIN_RESOURCE].manual == before["records"][MAIN_RESOURCE].manual
    restored.service.drain()
    assert restored.transport.calls == stand.transport.calls == []


def test_manual_denial_second_commit_failure_preserves_acceptance_without_success(tmp_path):
    stand = _manual_denial_stand(tmp_path)
    stand.store.fail_at_commit_revisions.add(stand.store.commit_revision + 2)
    with pytest.raises(StorageUnavailable):
        _submit_denied_manual(stand)
    image = FileControlStore(stand.store_path).load()
    _assert_manual_image_preserved(stand, image)
    assert len(image["receipts"]) == len(image["operations"]) == 1
    assert next(iter(image["operations"].values())).phase is OperationPhase.QUEUED
    receipt = next(iter(image["receipts"].values()))
    restored = restore_file_stand(stand)
    operation = restored.service.operations(MAIN_RESOURCE)[-1]
    assert operation.operation_id == receipt.operation_id
    assert operation.phase is OperationPhase.CANCELLED
    replay = _manual(restored, "durable-denial", target=BASE_TARGET_ON40)
    assert replay == replace(receipt, idempotent_replay=True)
    restored.service.drain()
    assert len(restored.service.operations(MAIN_RESOURCE)) == 1
    assert restored.transport.calls == stand.transport.calls == []


@pytest.mark.parametrize("fault_point", ["dir_fsync", "reread"])
def test_manual_denial_after_replace_failure_keeps_whole_manual_image(tmp_path, fault_point):
    stand = _manual_denial_stand(tmp_path)
    denial_revision = stand.store.commit_revision + 1

    def fail_denial_write(point):
        if stand.store.commit_revision == denial_revision and point == fault_point:
            raise OSError("synthetic terminal write failure")

    stand.store._fault_injector = fail_denial_write
    with pytest.raises(StorageUnavailable):
        _submit_denied_manual(stand)
    image = FileControlStore(stand.store_path).load()
    _assert_manual_image_preserved(stand, image)
    assert len(image["receipts"]) == len(image["operations"]) == 1
    receipt = next(iter(image["receipts"].values()))
    operation = image["operations"][receipt.operation_id]
    assert operation.phase in (OperationPhase.QUEUED, OperationPhase.CANCELLED)
    restored = restore_file_stand(stand)
    replay = _manual(restored, "durable-denial", target=BASE_TARGET_ON40)
    assert replay == replace(receipt, idempotent_replay=True)
    restored.service.drain()
    assert len(restored.service.operations(MAIN_RESOURCE)) == 1
    assert restored.transport.calls == stand.transport.calls == []


def test_manual_denial_first_commit_failure_has_no_acceptance(tmp_path):
    stand = _manual_denial_stand(tmp_path)
    before = FileControlStore(stand.store_path).load()
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable):
        _submit_denied_manual(stand)
    assert FileControlStore(stand.store_path).load() == before
    assert not stand.service.operations(MAIN_RESOURCE)
    stand.service.drain()
    assert stand.transport.calls == []


@pytest.mark.parametrize("closed_restore", [False, True])
def test_manual_terminal_at_acceptance_needs_no_extra_commit(tmp_path, closed_restore):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_file_stand(tmp_path, ArbitrationMode.RANKED_TARGET)
    if closed_restore:
        stand.service.close_dispatch_for_restore()
    revision = stand.store.commit_revision
    receipt = _manual(
        stand, "already-terminal", target=BASE_TARGET_ON40 if closed_restore else TARGET_OFF,
        expected=stand.service.record(MAIN_RESOURCE).control_revision,
    )
    assert stand.store.commit_revision == revision + 1
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is (OperationPhase.CANCELLED if closed_restore else OperationPhase.NO_OP)
    assert FileControlStore(stand.store_path).load()["operations"][receipt.operation_id] == operation
    assert stand.transport.calls == []
