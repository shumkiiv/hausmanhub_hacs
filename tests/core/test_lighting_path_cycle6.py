"""Cycle-6 discrimination tests (AUD-LP-ISO01): serialization, stop drain,
manual holds in frames, entity-free ids, store commit lock and bounded
history. Each test fails against the previous candidate behavior."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

from conftest import (
    BASE_TARGET_ON40,
    FakeClock,
    MAIN_RESOURCE,
    arbitration_mode_from_flag,
    build_stand,
)
from test_lighting_plugin import _config
from test_lighting_path import _branch, _state

from custom_components.hausman_hub.adapters.core.control_store import (
    CommitConflict,
    FileControlStore,
)
from custom_components.hausman_hub.adapters.core.room_lighting import (
    EntityBinding,
    RoomLightingInputAdapter,
)
from custom_components.hausman_hub.application.core.command_service import (
    RETAINED_TERMINAL_OPERATIONS,
    Origin,
)
from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig,
    ManualHoldView,
    RoomLightingCoreBranch,
)
from custom_components.hausman_hub.domain.core.models import (
    Authority,
    Evaluation,
    LightStateTarget,
    Operation,
    OperationPhase,
    Proposal,
    ProposalKind,
)


def _branch_with_provider(tmp_path, provider):
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id="test_room",
            config=_config(),
            timezone="Asia/Omsk",
            sunrise="08:00",
            sunset="18:00",
        ),
        store_path=tmp_path / "core_shadow_test_room.json",
        manual_holds_provider=provider,
    )
    branch.activate_shadow()
    return branch


def _hold(entity_id="light.test_main", *, until_offset_ms=3_600_000):
    now_ms = int(time.time() * 1000)
    return ManualHoldView(
        entity_id=entity_id,
        action="on",
        source="test.authority",
        proven_at_utc_ms=now_ms - 1000,
        priority_until_utc_ms=now_ms + until_offset_ms,
        evidence_id=f"fp.{entity_id}",
    )


# --- 1. serialization of branch input (AUD-LP-ISO01 cycle 6) ----------


def test_branch_input_is_serialized_under_threads(tmp_path):
    branch = _branch(tmp_path)
    violations: list[str] = []
    processed = {"n": 0}
    active = {"thread": None}
    bookkeeping = threading.Lock()
    original = branch._service.process_module_event

    def instrumented(module_id, event_id):
        with bookkeeping:
            if active["thread"] is not None:
                violations.append(event_id)
            active["thread"] = threading.current_thread()
        time.sleep(0.002)
        try:
            return original(module_id, event_id)
        finally:
            with bookkeeping:
                active["thread"] = None
                processed["n"] += 1

    branch._service.process_module_event = instrumented
    state = _state("on")

    def worker():
        for _ in range(20):
            branch.handle_runtime_event(
                "binary_sensor.test_presence", None, state
            )

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert violations == []
    assert processed["n"] == 60


# --- 2. stop waits for in-flight core input ---------------------------


class _RecordingBranch:
    """Port double: slow processing, records order and the stop hook."""

    def __init__(self, inner):
        self._inner = inner
        self.record: list[str] = []

    def __call__(self, entity_id, old_state, new_state):
        time.sleep(0.05)
        self._inner(entity_id, old_state, new_state)
        self.record.append(entity_id)

    def on_runtime_stop(self):
        self.record.append("stop")


class _FakeHass:
    def __init__(self, loop):
        self.loop = loop

    def async_create_task(self, coroutine):
        return self.loop.create_task(coroutine)


async def test_stop_awaits_in_flight_core_input(tmp_path):
    from custom_components.hausman_hub.application.room_lighting_runtime import (
        RoomLightingRuntime,
    )

    branch = _branch(tmp_path)
    slow = _RecordingBranch(branch)
    loop = asyncio.get_running_loop()
    runtime = RoomLightingRuntime(
        hass=_FakeHass(loop),
        service=MagicMock(),
        shadow=MagicMock(),
    )
    runtime._running = True
    runtime.set_core_input_port(slow)

    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.test_presence",
        "old_state": None,
        "new_state": _state("on"),
    }
    runtime._state_event(event)
    runtime._state_event(event)
    runtime._state_event(event)

    await runtime.stop()

    # All in-flight events were processed BEFORE the stop hook ran.
    assert slow.record[:3] == [
        "binary_sensor.test_presence",
        "binary_sensor.test_presence",
        "binary_sensor.test_presence",
    ]
    assert slow.record[3] == "stop"
    assert runtime._core_input_closed is True
    assert runtime._core_input_tasks == set()

    # A direct dispatch after stop never reaches the branch.
    coroutine = runtime._async_core_input("binary_sensor.test_presence", {})
    runtime._dispatch_core_input(coroutine)
    await asyncio.sleep(0)
    assert len(slow.record) == 4


# --- 3. manual holds reach the frame and the decisions ----------------


def _scripted_demand(branch):
    """Replace the module with a scripted TARGET demand on test_main.

    The engine policy is out of scope here: cycle 6 is about the record
    transfer and the visible arbitration refusal, so the module returns
    one deterministic demand regardless of the engine inputs."""

    module_id = branch._module.module_id

    def evaluate(frame, memory):
        return Evaluation(
            module_id=module_id,
            config_revision=frame.config_revision,
            input_revision=1,
            proposals=(
                Proposal(
                    proposal_id=f"{module_id}.scripted",
                    module_id=module_id,
                    resource_id="test_main",
                    kind=ProposalKind.TARGET,
                    target=LightStateTarget(
                        on=True, brightness_pct=None,
                        color_temperature_k=None,
                    ),
                    policy_priority=50,
                    created_at_mono_ms=frame.now_mono_ms,
                    valid_until_mono_ms=frame.now_mono_ms + 5000,
                    input_revision=1,
                    config_revision=frame.config_revision,
                    reason_code="presence_on",
                ),
            ),
        )

    return evaluate


def test_manual_hold_is_in_frame_and_blocks_target(tmp_path):
    frames = []
    branch = _branch_with_provider(
        tmp_path, lambda: {"light.test_main": _hold()}
    )
    branch._module.evaluate = _scripted_demand(branch)
    original = branch._module.evaluate

    def recording(frame, memory):
        frames.append(frame)
        return original(frame, memory)

    branch._module.evaluate = recording
    branch.handle_runtime_event(
        "binary_sensor.test_presence", None, _state("on")
    )

    assert frames
    controls = {
        record.resource_id: record for record in frames[-1].resource_controls
    }
    assert controls["test_main"].authority is Authority.MANUAL
    assert controls["test_main"].manual is not None
    assert controls["test_main"].manual.action == "on"

    explanation = branch._service.explanation("test_main")
    assert explanation is not None
    assert explanation.target_result is None
    assert explanation.rejected
    assert all(
        need.reason_code == "manual_priority" for need in explanation.rejected
    )


def test_unavailable_manual_authority_is_fail_closed(tmp_path):
    branch = _branch_with_provider(tmp_path, lambda: None)
    branch._module.evaluate = _scripted_demand(branch)
    branch.handle_runtime_event(
        "binary_sensor.test_presence", None, _state("on")
    )

    record = branch._service.record("test_main")
    assert record.authority is Authority.UNCERTAIN
    assert record.uncertainty_reason == "manual_authority_unavailable"
    # Sensors keep their baseline: holds are a light-target property.
    sensor = branch._service.record("sensor_presence")
    assert sensor.authority is Authority.AUTOMATIC_ELIGIBLE
    assert sensor.uncertainty_reason is None

    explanation = branch._service.explanation("test_main")
    assert explanation.rejected
    assert all(
        need.reason_code == "uncertain_rights"
        for need in explanation.rejected
    )


def test_expired_hold_restores_automatic_baseline(tmp_path):
    branch = _branch_with_provider(
        tmp_path, lambda: {"light.test_main": _hold(until_offset_ms=-1000)}
    )
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    record = branch._service.record("test_main")
    assert record.authority is Authority.AUTOMATIC_ELIGIBLE
    assert record.manual is None
    assert record.uncertainty_reason is None


def test_hold_release_after_recovery(tmp_path):
    holder = {"holds": {"light.test_main": _hold()}}
    branch = _branch_with_provider(tmp_path, lambda: holder["holds"])
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    assert (
        branch._service.record("test_main").authority is Authority.MANUAL
    )
    holder["holds"] = {}
    branch.handle_runtime_event("binary_sensor.test_motion", None, _state("on"))
    record = branch._service.record("test_main")
    assert record.authority is Authority.AUTOMATIC_ELIGIBLE
    assert record.manual is None


def test_core_manual_holds_fail_closed_on_unhealthy_authority():
    from custom_components.hausman_hub.application.room_lighting_runtime import (
        RoomLightingRuntime,
    )

    class _UnhealthyAuthority:
        @property
        def unhealthy(self) -> bool:
            return True

    runtime = RoomLightingRuntime(
        hass=None,
        service=MagicMock(),
        shadow=MagicMock(),
        manual_light_authority=_UnhealthyAuthority(),
    )
    # unhealthy is a bool property on the real authority: the provider
    # must treat it as unavailable (LP-C6-R3).
    assert runtime.core_manual_holds() is None


def test_foreign_uncertainty_is_never_cleared_by_hold_transfer(tmp_path):
    from dataclasses import replace as dc_replace

    branch = _branch_with_provider(tmp_path, lambda: {})
    record = branch._service.record("test_main")
    branch._service.update_control_record(
        "test_main",
        dc_replace(
            record,
            authority=Authority.UNCERTAIN,
            uncertainty_reason="clock_uncertain",
        ),
    )
    branch.handle_runtime_event(
        "binary_sensor.test_presence", None, _state("on")
    )
    kept = branch._service.record("test_main")
    assert kept.authority is Authority.UNCERTAIN
    assert kept.uncertainty_reason == "clock_uncertain"

    held_branch = _branch_with_provider(
        tmp_path, lambda: {"light.test_main": _hold()}
    )
    held_record = held_branch._service.record("test_main")
    held_branch._service.update_control_record(
        "test_main",
        dc_replace(
            held_record,
            authority=Authority.UNCERTAIN,
            uncertainty_reason="clock_uncertain",
        ),
    )
    held_branch.handle_runtime_event(
        "binary_sensor.test_presence", None, _state("on")
    )
    held = held_branch._service.record("test_main")
    assert held.authority is Authority.MANUAL
    assert held.manual is not None
    assert held.uncertainty_reason == "clock_uncertain"


# --- 4. observation ids are resource-scoped ---------------------------


def test_observation_ids_are_unique_and_entity_free():
    clock = FakeClock()
    clock.mono_ms = 1234
    clock.utc_ms = 1234
    adapter = RoomLightingInputAdapter(
        bindings={
            "binary_sensor.x": EntityBinding(
                resource_id="sensor_x", property="occupancy", unit="bool"
            )
        },
        clock=clock,
        source_epoch="room.shadow",
    )
    state = MagicMock()
    state.state = "on"
    state.attributes = {}

    first = adapter.observation("binary_sensor.x", None, state)
    second = adapter.observation("binary_sensor.x", None, state)

    assert first is not None and second is not None
    # Same resource at the same millisecond still yields distinct ids.
    assert first.observation_id != second.observation_id
    for observation in (first, second):
        assert "binary_sensor" not in observation.observation_id
        assert "binary_sensor" not in observation.source_id
        assert observation.observation_id.startswith("obs-sensor_x-")
        assert observation.source_id == "ha_state:sensor_x"


# --- 5. the file store commit is indivisible --------------------------


def test_store_commit_serializes_concurrent_writers(tmp_path):
    stand = build_stand(arbitration_mode_from_flag(False), store_path=None)
    store = FileControlStore(tmp_path / "commit_lock.json")
    # Seed one valid image through the service's own builder.
    image = stand.service._build_image()
    store.commit(store.commit_revision, image)

    outcomes = {"ok": 0, "conflict": 0}
    original = store._commit_locked
    gate = threading.Lock()

    def slow_locked(expected, state):
        time.sleep(0.01)
        original(expected, state)

    store._commit_locked = slow_locked

    def worker():
        try:
            # Both workers race with the same expected revision: exactly
            # one CAS may win, the write itself must never interleave.
            with gate:
                expected = store.commit_revision
            store.commit(expected, image)
            outcomes["ok"] += 1
        except CommitConflict:
            outcomes["conflict"] += 1

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes == {"ok": 1, "conflict": 1}
    # The store stays healthy and readable after the race.
    assert store.healthy() is True
    assert store.load() is not None


# --- 6. bounded history retention -------------------------------------


def _operation(operation_id, phase):
    return Operation(
        operation_id=operation_id,
        resource_id=MAIN_RESOURCE,
        decision_id=f"dec.{operation_id}",
        origin=Origin.AUTOMATION,
        target=BASE_TARGET_ON40,
        generation=1,
        control_revision=1,
        config_revision=1,
        binding_revision=1,
        scope_revisions={},
        created_at_mono_ms=0,
        phase=phase,
    )


def test_terminal_history_is_pruned_unresolved_is_kept():
    stand = build_stand(arbitration_mode_from_flag(False))
    service = stand.service
    total = RETAINED_TERMINAL_OPERATIONS + 100
    for index in range(total):
        operation_id = f"op.{index}"
        service._operations[operation_id] = _operation(
            operation_id, OperationPhase.FAILED
        )
        service._confirm_keys[operation_id] = ("key", operation_id)
    # Unknown and unresolved outcomes stay regardless of age.
    service._operations["op.unknown"] = _operation(
        "op.unknown", OperationPhase.UNKNOWN
    )
    service._operations["op.queued"] = _operation(
        "op.queued", OperationPhase.QUEUED
    )

    service._commit_state()

    failed = [
        operation_id
        for operation_id, operation in service._operations.items()
        if operation.phase is OperationPhase.FAILED
    ]
    assert len(failed) == RETAINED_TERMINAL_OPERATIONS
    # The oldest resolved entries are gone, the newest are retained.
    assert "op.0" not in service._operations
    assert "op.0" not in service._confirm_keys
    assert f"op.{total - 1}" in service._operations
    # Protected outcomes survive pruning.
    assert "op.unknown" in service._operations
    assert "op.queued" in service._operations


def test_no_op_and_rejected_history_is_pruned_too():
    from custom_components.hausman_hub.application.core import (
        command_service as command_service_module,
    )

    stand = build_stand(arbitration_mode_from_flag(False))
    service = stand.service
    for index in range(RETAINED_TERMINAL_OPERATIONS + 60):
        service._operations[f"op.failed.{index}"] = _operation(
            f"op.failed.{index}", OperationPhase.FAILED
        )
    for index in range(30):
        service._operations[f"op.noop.{index}"] = _operation(
            f"op.noop.{index}", OperationPhase.NO_OP
        )
        service._operations[f"op.rejected.{index}"] = _operation(
            f"op.rejected.{index}", OperationPhase.REJECTED
        )

    service._commit_state()

    resolved = [
        operation
        for operation in service._operations.values()
        if operation.phase in command_service_module._RESOLVED_PHASES
    ]
    # Every resolved phase is inside one bounded window (LP-C6-R2).
    assert len(resolved) <= RETAINED_TERMINAL_OPERATIONS
    assert "op.noop.0" in service._operations
    assert "op.rejected.0" in service._operations
    assert "op.failed.0" not in service._operations


def test_prune_keeps_operation_index_consistent(monkeypatch):
    """LP-C6-R1 repro: eviction must remove the id from every index,
    or the next arbitration/ingest crashes on a dangling id."""

    from custom_components.hausman_hub.application.core import (
        command_service as command_service_module,
    )

    monkeypatch.setattr(
        command_service_module, "RETAINED_TERMINAL_OPERATIONS", 40
    )
    stand = build_stand(arbitration_mode_from_flag(False))
    service = stand.service
    for index in range(120):
        operation_id = f"op.{index}"
        service._operations[operation_id] = _operation(
            operation_id, OperationPhase.SUPERSEDED
        )
        service._resource_ops[MAIN_RESOURCE].append(operation_id)
        service._confirm_keys[operation_id] = ("key", operation_id)

    service._commit_state()

    operations = service._operations
    resolved_count = sum(
        1
        for operation in operations.values()
        if operation.phase in command_service_module._RESOLVED_PHASES
    )
    assert resolved_count <= 40
    for resource_id, ids in service._resource_ops.items():
        for operation_id in ids:
            assert operation_id in operations, (
                f"dangling index entry {operation_id} for {resource_id}"
            )
    # The service still arbitrates and ingests after pruning.
    stand.ingest(
        MAIN_RESOURCE, sequence=99, mono_ms=1000, properties={"on": True}
    )
    assert "test.main" in service._facts
