"""Real physical parser and restricted GET transport, all synthetic."""
import asyncio
import json
from uuid import uuid4

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import (
    LightingMqttRefresh, READ_CHANNELS, POWER_ENTITY, LIGHT_ENTITY,
)
from custom_components.hausman_hub.adapters.lighting_mqtt_observations import ROLLOUT_BINDINGS
from tests.test_lighting_mqtt_observations import make_case, report, feed


def fixture(mode="normal"):
    port, native, clock = make_case(attributes={"brightness": 0})
    calls, session = [], str(uuid4())
    current = [True]

    async def publish(topic, payload):
        prop = next(iter(json.loads(payload)))
        calls.append((topic, json.loads(payload)))
        if mode == "error":
            raise OSError("synthetic read failure")
        if mode == "cancel":
            raise asyncio.CancelledError
        if mode == "manual":
            current[0] = False
        if mode == "disconnect":
            port.connection_changed(False)
        if mode == "rebind":
            port._enabled = frozenset()
        entity = POWER_ENTITY if prop == "state_2" else LIGHT_ENTITY
        channel = next(i for i, binding in enumerate(ROLLOUT_BINDINGS) if binding.entity_id == entity)
        clock["now"] += 10
        value = "OFF" if mode == "power_off" and entity == POWER_ENTITY else "ON"
        native[entity].state = value.lower()
        body = report(clock, channel=channel, session=session, sequence=len(calls), value=value)
        if prop == "brightness":
            body.update(source="physical_brightness", attribute="genLevelCtrl.currentLevel", value=0)
        if mode == "old":
            body["observed_at_ms"] -= 100
        if mode == "future":
            body["observed_at_ms"] += 1
        if mode == "gap" and len(calls) == 2:
            body["sequence"] += 1
        if mode == "new_session" and len(calls) == 2:
            body["session"] = str(uuid4())
        feed(port, body, retained=mode == "retained")

    async def no_real_wait(_seconds):
        current[0] = False

    adapter = LightingMqttRefresh(port, publish, now_ms=lambda: clock["now"], pause=no_real_wait)
    return adapter, calls, current


async def test_exact_pair_reads_three_existing_properties_without_any_set():
    adapter, calls, current = fixture()
    assert await adapter.refresh_zero_pair(adapter.zero_pair_entities, lambda: current[0])
    assert calls == [(READ_CHANNELS[2][1], {"state_2": ""}),
                     (READ_CHANNELS[3][1], {"state": ""}),
                     (READ_CHANNELS[3][1], {"brightness": ""})]


@pytest.mark.parametrize("mode", ["error", "manual", "disconnect", "rebind", "old", "future", "retained", "gap", "new_session", "power_off"])
async def test_bad_read_never_completes_or_retries(mode):
    adapter, calls, current = fixture(mode)
    assert not await adapter.refresh_zero_pair(adapter.zero_pair_entities, lambda: current[0])
    assert 1 <= len(calls) <= 2


@pytest.mark.parametrize("scope", [frozenset(), frozenset({LIGHT_ENTITY}), frozenset({"light.other", POWER_ENTITY})])
async def test_non_exact_scope_does_not_publish(scope):
    adapter, calls, _ = fixture()
    assert not await adapter.refresh_zero_pair(scope, lambda: True)
    assert calls == []


async def test_cancel_propagates_without_retry():
    adapter, calls, _ = fixture("cancel")
    with pytest.raises(asyncio.CancelledError):
        await adapter.refresh_zero_pair(adapter.zero_pair_entities, lambda: True)
    assert len(calls) == 1


async def test_one_common_timeout_is_bounded(monkeypatch):
    import custom_components.hausman_hub.adapters.lighting_mqtt_refresh as module
    adapter, calls, _ = fixture()
    async def never_return(*_args):
        await asyncio.Future()
    adapter._publish = never_return
    monkeypatch.setattr(module, "REFRESH_TIMEOUT_SECONDS", 0.001)
    assert not await adapter.refresh_zero_pair(adapter.zero_pair_entities, lambda: True)
    assert calls == []
