"""Actual physical input parser, fake MQTT GET transport; never touches the house."""

import asyncio
import json
from uuid import uuid4

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_observations import ROLLOUT_BINDINGS
from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import (
    LightingMqttRefresh, READ_CHANNELS, SUPPORTED_ENTITIES, POWER_ENTITY, LIGHT_ENTITY,
)
from tests.test_lighting_mqtt_observations import make_case, report, feed


def fixture(*, mode="normal", power="on"):
    port, native, clock = make_case()
    calls, session = [], str(uuid4())
    current = {"value": True}

    async def publish(topic, payload):
        calls.append((topic, json.loads(payload)))
        entity, _, _ = next(row for row in READ_CHANNELS
                           if (row[1], {row[2]: ""}) == calls[-1])
        channel = next(i for i, b in enumerate(ROLLOUT_BINDINGS) if b.entity_id == entity)
        if mode == "error":
            raise OSError("publish failed")
        if mode == "cancel":
            raise asyncio.CancelledError
        if mode == "manual":
            current["value"] = False
        if mode == "disconnect":
            port.connection_changed(False)
        if mode == "rebind":
            port._enabled = frozenset()
        value = power if entity == POWER_ENTITY else "on"
        native[entity].state = value
        clock["now"] += 10
        body = report(clock, channel=channel, session=session, sequence=len(calls), value=value.upper())
        if mode == "old":
            body["observed_at_ms"] -= 100
        if mode == "gap" and len(calls) == 2:
            body["sequence"] += 1
        if mode == "new_session" and len(calls) == 2:
            body["session"] = str(uuid4())
        feed(port, body, retained=mode == "retained")

    async def stop_waiting(_seconds):
        current["value"] = False

    adapter = LightingMqttRefresh(port, publish, now_ms=lambda: clock["now"], pause=stop_waiting)
    return adapter, port, native, clock, calls, current


@pytest.mark.parametrize("power, count", [("on", 4), ("off", 3)])
async def test_exact_gets_and_power_off_does_not_query_bulb(power, count):
    adapter, _, _, _, calls, current = fixture(power=power)
    assert await adapter(SUPPORTED_ENTITIES, lambda: current["value"])
    assert len(calls) == count
    assert all(topic.endswith("/get") and list(payload.values()) == [""] for topic, payload in calls)
    if power == "off":
        assert all(topic != READ_CHANNELS[3][1] for topic, _ in calls)


@pytest.mark.parametrize("mode", ["error", "manual", "disconnect", "rebind", "old", "retained", "gap", "new_session"])
async def test_no_completion_across_invalid_observation_or_manual_boundary(mode):
    adapter, _, _, _, calls, current = fixture(mode=mode)
    assert not await adapter(SUPPORTED_ENTITIES, lambda: current["value"])
    assert len(calls) <= 2


async def test_unsupported_scope_preflight_performs_no_publish():
    adapter, _, _, _, calls, _ = fixture()
    for scope in (frozenset(), frozenset({"light.other"}), SUPPORTED_ENTITIES - {LIGHT_ENTITY}):
        assert not await adapter(scope, lambda: True)
    assert calls == []


async def test_unready_registry_preflight_performs_no_publish():
    adapter, port, _, _, calls, _ = fixture()
    port._enabled = frozenset()
    assert not await adapter(SUPPORTED_ENTITIES, lambda: True)
    assert calls == []


async def test_cancel_is_propagated_without_background_retry():
    adapter, _, _, _, calls, _ = fixture(mode="cancel")
    with pytest.raises(asyncio.CancelledError):
        await adapter(SUPPORTED_ENTITIES, lambda: True)
    assert len(calls) == 1


async def test_total_timeout_includes_hung_publish(monkeypatch):
    adapter, _, _, _, calls, _ = fixture()

    async def hang(*_args):
        calls.append("hung")
        await asyncio.Future()

    adapter._publish = hang
    monkeypatch.setattr("custom_components.hausman_hub.adapters.lighting_mqtt_refresh.REFRESH_TIMEOUT_SECONDS", 0.01)
    assert not await adapter(SUPPORTED_ENTITIES, lambda: True)
    assert calls == ["hung"]


@pytest.mark.parametrize("manual_during_read", [False, True])
async def test_real_runtime_to_refresh_parser_and_original_return_transaction(manual_during_read):
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    from tests.test_room_lighting_force_auto import Store, seed_participants
    from tests.test_room_lighting_runtime import (
        _FakeHass, _config_payload, _make_runtime, _ROOM_ID, _dt,
    )

    adapter, observations, native, clock, calls, _ = fixture()
    hass = _FakeHass()
    hass.states.values.update(native)
    observations._native_reader = hass.states.get
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock["now"]))
    payload = _config_payload(power_switch_entity=POWER_ENTITY)
    primary = payload["devices"]["light_targets"][0]
    primary["entityId"] = LIGHT_ENTITY
    for entity, ident in ((READ_CHANNELS[0][0], "spots"), (READ_CHANNELS[1][0], "mirror")):
        payload["devices"]["light_targets"].append({**primary, "entityId": entity,
            "id": ident, "role": "accent", "kind": "switch", "brightness": False,
            "color_temperature": False})
    authority = ManualLightAuthority(Store())
    await authority.async_load()
    priority, _off = await seed_participants(hass)
    runtime = _make_runtime(hass, payload=payload, now_ms=lambda: clock["now"],
        manual_light_authority=authority, light_priority=priority, ownership_store=Store())
    runtime._state_reader = observations.read_state
    runtime._state_provider._state_reader = observations.read_state
    runtime._observation_refresher = adapter
    publish = adapter._publish

    async def publish_with_manual(topic, data):
        await publish(topic, data)
        if manual_during_read and len(calls) == 2:
            await authority.async_capture(LIGHT_ENTITY, "off", "service", "new-gesture")

    adapter._publish = publish_with_manual
    await runtime.start(hass, "refresh-integration")
    try:
        runtime._ownership.record_manual(_ROOM_ID, "light_main", clock["now"], turned_off=False)
        await authority.async_capture(LIGHT_ENTITY, "on", "service", "original-gesture")
        assert runtime._explicit_return_observations(runtime._configs[_ROOM_ID]) is None
        result = await runtime.async_force_auto(_ROOM_ID, reason="integration")
        assert result["outcome"] == ("blocked" if manual_during_read else "returned"), result
        assert authority.blocks(LIGHT_ENTITY) is manual_during_read
        assert len(calls) == (2 if manual_during_read else 4)
        if manual_during_read:
            assert result["blockers"] == ["manual_action_or_observation_changed"]
        else:
            assert result["evaluation"] == "requested"
    finally:
        await runtime.stop()


def test_startup_injects_same_observer_into_refresh_and_runtime():
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).parents[1] / "custom_components/hausman_hub/__init__.py").read_text())
    runtime = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "RoomLightingRuntime")
    port = next(k.value for k in runtime.keywords if k.arg == "observation_refresher")
    assert isinstance(port, ast.Call) and port.func.id == "LightingMqttRefresh"
    assert port.args[0].id == "lighting_observations"
    publish = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == port.args[1].id)
    call = next(n for n in ast.walk(publish) if isinstance(n, ast.Call))
    assert call.func.attr == "async_publish"
    assert {k.arg: k.value.value for k in call.keywords} == {"qos": 0, "retain": False}
