"""Selected physical input regressions; no real HA, MQTT or light commands."""

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_observations import (
    BOUNDARY_TOPIC, BRIDGE_TOPIC, MQTT_ENTRY, REPORT_TOPIC, ROLLOUT_BINDINGS,
    LightingMqttObservations,
)
from custom_components.hausman_hub.application.lighting_observation import ObservedLightState
from custom_components.hausman_hub.application.power_evidence import (
    trusted_power_state, trusted_target_observed_ms, trusted_target_state,
)
from custom_components.hausman_hub.application.room_lighting_runtime import RoomLightingRuntime
from custom_components.hausman_hub.application.room_lighting_ha_executor import RoomLightingHaExecutor


def make_case(value="on", attributes=None, *, now_ms: int | None = None):
    clock = {"now": (int(datetime.now(timezone.utc).timestamp() * 1000) - 1000
                     if now_ms is None else now_ms)}
    stamp = datetime.fromtimestamp((clock["now"] - 600_000) / 1000, timezone.utc)
    native = {
        b.entity_id: SimpleNamespace(entity_id=b.entity_id, state=value,
            attributes=attributes or {}, last_changed=stamp, last_updated=stamp,
            last_reported=stamp, context=SimpleNamespace(id="native-manual-context"))
        for b in ROLLOUT_BINDINGS
    }
    port = LightingMqttObservations(native.get, now_ms=lambda: clock["now"])
    port._enabled = frozenset(native)
    port._started = True
    port.connection_changed(True)
    clock["now"] += 10
    return port, native, clock


def report(clock, *, channel=0, session=None, sequence=1, **changes):
    binding = ROLLOUT_BINDINGS[channel]
    body = dict(schema=1, source="physical_onoff", ieee=binding.ieee,
        endpoint=binding.endpoint, attribute="genOnOff.onOff", value="ON",
        type="attributeReport", transaction=7, observed_at_ms=clock["now"],
        session=session or str(uuid4()), sequence=sequence)
    body.update(changes)
    return body


def feed(port, body, *, retained=False, topic=REPORT_TOPIC):
    port.receive(topic, json.dumps(body), retained=retained)


def trusted(port, clock, channel=0):
    return trusted_target_state(port.read_state(ROLLOUT_BINDINGS[channel].entity_id),
                                now_ms=clock["now"])


@pytest.mark.parametrize("delta", [-1, 0])
def test_fixed_clock_preserves_connection_boundary_and_gap_veto(delta):
    start = 1792189200000
    port, _, clock = make_case(now_ms=start)
    assert clock["now"] == start + 10
    feed(port, report(clock, observed_at_ms=start + delta))
    assert trusted(port, clock) is None
    # A rejected report closes the observed period at the current clock.
    clock["now"] += 1
    current = report(clock)
    feed(port, current)
    assert trusted(port, clock) == "on"
    clock["now"] += 1000
    port.connection_changed(False)
    port.connection_changed(True)
    feed(port, current)
    assert trusted(port, clock) is None


@pytest.mark.parametrize("value", ["on", "off"])
def test_same_value_report_confirms_only_its_channel_without_native_or_manual_writes(value):
    port, native, clock = make_case(value)
    entity = ROLLOUT_BINDINGS[0].entity_id
    original = dict(vars(native[entity]))
    manual = dict(generation=574, deadline=clock["now"] + 3_600_000, source="manual")
    before_manual = dict(manual)
    assert trusted(port, clock) is None
    body = report(clock, value=value.upper())
    feed(port, body)
    assert trusted(port, clock) == value
    assert trusted(port, clock, 1) is None
    view = port.read_state(entity)
    assert isinstance(view, ObservedLightState)
    assert trusted_target_observed_ms(view, now_ms=clock["now"]) == body["observed_at_ms"]
    assert vars(native[entity]) == original and manual == before_manual
    assert view.last_changed is original["last_changed"]
    assert view.last_reported is original["last_reported"]
    assert view.context is original["context"]


@pytest.mark.parametrize("changes", [
    {"schema": True}, {"source": "optimistic"}, {"source": "cache"},
    {"endpoint": True}, {"endpoint": 99}, {"ieee": "0xunknown"},
    {"attribute": "state_2"}, {"type": "commandOn"}, {"transaction": True},
    {"transaction": 256}, {"sequence": True}, {"sequence": 0},
    {"session": "cached-session"}, {"value": 1}, {"state_2": "ON"},
    {"observed_at_ms": True}, {"observed_at_ms": 1},
])
def test_invalid_or_nonphysical_report_fails_closed(changes):
    port, _, clock = make_case()
    feed(port, report(clock, **changes))
    assert trusted(port, clock) is None
    assert len(port._reports) == 0


@pytest.mark.parametrize("delta", [-300_001, 1])
def test_stale_and_future_time_rejected(delta):
    port, _, clock = make_case()
    feed(port, report(clock, observed_at_ms=clock["now"] + delta))
    assert trusted(port, clock) is None


@pytest.mark.parametrize("flag", ["restored", "assumed_state", "cached", "cache"])
def test_physical_report_does_not_override_restore_or_assumed_veto(flag):
    port, _, clock = make_case(attributes={flag: True})
    feed(port, report(clock))
    assert trusted(port, clock) is None


def test_native_disagreement_is_unknown_even_with_fresh_native_timestamp():
    port, native, clock = make_case("off")
    native[ROLLOUT_BINDINGS[0].entity_id].last_reported = datetime.now(timezone.utc)
    feed(port, report(clock, value="ON"))
    assert trusted(port, clock) is None


def test_duplicate_and_reorder_never_refresh_evidence_and_gap_closes_session():
    port, _, clock = make_case()
    body = report(clock)
    feed(port, body)
    stamp = port.read_state(ROLLOUT_BINDINGS[0].entity_id).observation.observed_at_ms
    clock["now"] += 1000
    feed(port, body)
    assert port.read_state(ROLLOUT_BINDINGS[0].entity_id).observation.observed_at_ms == stamp
    second = report(clock, session=body["session"], sequence=2, transaction=8)
    feed(port, second)
    feed(port, body)
    assert trusted(port, clock) == "on"
    clock["now"] += 1000
    feed(port, report(clock, session=body["session"], sequence=4))
    assert trusted(port, clock) is None
    clock["now"] += 1000
    feed(port, report(clock, session=body["session"], sequence=5))
    assert trusted(port, clock) is None


def test_first_sequence_n_opens_only_a_new_post_startup_window_then_gap_is_rejected():
    port, _, clock = make_case()
    first = report(clock, sequence=100)
    feed(port, first)
    assert trusted(port, clock) == "on"
    clock["now"] += 100
    feed(port, report(clock, session=first["session"], sequence=101, transaction=8))
    assert trusted(port, clock) == "on"
    clock["now"] += 100
    feed(port, report(clock, session=first["session"], sequence=103, transaction=9))
    assert trusted(port, clock) is None
    feed(port, first)
    assert trusted(port, clock) is None


@pytest.mark.parametrize("with_dependency", [False, True])
def test_provider_exact_dependency_path_uses_instance_reader(with_dependency):
    import test_room_lighting_runtime as fixture
    from custom_components.hausman_hub.application.room_lighting_ha_state import RoomLightingHaStateProvider
    port, _, clock = make_case()
    chandelier, power = ROLLOUT_BINDINGS[3].entity_id, ROLLOUT_BINDINGS[1].entity_id
    first = report(clock, channel=3)
    feed(port, first)
    provider = RoomLightingHaStateProvider(state_reader=port.read_state, now_ms=lambda: clock["now"])
    dependencies = {chandelier: fixture.DevicePowerDependency(dependent_entity_id=chandelier,
        power_source_entity_id=power, policy="auto_turn_on", warmup_seconds=0)} if with_dependency else {}
    result = provider._effective_state(None, chandelier, dependencies, now=clock["now"])
    assert result.value == ("unknown" if with_dependency else "on")
    if with_dependency:
        clock["now"] += 10
        feed(port, report(clock, channel=1, session=first["session"], sequence=2))
        result = provider._effective_state(None, chandelier, dependencies, now=clock["now"])
        assert result.value == "on"


def test_conflicting_sequence_and_expiration_fail_closed():
    port, _, clock = make_case()
    body = report(clock)
    feed(port, body)
    feed(port, {**body, "value": "OFF"})
    assert trusted(port, clock) is None
    port, _, clock = make_case()
    feed(port, report(clock))
    clock["now"] += 300_001
    assert trusted(port, clock) is None


@pytest.mark.parametrize("boundary", ["stop", "disconnect", "reconnect", "bridge", "closed"])
def test_gap_drops_volatile_evidence_and_late_old_session_cannot_reopen(boundary):
    port, _, clock = make_case()
    body = report(clock)
    feed(port, body)
    clock["now"] += 1000
    if boundary == "stop": port.stop()
    elif boundary == "disconnect": port.connection_changed(False)
    elif boundary == "reconnect": port.connection_changed(True)
    elif boundary == "bridge": port.receive(BRIDGE_TOPIC, '{"state":"online"}', retained=True)
    else: feed(port, dict(schema=1, source="observation_closed", session=body["session"],
                         sequence=2, observed_at_ms=clock["now"]), topic=BOUNDARY_TOPIC)
    assert trusted(port, clock) is None
    clock["now"] += 1000
    feed(port, report(clock, session=body["session"], sequence=2))
    assert trusted(port, clock) is None


def test_new_session_resets_other_channels_without_reopening_old_session():
    port, _, clock = make_case()
    first = report(clock)
    feed(port, first)
    clock["now"] += 100
    fresh = report(clock, channel=1)
    feed(port, fresh)
    assert trusted(port, clock) is None
    assert trusted(port, clock, 1) == "on"
    feed(port, first)
    assert trusted(port, clock) is None


@pytest.mark.parametrize("payload,retained", [('{"schema":1,"schema":1}', False),
    ("{}", True), ("x" * 2049, False), (b"\xff", False)])
def test_retained_duplicate_keys_oversize_and_invalid_encoding(payload, retained):
    port, _, clock = make_case()
    port.receive(REPORT_TOPIC, payload, retained=retained)
    assert trusted(port, clock) is None


def test_unscoped_entities_keep_original_reader_and_no_fallback_for_bound_missing_proof():
    port, native, clock = make_case()
    stamp = datetime.fromtimestamp(clock["now"] / 1000, timezone.utc)
    outside = SimpleNamespace(state="on", last_reported=stamp, attributes={})
    native["light.other_room"] = outside
    assert port.read_state("light.other_room") is outside
    assert trusted_target_state(outside, now_ms=clock["now"]) == "on"
    assert trusted(port, clock) is None


def test_runtime_provider_executor_share_explicit_view_and_physical_time():
    port, _, clock = make_case()
    runtime = RoomLightingRuntime(None, object(), object(), state_reader=port.read_state,
                                 now_ms=lambda: clock["now"])
    entity = ROLLOUT_BINDINGS[0].entity_id
    feed(port, report(clock))
    assert runtime._entity_trusted_state(entity) == "on"
    assert runtime._target_evidence_observed_ms(entity) == clock["now"]
    assert runtime._state_provider._state_reader == port.read_state
    assert runtime._executor._state_reader == port.read_state
    assert runtime._state_observed_ms(port.read_state(entity)) == clock["now"]


def test_same_millisecond_physical_sequence_is_a_distinct_evidence_revision():
    port, _, clock = make_case()
    runtime = RoomLightingRuntime(None, object(), object(), state_reader=port.read_state,
                                 now_ms=lambda: clock["now"])
    first = report(clock)
    feed(port, first)
    old = runtime._target_evidence_revision(ROLLOUT_BINDINGS[0].entity_id)
    feed(port, report(clock, session=first["session"], sequence=2, transaction=8))
    assert runtime._target_evidence_revision(ROLLOUT_BINDINGS[0].entity_id) != old


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_action", ["on", "off"])
@pytest.mark.parametrize("automatic_action", ["turn_on", "turn_off"])
async def test_fresh_physical_views_preserve_real_manual_claim_and_deny_alternate_source(
        manual_action, automatic_action):
    import test_room_lighting_runtime as fixture
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority

    port, native, clock = make_case()
    spots = ROLLOUT_BINDINGS[0].entity_id
    chandelier = ROLLOUT_BINDINGS[3].entity_id
    native[spots].state = manual_action
    native[chandelier].state = "off"
    first = report(clock, value=manual_action.upper())
    feed(port, first)
    clock["now"] += 10
    feed(port, report(clock, channel=3, value="OFF", session=first["session"], sequence=2))
    hass = fixture._FakeHass()
    hass.states.values.update(native)
    hass.states.set(fixture.AWAY_ENTITY_ID, "off", last_changed=fixture._dt(clock["now"]))
    payload = fixture._config_payload(commands_enabled=True)
    primary = payload["devices"]["light_targets"][0]
    primary["entityId"] = chandelier
    payload["devices"]["light_targets"].append({**primary, "id": "spots", "name": "Точки",
        "entityId": spots, "kind": "switch", "brightness": False, "color_temperature": False})
    authority = ManualLightAuthority()
    authority.capture(spots, manual_action, "owner", "original-gesture", observed_at_ms=clock["now"])
    before = authority.record(spots)
    executor = fixture._SpyExecutor()
    runtime = fixture._make_runtime(hass, commands_enabled=True, payload=payload,
        executor=executor, manual_light_authority=authority, now_ms=lambda: clock["now"])
    runtime._state_reader = port.read_state
    runtime._state_provider._state_reader = port.read_state
    await runtime.start(hass, "test-physical-port")
    try:
        config = fixture.config_from_payload(payload)
        action = fixture.LightAction.TURN_ON if automatic_action == "turn_on" else fixture.LightAction.TURN_OFF
        result = await runtime.async_execute_command(config, fixture.PlannedCommand("light_main", action))
        assert result["sent"] is False and executor.calls == []
        assert result["reason"] == "manual_light_priority"
        assert authority.record(spots) == before
        # A second unchanged physical report is not a second manual gesture.
        clock["now"] += 100
        feed(port, report(clock, value=manual_action.upper(), session=first["session"], sequence=3, transaction=8))
        assert authority.record(spots) == before
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_power_already_physically_on_needs_no_service_call():
    port, _, clock = make_case()
    entity = ROLLOUT_BINDINGS[1].entity_id
    feed(port, report(clock, channel=1))
    view = port.read_state(entity)
    executor = RoomLightingHaExecutor(state_reader=lambda _: view, now_ms=lambda: clock["now"])
    hass = SimpleNamespace(states=SimpleNamespace(get=port.read_state))
    assert trusted_power_state(view, same_state_confirmation=True) == "on"
    result = await executor.async_power_on(hass, entity)
    assert result["confirmed"] is True and result["sent"] is False


def test_virtual_power_expiration_does_not_depend_on_real_waiting():
    port, _, clock = make_case()
    feed(port, report(clock, channel=1))
    state = port.read_state(ROLLOUT_BINDINGS[1].entity_id)
    assert trusted_power_state(state, same_state_confirmation=True, now_ms=clock["now"]) == "on"
    clock["now"] += 300_001
    assert trusted_power_state(state, same_state_confirmation=True, now_ms=clock["now"]) is None


@pytest.mark.asyncio
async def test_real_subscription_shape_binding_checks_and_unload(monkeypatch):
    port, native, clock = make_case()
    port.stop()
    modules = {name: ModuleType(name) for name in (
        "homeassistant", "homeassistant.components", "homeassistant.components.mqtt",
        "homeassistant.core", "homeassistant.helpers", "homeassistant.helpers.entity_registry")}
    callbacks, closed = {}, []
    async def subscribe(hass, topic, callback, qos):
        assert qos == 0
        callbacks[topic] = callback
        return lambda: closed.append(topic)
    mqtt = modules["homeassistant.components.mqtt"]
    mqtt.async_subscribe = subscribe
    mqtt.is_connected = lambda _: True
    mqtt.async_subscribe_connection_status = lambda _, fn: (lambda: closed.append("connection"))
    modules["homeassistant.components"].mqtt = mqtt
    modules["homeassistant.core"].callback = lambda fn: fn
    er = modules["homeassistant.helpers.entity_registry"]
    registry = {b.entity_id: SimpleNamespace(platform="mqtt", config_entry_id=MQTT_ENTRY,
                  unique_id=b.unique_id, disabled_by=None) for b in ROLLOUT_BINDINGS}
    registry[ROLLOUT_BINDINGS[1].entity_id].unique_id = "wrong-channel"
    er.async_get = lambda _: SimpleNamespace(async_get=registry.get)
    modules["homeassistant.helpers"].entity_registry = er
    for name, module in modules.items(): monkeypatch.setitem(sys.modules, name, module)
    await port.async_start(object())
    assert set(callbacks) == {REPORT_TOPIC, BOUNDARY_TOPIC, BRIDGE_TOPIC}
    clock["now"] += 10
    callbacks[REPORT_TOPIC](SimpleNamespace(topic=REPORT_TOPIC, payload=json.dumps(report(clock)), retain=False))
    assert trusted(port, clock) == "on"
    registry[ROLLOUT_BINDINGS[0].entity_id].unique_id = "changed-after-start"
    assert trusted(port, clock) is None
    clock["now"] += 10
    feed(port, report(clock, channel=1, sequence=2, session=port._session))
    assert trusted(port, clock, 1) is None
    port.stop()
    assert len(closed) == 4 and trusted(port, clock) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("map_present", [False, True])
async def test_actual_build_context_through_exact_power_projection(map_present):
    import test_room_lighting_runtime as fixture
    from custom_components.hausman_hub.application.room_lighting_ha_state import RoomLightingHaStateProvider
    from custom_components.hausman_hub.application.room_power_migration import room_target_claim_key
    port, native, clock = make_case()
    chandelier, power = ROLLOUT_BINDINGS[3].entity_id, ROLLOUT_BINDINGS[1].entity_id
    first = report(clock, channel=3)
    feed(port, first)
    payload = fixture._config_payload()
    payload["devices"]["light_targets"][0]["entityId"] = chandelier
    config = fixture.config_from_payload(payload)
    dependencies = {chandelier: fixture.DevicePowerDependency(dependent_entity_id=chandelier,
        power_source_entity_id=power, policy="auto_turn_on", warmup_seconds=0)} if map_present else {}
    provider = RoomLightingHaStateProvider(state_reader=port.read_state, now_ms=lambda: clock["now"],
        power_dependency_resolver=lambda: dependencies,
        power_migration_manifest={room_target_claim_key(config.room_id, "light_main"): power})
    hass = fixture._FakeHass()
    hass.states.values.update(native)
    context = await provider.build_context(hass, config, clock["now"])
    assert context.lights[0].state.value == "unknown"
    clock["now"] += 10
    feed(port, report(clock, channel=1, session=first["session"], sequence=2))
    context = await provider.build_context(hass, config, clock["now"])
    assert context.lights[0].state.value == ("on" if map_present else "unknown")


def test_actual_startup_wires_the_same_reader_without_manual_event_or_state_write():
    root = Path(__file__).parents[1] / "custom_components/hausman_hub"
    tree = ast.parse((root / "__init__.py").read_text())
    runtime = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name) and n.func.id == "RoomLightingRuntime"]
    assert len(runtime) == 1
    reader = next(k.value for k in runtime[0].keywords if k.arg == "state_reader")
    assert isinstance(reader, ast.Attribute) and reader.attr == "read_state"
    source = (root / "adapters/lighting_mqtt_observations.py").read_text()
    assert "async_call(" not in source and "async_publish(" not in source
    assert "async_set(" not in source and "async_save(" not in source
