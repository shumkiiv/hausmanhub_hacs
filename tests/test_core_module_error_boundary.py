"""K04 failure reaches the real closed branch and runtime callback."""
import asyncio

import pytest

from test_core_local_handoff_matrix import branch_at
from test_core_stop_drain import started, sensor_event
from custom_components.hausman_hub.domain.core.models import (
    Evaluation, LightStateTarget, Proposal, ProposalKind,
)


def seed_then_crash(branch, monkeypatch):
    def accepted(frame, previous_memory):
        return Evaluation(
            module_id=branch._module_id, config_revision=frame.config_revision,
            input_revision=1, proposals=(Proposal(
                proposal_id="synthetic.before.failure", module_id=branch._module_id,
                resource_id="test_main", kind=ProposalKind.TARGET,
                target=LightStateTarget(on=True), policy_priority=50,
                created_at_mono_ms=frame.now_mono_ms,
                valid_until_mono_ms=frame.now_mono_ms + 5_000,
                input_revision=1, config_revision=frame.config_revision,
                reason_code="synthetic_test",
            ),),
        )
    monkeypatch.setattr(branch._module, "evaluate", accepted)
    branch.core_service.process_module_event(branch._module_id, "seed")
    assert branch.core_service._needs[branch._module_id]

    def failed(frame, previous_memory):
        raise RuntimeError("synthetic branch module error")
    monkeypatch.setattr(branch._module, "evaluate", failed)


def test_real_branch_tick_retires_failed_module_demands(tmp_path, monkeypatch):
    branch, _, _ = branch_at(tmp_path, "module-error")
    branch.activate_shadow()
    seed_then_crash(branch, monkeypatch)
    with pytest.raises(RuntimeError, match="synthetic branch module error"):
        branch.process_schedule_tick()
    assert branch.core_service._needs.get(branch._module_id, ()) == ()
    assert not branch.physical_send_enabled()


@pytest.mark.asyncio
async def test_runtime_worker_error_retires_real_branch_demands(tmp_path, monkeypatch):
    config, hass, intake, executor, runtime, hooks, branch = await started(tmp_path)
    try:
        seed_then_crash(branch, monkeypatch)
        manual = branch.core_service.record("test_main").manual
        runtime._state_event(sensor_event())
        results = await asyncio.gather(*list(hass.tasks), return_exceptions=True)
        assert any(isinstance(result, RuntimeError) for result in results)
        assert branch.core_service._needs.get(branch._module_id, ()) == ()
        assert branch.core_service.record("test_main").manual == manual
        assert executor.calls == [] and not branch.physical_send_enabled()
    finally:
        await runtime.stop()
