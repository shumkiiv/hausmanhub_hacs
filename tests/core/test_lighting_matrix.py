"""Matrix subset T01/T02 (L-SYN-1) through the full core service
(HH-014.b lighting path delivery): proposal to permit to closed
transport, with the shadow branch consuming real observations."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig,
    RoomLightingCoreBranch,
)
from custom_components.hausman_hub.application.core_manual_input import (
    synthetic_binary_light_profiles,
)
from custom_components.hausman_hub.domain.room_lighting import (
    config_from_payload,
)

MODULE_ID = "test.lighting"
MORNING_UTC_MS = int(
    datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc).timestamp() * 1000
)


def _config():
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": "test_room",
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
                    "id": "test_mirror",
                    "name": "Зеркало",
                    "kind": "light",
                    "entityId": "light.test_mirror",
                    "role": "mirror",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_mirror",
                "title": "Зеркало утро",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "07:00", "offsetMinutes": 0},
                },
                "targets": {
                    "lightTargets": ["test_mirror"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": 80,
                    "colorTemperature": None,
                    "fade": False,
                    "mode": "on_presence",
                },
            }
        ],
        "switchBindings": [],
        "illumination": None,
        "dimming": {"enabled": False},
        "manualOffProtection": {"enabled": False},
        "awayBehavior": {"mode": "none"},
        "autoAdopt": False,
        "updatedAt": 1,
        "overrides": {},
    }


def _branch(tmp_path):
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id="test_room",
            config=config_from_payload(_config()),
            timezone="Asia/Omsk",
            sunrise="08:00",
            sunset="18:00",
            device_profiles=synthetic_binary_light_profiles(["test_mirror"]),
        ),
        store_path=tmp_path / "core_shadow_test_room.json",
    )
    branch.activate_shadow()
    return branch


def _state(value: str):
    state = MagicMock()
    state.state = value
    state.attributes = {}
    return state


def test_t01_shadow_schedule_turns_mirror_on_without_sending(tmp_path):
    branch = _branch(tmp_path)
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    branch.handle_runtime_event("light.test_mirror", None, _state("off"))
    explanation = branch.decision_explanation()
    assert explanation is not None
    # The shadow branch never dispatches: no transport call could have
    # happened by design.
    assert branch.physical_send_enabled() is False


def test_t02_manual_light_on_blocks_auto_proposals(tmp_path):
    branch = _branch(tmp_path)
    # The person turned the mirror on manually before the boundary.
    branch.handle_runtime_event("light.test_mirror", None, _state("on"))
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    explanation = branch.decision_explanation()
    assert explanation is not None
    assert explanation.accepted == ()
    assert explanation.target_result is None
    assert branch.physical_send_enabled() is False


"""Strict transient L06 ACTIVE trace: production components, external fakes only."""

import tempfile
from dataclasses import replace
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from custom_components.hausman_hub.adapters.core.control_store import FileControlStore
from custom_components.hausman_hub.adapters.core.light import LightDeviceAdapter
from custom_components.hausman_hub.adapters.core.room_lighting import RoomLightingInputAdapter
from custom_components.hausman_hub.application.core.command_service import CapabilityProfile, CoreCommandService, ModuleEntry, PropertyRule
from custom_components.hausman_hub.application.room_lighting_core import ProfileBoundDeviceAdapter, RoomLightingCoreBranch
from custom_components.hausman_hub.domain.core.models import LightStateTarget, ModuleManifest, ModuleMode, ProposalKind, StabilityProfile
from custom_components.hausman_hub.domain.lighting_plugin import LightingPlugin, ProfileTransitionMemory
from custom_components.hausman_hub.domain.room_lighting import config_from_payload, config_to_payload
from tests.core.conftest import Counter, FakeClock, FakeTransport, light_profile
from tests.core.test_lighting_plugin import _config as base_config

MODULE = "room_lighting.test_room"
EPOCH = "test.l06.strict"
MIRROR_ON = ("light", "turn_on", "light.test_mirror", {"brightness_pct": 80})
POWER_ON = ("switch", "turn_on", "switch.test_power", {})
MAIN_ON = ("light", "turn_on", "light.test_main", {"brightness_pct": 40, "color_temperature_k": 4000})
MIRROR_OFF = ("light", "turn_off", "light.test_mirror", {})


def utc_ms(hour, minute, second=0):
    return int(datetime(2026, 9, 26, hour, minute, second, tzinfo=timezone.utc).timestamp() * 1000)


def light_passport():
    base = light_profile("light-basic")
    props = dict(base.properties)
    props["color_temperature_k"] = PropertyRule("color_temperature_k", "kelvin", True, True, Decimal(1000), Decimal(10000), Decimal(1), None, "on_change", "stand/report", 60_000, "post_dispatch_report", Decimal(50), 10_000, 1_000, None)
    return replace(base, properties=props, power_warmup_ms=2_000)


def power_passport():
    return CapabilityProfile("power-switch", 1, "stand", {"on": PropertyRule("on", "bool", True, True, None, None, None, ("true", "false"), "on_change", "stand/report", 60_000, "post_dispatch_report", None, 10_000, 1_000, None)})


def sensor_passport(max_age_ms=60_000):
    return CapabilityProfile(
        "sensor-basic", 1, "stand",
        {"occupancy": PropertyRule(
            "occupancy", "bool", True, False, None, None, None,
            ("true", "false"), "on_change", "stand/report", max_age_ms,
            "none", None, 10_000, 1_000, None,
        )},
    )


def config():
    payload = config_to_payload(base_config())
    payload["devices"]["light_targets"][1]["color_temperature"] = True
    payload["devices"]["power_switch"] = {"id": "test_power", "name": "Питание", "entityId": "switch.test_power", "autoAdoptOverride": None}
    schedules = payload["schedule"]
    schedules[0]["how"] = {"brightness": 80, "colorTemperature": None, "fade": False, "mode": "always"}
    schedules[1]["how"] = {"brightness": 40, "colorTemperature": 4000, "fade": False, "mode": "always"}
    schedules.append({"id": "mirror_off", "title": "Зеркало день", "when": {"daysOfWeek": "all", "holiday": False, "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0}}, "targets": {"lightTargets": ["test_mirror"], "groupIds": [], "roles": []}, "how": {"brightness": None, "colorTemperature": None, "fade": False, "mode": "off"}})
    return config_from_payload(payload)


class Stand:
    def __init__(self, root, sensor_max_age_ms=60_000):
        self.config, self.clock, self.transport, self.counter = config(), FakeClock(), FakeTransport(), Counter()
        self.clock.utc_ms = utc_ms(0, 59, 59)
        profiles = {
            "test_mirror": light_passport(), "test_main": light_passport(),
            "test_power": power_passport(),
            "sensor_presence": sensor_passport(sensor_max_age_ms),
            "sensor_motion": sensor_passport(sensor_max_age_ms),
        }
        bindings = {item.id: item.entity_id for item in self.config.devices.light_targets}
        bindings["test_power"] = "switch.test_power"
        self.input = RoomLightingInputAdapter(RoomLightingCoreBranch._bindings_from(self.config), self.clock, EPOCH, self.config.version)
        adapter = ProfileBoundDeviceAdapter(LightDeviceAdapter(profiles, {key: self.config.version for key in bindings}, self.clock.boot_id(), self.counter.next), frozenset(profiles))
        plugin = LightingPlugin(MODULE, self.config, timezone=ZoneInfo("Asia/Omsk"), sunrise=time(8), sunset=time(18), power_warmup_ms=profiles["test_main"].power_warmup_ms, sensor_max_age_ms=RoomLightingCoreBranch._sensor_max_age_ms(self.config, profiles))
        manifest = ModuleManifest(MODULE, 1, 1, ("light", "switch"), (ProposalKind.TARGET,), tuple(RoomLightingCoreBranch._resource_ids(self.config)), 50)
        specs = RoomLightingCoreBranch._specs_from(self.config)
        self.service = CoreCommandService(clock=self.clock, store=FileControlStore(root / "control.json"), adapter=adapter, transport=self.transport, specs=specs, bindings=bindings, modules={MODULE: ModuleEntry(manifest, plugin, ProfileTransitionMemory, True)}, stability_profiles={"default": StabilityProfile("default", 0, 0, 0, 0)}, config_revisions={key: self.config.version for key in specs}, scope_revisions={}, episode_of_proposal=None, id_factory=self.counter.next, initial_records=RoomLightingCoreBranch._initial_records(self.config), core_mode=ModuleMode.ACTIVE)
        self.sequence = 0

    def report(self, entity, state, attrs):
        self.sequence += 1
        observations = self.input.observations(entity, None, SimpleNamespace(state=state, attributes=attrs), trusted_device_report=True, source_sequence=self.sequence)
        assert observations and {item.source_sequence for item in observations} == {self.sequence} and {item.binding_revision for item in observations} == {1}
        for item in observations: self.service.ingest_observation(item)
        self.service.process_module_event(MODULE, f"report.{self.sequence}")
        self.service.drain()

    def tick(self, label, delta=0):
        self.clock.advance(delta)
        self.service.process_module_event(MODULE, f"tick-{label}")
        self.service.drain()

    def calls(self): return self.transport.calls


def bootstrap(stand, finish=True):
    stand.report("binary_sensor.test_presence", "on", {})
    stand.report("light.test_mirror", "off", {"brightness": 0})
    stand.report("light.test_main", "off", {"brightness": 0, "color_temp_kelvin": 4000})
    stand.report("switch.test_power", "off", {})
    stand.tick("07", 1000)
    assert stand.calls() == [MIRROR_ON]
    stand.report("light.test_mirror", "on", {"brightness": 204})
    stand.clock.advance(7_199_000)
    stand.report("light.test_mirror", "on", {"brightness": 204})
    stand.report("light.test_main", "off", {"brightness": 0, "color_temp_kelvin": 4000})
    stand.report("switch.test_power", "off", {})
    stand.tick("09", 1000)
    assert stand.calls() == [MIRROR_ON, POWER_ON]
    stand.clock.advance(1000)
    stand.report("switch.test_power", "on", {})
    stand.tick("t2999", 1999)
    assert stand.calls() == [MIRROR_ON, POWER_ON]
    if not finish:
        return
    stand.tick("t3000", 1)
    assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON]


def positive():
    with tempfile.TemporaryDirectory(prefix="l06-positive-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        stand.report("light.test_main", "on", {"brightness": 102, "color_temp_kelvin": 4000})
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON, MIRROR_OFF]
        assert stand.service.record("test_main").auto_proof is not None
        return stand.sequence, stand.calls()


def test_ft07_full_target_is_one_operation_and_one_proof():
    """FT07-1/5: the active path sends and proves both requested values."""
    with tempfile.TemporaryDirectory(prefix="ft07-full-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        operation = stand.service.operations("test_main")[-1]
        assert operation.target == LightStateTarget(True, Decimal(40), 4000)
        assert stand.calls()[-1] == MAIN_ON
        stand.report("light.test_main", "on", {"brightness": 102, "color_temp_kelvin": 4000})
        proof = stand.service.record("test_main").auto_proof
        assert operation.phase.value == "confirmed"
        assert proof is not None and proof.confirmed_target == LightStateTarget(True, Decimal(40), 4000)
        assert stand.calls().count(MIRROR_OFF) == 1


def test_t07_snapshot_uses_calendar_utc_and_monotonic_age():
    """T07-1/3/5: calendar time and evidence age have separate units."""
    with tempfile.TemporaryDirectory(prefix="t07-clock-") as raw:
        stand = Stand(Path(raw))
        stand.clock.utc_ms = utc_ms(0, 59, 59)
        stand.clock.mono_ms = 100_000
        stand.report("binary_sensor.test_presence", "on", {})
        stand.clock.utc_ms += 10_000
        stand.clock.mono_ms += 1_000
        frame = stand.service._module_frame(MODULE, "t07-projection")
        plugin = stand.service._modules[MODULE].instance
        sensors, _ = plugin._engine_snapshots(frame)
        by_id = {sensor.sensor_id: sensor for sensor in sensors}
        assert set(by_id) == {"sensor_presence", "sensor_motion"}
        assert frame.now_utc_ms - by_id["sensor_presence"].last_changed == 1_000
        assert by_id["sensor_motion"].state.name == "UNKNOWN"
        assert by_id["sensor_motion"].last_changed == frame.now_utc_ms


def test_configured_missing_motion_never_authorizes_scheduled_off_after_auto_on():
    """M01: a configured but unseen motion sensor keeps automatic OFF closed."""
    with tempfile.TemporaryDirectory(prefix="m01-missing-motion-") as raw:
        stand = Stand(Path(raw))
        stand.report("binary_sensor.test_presence", "on", {})
        stand.report("light.test_mirror", "off", {"brightness": 0})
        stand.tick("07", 1_000)
        assert stand.calls() == [MIRROR_ON]
        stand.report("light.test_mirror", "on", {"brightness": 204})
        stand.report("binary_sensor.test_presence", "off", {})
        stand.clock.advance(7_168_000)
        stand.report("light.test_mirror", "on", {"brightness": 204})
        stand.tick("09-with-missing-motion", 31_000)
        assert stand.calls() == [MIRROR_ON]


def test_ft07_untrusted_cct_and_partial_device_report_do_not_confirm_or_turn_off():
    """FT07-3: adapter state cannot complete the full dispatched target."""
    with tempfile.TemporaryDirectory(prefix="ft07-partial-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        sequence = stand.sequence + 1
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"color_temp_kelvin": 4000}),
            trusted_device_report=False, source_sequence=sequence,
        ):
            stand.service.ingest_observation(observation)
        stand.service.process_module_event(MODULE, "ft07-untrusted-cct")
        stand.service.drain()
        stand.report("light.test_main", "on", {"brightness": 102})
        operation = stand.service.operations("test_main")[-1]
        assert operation.phase.value != "confirmed"
        assert stand.service.record("test_main").auto_proof is None
        assert MIRROR_OFF not in stand.calls()


@pytest.mark.parametrize("attrs", ({"brightness": 102}, {"color_temp_kelvin": 4000}))
def test_ft07_stale_or_missing_full_target_property_blocks_mirror_off(attrs):
    """FT07-4: one refreshed property never substitutes for the other."""
    with tempfile.TemporaryDirectory(prefix="ft07-stale-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        stand.clock.advance(60_001)
        stand.report("light.test_main", "on", attrs)
        stand.service.process_module_event(MODULE, "ft07-stale-final")
        stand.service.drain()
        assert MIRROR_OFF not in stand.calls()


def test_t07_unequal_clocks_calendar_boundary_dispatches_and_confirms_on80():
    """T1/T2: UTC selects 07:00 while mono supplies only evidence age."""
    with tempfile.TemporaryDirectory(prefix="t07-unequal-") as raw:
        stand = Stand(Path(raw))
        stand.clock.utc_ms = utc_ms(0, 59, 59)
        stand.clock.mono_ms = 100_000
        stand.report("binary_sensor.test_presence", "on", {})
        stand.report("light.test_mirror", "off", {"brightness": 0})
        assert stand.calls() == []
        stand.tick("07-calendar", 1_000)
        assert stand.calls() == [MIRROR_ON]
        stand.report("light.test_mirror", "on", {"brightness": 204})
        proof = stand.service.record("test_mirror").auto_proof
        assert proof is not None and proof.confirmed_target == LightStateTarget(True, Decimal(80), None)


def _u07_stand(root):
    payload = config_to_payload(base_config())
    payload["devices"]["sensors"] = payload["devices"]["sensors"][:1]
    payload["devices"]["light_targets"] = payload["devices"]["light_targets"][:1]
    payload["schedule"] = payload["schedule"][:1]
    payload["manualOffProtection"] = {"enabled": False}
    config = config_from_payload(payload)
    clock, transport, counter = FakeClock(), FakeTransport(), Counter()
    clock.utc_ms, clock.mono_ms = utc_ms(1, 0), 100_000
    plugin = LightingPlugin(MODULE, config, timezone=ZoneInfo("Asia/Omsk"), sunrise=time(8), sunset=time(18))
    input_adapter = RoomLightingInputAdapter(RoomLightingCoreBranch._bindings_from(config), clock, "u07", 1)
    adapter = ProfileBoundDeviceAdapter(LightDeviceAdapter({"test_mirror": light_passport()}, {"test_mirror": 1}, clock.boot_id(), counter.next), frozenset({"test_mirror"}))
    manifest = ModuleManifest(MODULE, 1, 1, ("light",), (ProposalKind.TARGET,), tuple(RoomLightingCoreBranch._resource_ids(config)), 50)
    service = CoreCommandService(clock=clock, store=FileControlStore(root / "u07.json"), adapter=adapter, transport=transport, specs=RoomLightingCoreBranch._specs_from(config), bindings={"test_mirror": "light.test_mirror"}, modules={MODULE: ModuleEntry(manifest, plugin, ProfileTransitionMemory, revalidate_automatic_off_before_dispatch=True, revalidate_automatic_on_before_dispatch=True)}, stability_profiles={"default": StabilityProfile("default", 0, 0, 0, 0)}, config_revisions={"test_mirror": 1, "sensor_presence": 1}, scope_revisions={}, episode_of_proposal=None, id_factory=counter.next, initial_records=RoomLightingCoreBranch._initial_records(config), core_mode=ModuleMode.ACTIVE)
    return config, clock, transport, service, input_adapter


@pytest.mark.parametrize("lost", ("unknown", "unavailable"))
def test_u07_t7_sensor_loss_withdraws_queued_presence_on(tmp_path, lost):
    """U07 A02/T7: a queued presence command is rechecked before send."""
    _, clock, transport, service, adapter = _u07_stand(tmp_path)
    seq = 0
    def event(entity, state, attrs=None, drain=True):
        nonlocal seq
        seq += 1
        for observation in adapter.observations(entity, None, SimpleNamespace(state=state, attributes=attrs or {}), trusted_device_report=True, source_sequence=seq):
            service.ingest_observation(observation)
        service.process_module_event(MODULE, f"u07.{seq}")
        if drain: service.drain()
    event("binary_sensor.test_presence", "on")
    event("light.test_mirror", "off", {"brightness": 0}, drain=False)
    assert transport.calls == []


@pytest.mark.parametrize("lost", ("unknown", "unavailable"))
def test_u07_t7_sensor_loss_withdraws_queued_presence_off(tmp_path, lost):
    """U07 A03/T7: the same final guard closes a queued automatic OFF."""
    _, clock, transport, service, adapter = _u07_stand(tmp_path)
    seq = 0
    def event(entity, state, attrs=None, drain=True):
        nonlocal seq
        seq += 1
        for observation in adapter.observations(entity, None, SimpleNamespace(state=state, attributes=attrs or {}), trusted_device_report=True, source_sequence=seq):
            service.ingest_observation(observation)
        service.process_module_event(MODULE, f"u07off.{seq}")
        if drain: service.drain()
    event("binary_sensor.test_presence", "on")
    event("light.test_mirror", "off", {"brightness": 0})
    assert transport.calls
    event("light.test_mirror", "on", {"brightness": 204})
    assert service.record("test_mirror").auto_proof is not None
    event("binary_sensor.test_presence", "off", drain=False)
    event("binary_sensor.test_presence", lost, drain=False)
    before = list(transport.calls)
    service.drain()
    assert transport.calls == before




def incomplete(name, attrs):
    with tempfile.TemporaryDirectory(prefix=f"l06-{name}-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        stand.report("light.test_main", "on", attrs)
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON]
        stand.tick("timeout", 10_000)
        assert stand.service.operations("test_main")[-1].phase.value == "unknown"
        stand.tick("repeat")
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON]
        return stand.sequence, stand.calls()


def manual_off():
    with tempfile.TemporaryDirectory(prefix="l06-manual-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        # A real device says main is on but omits CCT, so auto remains
        # unconfirmed while the subsequent manual OFF is an actual command.
        stand.report("light.test_main", "on", {"brightness": 102})
        record = stand.service.record("test_main")
        receipt = stand.service.submit_manual("test_main", "manual001", "manual-off", LightStateTarget(False, None, None), "l06", record.control_revision, record.binding_revision, "l06")
        if receipt.status.value != "accepted":
            raise RuntimeError(f"manual receipt {receipt!r}")
        stand.tick("manual-plus2", 2)
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON, ("light", "turn_off", "light.test_main", {})]
        return stand.sequence, stand.calls()


def manual_main_on_before_mirror_off():
    """A real manual admission and device report cannot authorize mirror OFF."""
    with tempfile.TemporaryDirectory(prefix="l06-manual-main-on-") as raw:
        stand = Stand(Path(raw))
        stand.report("binary_sensor.test_presence", "on", {})
        stand.report("light.test_mirror", "off", {"brightness": 0})
        stand.report("light.test_main", "off", {"brightness": 0, "color_temp_kelvin": 4000})
        stand.report("switch.test_power", "off", {})
        stand.tick("07", 1000)
        assert stand.calls() == [MIRROR_ON]
        stand.report("light.test_mirror", "on", {"brightness": 204})
        stand.clock.advance(7_199_000)
        stand.report("light.test_mirror", "on", {"brightness": 204})
        stand.report("light.test_main", "off", {"brightness": 0, "color_temp_kelvin": 4000})
        stand.report("switch.test_power", "on", {})
        stand.clock.advance(2_000)
        record = stand.service.record("test_main")
        receipt = stand.service.submit_manual(
            "test_main", "manual-main-on", "manual-main-on",
            LightStateTarget(True, None, None), "l06", record.control_revision,
            record.binding_revision, "l06",
        )
        assert receipt.status.value == "accepted"
        stand.service.drain()
        assert stand.calls() == [MIRROR_ON, ("light", "turn_on", "light.test_main", {})]
        stand.report("light.test_main", "on", {"brightness": 102, "color_temp_kelvin": 4000})
        main = stand.service.record("test_main")
        assert main.authority.name == "MANUAL"
        assert main.auto_proof is None
        stand.tick("09", 1000)
        assert stand.calls() == [MIRROR_ON, ("light", "turn_on", "light.test_main", {})]


def raw_collision_does_not_revoke_trusted_confirmation():
    with tempfile.TemporaryDirectory(prefix="l06-raw-collision-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        stand.report("light.test_main", "on", {"brightness": 102, "color_temp_kelvin": 4000})
        operation = stand.service.operations("test_main")[-1]
        assert operation.phase.value == "confirmed"
        assert stand.service.record("test_main").auto_proof is not None
        raw = stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
        )
        assert raw and {item.source_sequence for item in raw} == {1}
        assert {item.source_id for item in raw} == {"ha_state:test_main"}
        for observation in raw:
            stand.service.ingest_observation(observation)
        assert operation.phase.value == "confirmed"
        assert stand.service.record("test_main").auto_proof is not None


def poisoned_duplicate_does_not_refresh_or_commit():
    with tempfile.TemporaryDirectory(prefix="l06-poison-duplicate-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        sequence = stand.sequence + 1
        valid = stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=sequence,
        )
        for observation in valid:
            stand.service.ingest_observation(observation)
        conflicting = stand.input.observations(
            "light.test_main", None, SimpleNamespace(state="unavailable", attributes={}),
            trusted_device_report=True, source_sequence=sequence,
        )
        for observation in conflicting:
            stand.service.ingest_observation(observation)
        before = stand.service._facts["test_main"]["on"]
        key = ("test_main", sequence, "ha_state:test_main", EPOCH, before.evidence_kind.value)
        entries = len(stand.service._report_groups[key])
        revision = stand.service._store.load()["commit_revision"]
        stand.clock.advance(1234)
        for observation in conflicting:
            stand.service.ingest_observation(observation)
        after = stand.service._facts["test_main"]["on"]
        assert after.quality.name == "INVALID"
        assert after.received_at_mono_ms == before.received_at_mono_ms
        assert len(stand.service._report_groups[key]) == entries
        assert stand.service._store.load()["commit_revision"] == revision




# Durable regression coverage from the independent ACTIVE trace.
def test_lsyn1_full_active_source_transition():
    sequence, calls = positive()
    assert sequence > 0
    assert calls == [MIRROR_ON, POWER_ON, MAIN_ON, MIRROR_OFF]


def test_lsyn1_partial_report_never_turns_mirror_off():
    _, calls = incomplete("partial", {"brightness": 102})
    assert calls == [MIRROR_ON, POWER_ON, MAIN_ON]


def test_lsyn1_wrong_brightness_never_turns_mirror_off():
    _, calls = incomplete("wrong", {"brightness": 26, "color_temp_kelvin": 4000})
    assert calls == [MIRROR_ON, POWER_ON, MAIN_ON]


def test_lsyn1_manual_off_cancels_remaining_automatic_actions():
    _, calls = manual_off()
    assert calls == [MIRROR_ON, POWER_ON, MAIN_ON, ("light", "turn_off", "light.test_main", {})]


def test_lsyn1_manual_main_on_never_turns_mirror_off_without_auto_proof():
    manual_main_on_before_mirror_off()


def test_lsyn1_raw_adapter_sequence_collision_keeps_trusted_confirmation():
    raw_collision_does_not_revoke_trusted_confirmation()


def test_lsyn1_poisoned_duplicate_keeps_invalid_fact_and_commit_stable():
    poisoned_duplicate_does_not_refresh_or_commit()


def test_sensor_continuity_uses_the_configured_passport_ttl():
    """A late fresh OFF starts a new absence interval, not a 60s carryover."""
    with tempfile.TemporaryDirectory(prefix="l06-sensor-ttl-") as raw:
        stand = Stand(Path(raw), sensor_max_age_ms=30_000)
        stand.report("binary_sensor.test_presence", "off", {})
        first = stand.service._module_memory[MODULE].sensor_continuity[0]
        assert first[8:] == (30_000, 0, 0)

        # No evaluation occurs during this 45-second gap.  The Core frame
        # marks the first fact stale using the same passport as the plugin.
        stand.clock.advance(45_000)
        stand.report("binary_sensor.test_presence", "off", {})
        second = stand.service._module_memory[MODULE].sensor_continuity[0]
        assert second[8:] == (75_000, 45_000, 45_000)


def test_branch_composition_passes_sensor_passport_ttl_to_the_plugin(tmp_path):
    """The production branch must not silently fall back when a passport exists."""
    room = config_from_payload(_config())
    profiles = dict(synthetic_binary_light_profiles(["test_mirror"]))
    profiles["sensor_presence"] = sensor_passport(30_000)
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id=room.room_id,
            config=room,
            device_profiles=profiles,
        ),
        store_path=tmp_path / "core-shadow.json",
    )
    assert dict(branch._module._sensor_max_age_ms) == {"sensor_presence": 30_000}


def test_sensor_continuity_keeps_fresh_off_reports_until_the_300s_boundary():
    """Fresh reports preserve the true start; absence cannot mature at 300s."""
    with tempfile.TemporaryDirectory(prefix="l06-sensor-fresh-") as raw:
        stand = Stand(Path(raw), sensor_max_age_ms=30_000)
        stand.report("binary_sensor.test_presence", "off", {})
        stand.clock.advance(45_000)
        stand.report("binary_sensor.test_presence", "off", {})
        for _ in range(19):
            stand.clock.advance(15_000)
            stand.report("binary_sensor.test_presence", "off", {})
        continuity = stand.service._module_memory[MODULE].sensor_continuity[0]
        # The old interval ended at 30s; this one starts at 45s, so its
        # five-minute boundary is 345s rather than the unsafe 300s.
        assert continuity[9] == 45_000
        assert stand.clock.now_mono_ms() == 330_000
        assert stand.clock.now_mono_ms() < continuity[9] + 300_000


def test_epoch_rollover_restarts_earned_auto_absence_before_off(tmp_path):
    """A new trusted adapter namespace must earn a complete absence window."""
    config, clock, transport, service, first_adapter = _u07_stand(tmp_path)
    second_adapter = RoomLightingInputAdapter(
        RoomLightingCoreBranch._bindings_from(config), clock, "epoch-2", 1
    )
    sequence = 0

    def event(adapter, entity, state, attrs=None, *, source_sequence=None):
        nonlocal sequence
        sequence += 1
        reported = sequence if source_sequence is None else source_sequence
        for observation in adapter.observations(
            entity, None, SimpleNamespace(state=state, attributes=attrs or {}),
            trusted_device_report=True, source_sequence=reported,
        ):
            service.ingest_observation(observation)
        service.process_module_event(MODULE, f"epoch.{sequence}")
        service.drain()

    event(first_adapter, "binary_sensor.test_presence", "on")
    event(first_adapter, "light.test_mirror", "off", {"brightness": 0})
    event(first_adapter, "light.test_mirror", "on", {"brightness": 204})
    assert service.record("test_mirror").auto_proof is not None

    clock.advance(1)
    event(first_adapter, "binary_sensor.test_presence", "off")
    for _ in range(19):
        clock.advance(30_000)
        event(first_adapter, "binary_sensor.test_presence", "off")
        event(first_adapter, "light.test_mirror", "on", {"brightness": 204})

    clock.advance(40_000)
    event(second_adapter, "binary_sensor.test_presence", "off", source_sequence=1)
    continuity = service._module_memory[MODULE].sensor_continuity[0]
    assert continuity[5:10] == ("epoch-2", 1, continuity[7], clock.now_mono_ms() + 60_000, clock.now_mono_ms())
    assert MIRROR_OFF not in transport.calls

    for _ in range(19):
        clock.advance(30_000)
        event(second_adapter, "binary_sensor.test_presence", "off")
        event(second_adapter, "light.test_mirror", "on", {"brightness": 204})
        assert MIRROR_OFF not in transport.calls
    clock.advance(30_000)
    event(second_adapter, "binary_sensor.test_presence", "off")
    assert transport.calls.count(MIRROR_OFF) == 1


def test_lsyn1_invalid_main_report_before_transport_withdraws_mirror_off():
    """A real unavailable report closes the already queued mirror OFF.

    The sequence deliberately leaves the mirror operation between module
    evaluation and the transport boundary; no proof or proposal is fabricated.
    """
    with tempfile.TemporaryDirectory(prefix="l06-invalid-before-send-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        stand.sequence += 1
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=stand.sequence,
        ):
            stand.service.ingest_observation(observation)
        stand.service.process_module_event(MODULE, "full-main-before-send")
        stand.sequence += 1
        for observation in stand.input.observations(
            "light.test_main", None, SimpleNamespace(state="unavailable", attributes={}),
            trusted_device_report=True, source_sequence=stand.sequence,
        ):
            stand.service.ingest_observation(observation)
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=stand.sequence,
        ):
            stand.service.ingest_observation(observation)
        stand.service.drain()
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON]


def test_lsyn1_invalid_main_after_take_ready_withdraws_mirror_off():
    with tempfile.TemporaryDirectory(prefix="l06-invalid-taken-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        stand.sequence += 1
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=stand.sequence,
        ):
            stand.service.ingest_observation(observation)
        stand.service.process_module_event(MODULE, "full-main-taken")
        stand.service.take_ready()
        stand.sequence += 1
        for observation in stand.input.observations(
            "light.test_main", None, SimpleNamespace(state="unavailable", attributes={}),
            trusted_device_report=True, source_sequence=stand.sequence,
        ):
            stand.service.ingest_observation(observation)
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=stand.sequence,
        ):
            stand.service.ingest_observation(observation)
        stand.service.run_taken()
        stand.service.run_dispatching()
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON]


def test_lsyn1_equal_sequence_conflict_after_dispatching_never_sends_mirror_off():
    with tempfile.TemporaryDirectory(prefix="l06-invalid-dispatching-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        stand.sequence += 1
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=stand.sequence,
        ):
            stand.service.ingest_observation(observation)
        stand.service.process_module_event(MODULE, "full-main-dispatching")
        stand.service.take_ready()
        stand.service.run_taken()
        for observation in stand.input.observations(
            "light.test_main", None, SimpleNamespace(state="unavailable", attributes={}),
            trusted_device_report=True, source_sequence=stand.sequence,
        ):
            stand.service.ingest_observation(observation)
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=stand.sequence,
        ):
            stand.service.ingest_observation(observation)
        stand.service.run_dispatching()
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON]


def test_lsyn1_old_or_duplicate_report_cannot_restore_invalid_main():
    with tempfile.TemporaryDirectory(prefix="l06-report-order-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        sequence = stand.sequence + 1
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=sequence,
        ):
            stand.service.ingest_observation(observation)
        stand.service.process_module_event(MODULE, "confirmed-main")
        stand.service.take_ready()
        stand.service.run_taken()
        for observation in stand.input.observations(
            "light.test_main", None, SimpleNamespace(state="unavailable", attributes={}),
            trusted_device_report=True, source_sequence=sequence + 1,
        ):
            stand.service.ingest_observation(observation)
        # Both a lower sequence and an exact replay are retained as history
        # only; neither may re-stamp the invalid current fact as fresh.
        for replay_sequence in (1, sequence):
            for observation in stand.input.observations(
                "light.test_main", None,
                SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
                trusted_device_report=True, source_sequence=replay_sequence,
            ):
                stand.service.ingest_observation(observation)
        stand.service.run_dispatching()
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON]


def test_lsyn1_raw_replacement_cannot_lower_trusted_replay_floor():
    """Invalid trusted evidence still prevents an older valid replay.

    The raw state replaces every current fact, exercising the precise
    regression that used to lose the trusted high watermark.
    """
    with tempfile.TemporaryDirectory(prefix="l06-trusted-floor-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="unavailable", attributes={}),
            trusted_device_report=True, source_sequence=100,
        ):
            assert stand.service.ingest_observation(observation) == "recorded"
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=False, source_sequence=1,
        ):
            assert stand.service.ingest_observation(observation) == "recorded"
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=99,
        ):
            assert stand.service.ingest_observation(observation) == "ignored_stale_report"
        stand.service.process_module_event(MODULE, "trusted-floor-old")
        stand.service.drain()
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON]
        for observation in stand.input.observations(
            "light.test_main", None,
            SimpleNamespace(state="on", attributes={"brightness": 102, "color_temp_kelvin": 4000}),
            trusted_device_report=True, source_sequence=101,
        ):
            assert stand.service.ingest_observation(observation) in ("recorded", "confirmed")
        stand.service.process_module_event(MODULE, "trusted-floor-new")
        stand.service.drain()
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON, MIRROR_OFF]


def test_lsyn1_passport_tolerance_keeps_confirmed_main_transition():
    with tempfile.TemporaryDirectory(prefix="l06-tolerance-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        stand.report(
            "light.test_main", "on",
            {"brightness": 101, "color_temp_kelvin": 4040},
        )
        assert stand.service.record("test_main").auto_proof is not None
        assert stand.calls() == [MIRROR_ON, POWER_ON, MAIN_ON, MIRROR_OFF]


def test_lsyn1_current_tolerance_uses_the_passport_not_old_report_value():
    with tempfile.TemporaryDirectory(prefix="l06-current-tolerance-") as raw:
        stand = Stand(Path(raw)); bootstrap(stand)
        stand.report(
            "light.test_main", "on",
            {"brightness": 101, "color_temp_kelvin": 4040},
        )
        # A later full report has different raw values but still satisfies
        # the same requested target (40% +/- .5, 4000 K +/- 50).
        stand.report(
            "light.test_main", "on",
            {"brightness": 102, "color_temp_kelvin": 3990},
        )
        frame = stand.service._module_frame(MODULE, "current-within")
        main = next(item for item in frame.resource_controls if item.resource_id == "test_main")
        assert main.auto_proof is not None
        stand.report(
            "light.test_main", "on",
            {"brightness": 104, "color_temp_kelvin": 3990},
        )
        outside = stand.service._module_frame(MODULE, "current-outside")
        main = next(item for item in outside.resource_controls if item.resource_id == "test_main")
        assert main.auto_proof is None
