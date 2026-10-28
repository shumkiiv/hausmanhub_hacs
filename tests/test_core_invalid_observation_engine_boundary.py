"""S02: invalid input must remain unknown in the actual engine context."""
from dataclasses import replace

import pytest

from test_core_ha_state_provenance import state
from test_core_local_handoff_matrix import branch_at
from test_core_lighting_setup import _config_payload
from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig, RoomLightingCoreBranch,
)
from custom_components.hausman_hub.domain import lighting_plugin as plugin_module
from custom_components.hausman_hub.domain.core.models import (
    Evidence, LightStateTarget, Quality,
)
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import (
    EnginePolicy, SensorState, _presence_state,
)


@pytest.fixture
def engine_calls(monkeypatch):
    calls = []
    original = plugin_module.evaluate_room_lighting

    def capture(config, context, **kwargs):
        result = original(config, context, **kwargs)
        calls.append((context, result))
        return result

    monkeypatch.setattr(plugin_module, "evaluate_room_lighting", capture)
    return calls


@pytest.mark.parametrize("known", ["on", "off"])
@pytest.mark.parametrize("invalid", ["unknown", "unavailable"])
def test_real_invalid_presence_reaches_engine_as_unknown(
    tmp_path, engine_calls, known, invalid,
):
    branch, _, _ = branch_at(tmp_path, "engine-invalid")
    branch.activate_shadow()
    branch.admit_manual(
        resource_id="test_main", request_id="owner", action_id="turn_off",
        target=LightStateTarget(on=False), source="owner",
        expected_control_revision=branch.core_service.record("test_main").control_revision,
        expected_binding_revision=1, receipt_scope="owner",
    )
    manual = branch.core_service.record("test_main").manual
    branch.handle_runtime_event("binary_sensor.test_presence", None, state(known))
    branch.handle_runtime_event("binary_sensor.test_presence", state(known), state(invalid))
    context, result = engine_calls[-1]
    sensor = next(s for s in context.sensors if s.sensor_id == "sensor_presence")
    assert sensor.state is SensorState.UNKNOWN
    assert _presence_state(context, EnginePolicy()) is SensorState.UNKNOWN
    assert not result.commands
    assert branch.core_service.record("test_main").manual == manual
    assert branch.core_service.record("test_main").auto_proof is None
    assert not branch.physical_send_enabled()


@pytest.mark.parametrize("second", ["missing", "unknown", "unavailable"])
def test_configured_unknown_participant_is_not_dropped(
    tmp_path, engine_calls, second,
):
    payload = _config_payload()
    payload["devices"]["sensors"].append({
        "id": "sensor_other", "name": "Второй", "kind": "motion",
        "entityId": "binary_sensor.other", "autoAdoptOverride": None,
    })
    config = config_from_payload(payload)
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(room_id=config.room_id, config=config),
        store_path=tmp_path / "two-sensors.json",
    )
    branch.activate_shadow()
    branch.handle_runtime_event("binary_sensor.test_presence", state("on"), state("off"))
    if second != "missing":
        branch.handle_runtime_event("binary_sensor.other", state("off"), state(second))
    context, result = engine_calls[-1]
    sensors = {sensor.sensor_id: sensor for sensor in context.sensors}
    assert sensors["sensor_presence"].state is SensorState.OFF
    assert "sensor_other" in sensors
    assert sensors["sensor_other"].state is SensorState.UNKNOWN
    assert _presence_state(context, EnginePolicy()) is SensorState.UNKNOWN
    assert not result.commands
    assert not branch.physical_send_enabled()


@pytest.mark.parametrize("quality,evidence", [
    (Quality.FRESH, Evidence.ADAPTER_STATE),
    (Quality.INVALID, Evidence.ADAPTER_STATE),
    (Quality.STALE, Evidence.ADAPTER_STATE),
    (Quality.FRESH, Evidence.OPTIMISTIC),
    (Quality.FRESH, Evidence.RESTORED),
])
@pytest.mark.parametrize("active", [False, True])
def test_normalized_property_quality_survives_plugin_boundary(
    tmp_path, engine_calls, quality, evidence, active,
):
    # Normalized adapter-to-service seam, not the still-broken HA level route.
    from custom_components.hausman_hub.adapters.core.room_lighting import (
        EntityBinding, RoomLightingInputAdapter,
    )
    branch, _, _ = branch_at(tmp_path, "quality-seam")
    branch.activate_shadow()
    for resource, property_name, entity, before, after in [
        ("test_main", "on", "light.synthetic", state("off" if active else "on"), state("on" if active else "off")),
        ("test_main", "brightness_pct", "light.level", state("off"), state("on", brightness=128)),
        ("sensor_presence", "occupancy", "sensor.synthetic", state("off" if active else "on"), state("on" if active else "off")),
    ]:
        adapter = RoomLightingInputAdapter(
            {entity: EntityBinding(resource, property_name)}, branch._clock, "seam",
        )
        observation = adapter.observation(entity, before, after)
        branch.core_service.ingest_observation(replace(
            observation, quality=quality, evidence_kind=evidence,
        ))
    branch.process_schedule_tick()
    context, _ = engine_calls[-1]
    trusted = quality is Quality.FRESH and evidence is Evidence.ADAPTER_STATE
    expected = (SensorState.ON if active else SensorState.OFF) if trusted else SensorState.UNKNOWN
    assert context.sensors[0].state is expected
    light = context.light("test_main")
    assert light.state is expected
    assert light.brightness == (50 if trusted else None)
    assert not branch.physical_send_enabled()
