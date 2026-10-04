"""Device-report normalization for the L-SYN-1 lighting path."""

import subprocess
import sys
from unittest.mock import MagicMock

import pytest
from custom_components.hausman_hub.application.core.command_service import (
    ManualStatus,
)
from custom_components.hausman_hub.adapters.core.room_lighting import (
    EntityBinding,
    RoomLightingInputAdapter,
)
from custom_components.hausman_hub.domain.core.models import (
    ArbitrationMode,
    Evidence,
    OperationPhase,
    Quality,
)
from tests.core.conftest import FakeClock
from tests.core.conftest import (
    BASE_TARGET_ON40,
    MAIN_RESOURCE,
    OWNER_EPISODE,
    OWNER_MODULE,
    OWNER_PROPOSAL,
    TARGET_OFF,
    build_file_stand,
    owner_proposal,
    restore_file_stand,
)


def _state(state, **attributes):
    result = MagicMock()
    result.state = state
    result.attributes = attributes
    return result


def test_explicit_full_device_report_maps_one_light_group():
    adapter = RoomLightingInputAdapter(
        bindings={"light.main": (
            EntityBinding("main", "on", "bool"),
            EntityBinding("main", "brightness_pct", "pct"),
            EntityBinding("main", "color_temperature_k", "kelvin"),
        )},
        clock=FakeClock(), source_epoch="test.epoch", binding_revision=7,
    )

    observations = adapter.observations(
        "light.main", None, _state("on", brightness=102, color_temp_kelvin=4000),
        trusted_device_report=True, source_sequence=11,
    )

    assert [item.property for item in observations] == [
        "on", "brightness_pct", "color_temperature_k",
    ]
    assert {item.source_sequence for item in observations} == {11}
    assert {item.binding_revision for item in observations} == {7}
    assert {item.evidence_kind for item in observations} == {Evidence.DEVICE_REPORT}
    assert {item.quality for item in observations} == {Quality.FRESH}
    assert observations[1].value == 40
    assert observations[2].value == 4000
    assert len({item.observation_id for item in observations}) == 3


def test_state_snapshot_is_not_a_confirmation_report():
    adapter = RoomLightingInputAdapter(
        bindings={"light.main": EntityBinding("main", "on", "bool")},
        clock=FakeClock(), source_epoch="test.epoch",
    )
    observation = adapter.observations("light.main", None, _state("on"))[0]
    assert observation.evidence_kind is Evidence.ADAPTER_STATE
    assert observation.quality is Quality.FRESH


def test_invalid_or_partial_attributes_replace_only_their_own_facts():
    adapter = RoomLightingInputAdapter(
        bindings={"light.main": (
            EntityBinding("main", "on", "bool"),
            EntityBinding("main", "brightness_pct", "pct"),
            EntityBinding("main", "color_temperature_k", "kelvin"),
        )},
        clock=FakeClock(), source_epoch="test.epoch", binding_revision=3,
    )
    observations = adapter.observations(
        "light.main", None,
        _state("on", brightness=999, color_temp_kelvin="not-a-temperature"),
        trusted_device_report=True, source_sequence=4,
    )
    assert [item.property for item in observations] == [
        "on", "brightness_pct", "color_temperature_k",
    ]
    assert observations[0].evidence_kind is Evidence.DEVICE_REPORT
    assert [item.quality for item in observations[1:]] == [
        Quality.INVALID, Quality.INVALID,
    ]
    assert [item.value for item in observations[1:]] == [None, None]


def test_non_finite_attributes_are_invalid_observations_not_exceptions():
    adapter = RoomLightingInputAdapter(
        bindings={"light.main": (
            EntityBinding("main", "brightness_pct", "pct"),
            EntityBinding("main", "color_temperature_k", "kelvin"),
        )},
        clock=FakeClock(), source_epoch="test.epoch",
    )
    observations = adapter.observations(
        "light.main", None,
        _state("on", brightness=float("nan"), color_temp_kelvin=float("inf")),
        trusted_device_report=True, source_sequence=5,
    )
    assert [item.property for item in observations] == [
        "brightness_pct", "color_temperature_k",
    ]
    assert {item.quality for item in observations} == {Quality.INVALID}
    assert {item.value for item in observations} == {None}


def test_color_temperature_huge_decimal_is_rejected_in_bounded_child():
    """Range validation must precede integer materialization of Decimal."""
    program = """
from types import SimpleNamespace
from custom_components.hausman_hub.adapters.core.room_lighting import EntityBinding, RoomLightingInputAdapter
from tests.core.conftest import FakeClock
adapter = RoomLightingInputAdapter({'light.main': EntityBinding('main', 'color_temperature_k', 'kelvin')}, FakeClock(), 'epoch')
for value in ('1e5000000', '1e-5000000', '-1e5000000', 'NaN', 'Infinity', '4000.5', True, 999.99, 10000.01):
    result = adapter.observations('light.main', None, SimpleNamespace(state='on', attributes={'color_temp_kelvin': value}), trusted_device_report=True, source_sequence=1)
    assert len(result) == 1 and result[0].quality.name == 'INVALID' and result[0].value is None
for value in (1000, 10000, '4000.0', 4000):
    result = adapter.observations('light.main', None, SimpleNamespace(state='on', attributes={'color_temp_kelvin': value}), trusted_device_report=True, source_sequence=1)
    assert len(result) == 1 and result[0].quality.name == 'FRESH' and result[0].value == int(float(value))
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", program],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _restore06_stand(tmp_path):
    return build_file_stand(
        tmp_path,
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )


def _dispatch_auto_on40(stand):
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "restore06-auto")
    stand.clock.advance(1)
    stand.service.drain()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.OBSERVING
    assert len(stand.transport.calls) == 1
    return operation


def _room_report(stand, sequence=2, state="on", brightness=102):
    adapter = RoomLightingInputAdapter(
        bindings={"light.test_main": (
            EntityBinding(MAIN_RESOURCE, "on", "bool"),
            EntityBinding(MAIN_RESOURCE, "brightness_pct", "pct"),
        )},
        clock=stand.clock,
        source_epoch="test.room.shadow",
        binding_revision=3,
    )
    result = []
    for observation in adapter.observations(
        "light.test_main",
        None,
        _state(state, brightness=brightness),
        trusted_device_report=True,
        source_sequence=sequence,
    ):
        result.append(stand.service.ingest_observation(observation))
    return result


def test_restore06_a_room_adapter_replay_keeps_historical_confirmation(tmp_path):
    """R06-A: a benign HA report after restore retains prior history."""
    stand = _restore06_stand(tmp_path)
    operation = _dispatch_auto_on40(stand)
    stand.clock.advance(99)
    assert _room_report(stand)[-1] == "confirmed"
    assert operation.phase is OperationPhase.CONFIRMED
    proof_before = stand.service.record(MAIN_RESOURCE).auto_proof
    key_before = stand.service._confirm_keys[operation.operation_id]
    assert proof_before is not None

    restarted = restore_file_stand(stand, episode_map={OWNER_PROPOSAL: OWNER_EPISODE})
    restarted.clock.advance(200)
    restored = restarted.service.operation(operation.operation_id)
    assert restored.phase is OperationPhase.CONFIRMED
    assert restarted.service.record(MAIN_RESOURCE).uncertainty_reason == "restart_continuity_unproven"
    assert restarted.transport.calls == []
    assert _room_report(restarted) == ["recorded", "recorded"]

    assert restored.phase is OperationPhase.CONFIRMED
    assert restarted.service.record(MAIN_RESOURCE).auto_proof == proof_before
    assert restarted.service._confirm_keys[operation.operation_id] == key_before
    image = restarted.store.load()
    assert image["operations"][operation.operation_id].phase is OperationPhase.CONFIRMED
    assert operation.operation_id in image["confirm_keys"]
    assert (MAIN_RESOURCE, OWNER_EPISODE) not in restarted.service._episode_locks
    assert restarted.transport.calls == []


def test_restore06_b_generic_auto_and_manual_history_stay_durable(tmp_path):
    """R06-B: both supported report paths retain confirmed history."""
    stand = _restore06_stand(tmp_path)
    auto = _dispatch_auto_on40(stand)
    stand.clock.advance(99)
    assert stand.ingest(MAIN_RESOURCE, 2, 100, {"on": True, "brightness_pct": 40}) == "confirmed"
    restarted = restore_file_stand(stand, episode_map={OWNER_PROPOSAL: OWNER_EPISODE})
    restarted.clock.advance(200)
    assert restarted.ingest(MAIN_RESOURCE, 2, 200, {"on": True, "brightness_pct": 40}) == "recorded"
    assert restarted.service.operation(auto.operation_id).phase is OperationPhase.CONFIRMED

    manual = restarted.service.submit_manual(
        MAIN_RESOURCE, "restore06-manual", "manual.off", TARGET_OFF,
        "test.ui", restarted.service.record(MAIN_RESOURCE).control_revision,
    )
    assert manual.status is ManualStatus.ACCEPTED
    restarted.service.drain()
    assert restarted.ingest(MAIN_RESOURCE, 3, 201, {"on": False}) == "confirmed"
    receipt_before = restarted.service.receipt("restore06-manual")
    second = restore_file_stand(restarted, episode_map={OWNER_PROPOSAL: OWNER_EPISODE})
    second.clock.advance(200)
    assert second.ingest(MAIN_RESOURCE, 3, 200, {"on": False}) == "recorded"
    assert second.service.operation(manual.operation_id).phase is OperationPhase.CONFIRMED
    assert second.service.record(MAIN_RESOURCE).auto_proof is None
    assert second.service.receipt("restore06-manual") == receipt_before
    assert "restore06-manual" in second.store.load()["receipts"]


def test_restore06_c_restored_unconfirmed_never_confirms(tmp_path):
    """R06-C preserves K10 for an operation without old confirmation."""
    stand = _restore06_stand(tmp_path)
    operation = _dispatch_auto_on40(stand)
    restarted = restore_file_stand(stand, episode_map={OWNER_PROPOSAL: OWNER_EPISODE})
    restarted.clock.advance(200)
    assert restarted.service.operation(operation.operation_id).phase is OperationPhase.UNKNOWN
    assert restarted.ingest(MAIN_RESOURCE, 2, 200, {"on": True, "brightness_pct": 40}) == "recorded"
    assert restarted.ingest(MAIN_RESOURCE, 3, 201, {"on": True, "brightness_pct": 40}) == "recorded"
    assert restarted.service.operation(operation.operation_id).phase is OperationPhase.UNKNOWN
    assert restarted.service.record(MAIN_RESOURCE).auto_proof is None
    assert restarted.transport.calls == []


def test_restore06_d_current_process_contradiction_still_revokes(tmp_path):
    """R06-D keeps current-process contradictory-report revocation."""
    stand = _restore06_stand(tmp_path)
    operation = _dispatch_auto_on40(stand)
    stand.clock.advance(99)
    assert stand.ingest(MAIN_RESOURCE, 2, 100, {"on": True, "brightness_pct": 40}) == "confirmed"
    assert stand.ingest(MAIN_RESOURCE, 2, 101, {"on": False, "brightness_pct": 40}) == "recorded"
    assert operation.phase is OperationPhase.UNKNOWN
    assert operation.reason_code == "ambiguous_feedback_revoked"
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None
    assert operation.operation_id not in stand.service._confirm_keys
    assert stand.service._episode_locks[(MAIN_RESOURCE, OWNER_EPISODE)] == "ambiguous_feedback_revoked"
    assert len(stand.transport.calls) == 1


def test_restore06_e_new_manual_operation_still_revalidates_after_restore(tmp_path):
    """R06-E applies the exception per restored operation, not per core."""
    stand = _restore06_stand(tmp_path)
    _dispatch_auto_on40(stand)
    stand.clock.advance(99)
    stand.ingest(MAIN_RESOURCE, 2, 100, {"on": True, "brightness_pct": 40})
    restarted = restore_file_stand(stand, episode_map={OWNER_PROPOSAL: OWNER_EPISODE})
    # A fresh complete report supplies facts for the new manual dispatch;
    # it cannot re-confirm the restored automatic operation.
    assert restarted.ingest(MAIN_RESOURCE, 3, 0, {"on": True, "brightness_pct": 40}) == "recorded"
    receipt = restarted.service.submit_manual(
        MAIN_RESOURCE, "restore06-new-manual", "manual.off", TARGET_OFF,
        "test.ui", restarted.service.record(MAIN_RESOURCE).control_revision,
    )
    assert receipt.status is ManualStatus.ACCEPTED
    restarted.clock.advance(1)
    restarted.service.drain()
    assert restarted.service.operation(receipt.operation_id).phase is OperationPhase.OBSERVING
    assert restarted.ingest(MAIN_RESOURCE, 4, 1, {"on": False}) == "confirmed"
    operation = restarted.service.operation(receipt.operation_id)
    assert operation.phase is OperationPhase.CONFIRMED
    assert restarted.ingest(MAIN_RESOURCE, 4, 2, {"on": True}) == "recorded"
    assert operation.phase is OperationPhase.UNKNOWN
    assert operation.reason_code == "ambiguous_feedback_revoked"


def test_restore06_f_historical_proof_does_not_reopen_automatic_rights(tmp_path):
    """R06-F keeps retained proof as history while restart rights are closed."""
    stand = _restore06_stand(tmp_path)
    _dispatch_auto_on40(stand)
    stand.clock.advance(99)
    _room_report(stand)
    restarted = restore_file_stand(stand, episode_map={OWNER_PROPOSAL: OWNER_EPISODE})
    restarted.modules[OWNER_MODULE].queue_proposal(
        owner_proposal(proposal_id="restore06-off", target=TARGET_OFF)
    )
    restarted.service.process_module_event(OWNER_MODULE, "restore06-off")
    restarted.clock.advance(1)
    restarted.service.drain()
    assert restarted.service.record(MAIN_RESOURCE).uncertainty_reason == "restart_continuity_unproven"
    assert restarted.transport.calls == []


@pytest.mark.parametrize("state", ("unknown", "unavailable", None))
def test_u07_bound_occupancy_loss_is_explicit_invalid_observation(state):
    """U07 A01: a configured sensor cannot retain a prior presence fact."""
    adapter = RoomLightingInputAdapter(
        {"binary_sensor.presence": EntityBinding("presence", "occupancy", "bool")},
        FakeClock(), "u07", binding_revision=3,
    )
    incoming = None if state is None else _state(state)
    observations = adapter.observations(
        "binary_sensor.presence", None, incoming,
        trusted_device_report=True, source_sequence=2,
    )
    assert len(observations) == 1
    assert observations[0].quality is Quality.INVALID
    assert observations[0].value is None
