"""S02: HA entity removal invalidates state, never proves physical OFF."""
import asyncio
from types import SimpleNamespace

import pytest

from test_core_ha_brightness_routing import ControlledClock
from test_core_ha_state_provenance import state
from test_core_invalid_observation_engine_boundary import engine_calls
from test_core_local_handoff_matrix import branch_at
from test_core_stop_drain import started
from custom_components.hausman_hub.adapters.core.control_store import FileControlStore
from custom_components.hausman_hub.application import room_lighting_core as branch_module
from custom_components.hausman_hub.domain.core.models import LightStateTarget, Quality
from custom_components.hausman_hub.domain.room_lighting_engine import SensorState


@pytest.fixture
def removal_branch(tmp_path, monkeypatch):
    clock = ControlledClock()
    monkeypatch.setattr(branch_module, "SystemClock", lambda: clock)
    branch, path, _ = branch_at(tmp_path, "entity-removal")
    branch.activate_shadow()
    branch.admit_manual(
        resource_id="test_main", request_id="removal-owner", action_id="turn_off",
        target=LightStateTarget(on=False), source="owner",
        expected_control_revision=branch.core_service.record("test_main").control_revision,
        expected_binding_revision=1, receipt_scope="owner",
    )
    return branch, path


def history(branch, path):
    image = FileControlStore(path).load()
    return (
        branch.core_service.record("test_main").manual,
        image["operations"], image["receipts"], image["receipt_times"],
    )


def seed(branch, entity, known):
    level = 128 if known == "on" else 0
    current = state(known, brightness=level) if entity.startswith("light.") else state(known)
    previous = state("off" if known == "on" else "on", brightness=255)
    branch.handle_runtime_event(entity, previous, current)
    return current


def assert_unknown(branch, context, entity):
    resource = "test_main" if entity.startswith("light.") else "sensor_presence"
    assert all(fact.quality is not Quality.FRESH for fact in branch.resource_view(resource).facts)
    if resource == "test_main":
        assert context.light(resource).state is SensorState.UNKNOWN
        assert context.light(resource).brightness is None
    else:
        assert context.sensors[0].state is SensorState.UNKNOWN
    assert branch.core_service.record("test_main").auto_proof is None
    assert not branch.physical_send_enabled()


@pytest.mark.parametrize("entity", ["binary_sensor.test_presence", "light.test_main"])
@pytest.mark.parametrize("known", ["on", "off"])
@pytest.mark.parametrize("missing_old", [False, True])
def test_removal_invalidates_previously_known_entity(
    removal_branch, engine_calls, entity, known, missing_old,
):
    branch, path = removal_branch
    previous = seed(branch, entity, known)
    before = history(branch, path)
    count = len(engine_calls)
    branch.handle_runtime_event(entity, None if missing_old else previous, None)
    assert len(engine_calls) == count + 1
    assert_unknown(branch, engine_calls[-1][0], entity)
    assert history(branch, path) == before


@pytest.mark.parametrize("entity", ["binary_sensor.test_presence", "light.test_main"])
def test_repeat_removal_does_not_restore_fact_or_extend_manual(
    removal_branch, engine_calls, entity,
):
    branch, path = removal_branch
    previous = seed(branch, entity, "on")
    before = history(branch, path)
    branch.handle_runtime_event(entity, previous, None)
    branch.handle_runtime_event(entity, None, None)
    assert_unknown(branch, engine_calls[-1][0], entity)
    assert history(branch, path) == before


@pytest.mark.parametrize("entity", ["binary_sensor.test_presence", "light.test_main"])
def test_removal_without_previous_fact_does_not_invent_known_off(
    removal_branch, engine_calls, entity,
):
    branch, path = removal_branch
    before = history(branch, path)
    branch.handle_runtime_event(entity, None, None)
    branch.process_schedule_tick()
    assert_unknown(branch, engine_calls[-1][0], entity)
    assert history(branch, path) == before


def test_unknown_entity_removal_is_ignored(removal_branch, engine_calls):
    branch, path = removal_branch
    seed(branch, "light.test_main", "on")
    before = history(branch, path), branch.resource_view("test_main").facts, path.read_bytes()
    count = len(engine_calls)
    branch.handle_runtime_event("light.not_bound", state("on", brightness=128), None)
    assert len(engine_calls) == count
    assert (history(branch, path), branch.resource_view("test_main").facts, path.read_bytes()) == before


def test_closed_branch_drops_late_removal(removal_branch):
    branch, path = removal_branch
    seed(branch, "light.test_main", "on")
    branch.on_runtime_stop()
    before = path.read_bytes(), branch.resource_view("test_main").facts
    branch.handle_runtime_event("light.test_main", state("on", brightness=128), None)
    assert (path.read_bytes(), branch.resource_view("test_main").facts) == before


@pytest.mark.asyncio
async def test_real_runtime_callback_delivers_removal_to_real_branch(tmp_path, engine_calls):
    config, hass, intake, executor, runtime, hooks, branch = await started(tmp_path)
    path = tmp_path / f"hausman_hub.core_shadow_{config.room_id}.json"
    before = history(branch, path)
    try:
        runtime._state_event(SimpleNamespace(data={
            "entity_id": "binary_sensor.test_presence", "old_state": state("off"),
            "new_state": state("on"),
        }))
        await asyncio.gather(*hass.tasks, return_exceptions=False)
        assert branch.resource_view("sensor_presence").facts[0].quality is Quality.FRESH
        count = len(engine_calls)
        runtime._state_event(SimpleNamespace(data={
            "entity_id": "binary_sensor.test_presence", "old_state": state("on"),
            "new_state": None,
        }))
        await asyncio.gather(*hass.tasks, return_exceptions=False)
        assert len(engine_calls) == count + 1
        assert_unknown(branch, engine_calls[-1][0], "binary_sensor.test_presence")
        assert history(branch, path) == before
        assert executor.calls == []
    finally:
        await runtime.stop()
