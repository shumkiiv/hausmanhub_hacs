"""S02: one real HA entity event, independent property freshness."""
import pytest

from test_core_ha_state_provenance import state
from test_core_local_handoff_matrix import branch_at
from test_core_invalid_observation_engine_boundary import engine_calls
from test_core_lighting_setup import _config_payload
from custom_components.hausman_hub.adapters.core.clock import SystemClock
from custom_components.hausman_hub.application import room_lighting_core as branch_module
from custom_components.hausman_hub.domain.core.models import Evidence, LightStateTarget, Quality
from custom_components.hausman_hub.domain.room_lighting import config_from_payload


class ControlledClock(SystemClock):
    def __init__(self):
        super().__init__()
        self.elapsed = 10_000

    def now_mono_ms(self):
        return self.elapsed

    def now_utc_ms(self):
        return 1_790_000_000_000 + self.elapsed


@pytest.fixture
def routed_branch(tmp_path, monkeypatch):
    clock = ControlledClock()
    monkeypatch.setattr(branch_module, "SystemClock", lambda: clock)
    branch, _, _ = branch_at(tmp_path, "routing")
    branch.activate_shadow()
    return branch, clock


def facts(branch):
    return {fact.property: fact for fact in branch.resource_view("test_main").facts}


def initial(branch):
    branch.handle_runtime_event(
        "light.test_main", state("off", brightness=0), state("on", brightness=128),
    )


def test_real_event_calculates_once_after_complete_property_group(routed_branch, engine_calls):
    branch, _ = routed_branch
    count = len(engine_calls)
    initial(branch)
    current = facts(branch)
    assert set(current) == {"on", "brightness_pct"}
    assert current["on"].value is True and current["brightness_pct"].value == 50
    assert len(engine_calls) == count + 1
    light = engine_calls[-1][0].light("test_main")
    assert light.state.value == "on" and light.brightness == 50
    assert branch.core_service.record("test_main").auto_proof is None
    assert not branch.physical_send_enabled()


def test_property_group_preserves_manual_hold_and_receipt(routed_branch):
    branch, _ = routed_branch
    branch.admit_manual(
        resource_id="test_main", request_id="owner-preserved", action_id="turn_off",
        target=LightStateTarget(on=False), source="owner",
        expected_control_revision=branch.core_service.record("test_main").control_revision,
        expected_binding_revision=1, receipt_scope="owner",
    )
    manual = branch.core_service.record("test_main").manual
    receipt = branch.core_service.receipt("owner-preserved", receipt_scope="owner")
    initial(branch)
    assert branch.core_service.record("test_main").manual == manual
    assert branch.core_service.receipt("owner-preserved", receipt_scope="owner") == receipt
    assert branch.core_service.record("test_main").auto_proof is None
    assert not branch.physical_send_enabled()


@pytest.mark.parametrize("raw, expected", [(255, 100), (129, 51)])
def test_level_change_with_unchanged_on_reaches_core(routed_branch, raw, expected):
    branch, clock = routed_branch
    initial(branch)
    clock.elapsed += 1_000
    branch.handle_runtime_event(
        "light.test_main", state("on", brightness=128), state("on", brightness=raw),
    )
    current = facts(branch)["brightness_pct"]
    assert current.value == expected and current.quality is Quality.FRESH
    assert current.evidence is Evidence.ADAPTER_STATE
    assert current.observed_at_mono_ms == clock.elapsed


@pytest.mark.parametrize("after", [
    state("on", brightness=128, battery=90), state("on", brightness=128),
    state("on", brightness="128"), state("on", brightness=128.0),
    state("on", brightness=127), state("on", battery=90),
])
def test_unrelated_or_same_level_keeps_property_age(routed_branch, after):
    branch, clock = routed_branch
    initial(branch)
    before = facts(branch)["brightness_pct"]
    clock.elapsed += 2_000
    branch.handle_runtime_event("light.test_main", state("on", brightness=128), after)
    assert facts(branch)["brightness_pct"] == before


@pytest.mark.parametrize("raw", [True, False, -1, 256, 128.5, float("inf"), float("-inf")])
def test_real_invalid_level_replaces_previous_fresh_level(routed_branch, raw):
    branch, clock = routed_branch
    initial(branch)
    clock.elapsed += 1_000
    branch.handle_runtime_event(
        "light.test_main", state("on", brightness=128), state("on", brightness=raw),
    )
    assert facts(branch)["brightness_pct"].quality is Quality.INVALID
    assert branch.core_service.record("test_main").auto_proof is None
    assert not branch.physical_send_enabled()


@pytest.mark.parametrize("after", [
    state("on", brightness=128, restored=True),
    state("on", brightness=128, assumed_state=True),
    state("unknown", brightness=128), state("unavailable", brightness=128),
])
def test_same_level_does_not_hide_worsened_trust(routed_branch, after):
    branch, clock = routed_branch
    initial(branch)
    clock.elapsed += 1_000
    branch.handle_runtime_event("light.test_main", state("on", brightness=128), after)
    assert facts(branch)["brightness_pct"].quality is not Quality.FRESH


@pytest.mark.parametrize("after,evidence", [
    (state("unknown"), Evidence.ADAPTER_STATE),
    (state("unavailable"), Evidence.ADAPTER_STATE),
    (state("on", restored=True), Evidence.RESTORED),
    (state("on", assumed_state=True), Evidence.OPTIMISTIC),
])
@pytest.mark.parametrize("intermediate", [False, True])
def test_loss_of_device_trust_invalidates_level_even_when_attribute_missing(
    routed_branch, engine_calls, after, evidence, intermediate,
):
    branch, clock = routed_branch
    initial(branch)
    previous = state("on", brightness=128)
    if intermediate:
        branch.handle_runtime_event("light.test_main", previous, state("on", battery=80))
        previous = state("on", battery=80)
    clock.elapsed += 1_000
    branch.handle_runtime_event("light.test_main", previous, after)
    level = facts(branch)["brightness_pct"]
    assert level.quality is Quality.INVALID and level.evidence is evidence
    assert engine_calls[-1][0].light("test_main").brightness is None
    assert branch.core_service.record("test_main").auto_proof is None
    assert not branch.physical_send_enabled()


def test_non_dimmable_target_and_sensor_route_stay_single_property(tmp_path):
    payload = _config_payload()
    payload["devices"]["light_targets"][0]["brightness"] = False
    config = config_from_payload(payload)
    branch = branch_module.RoomLightingCoreBranch(
        branch_config=branch_module.CoreBranchConfig(room_id=config.room_id, config=config),
        store_path=tmp_path / "non-dimmable.json",
    )
    branch.activate_shadow()
    initial(branch)
    assert set(facts(branch)) == {"on"}
    branch.handle_runtime_event("binary_sensor.test_presence", state("off"), state("on"))
    assert [fact.property for fact in branch.resource_view("sensor_presence").facts] == ["occupancy"]
    assert not branch.physical_send_enabled()
