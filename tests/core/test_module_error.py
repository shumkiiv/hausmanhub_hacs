"""K04: a failed pure module cannot leave automatic work executable."""
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import (
    CONTROL_REVISION_START, MAIN_RESOURCE, OTHER_ENTITY, OTHER_RESOURCE,
    OWNER_MODULE, TARGET_OFF, build_stand, owner_proposal,
    restore_file_stand,
)
from custom_components.hausman_hub.application.core.command_service import StorageUnavailable
from custom_components.hausman_hub.domain.core.permit import PermitDenial
from custom_components.hausman_hub.domain.core.models import (
    ArbitrationMode, Evaluation, LightStateTarget, OperationPhase, Origin,
)


def queued(*, other=False, file_path=None):
    modules = {OWNER_MODULE: MAIN_RESOURCE}
    if other:
        modules["test.other_module"] = OTHER_RESOURCE
    stand = build_stand(ArbitrationMode.RANKED_TARGET, module_specs=modules,
                        use_file_store=file_path is not None, store_path=file_path)
    for module_id, resource in modules.items():
        stand.modules[module_id].queue_proposal(owner_proposal(
            module_id=module_id, resource_id=resource,
            proposal_id=module_id + ".on",
        ))
        stand.service.process_module_event(module_id, "accepted")
    assert stand.service._needs[OWNER_MODULE]
    return stand


def crash(stand, monkeypatch):
    def fail(frame, previous_memory):
        raise RuntimeError("synthetic algorithm error")
    monkeypatch.setattr(stand.modules[OWNER_MODULE], "evaluate", fail)
    with pytest.raises(RuntimeError, match="synthetic algorithm error"):
        stand.service.process_module_event(OWNER_MODULE, "failed")


def test_module_exception_retires_its_previous_nonempty_demands(monkeypatch):
    stand = queued()
    crash(stand, monkeypatch)
    assert stand.service._needs.get(OWNER_MODULE, ()) == ()
    assert stand.transport.calls == []


@pytest.mark.parametrize("phase", [
    OperationPhase.QUEUED, OperationPhase.TAKEN, OperationPhase.DISPATCHING,
])
def test_module_exception_closes_already_prepared_work(monkeypatch, phase):
    stand = queued()
    stand.clock.advance(1)
    if phase is not OperationPhase.QUEUED:
        stand.service.take_ready()
    if phase is OperationPhase.DISPATCHING:
        stand.service.run_taken()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is phase
    assert stand.transport.calls == []
    crash(stand, monkeypatch)
    stand.service.drain()
    assert stand.transport.calls == []
    assert operation.phase is OperationPhase.CANCELLED


def test_module_exception_keeps_other_demands_and_control_records(monkeypatch):
    stand = queued(other=True)
    other_needs = stand.service._needs["test.other_module"]
    records = {resource: replace(stand.service.record(resource))
               for resource in (MAIN_RESOURCE, OTHER_RESOURCE)}
    crash(stand, monkeypatch)
    assert stand.service._needs["test.other_module"] == other_needs
    for resource, record in records.items():
        assert stand.service.record(resource) == record
    assert stand.transport.calls == []


def test_module_exception_is_not_a_global_or_manual_barrier(monkeypatch):
    stand = queued(other=True)
    receipt = stand.service.submit_manual(
        resource_ref=MAIN_RESOURCE, request_id="error-manual",
        action_id="manual.error", target=TARGET_OFF, source="test.ui",
        expected_control_revision=CONTROL_REVISION_START,
    )
    manual = stand.service.record(MAIN_RESOURCE).manual
    assert manual is not None
    crash(stand, monkeypatch)
    assert stand.service.record(MAIN_RESOURCE).manual == manual
    assert stand.service.receipt("error-manual") == receipt
    stand.clock.advance(1)
    stand.service.drain()
    assert stand.transport.calls == [
        ("light", "turn_on", OTHER_ENTITY, {"brightness_pct": 40}),
    ]
    assert stand.service.record(MAIN_RESOURCE).manual == manual


def test_successful_diagnostic_evaluation_cannot_republish_demands_or_memory(monkeypatch):
    stand = queued()
    accepted_memory = stand.service._module_memory.get(OWNER_MODULE)
    crash(stand, monkeypatch)
    seen = []
    def diagnostic(frame, memory):
        seen.append(memory)
        return Evaluation(module_id=OWNER_MODULE, config_revision=2, input_revision=2,
            proposals=(owner_proposal(proposal_id="diagnostic"),),
            next_memory=("not accepted",))
    monkeypatch.setattr(stand.modules[OWNER_MODULE], "evaluate", diagnostic)
    revision = stand.store.commit_revision
    stand.service.process_module_event(OWNER_MODULE, "diagnostic")
    assert seen == [accepted_memory]
    assert stand.service._needs.get(OWNER_MODULE, ()) == ()
    assert stand.service._module_memory.get(OWNER_MODULE) == accepted_memory
    assert stand.store.commit_revision == revision
    stand.service.drain()
    assert stand.transport.calls == []


@pytest.mark.parametrize("unknown", [False, True])
def test_error_preserves_already_sent_history(monkeypatch, unknown):
    stand = queued()
    stand.clock.advance(1)
    stand.service.drain()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.OBSERVING
    if unknown:
        stand.clock.advance(100_000)
        stand.service.advance_timeouts()
        assert operation.phase is OperationPhase.UNKNOWN
    before = replace(operation)
    crash(stand, monkeypatch)
    assert operation == before
    assert len(stand.transport.calls) == 1


def test_error_does_not_cancel_manual_dispatching_on_same_resource(monkeypatch):
    stand = queued()
    with monkeypatch.context() as patch:
        patch.setattr(stand.service, "run_dispatching", lambda: None)
        receipt = stand.service.submit_manual(resource_ref=MAIN_RESOURCE,
            request_id="same-resource-manual", action_id="manual.same",
            target=LightStateTarget(on=True), source="test.ui",
            expected_control_revision=CONTROL_REVISION_START)
    operation = next(op for op in stand.service.operations(MAIN_RESOURCE)
                     if op.operation_id == receipt.operation_id)
    assert not operation.origin_automation and operation.phase is OperationPhase.DISPATCHING
    before = replace(operation)
    pointer = stand.service._dispatching[MAIN_RESOURCE]
    crash(stand, monkeypatch)
    assert operation == before and stand.service._dispatching[MAIN_RESOURCE] == pointer
    assert stand.service.receipt("same-resource-manual") == receipt
    stand.service.run_dispatching()
    assert operation.phase is OperationPhase.OBSERVING
    assert len(stand.transport.calls) == 1


def test_error_does_not_cancel_foreign_taken_on_same_resource(monkeypatch):
    other = "test.module_b"
    stand = build_stand(ArbitrationMode.RANKED_TARGET,
        module_specs={OWNER_MODULE: MAIN_RESOURCE, other: MAIN_RESOURCE})
    for module_id, level, priority in ((OWNER_MODULE, 40, 50), (other, 60, 60)):
        stand.modules[module_id].queue_proposal(replace(owner_proposal(
            module_id=module_id, proposal_id=module_id + ".same"),
            target=LightStateTarget(on=True, brightness_pct=level), policy_priority=priority))
        stand.service.process_module_event(module_id, "same")
    stand.clock.advance(1)
    stand.service.take_ready()
    operation = stand.service._operations[stand.service._taken[MAIN_RESOURCE]]
    assert operation.module_id == other and operation.phase is OperationPhase.TAKEN
    before = replace(operation)
    crash(stand, monkeypatch)
    assert operation == before and stand.service._taken[MAIN_RESOURCE] == operation.operation_id
    stand.service.run_taken()
    stand.service.run_dispatching()
    assert operation.phase is OperationPhase.OBSERVING
    assert len(stand.transport.calls) == 1


def test_cancelled_prepared_work_is_immediately_durable(tmp_path, monkeypatch):
    stand = queued(file_path=tmp_path / "cancel.json")
    stand.service.take_ready()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    crash(stand, monkeypatch)
    durable = stand.store.load()["operations"][operation.operation_id]
    assert durable.phase is OperationPhase.CANCELLED
    restored = restore_file_stand(stand)
    restored.service.drain()
    assert restored.transport.calls == stand.transport.calls == []


@pytest.mark.parametrize("dispatching", [False, True])
@pytest.mark.parametrize("restart", [False, True])
def test_failed_cancel_write_keeps_gate_and_restart_never_replays(tmp_path, monkeypatch, dispatching, restart):
    stand = queued(file_path=tmp_path / "failed-cancel.json")
    receipt = stand.service.submit_manual(resource_ref=OTHER_RESOURCE,
        request_id="preserved-during-failure", action_id="manual.failure",
        target=TARGET_OFF, source="test.ui", expected_control_revision=CONTROL_REVISION_START)
    manual = stand.service.record(OTHER_RESOURCE).manual
    assert manual is not None
    stand.service.take_ready()
    if dispatching:
        stand.service.run_taken()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    before_file = Path(stand.store_path).read_bytes()
    def fail(frame, memory):
        raise RuntimeError("algorithm and store fail")
    monkeypatch.setattr(stand.modules[OWNER_MODULE], "evaluate", fail)
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable) as error:
        stand.service.process_module_event(OWNER_MODULE, "storage-failure")
    assert isinstance(error.value.__cause__, RuntimeError)
    assert Path(stand.store_path).read_bytes() == before_file
    assert stand.service._needs.get(OWNER_MODULE, ()) == ()
    assert stand.service.record(OTHER_RESOURCE).manual == manual
    assert stand.service.receipt("preserved-during-failure") == receipt
    if restart:
        # Simulate restart immediately at the failed-commit boundary, not
        # after a later old-instance tick has durably saved cancellation.
        restored = restore_file_stand(stand)
        restored_operation = restored.service.operations(MAIN_RESOURCE)[-1]
        assert restored_operation.phase is (OperationPhase.UNKNOWN if dispatching else OperationPhase.CANCELLED)
        restored.service.drain()
        assert restored.transport.calls == []
        assert restored.service.record(OTHER_RESOURCE).manual == manual
        assert restored.service.receipt("preserved-during-failure") == receipt
    else:
        # Independent history: do not run two writers against the same file.
        stand.service.drain()
    assert stand.transport.calls == []


def test_repeated_algorithm_exception_does_not_retry_its_completed_cancel_commit(monkeypatch):
    stand = queued()
    crash(stand, monkeypatch)
    revision = stand.store.commit_revision
    crash(stand, monkeypatch)
    assert stand.store.commit_revision == revision
    assert stand.service._needs.get(OWNER_MODULE, ()) == ()
    assert stand.transport.calls == []


def test_error_during_transport_preserves_possible_dispatch_history(monkeypatch):
    stand = queued()
    phases = []
    def at_transport():
        operation = stand.service.operations(MAIN_RESOURCE)[-1]
        assert operation.phase is OperationPhase.DISPATCHING
        assert operation.dispatch_started_at_mono_ms is not None
        crash(stand, monkeypatch)
        phases.append(operation.phase)
    stand.transport.on_call = at_transport
    stand.clock.advance(1)
    stand.service.drain()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert phases == [OperationPhase.DISPATCHING]
    assert operation.phase is OperationPhase.OBSERVING
    assert len(stand.transport.calls) == 1


def test_final_permit_blocks_failed_module_independently_of_cancelled_phase(monkeypatch):
    stand = queued(other=True)
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    crash(stand, monkeypatch)
    assert operation.phase is OperationPhase.CANCELLED
    stale = replace(operation, phase=OperationPhase.TAKEN)
    denial = stand.service._final_permit(stale)
    assert isinstance(denial, PermitDenial)
    assert denial.reason_code == "module_error"
    assert (denial.operation_id, denial.resource_id) == (stale.operation_id, MAIN_RESOURCE)
    other = stand.service.operations(OTHER_RESOURCE)[-1]
    decision = stand.service._final_permit(other)
    assert getattr(decision, "reason_code", None) != "module_error"
    assert stand.transport.calls == []


def confirmed_owner_on(*, file_path=None, other=False):
    stand = queued(file_path=file_path, other=other)
    stand.clock.advance(1)
    stand.service.drain()
    stand.ingest(MAIN_RESOURCE, sequence=2, mono_ms=100,
                 properties={"on": True, "brightness_pct": 40})
    assert stand.service.record(MAIN_RESOURCE).auto_proof is not None
    assert stand.service.operations(MAIN_RESOURCE)[-1].phase is OperationPhase.CONFIRMED
    return stand


def prepare_owner_idle(stand):
    stand.service.process_module_event(OWNER_MODULE, "withdraw")
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.origin is Origin.AUTOMATION
    assert operation.module_id is None and operation.target == TARGET_OFF
    return operation


def test_failed_owner_cannot_turn_confirmed_light_off_via_idle(monkeypatch):
    stand = confirmed_owner_on()
    proof = stand.service.record(MAIN_RESOURCE).auto_proof
    before = tuple(stand.service.operations(MAIN_RESOURCE))
    calls = list(stand.transport.calls)
    selected = stand.service._selected_target[MAIN_RESOURCE]
    crash(stand, monkeypatch)
    stand.service.evaluate_resource(MAIN_RESOURCE)
    stand.service.drain()
    assert stand.transport.calls == calls
    assert tuple(stand.service.operations(MAIN_RESOURCE)) == before
    assert stand.service._selected_target[MAIN_RESOURCE] == selected
    explanation = stand.service._explanations[MAIN_RESOURCE]
    assert explanation.target_result is None
    assert explanation.result_reason == "module_error"
    assert stand.service.record(MAIN_RESOURCE).auto_proof == proof


@pytest.mark.parametrize("phase", [
    OperationPhase.QUEUED, OperationPhase.TAKEN, OperationPhase.DISPATCHING,
])
def test_error_cancels_prepared_idle_without_module_id(monkeypatch, phase):
    stand = confirmed_owner_on()
    operation = prepare_owner_idle(stand)
    if phase is not OperationPhase.QUEUED:
        stand.service.take_ready()
    if phase is OperationPhase.DISPATCHING:
        stand.service.run_taken()
    assert operation.phase is phase
    calls = list(stand.transport.calls)
    crash(stand, monkeypatch)
    assert operation.phase is OperationPhase.CANCELLED
    stale = replace(operation, phase=OperationPhase.TAKEN)
    denial = stand.service._final_permit(stale)
    assert isinstance(denial, PermitDenial) and denial.reason_code == "module_error"
    stand.service.drain()
    assert stand.transport.calls == calls


def test_healthy_owner_idle_still_turns_light_off():
    stand = confirmed_owner_on()
    operation = prepare_owner_idle(stand)
    calls = len(stand.transport.calls)
    stand.service.drain()
    assert len(stand.transport.calls) == calls + 1
    assert operation.phase is OperationPhase.OBSERVING
    assert stand.transport.calls[-1][1] == "turn_off"


def test_idle_final_permit_checks_owner_even_for_unregistered_stale_copy(monkeypatch):
    stand = confirmed_owner_on()
    operation = prepare_owner_idle(stand)
    stale = replace(operation, phase=OperationPhase.TAKEN)
    crash(stand, monkeypatch)
    decision = stand.service._final_permit(stale)
    assert isinstance(decision, PermitDenial)
    assert decision.reason_code == "module_error"


def test_failed_owner_does_not_block_new_foreign_choice_on_same_resource(monkeypatch):
    other = "test.module_b"
    stand = build_stand(ArbitrationMode.RANKED_TARGET,
        module_specs={OWNER_MODULE: MAIN_RESOURCE, other: MAIN_RESOURCE})
    crash(stand, monkeypatch)
    stand.modules[other].queue_proposal(owner_proposal(
        module_id=other, proposal_id="foreign.after.error"))
    stand.service.process_module_event(other, "healthy")
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.module_id == other
    stand.clock.advance(1)
    stand.service.drain()
    assert operation.phase is OperationPhase.OBSERVING
    assert len(stand.transport.calls) == 1


def test_idle_cancellation_is_durable_without_erasing_proof(tmp_path, monkeypatch):
    stand = confirmed_owner_on(file_path=tmp_path / "idle-cancel.json")
    operation = prepare_owner_idle(stand)
    proof = stand.service.record(MAIN_RESOURCE).auto_proof
    crash(stand, monkeypatch)
    assert stand.store.load()["operations"][operation.operation_id].phase is OperationPhase.CANCELLED
    assert stand.service.record(MAIN_RESOURCE).auto_proof == proof
    restored = restore_file_stand(stand)
    restored.service.drain()
    assert restored.transport.calls == []


@pytest.mark.parametrize("restart", [False, True])
def test_idle_cancel_write_failure_stays_closed(tmp_path, monkeypatch, restart):
    stand = confirmed_owner_on(file_path=tmp_path / "idle-failed.json")
    operation = prepare_owner_idle(stand)
    stand.service.take_ready()
    stand.service.run_taken()
    assert operation.phase is OperationPhase.DISPATCHING
    calls = list(stand.transport.calls)
    before_file = Path(stand.store_path).read_bytes()
    stand.store.fail_next_commit = True
    with pytest.raises(StorageUnavailable):
        crash(stand, monkeypatch)
    assert Path(stand.store_path).read_bytes() == before_file
    if restart:
        restored = restore_file_stand(stand)
        assert restored.service.operations(MAIN_RESOURCE)[-1].phase is OperationPhase.UNKNOWN
        restored.service.drain()
        assert restored.transport.calls == []
    else:
        stand.service.drain()
    assert stand.transport.calls == calls
