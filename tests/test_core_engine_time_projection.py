"""S02/S05: observed age uses one boot's monotonic duration, not UTC age."""
from dataclasses import replace

import pytest

from test_core_ha_brightness_routing import ControlledClock, initial
from test_core_ha_state_provenance import state
from test_core_invalid_observation_engine_boundary import engine_calls
from test_core_local_handoff_matrix import branch_at
from custom_components.hausman_hub.application import room_lighting_core as branch_module
from custom_components.hausman_hub.adapters.core.room_lighting import (
    EntityBinding, RoomLightingInputAdapter,
)
from custom_components.hausman_hub.domain.core.models import InputFrame, LightStateTarget
from custom_components.hausman_hub.domain.room_lighting_engine import (
    EnginePolicy, SensorState, _presence_state,
)


class ShiftableClock(ControlledClock):
    def __init__(self):
        super().__init__()
        self.utc_shift = 0

    def now_utc_ms(self):
        return super().now_utc_ms() + self.utc_shift


@pytest.fixture
def timed_branch(tmp_path, monkeypatch):
    clock = ShiftableClock()
    monkeypatch.setattr(branch_module, "SystemClock", lambda: clock)
    branch, _, _ = branch_at(tmp_path, "time-projection")
    branch.activate_shadow()
    branch.admit_manual(
        resource_id="test_main", request_id="time-owner", action_id="turn_off",
        target=LightStateTarget(on=False), source="owner",
        expected_control_revision=branch.core_service.record("test_main").control_revision,
        expected_binding_revision=1, receipt_scope="owner",
    )
    initial(branch)
    branch.handle_runtime_event("binary_sensor.test_presence", state("off"), state("on"))
    return branch, clock


@pytest.mark.parametrize("elapsed", [0, 1_000, 300_000, 300_001])
def test_real_snapshot_age_equals_same_boot_monotonic_duration(
    timed_branch, engine_calls, elapsed,
):
    branch, clock = timed_branch
    manual = branch.core_service.record("test_main").manual
    clock.elapsed += elapsed
    branch.process_schedule_tick()
    context, _ = engine_calls[-1]
    sensor = next(sensor for sensor in context.sensors if sensor.sensor_id == "sensor_presence")
    assert context.now - sensor.last_changed == elapsed
    assert context.now - context.light("test_main").last_changed == elapsed
    expected = SensorState.ON if elapsed <= 300_000 else SensorState.UNKNOWN
    assert _presence_state(context, EnginePolicy()) is expected
    assert branch.core_service.record("test_main").manual == manual
    assert branch.core_service.record("test_main").auto_proof is None
    assert not branch.physical_send_enabled()


def test_schedule_tick_does_not_refresh_observation_time(timed_branch, engine_calls):
    branch, clock = timed_branch
    before = branch.resource_view("sensor_presence").facts
    clock.elapsed += 1_234
    branch.process_schedule_tick()
    context, _ = engine_calls[-1]
    assert branch.resource_view("sensor_presence").facts == before
    assert context.now - context.sensors[0].last_changed == 1_234
    assert context.sensors[0].continuous_absence_since is None


@pytest.mark.parametrize("jump", [3_600_000, -3_600_000])
def test_calendar_jump_does_not_increase_monotonic_age(timed_branch, engine_calls, jump):
    branch, clock = timed_branch
    manual = branch.core_service.record("test_main").manual
    clock.elapsed += 1_234
    clock.utc_shift = jump
    branch.process_schedule_tick()
    context, _ = engine_calls[-1]
    assert context.now - context.sensors[0].last_changed == 1_234
    assert context.now - context.light("test_main").last_changed == 1_234
    assert branch.core_service.record("test_main").manual == manual
    assert not branch._clock.wall_clock_trust().trusted
    assert not branch.physical_send_enabled()


def test_negative_utc_projection_does_not_become_fresh(timed_branch, engine_calls):
    branch, clock = timed_branch
    clock.elapsed += 1_234
    clock.utc_shift = 1 - (1_790_000_000_000 + clock.elapsed)
    branch.process_schedule_tick()
    context, _ = engine_calls[-1]
    assert context.now == 1
    assert context.sensors[0].state is SensorState.UNKNOWN
    assert context.light("test_main").state is SensorState.UNKNOWN
    assert context.light("test_main").brightness is None
    assert _presence_state(context, EnginePolicy()) is SensorState.UNKNOWN
    assert not branch.physical_send_enabled()


@pytest.mark.parametrize("property_name", ["on", "brightness_pct"])
@pytest.mark.parametrize("cause", ["different-boot", "future-mono", "negative-projection"])
def test_synthetic_normalized_properties_have_independent_time_guards(
    timed_branch, engine_calls, property_name, cause,
):
    # Explicit normalized seam, not a physical HA report or a service mutation.
    branch, clock = timed_branch
    manual = branch.core_service.record("test_main").manual
    adapter = RoomLightingInputAdapter({
        "light.on": EntityBinding("test_main", "on"),
        "light.level": EntityBinding("test_main", "brightness_pct"),
    }, clock, "synthetic-time-seam")
    observations = [
        adapter.observation("light.on", state("off"), state("on")),
        adapter.observation("light.level", state("off"), state("on", brightness=128)),
    ]
    for index, observation in enumerate(observations):
        if observation.property != property_name:
            continue
        if cause == "different-boot":
            observation = replace(observation, boot_id="foreign-process")
        elif cause == "future-mono":
            observation = replace(observation, received_at_mono_ms=clock.now_mono_ms() + 1)
        else:
            observation = replace(observation, received_at_mono_ms=0)
        observations[index] = observation
    frame = InputFrame(
        now_utc_ms=1 if cause == "negative-projection" else clock.now_utc_ms(),
        now_mono_ms=clock.now_mono_ms(), boot_id=clock.boot_id(),
        event_id="synthetic-time-seam", config_revision=1,
        observations=tuple(observations), resource_controls=(),
        related_scope_revisions={}, inbound_needs=(),
    )
    branch._module.evaluate(frame, None)
    context, _ = engine_calls[-1]
    light = context.light("test_main")
    assert light.state is (SensorState.UNKNOWN if property_name == "on" else SensorState.ON)
    assert light.brightness == (None if property_name == "brightness_pct" else 50)
    assert branch.core_service.record("test_main").manual == manual
    assert branch.core_service.record("test_main").auto_proof is None
    assert not branch.physical_send_enabled()


@pytest.mark.parametrize("cause", ["future-mono", "different-boot"])
def test_unprojectable_observation_is_unknown_not_current(timed_branch, engine_calls, cause):
    branch, clock = timed_branch
    if cause == "future-mono":
        clock.elapsed -= 1
    else:
        clock._boot_id = "different-process"
    branch.process_schedule_tick()
    context, _ = engine_calls[-1]
    assert context.sensors[0].state is SensorState.UNKNOWN
    assert context.light("test_main").state is SensorState.UNKNOWN
    assert _presence_state(context, EnginePolicy()) is SensorState.UNKNOWN
    assert branch.core_service.record("test_main").auto_proof is None
    assert not branch.physical_send_enabled()
