"""Common manual admission at the branch level (S-06.a, HH-014.b).

Every test drives the production branch: the synthetic binary profile,
the core manual intake semantics on its service, the legacy-holds
transfer guard and the typed capabilities refusal. The transport is the
production ClosedTransport — nothing physical can be sent here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from custom_components.hausman_hub.adapters.core.control_store import (
    FileControlStore,
)
from custom_components.hausman_hub.application.core.command_service import (
    IdempotencyConflict,
    ManualStatus,
    RevisionConflict,
    StorageUnavailable,
    UnknownResource,
)
from custom_components.hausman_hub.application.core_manual_input import (
    SYNTHETIC_PROFILE_MAX_AGE_MS,
    CoreManualInput,
    CoreManualInputError,
    CoreManualTargetBinding,
    synthetic_binary_light_profiles,
)
from custom_components.hausman_hub.application.room_lighting_core import (
    CORE_MANUAL_HOLD_SOURCE,
    CoreBranchConfig,
    ManualHoldView,
    ProfileIncompleteError,
    RoomLightingCoreBranch,
)
from custom_components.hausman_hub.domain.core.models import LightStateTarget
from custom_components.hausman_hub.domain.room_lighting import (
    config_from_payload,
)

RESOURCE_ID = "test_main"
ENTITY_ID = "light.test_main"
PUBLIC_TARGET_ID = "stand.light.main"
ROOM_ID = "test_room"
# The room payload carries version 1: the initial control revision.
START_CONTROL_REVISION = 1


def make_config_payload(room_id: str = ROOM_ID) -> dict:
    """One synthetic room profile with a single binary light target."""

    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": "Тест",
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.test_presence",
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": RESOURCE_ID,
                    "name": "Основной",
                    "kind": "light",
                    "entityId": ENTITY_ID,
                    "role": "main",
                    "groupId": None,
                    "brightness": False,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [],
        "switchBindings": [],
        "illumination": None,
        "dimming": {"enabled": False},
        "manualOffProtection": {"enabled": False},
        "awayBehavior": {"mode": "none"},
        "autoAdopt": False,
        "updatedAt": 1,
        "overrides": {},
    }


def make_state(value: str):
    from unittest.mock import MagicMock

    state = MagicMock()
    state.state = value
    state.attributes = {}
    return state


def build_stand_branch(tmp_path, *, with_profile: bool = True, holds=None):
    config = config_from_payload(make_config_payload(ROOM_ID))
    profiles = (
        synthetic_binary_light_profiles([RESOURCE_ID])
        if with_profile
        else None
    )
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id=ROOM_ID,
            config=config,
            device_profiles=profiles,
        ),
        store_path=Path(tmp_path) / f"core_shadow_{ROOM_ID}.json",
        manual_holds_provider=(lambda: holds),
    )
    branch.activate_shadow()
    return branch


def report_light_state(branch, value: str) -> None:
    branch.handle_runtime_event(ENTITY_ID, None, make_state(value))


def report_light_state_repeat(branch, value: str) -> None:
    """A repeated report with no state change: the input adapter marks
    it STALE, so the stored fact loses freshness."""

    state = make_state(value)
    branch.handle_runtime_event(ENTITY_ID, state, state)


def _binding() -> CoreManualTargetBinding:
    return CoreManualTargetBinding(
        target_id=PUBLIC_TARGET_ID,
        resource_id=RESOURCE_ID,
        entity_id=ENTITY_ID,
        room_id=ROOM_ID,
    )


def _submit(
    branch,
    *,
    request_id="core-manual-1",
    action="turn_off",
    expected=START_CONTROL_REVISION,
    scope="tablet",
):
    return branch.core_service.submit_manual(
        RESOURCE_ID,
        request_id,
        action,
        LightStateTarget(on=action == "turn_on"),
        CORE_MANUAL_HOLD_SOURCE,
        expected,
        receipt_scope=scope,
    )


def _envelope(branch, *, request_id="core-manual-full", control=None):
    return {
        "version": 1,
        "requestId": request_id,
        "expectedControlRevision": (
            control
            if control is not None
            else branch.core_service.record(RESOURCE_ID).control_revision
        ),
        "expectedBindingRevision": branch.core_service.spec(
            RESOURCE_ID
        ).binding_revision,
    }


def test_missing_profile_fails_typed_not_with_missing_method(tmp_path):
    """S-06.a capabilities: the executive path refuses an unprofiled
    resource with a typed error; the old missing-attribute crash is gone
    (reproduced on the basis, see the delivery packet)."""

    branch = build_stand_branch(tmp_path, with_profile=False)
    with pytest.raises(ProfileIncompleteError):
        _submit(branch)


def test_admitted_manual_accepts_and_never_sends(tmp_path):
    branch = build_stand_branch(tmp_path)
    report_light_state(branch, "on")
    receipt = _submit(branch)
    assert receipt.status is ManualStatus.ACCEPTED
    assert receipt.idempotent_replay is False
    record = branch.core_service.record(RESOURCE_ID)
    assert record.authority.name == "MANUAL"
    assert record.manual is not None
    assert record.manual.source == CORE_MANUAL_HOLD_SOURCE
    # SHADOW: the permit refuses every dispatch; the transport is closed
    # by construction anyway.
    operation = branch.core_service.operation(receipt.operation_id)
    assert operation.phase.name == "CANCELLED"
    assert operation.reason_code == "core_not_active"
    assert branch.physical_send_enabled() is False


def test_no_op_admission_when_already_in_target_state(tmp_path):
    branch = build_stand_branch(tmp_path)
    report_light_state(branch, "on")
    receipt = _submit(branch, action="turn_on")
    assert receipt.status is ManualStatus.NO_OP
    record = branch.core_service.record(RESOURCE_ID)
    assert record.authority.name == "MANUAL"


def test_core_hold_survives_the_legacy_transfer_tick(tmp_path):
    """The next tick after a manual admission must not clear the
    core-accepted hold, with or without a matching runtime hold
    (S-06.a step 3)."""

    holds: dict = {}
    branch = build_stand_branch(tmp_path, holds=holds)
    report_light_state(branch, "on")
    _submit(branch)
    priority_until = branch.core_service.record(
        RESOURCE_ID
    ).manual.priority_until_utc_ms

    # Ticks without a matching runtime hold: the core hold survives.
    branch.process_schedule_tick()
    branch.process_schedule_tick()
    record = branch.core_service.record(RESOURCE_ID)
    assert record.authority.name == "MANUAL"
    assert record.manual is not None
    assert record.manual.source == CORE_MANUAL_HOLD_SOURCE
    assert record.manual.priority_until_utc_ms == priority_until

    # A foreign runtime hold for another entity must not overwrite it.
    holds["light.unrelated"] = ManualHoldView(
        entity_id="light.unrelated",
        action="turn_on",
        source="test.runtime",
        proven_at_utc_ms=1,
        priority_until_utc_ms=10**15,
        evidence_id="ev-1",
    )
    branch.process_schedule_tick()
    record = branch.core_service.record(RESOURCE_ID)
    assert record.manual.source == CORE_MANUAL_HOLD_SOURCE

    # A vanished snapshot (unavailable authority) keeps the accepted
    # hold as well: the core store is the only authority here.
    branch._manual_holds_provider = lambda: None
    branch.process_schedule_tick()
    record = branch.core_service.record(RESOURCE_ID)
    assert record.authority.name == "MANUAL"
    assert record.manual.source == CORE_MANUAL_HOLD_SOURCE


def test_legacy_transfer_behaviour_without_core_hold_is_unchanged(
    tmp_path,
):
    """A plain shadow room without a core admission keeps the recorded
    transfer behaviour: an unavailable authority makes records
    uncertain (fail closed)."""

    holds_holder: dict = {"value": None}
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id=ROOM_ID,
            config=config_from_payload(make_config_payload(ROOM_ID)),
            device_profiles=synthetic_binary_light_profiles([RESOURCE_ID]),
        ),
        store_path=Path(tmp_path) / "core_shadow_plain.json",
        manual_holds_provider=lambda: holds_holder["value"],
    )
    branch.activate_shadow()
    branch.process_schedule_tick()
    record = branch.core_service.record(RESOURCE_ID)
    assert record.authority.name == "UNCERTAIN"
    assert record.uncertainty_reason == "manual_authority_unavailable"


def test_scoped_replay_preserves_receipt_and_hold(tmp_path):
    branch = build_stand_branch(tmp_path)
    report_light_state(branch, "on")
    receipt = _submit(branch)
    record = branch.core_service.record(RESOURCE_ID)
    priority_until = record.manual.priority_until_utc_ms
    control_revision = record.control_revision

    replay = _submit(branch)
    assert replay.idempotent_replay is True
    assert replay.operation_id == receipt.operation_id
    assert replay.control_revision == control_revision
    record = branch.core_service.record(RESOURCE_ID)
    # A replay never extends the manual hold and never rewrites rights.
    assert record.manual.priority_until_utc_ms == priority_until
    assert record.control_revision == control_revision


def test_same_id_with_other_expected_revision_conflicts(tmp_path):
    branch = build_stand_branch(tmp_path)
    report_light_state(branch, "on")
    _submit(branch, request_id="core-manual-dup")
    with pytest.raises(IdempotencyConflict):
        _submit(branch, request_id="core-manual-dup", expected=99)


def test_stale_control_revision_is_refused_before_acceptance(tmp_path):
    branch = build_stand_branch(tmp_path)
    report_light_state(branch, "on")
    with pytest.raises(RevisionConflict):
        _submit(branch, expected=99)
    assert (
        branch.core_service.receipt("core-manual-1", receipt_scope="tablet")
        is None
    )
    record = branch.core_service.record(RESOURCE_ID)
    assert record.authority.name != "MANUAL"


def test_principals_do_not_share_receipts(tmp_path):
    branch = build_stand_branch(tmp_path)
    report_light_state(branch, "on")
    first = _submit(branch, request_id="core-manual-shared", scope="tablet")
    # The second principal presents the now-current revision: it is a
    # fresh admission, not a replay of somebody else's receipt.
    current = branch.core_service.record(RESOURCE_ID).control_revision
    second = _submit(
        branch,
        request_id="core-manual-shared",
        scope="admin",
        expected=current,
    )
    assert first.idempotent_replay is False
    assert second.idempotent_replay is False
    assert second.operation_id != first.operation_id


def test_storage_failure_keeps_the_request_unaccepted(tmp_path):
    branch = build_stand_branch(tmp_path)
    report_light_state(branch, "on")
    store = branch.core_service._store
    assert isinstance(store, FileControlStore)
    original_commit = store.commit

    def failing_commit(expected_commit_revision, next_state):
        raise StorageUnavailable("test store commit failure")

    store.commit = failing_commit
    try:
        with pytest.raises(StorageUnavailable):
            _submit(branch)
    finally:
        store.commit = original_commit
    assert (
        branch.core_service.receipt("core-manual-1", receipt_scope="tablet")
        is None
    )
    record = branch.core_service.record(RESOURCE_ID)
    assert record.authority.name != "MANUAL"


def test_unknown_resource_is_typed(tmp_path):
    branch = build_stand_branch(tmp_path)
    with pytest.raises(UnknownResource):
        branch.core_service.spec("test.absent")


def test_intake_refuses_without_envelope_and_marks_transferred(tmp_path):
    """The registry routes exactly the transferred target; a request
    without the new envelope is refused before acceptance."""

    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    assert intake.is_transferred(PUBLIC_TARGET_ID) is True
    assert intake.is_transferred("light.neighbor") is False
    report_light_state(branch, "on")
    with pytest.raises(CoreManualInputError) as error:
        asyncio.run(
            intake.async_submit(
                target_id=PUBLIC_TARGET_ID,
                action_id="turn_off",
                core_control=None,
                principal="tablet",
                correlation_id="corr-1",
                request_id="request-1",
            )
        )
    assert error.value.code == "core_control_envelope_required"
    record = branch.core_service.record(RESOURCE_ID)
    assert record.authority.name != "MANUAL"


def test_intake_binding_revision_conflict_blocks_admission(tmp_path):
    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    report_light_state(branch, "on")
    envelope = _envelope(branch)
    envelope["expectedBindingRevision"] += 1
    with pytest.raises(CoreManualInputError) as error:
        asyncio.run(
            intake.async_submit(
                target_id=PUBLIC_TARGET_ID,
                action_id="turn_off",
                core_control=envelope,
                principal="tablet",
                correlation_id="corr-1",
                request_id="request-1",
            )
        )
    assert error.value.code == "core_binding_revision_conflict"
    assert (
        branch.core_service.receipt("core-manual-full", receipt_scope="tablet")
        is None
    )


def test_intake_full_projection_carries_core_operation(tmp_path):
    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    report_light_state(branch, "on")
    receipt = asyncio.run(
        intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_off",
            core_control=_envelope(branch, request_id="core-manual-full"),
            principal="tablet",
            correlation_id="corr-full",
            request_id="request-full",
            full=True,
        )
    )
    assert receipt["accepted"] is True
    assert receipt["confirmed"] is False
    assert receipt["commandSent"] is False
    assert receipt["decision"] == "skipped"
    assert receipt["reasonCode"] == "core_manual_admitted"
    assert receipt["requestId"] == "core-manual-full"
    assert receipt["targetType"] == "light"
    core_operation = receipt["coreOperation"]
    assert core_operation["version"] == 1
    assert core_operation["operationId"]
    assert core_operation["controlRevision"] == START_CONTROL_REVISION + 1
    # Nothing was sent and nothing is claimed as sent.
    assert receipt["evidence"]["effectiveState"] in ("on", "off", "unknown")


def test_intake_dry_run_creates_no_manual_and_no_operation(tmp_path):
    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    report_light_state(branch, "on")
    before = branch.core_service.record(RESOURCE_ID)
    receipt = asyncio.run(
        intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_off",
            core_control=_envelope(branch, request_id="core-manual-dry"),
            principal="tablet",
            correlation_id="corr-dry",
            request_id="request-dry",
            dry_run=True,
            full=True,
        )
    )
    assert receipt["dryRun"] is True
    assert "coreOperation" not in receipt
    after = branch.core_service.record(RESOURCE_ID)
    assert after.control_revision == before.control_revision
    assert after.authority == before.authority
    assert after.manual == before.manual
    # A dry run never creates an acceptance receipt: the read-only
    # projection cannot be replayed or mistaken for an admission.
    assert (
        branch.core_service.receipt("core-manual-dry", receipt_scope="tablet")
        is None
    )


def test_scoped_replay_survives_branch_restart(tmp_path):
    """The scoped replay key and fingerprint are durable: the same
    principal replays the same acceptance after a reload, with the same
    operation and without a new admission."""

    store_path = Path(tmp_path) / "core_shadow_restart.json"
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id=ROOM_ID,
            config=config_from_payload(make_config_payload(ROOM_ID)),
            device_profiles=synthetic_binary_light_profiles([RESOURCE_ID]),
        ),
        store_path=store_path,
    )
    branch.activate_shadow()
    report_light_state(branch, "on")
    first = _submit(branch, request_id="core-manual-restart")
    restarted = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id=ROOM_ID,
            config=config_from_payload(make_config_payload(ROOM_ID)),
            device_profiles=synthetic_binary_light_profiles([RESOURCE_ID]),
        ),
        store_path=store_path,
    )
    restarted.activate_shadow()
    record = restarted.core_service.record(RESOURCE_ID)
    assert record.authority.name == "MANUAL"
    replay = _submit(
        restarted,
        request_id="core-manual-restart",
        expected=START_CONTROL_REVISION,
    )
    # The replaying client presents the same envelope it originally
    # sent; the durable scoped fingerprint matches before any revision
    # check, so the acceptance replays instead of re-admitting.
    assert replay.idempotent_replay is True
    assert replay.operation_id == first.operation_id
    assert restarted.core_service.record(
        RESOURCE_ID
    ).control_revision == record.control_revision


def test_admission_after_stop_is_typed_closed(tmp_path):
    """After the stop hook closed the input, the branch-serialized
    admission refuses typed and never accepts (SEC-S06-01)."""

    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    branch.on_runtime_stop()
    with pytest.raises(CoreManualInputError) as error:
        asyncio.run(
            intake.async_submit(
                target_id=PUBLIC_TARGET_ID,
                action_id="turn_off",
                core_control=_envelope(branch),
                principal="tablet",
                correlation_id="corr-stop",
                request_id="request-stop",
            )
        )
    assert error.value.code == "core_unavailable"
    assert (
        branch.core_service.receipt("core-manual-full", receipt_scope="tablet")
        is None
    )


def test_admission_is_serialized_with_branch_lock(tmp_path):
    """The admission waits for the branch lock: a tick holding the lock
    cannot interleave with the manual acceptance (SEC-S06-01)."""

    import threading

    branch = build_stand_branch(tmp_path)
    report_light_state(branch, "on")
    lock_acquired = threading.Event()
    release = threading.Event()

    def holding_tick():
        with branch._lock:
            lock_acquired.set()
            release.wait(timeout=5)

    worker = threading.Thread(target=holding_tick)
    worker.start()
    assert lock_acquired.wait(timeout=5)

    outcomes: list = []

    def admit():
        try:
            outcomes.append(
                branch.admit_manual(
                    resource_id=RESOURCE_ID,
                    request_id="core-manual-locked",
                    action_id="turn_off",
                    target=LightStateTarget(on=False),
                    source=CORE_MANUAL_HOLD_SOURCE,
                    expected_control_revision=START_CONTROL_REVISION,
                    expected_binding_revision=branch.core_service.spec(
                        RESOURCE_ID
                    ).binding_revision,
                    receipt_scope="tablet",
                )
            )
        except Exception as exc:  # pragma: no cover - surfaced below
            outcomes.append(exc)

    admission = threading.Thread(target=admit)
    admission.start()
    # The admission must wait: nothing accepted while the tick holds
    # the lock.
    admission.join(timeout=0.3)
    assert outcomes == []
    release.set()
    admission.join(timeout=5)
    worker.join(timeout=5)
    receipt = outcomes[0]
    assert receipt.status.name == "ACCEPTED"
    assert branch.core_service.receipt(
        "core-manual-locked", receipt_scope="tablet"
    ) is not None


def test_intake_admission_runs_off_the_calling_thread(tmp_path):
    """The intake executes the branch admission outside the event loop
    thread: the core commit's file I/O never blocks the loop
    (SEC-S06-01)."""

    import threading

    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    report_light_state(branch, "on")
    loop_thread = threading.get_ident()
    seen_threads: list = []
    original = branch.admit_manual

    def spying(**kwargs):
        seen_threads.append(threading.get_ident())
        return original(**kwargs)

    branch.admit_manual = spying
    asyncio.run(
        intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_off",
            core_control=_envelope(branch),
            principal="tablet",
            correlation_id="corr-thread",
            request_id="request-thread",
        )
    )
    assert seen_threads and seen_threads[0] != loop_thread


def test_stale_fact_is_not_reported_fresh(tmp_path):
    """Freshness is the fact's own age: after the profile window the
    read-only probe answers suspect with unknown effective state and
    keeps the original observation time (SEC-S06-03)."""

    import time as time_module

    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    report_light_state(branch, "off")
    base_mono = time_module.monotonic_ns() // 1_000_000
    shift = {"ms": 0}
    aged_intake = CoreManualInput(
        mono_now=lambda: base_mono + shift["ms"]
    )
    aged_intake.register_transferred_target(branch, _binding())
    envelope = _envelope(branch, request_id="core-manual-aged")

    fresh_view = asyncio.run(
        aged_intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_on",
            core_control=envelope,
            principal="tablet",
            correlation_id="corr-aged",
            request_id="request-aged",
            dry_run=True,
            full=True,
        )
    )
    assert (
        fresh_view["evidence"]["reasonCode"] == "device_report_off_confirmed"
    )
    original_observed_at = fresh_view["evidence"]["observedAt"]

    shift["ms"] = SYNTHETIC_PROFILE_MAX_AGE_MS + 1
    aged_view = asyncio.run(
        aged_intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_on",
            core_control=envelope,
            principal="tablet",
            correlation_id="corr-aged",
            request_id="request-aged",
            dry_run=True,
            full=True,
        )
    )
    evidence = aged_view["evidence"]
    assert evidence["confidence"] == "suspect"
    assert evidence["effectiveState"] in ("off", "unknown")
    assert evidence["reasonCode"] == "state_suspect"
    # The observation time is not re-stamped to the read time: the aged
    # read keeps reporting the original report moment (the fake clock
    # shift may only pull the estimate backwards, never forwards).
    assert evidence["observedAt"] <= original_observed_at + 1_000


def test_unavailable_report_is_never_a_proven_off(tmp_path):
    """An unknown/unavailable report replaces the fact with an INVALID
    observation: a manual turn_off is a real admission (not a no_op),
    and the read-only probe claims nothing (SEC-S06-04)."""

    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    report_light_state(branch, "on")
    unavailable = make_state("unavailable")
    branch.handle_runtime_event(ENTITY_ID, make_state("on"), unavailable)

    receipt = asyncio.run(
        intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_off",
            core_control=_envelope(branch, request_id="core-manual-unavail"),
            principal="tablet",
            correlation_id="corr-unavail",
            request_id="request-unavail",
            full=True,
        )
    )
    # Not a no_op: the silence of the device proves nothing.
    assert receipt["status"] == "accepted"
    operation = branch.core_service.operation(
        receipt["coreOperation"]["operationId"]
    )
    assert operation.phase.name == "CANCELLED"
    assert branch.core_service.record(RESOURCE_ID).manual is not None

    dry_view = asyncio.run(
        intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_on",
            core_control=_envelope(branch, request_id="core-manual-unavail-2"),
            principal="tablet",
            correlation_id="corr-unavail-2",
            request_id="request-unavail-2",
            dry_run=True,
            full=True,
        )
    )
    assert dry_view["evidence"]["reportedState"] == "unknown"
    assert dry_view["evidence"]["confidence"] == "suspect"


def test_numeric_observation_keeps_its_value(tmp_path):
    """A numeric property keeps its typed value end to end: the adapter
    never casts it to bool, including zero (SEC-S06-05/R-S06-05)."""

    from custom_components.hausman_hub.adapters.core.room_lighting import (
        EntityBinding,
        RoomLightingInputAdapter,
    )
    from custom_components.hausman_hub.adapters.core.clock import SystemClock

    adapter = RoomLightingInputAdapter(
        bindings={
            "light.test_main.brightness": EntityBinding(
                resource_id=RESOURCE_ID,
                property="brightness_pct",
                unit="pct",
            )
        },
        clock=SystemClock(),
        source_epoch="test",
    )
    bright = make_state("on")
    bright.attributes = {"brightness": 128}
    observation = adapter.observation(
        "light.test_main.brightness", None, bright
    )
    assert observation is not None
    assert observation.value == 50
    assert isinstance(observation.value, int)

    zero = make_state("on")
    zero.attributes = {"brightness": 0}
    observation_zero = adapter.observation(
        "light.test_main.brightness", None, zero
    )
    assert observation_zero is not None
    assert observation_zero.value == 0
    assert observation_zero.value is not True


def test_stale_fact_keeps_its_observation_time_exactly(tmp_path):
    """The observed time comes from the stored observation stamp: reads
    and wall-clock jumps never move it (SEC-S06-03R1)."""

    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    report_light_state(branch, "off")
    envelope = _envelope(branch, request_id="core-manual-time")
    first = asyncio.run(
        intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_on",
            core_control=envelope,
            principal="tablet",
            correlation_id="corr-time",
            request_id="request-time",
            dry_run=True,
            full=True,
        )
    )
    second = asyncio.run(
        intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_on",
            core_control=envelope,
            principal="tablet",
            correlation_id="corr-time",
            request_id="request-time",
            dry_run=True,
            full=True,
        )
    )
    assert (
        first["evidence"]["observedAt"]
        == second["evidence"]["observedAt"]
    )
    # The stamp is the observation's own report time, not the read.
    resource_view = branch.core_service.resource_view(RESOURCE_ID)
    on_fact = next(
        fact for fact in resource_view.facts if fact.property == "on"
    )
    assert on_fact.received_at_utc_ms is not None
    assert first["evidence"]["observedAt"] == on_fact.received_at_utc_ms


def test_incomplete_profile_reports_unavailable(tmp_path):
    """A transferred resource without a capability passport is
    unavailable for the catalog read, never ready (SEC-HACS-03)."""

    branch = build_stand_branch(tmp_path, with_profile=False)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    state = intake.core_control_state(PUBLIC_TARGET_ID)
    assert state == {"version": 1, "status": "unavailable"}
    # The full branch keeps answering ready.
    ready_branch = build_stand_branch(Path(tmp_path) / "ready")
    ready_intake = CoreManualInput()
    ready_intake.register_transferred_target(
        ready_branch, _binding()
    )
    ready_state = ready_intake.core_control_state(PUBLIC_TARGET_ID)
    assert ready_state["status"] == "ready"


def test_zero_utc_stamp_is_not_replaced_by_read_time(tmp_path):
    """A legitimate zero observation stamp survives projection: reads
    at different wall-clock moments keep observedAt at zero
    (SEC-S06-03R1 residual)."""

    from custom_components.hausman_hub.domain.core.models import (
        Evidence,
        Observation,
        ObservationAttribution,
        Quality,
    )

    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    branch.core_service.ingest_observation(
        Observation(
            observation_id="obs-zero-stamp",
            resource_id=RESOURCE_ID,
            property="on",
            value=True,
            unit="bool",
            received_at_utc_ms=0,
            received_at_mono_ms=branch.core_service._clock.now_mono_ms(),
            boot_id=branch.core_service._clock.boot_id(),
            source_at_utc_ms=None,
            source_sequence=None,
            source_id="ha_state:zero",
            source_epoch="test.epoch",
            quality=Quality.FRESH,
            evidence_kind=Evidence.DEVICE_REPORT,
            binding_revision=0,
            observation_revision=0,
            related_operation_id=None,
            attribution=ObservationAttribution.UNKNOWN,
        )
    )
    envelope = _envelope(branch, request_id="core-manual-zero")
    stamps = set()
    for index in range(2):
        view = asyncio.run(
            intake.async_submit(
                target_id=PUBLIC_TARGET_ID,
                action_id="turn_off",
                core_control=envelope,
                principal="tablet",
                correlation_id=f"corr-zero-{index}",
                request_id=f"request-zero-{index}",
                dry_run=True,
                full=True,
            )
        )
        stamps.add(view["evidence"]["observedAt"])
    assert stamps == {0}


def test_stop_after_saved_admission_keeps_the_accepted_answer(tmp_path):
    """A stop landing between the saved admission and the projection
    does not turn the accepted result into a refusal (SEC-S06-06)."""

    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    report_light_state(branch, "on")
    original = branch.admit_manual

    def admit_then_stop(**kwargs):
        result = original(**kwargs)
        branch.on_runtime_stop()
        return result

    branch.admit_manual = admit_then_stop
    receipt = asyncio.run(
        intake.async_submit(
            target_id=PUBLIC_TARGET_ID,
            action_id="turn_off",
            core_control=_envelope(branch, request_id="core-manual-stop"),
            principal="tablet",
            correlation_id="corr-stop-late",
            request_id="request-stop-late",
            full=True,
        )
    )
    assert receipt["accepted"] is True
    assert receipt["status"] == "accepted"
    assert receipt["coreOperation"]["operationId"]


def test_intake_reports_core_unavailable_without_legacy_fallback(tmp_path):
    """A missing branch for the transferred resource is a typed visible
    refusal — the legacy writer never opens (S-06.a)."""

    branch = build_stand_branch(tmp_path)
    intake = CoreManualInput()
    intake.register_transferred_target(branch, _binding())
    intake._branches.clear()
    with pytest.raises(CoreManualInputError) as error:
        asyncio.run(
            intake.async_submit(
                target_id=PUBLIC_TARGET_ID,
                action_id="turn_off",
                core_control=_envelope(branch),
                principal="tablet",
                correlation_id="corr-1",
                request_id="request-1",
            )
        )
    assert error.value.code == "core_unavailable"
    assert error.value.http_status == 503
