"""Atomic closed intake projections and legacy writer reservations (HH-014.b)."""

from dataclasses import replace
import importlib
import sys
from types import SimpleNamespace

import pytest
import test_local_summary_access as harness


@pytest.fixture
def api(monkeypatch):
    for name, module in harness.fake_home_assistant_modules().items():
        monkeypatch.setitem(sys.modules, name, module)
    return importlib.import_module(
        "custom_components.hausman_hub.application.core_manual_input"
    )


def registration(room="room_a", suffix="a"):
    from custom_components.hausman_hub.application.core.command_service import (
        ClosedRegistration, ClosedTargetRegistration,
    )
    return ClosedRegistration(
        branch_id=room, import_id=f"import_{suffix}",
        source_identity=f"source_{suffix}", source_revision=3,
        source_payload_digest="a" * 64,
        targets=(ClosedTargetRegistration(
            target_id=f"target_{suffix}", resource_id=f"resource_{suffix}",
            entity_id=f"light.{suffix}", property_name="on",
            allowed_actions=("turn_on", "turn_off"),
            binding_revision=1, config_revision=2,
        ),),
    )


def snapshot(intake):
    return (
        dict(intake._bindings), dict(intake._branches),
        dict(intake._closed_projection_by_room),
        dict(intake._unresolved_registration_scopes), intake._unavailable,
    )


@pytest.mark.parametrize("fault", [
    "source_bool", "digest", "targets_list", "target_shape", "entity",
    "property", "binding_bool", "config_negative", "action_duplicate",
    "action_unknown", "duplicate_target", "duplicate_resource", "duplicate_entity",
])
def test_invalid_last_participant_never_partially_publishes(api, fault):
    intake = api.CoreManualInput()
    intake.mark_closed_registration_scope_unavailable(
        branch_id="room_a", entity_ids=frozenset({"light.old"})
    )
    before = snapshot(intake)
    value = registration()
    target = value.targets[0]
    if fault == "source_bool":
        value = replace(value, source_revision=True)
    elif fault == "digest":
        value = replace(value, source_payload_digest="invalid")
    elif fault == "targets_list":
        value = replace(value, targets=list(value.targets))
    elif fault == "target_shape":
        value = replace(value, targets=(target, object()))
    elif fault.startswith("duplicate_"):
        second = replace(target, target_id="target_b", resource_id="resource_b", entity_id="light.b")
        field = {"duplicate_target": "target_id", "duplicate_resource": "resource_id", "duplicate_entity": "entity_id"}[fault]
        value = replace(value, targets=(target, replace(second, **{field: getattr(target, field)})))
    else:
        field, invalid = {
            "entity": ("entity_id", "climate.a"),
            "property": ("property_name", "brightness"),
            "binding_bool": ("binding_revision", True),
            "config_negative": ("config_revision", -1),
            "action_duplicate": ("allowed_actions", ("turn_on", "turn_on")),
            "action_unknown": ("allowed_actions", ("toggle",)),
        }[fault]
        second = replace(target, target_id="target_b", resource_id="resource_b", entity_id="light.b")
        second = replace(second, **{field: invalid})
        value = replace(value, targets=(target, second))
    with pytest.raises(api.CoreManualInputError):
        intake.install_closed_registration_projection(value)
    assert snapshot(intake) == before


@pytest.mark.parametrize("field", ["target_id", "resource_id", "entity_id"])
def test_other_room_collision_preserves_existing_scope(api, field):
    intake = api.CoreManualInput()
    first, second = registration(), registration("room_b", "b")
    intake.install_closed_registration_projection(first)
    before = snapshot(intake)
    second = replace(second, targets=(replace(
        second.targets[0], **{field: getattr(first.targets[0], field)}
    ),))
    with pytest.raises(api.CoreManualInputError):
        intake.install_closed_registration_projection(second)
    assert snapshot(intake) == before
    assert intake.command_entity_ids() == frozenset({"light.a"})


def test_projection_without_branch_reserves_and_never_falls_back(api):
    intake = api.CoreManualInput()
    value = registration()
    intake.install_closed_registration_projection(value)
    assert intake.is_transferred("target_a")
    assert not intake.registration_ready("target_a")
    assert intake.registration_ready("unrelated")
    assert intake.command_entity_ids() == frozenset({"light.a"})
    branch = SimpleNamespace(room_id="room_a", input_closed=False)
    intake.install_closed_registration_projection(value, branch=branch)
    assert intake.registration_ready("target_a")
    branch.input_closed = True
    assert not intake.registration_ready("target_a")
    assert intake.command_entity_ids() == frozenset({"light.a"})


def test_global_unknown_survives_successful_other_room_projection(api):
    intake = api.CoreManualInput()
    intake.mark_closed_registration_unavailable()
    intake.install_closed_registration_projection(registration())
    assert not intake.registration_ready("unrelated")
    with pytest.raises(api.CoreManualInputError):
        intake.command_entity_ids()


def test_branch_mismatch_and_changed_registration_are_atomic(api):
    intake = api.CoreManualInput()
    value = registration()
    before = snapshot(intake)
    with pytest.raises(api.CoreManualInputError):
        intake.install_closed_registration_projection(value, branch=SimpleNamespace(room_id="wrong"))
    assert snapshot(intake) == before
    intake.install_closed_registration_projection(value)
    intake.install_closed_registration_projection(value)
    before = snapshot(intake)
    with pytest.raises(api.CoreManualInputError):
        intake.install_closed_registration_projection(replace(value, source_revision=4))
    with pytest.raises(api.CoreManualInputError):
        intake.resolve_closed_registration_as_legacy(branch_id="room_a")
    assert snapshot(intake) == before


@pytest.mark.parametrize("invalid", ["light.a", {"light.a"}, frozenset({"climate.a"}), frozenset({True})])
def test_unproved_scope_shape_cannot_reopen_legacy(api, invalid):
    intake = api.CoreManualInput()
    intake.mark_closed_registration_unavailable(branch_id="room_a")
    intake.resolve_closed_registration_scope(branch_id="room_a", entity_ids=invalid)
    assert not intake.registration_ready("unrelated")
    with pytest.raises(api.CoreManualInputError):
        intake.command_entity_ids()


def test_proved_empty_room_does_not_block_neighbour(api):
    intake = api.CoreManualInput()
    intake.mark_closed_registration_unavailable(branch_id="room_a")
    intake.resolve_closed_registration_scope(branch_id="room_a", entity_ids=frozenset())
    assert intake.registration_ready("unrelated")
    assert intake.command_entity_ids() == frozenset()


@pytest.mark.parametrize("count", [0, 1, 2])
def test_branch_install_requires_one_exact_registration(api, count):
    intake = api.CoreManualInput()
    value = registration()
    branch = SimpleNamespace(
        room_id="room_a", input_closed=False,
        core_service=SimpleNamespace(closed_registrations=lambda: (value,) * count),
    )
    if count == 1:
        intake.install_closed_registration(branch)
        assert intake.registration_ready("target_a")
    else:
        with pytest.raises(api.CoreManualInputError):
            intake.install_closed_registration(branch)
        assert not intake.transferred_target_ids()
