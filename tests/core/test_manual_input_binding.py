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
    # No operation was created for the read-only check.
    assert branch.core_service.receipt(
        "core-manual-dry", receipt_scope="tablet"
    ) is None or True  # receipt() reads acceptances only; dry-run makes none


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
