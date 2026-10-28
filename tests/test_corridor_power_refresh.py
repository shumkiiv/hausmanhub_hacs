"""Corridor runtime, real dependency service and parser; synthetic lower ports only."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from custom_components.hausman_hub.adapters import lighting_mqtt_refresh as refresh
from custom_components.hausman_hub.adapters.lighting_mqtt_observations import (
    LightingMqttObservations, MQTT_ENTRY, ROLLOUT_BINDINGS,
)
from custom_components.hausman_hub.application.device_power_dependencies import DevicePowerDependencyService
from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
from custom_components.hausman_hub.application.power_evidence import trusted_target_state, trusted_target_observed_ms
from tests.test_lighting_mqtt_observations import feed, make_case, report
from tests.test_manual_light_off_protection import _settings
from tests.test_room_lighting_force_auto import Store, seed_participants
from tests.test_room_lighting_runtime import _FakeHass, _SpyExecutor, _config_payload, _dt, _make_runtime

ROOM = "malyi_koridor"
BULB = "light.0xa4c138d69d102803"
RELAY = "switch.0x603d61fffe759363_1"
PAIR = frozenset({BULB, RELAY})
GETS = [
    ("zigbee2mqtt/Выключатель малый коридор/get", {"state_1": ""}),
    ("zigbee2mqtt/Люстра малый коридор/get", {"state": ""}),
]


def _channel(entity):
    return next(i for i, binding in enumerate(ROLLOUT_BINDINGS) if binding.entity_id == entity)


def _documents(case):
    return deepcopy({
        "authority": case.authority._payload(case.authority._records, case.authority.generation),
        "priority": case.priority._payload(), "off": case.off._payload(),
        "ownership": case.runtime._ownership.to_payload(),
    })


def _assert_preserved(case, before):
    assert _documents(case) == before
    assert case.authority._store.payload == before["authority"]
    assert case.priority._store.payload == before["priority"]
    assert case.off._store.payload == before["off"]
    assert case.runtime._ownership_store.payload == before["ownership"]
    assert case.executor.calls == []
    assert case.payload["commandsEnabled"] is False
    assert case.runtime._configs[ROOM].commands_enabled is False


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


@asynccontextmanager
async def _corridor(*, power="on", mode=None):
    port, native, clock = make_case()
    native[RELAY].state = power
    hass = _FakeHass()
    hass.states.values.update(native)
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock["now"]))
    port._native_reader = hass.states.get
    dependency_store = Store()
    dependency_store.payload = {
        "revision": 5, "updatedAt": "2026-10-25T12:00:00Z",
        "dependencies": [{"dependentEntityId": BULB, "powerSourceEntityId": RELAY,
                          "policy": "auto_turn_on", "warmupSeconds": 3}],
    }
    service = DevicePowerDependencyService(
        dependency_store, entity_pair_validator=lambda bulb, relay: (bulb, relay) == (BULB, RELAY),
        electrical_breaker_resolver=lambda _: False,
        now=lambda: datetime(2026, 10, 25, 12, tzinfo=timezone.utc),
    )
    await service.async_load()
    payload = _config_payload()
    payload["roomId"] = ROOM
    payload["devices"]["light_targets"][0]["entityId"] = BULB
    assert payload["devices"]["light_targets"][0]["id"] == "light_main"
    assert payload["devices"]["light_targets"][0]["role"] == "main"
    assert payload["devices"]["power_switch"] is None
    assert payload["commandsEnabled"] is False
    authority = ManualLightAuthority(Store())
    await authority.async_load()
    priority, off = await seed_participants(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, payload=payload, now_ms=lambda: clock["now"],
                            manual_light_authority=authority, light_priority=priority,
                            ownership_store=Store(), executor=executor)
    resolver = lambda: service.mapping
    runtime._power_dependency_resolver = resolver
    runtime._state_provider._power_dependency_resolver = resolver
    runtime._state_reader = port.read_state
    runtime._state_provider._state_reader = port.read_state
    case = SimpleNamespace(port=port, native=native, clock=clock, hass=hass, payload=payload,
        service=service, dependency_store=dependency_store, authority=authority, priority=priority,
        off=off, executor=executor, runtime=runtime, calls=[], scopes=[], injected=[],
        session=str(uuid4()), sequence=0, entered=asyncio.Event(), release=asyncio.Event())

    def deliver(entity, **changes):
        case.sequence += 1
        clock["now"] += 10
        body = report(clock, channel=_channel(entity), session=case.session,
                      sequence=case.sequence, value=native[entity].state.upper())
        body.update(changes)
        feed(port, body, retained=mode == "retained")
        return body
    case.deliver = deliver

    async def publish(topic, data):
        call = (topic, json.loads(data))
        assert call in GETS, "Only the two exact empty GETs are permitted"
        case.calls.append(call)
        entity = RELAY if call == GETS[0] else BULB
        if mode in {"hold", "hang"} and len(case.calls) == 1:
            case.entered.set()
            await case.release.wait()
        if mode == "wrong_endpoint":
            case.injected.append(deliver(entity, endpoint=2))
        elif mode == "stale":
            case.injected.append(deliver(entity, observed_at_ms=clock["now"] - 300_001))
        elif mode == "future":
            case.injected.append(deliver(entity, observed_at_ms=clock["now"] + 1000))
        elif mode == "native_mismatch":
            case.injected.append(deliver(entity, value="OFF"))
        elif mode in {"gap", "new_session", "disconnect"}:
            first = deliver(entity)
            assert trusted_target_state(port.read_state(entity), now_ms=clock["now"]) == power
            clock["now"] += 10
            if mode == "gap":
                body = report(clock, channel=_channel(entity), session=first["session"], sequence=first["sequence"] + 2)
                feed(port, body)
                assert port.reason == "sequence_gap"
            elif mode == "new_session":
                body = report(clock, channel=_channel(entity), session=str(uuid4()), sequence=1)
                feed(port, body)
                assert port.boundary_generation > boundary_before[0]
            else:
                port.connection_changed(False)
                assert not port._connected
                body = first
            case.injected.append(body)
        else:
            case.injected.append(deliver(entity))

    adapter = refresh.LightingMqttRefresh(port, publish, now_ms=lambda: clock["now"])
    case.adapter = adapter
    async def observed_scope(entities, current):
        case.scopes.append(entities)
        return await adapter(entities, current)
    runtime._observation_refresher = observed_scope
    boundary_before = [port.boundary_generation]
    await runtime.start(hass, "corridor-pair")
    try:
        # All four real participants are populated only after startup recovery.
        await authority.async_capture(BULB, "off", "service", "original-gesture", observed_at_ms=clock["now"])
        runtime._ownership.record_manual(ROOM, "light_main", clock["now"], turned_off=True)
        assert await runtime._persist_ownership()
        settings = _settings()
        settings["profiles"][0].update(roomId=ROOM, profileId="light_main", lightIds=[BULB], presenceSensorIds=[])
        await off.async_replace_settings("settings", 0, settings)
        await off.async_note_state_transition(BULB, SimpleNamespace(state="on"), SimpleNamespace(state="off"), None)
        priority._manual_records[BULB] = dict(entityId=BULB, targetId="light_main", ownership="manual",
                                               evidenceRevision=None, confirmedAt=clock["now"])
        await priority._store.async_save(priority._payload())
        documents = _documents(case)
        assert all(documents[key]["records"] for key in ("authority", "priority", "ownership"))
        assert documents["off"]["protections"] and documents["off"]["receipts"]
        assert documents["authority"]["recentFingerprints"] == ["original-gesture"]
        assert service.document["revision"] == 5
        await _settle()
        yield case
    finally:
        case.release.set()
        await runtime.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("power", ["on", "off"])
async def test_real_global_service_runtime_pair_on_off_preserves_four_full_stores(power):
    async with _corridor(power=power) as case:
        if power == "off":
            case.deliver(BULB)
            old_observation = case.port.read_state(BULB).observation
            old_time = trusted_target_observed_ms(case.port.read_state(BULB), now_ms=case.clock["now"])
            assert old_observation is not None and old_time is not None
        before = _documents(case)
        assert await case.runtime._refresh_manual_return_observations(ROOM) is None
        assert case.scopes == [PAIR]
        assert case.calls == (GETS if power == "on" else GETS[:1])
        assert trusted_target_state(case.port.read_state(RELAY), now_ms=case.clock["now"]) == power
        if power == "on":
            assert trusted_target_state(case.port.read_state(BULB), now_ms=case.clock["now"]) == "on"
            assert trusted_target_observed_ms(case.port.read_state(BULB), now_ms=case.clock["now"]) == case.clock["now"]
        else:
            assert case.port.read_state(BULB).observation == old_observation
            assert trusted_target_observed_ms(case.port.read_state(BULB), now_ms=case.clock["now"]) == old_time
        _assert_preserved(case, before)


@pytest.mark.asyncio
async def test_pair_scope_fault_injection_is_detected_without_side_effects(monkeypatch):
    async with _corridor() as case:
        before = _documents(case)
        monkeypatch.delitem(refresh.READ_SCOPES, PAIR)
        assert await case.runtime._refresh_manual_return_observations(ROOM) == "observations_unavailable"
        assert case.scopes == [PAIR] and case.calls == []
        _assert_preserved(case, before)


@pytest.mark.asyncio
async def test_singleton_still_reads_once_new_relay_confirmation_and_unsupported_scopes_never_read():
    async with _corridor() as case:
        before = _documents(case)
        assert await case.adapter(frozenset({BULB}), lambda: True)
        assert case.calls == GETS[1:]
        case.calls.clear()
        assert not await case.adapter.refresh_confirmation(RELAY, lambda: True)
        for scope in (frozenset({RELAY}), frozenset({BULB, "light.foreign"}),
                      frozenset({RELAY, BULB, "switch.0x603d61fffe767766_1"}), frozenset({"light.foreign"})):
            assert not await case.adapter(scope, lambda: True)
        assert case.calls == []
        _assert_preserved(case, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["retained", "wrong_endpoint", "disabled_registry", "stale", "future", "native_mismatch"])
async def test_pair_negative_reports_and_registry_refuse_without_retry_or_store_changes(mode):
    async with _corridor(mode=mode) as case:
        if mode == "disabled_registry":
            rows = {b.entity_id: SimpleNamespace(platform="mqtt", config_entry_id=MQTT_ENTRY,
                    unique_id=b.unique_id, disabled_by="user" if b.entity_id == RELAY else None)
                    for b in ROLLOUT_BINDINGS}
            case.port._registry_reader = rows.get
            assert not case.port.refresh_ready(PAIR)
        before = _documents(case)
        assert await case.runtime._refresh_manual_return_observations(ROOM) == "observations_unavailable"
        assert case.scopes == [PAIR]
        assert case.calls == ([] if mode == "disabled_registry" else GETS[:1])
        assert len(case.injected) == (0 if mode == "disabled_registry" else 1)
        assert trusted_target_state(case.port.read_state(RELAY), now_ms=case.clock["now"]) is None
        await _settle()
        assert case.calls == ([] if mode == "disabled_registry" else GETS[:1])
        _assert_preserved(case, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["gap", "disconnect", "new_session"])
async def test_real_boundary_after_accepted_same_session_report_closes_current_pass(mode):
    async with _corridor(mode=mode) as case:
        before = _documents(case)
        generation = case.port.boundary_generation
        assert await case.runtime._refresh_manual_return_observations(ROOM) == "observations_unavailable"
        assert case.injected and case.port.boundary_generation > generation
        assert case.calls == GETS[:1]
        await _settle()
        assert case.calls == GETS[:1]
        _assert_preserved(case, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["manual", "dependency"])
async def test_manual_and_real_dependency_cas_during_publish_are_not_overwritten(change):
    async with _corridor(mode="hold") as case:
        task = asyncio.create_task(case.runtime._refresh_manual_return_observations(ROOM))
        try:
            await asyncio.wait_for(case.entered.wait(), 2)
            if change == "manual":
                await case.authority.async_capture(BULB, "on", "service", "new-gesture", observed_at_ms=case.clock["now"])
                assert case.authority.record(BULB).fingerprint == "new-gesture"
            else:
                current = case.service.document["dependencies"][0]
                await case.service.async_replace(5, [{**current, "warmupSeconds": 4}])
                assert case.service.document["revision"] == 6
                assert case.dependency_store.payload == {
                    key: value for key, value in case.service.document.items() if key != "contract"}
            after_action = _documents(case)
            dependency_after = deepcopy(case.dependency_store.payload)
            case.release.set()
            assert await asyncio.wait_for(task, 2) == "manual_action_or_observation_changed"
            assert case.calls == GETS[:1]
            _assert_preserved(case, after_action)
            assert case.dependency_store.payload == dependency_after
        finally:
            case.release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_actual_eight_second_hung_publish_timeout_and_cancellation_leave_no_new_tasks(cancel):
    async with _corridor(mode="hang") as case:
        assert refresh.REFRESH_TIMEOUT_SECONDS == 8
        before = _documents(case)
        baseline = set(asyncio.all_tasks())
        start = time.monotonic()
        task = asyncio.create_task(case.runtime._refresh_manual_return_observations(ROOM))
        try:
            await asyncio.wait_for(case.entered.wait(), 2)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert await asyncio.wait_for(task, 10) == "observations_unavailable"
                assert 7.5 <= time.monotonic() - start < 10
            case.release.set()
            await _settle()
            assert task.done() and case.calls == GETS[:1]
            assert not (set(asyncio.all_tasks()) - baseline)
            _assert_preserved(case, before)
        finally:
            case.release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_real_node_relay_endpoint_on_off_only_and_old_bindings_reject_new_channel():
    path = Path(__file__).parents[1] / "tools/zigbee2mqtt/hausman_light_reports.mjs"
    port, native, clock = make_case()
    clock["now"] += 20
    script = '''import {physicalOnOff,physicalBrightness,physicalColorTemperature} from %s;
const now=%s;
const frame=(changes={})=>({device:{ieeeAddr:'0x603d61fffe759363'},endpoint:{ID:1},
type:'attributeReport',cluster:'genOnOff',data:{onOff:1},meta:{zclTransactionSequenceNumber:7,rawData:Buffer.from([1])},...changes});
const accepted=[0,1].map(value=>physicalOnOff(frame({data:{onOff:value}}),now));
const rejected=[{endpoint:{ID:2}},{type:'commandOn'},{type:'commandOff'},{type:'publishCached'},
{cluster:'genLevelCtrl',data:{currentLevel:64}},{cluster:'lightingColorCtrl',data:{colorTemperature:300}}]
.map(changes=>physicalOnOff(frame(changes),now));
const properties=[physicalBrightness(frame({cluster:'genLevelCtrl',data:{currentLevel:64}}),now),
physicalColorTemperature(frame({cluster:'lightingColorCtrl',data:{colorTemperature:300}}),now)];
console.log(JSON.stringify({accepted,rejected,properties}));
''' % (json.dumps(path.as_uri()), clock["now"])
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True,
                            text=True, timeout=5, check=True)
    value = json.loads(result.stdout)
    assert [body["value"] for body in value["accepted"]] == ["OFF", "ON"]
    assert value["rejected"] == [None] * 6 and value["properties"] == [None, None]
    old_bindings = tuple(binding for binding in ROLLOUT_BINDINGS if binding.entity_id != RELAY)
    old = LightingMqttObservations(native.get, now_ms=lambda: clock["now"], bindings=old_bindings)
    old._started, old._enabled = True, frozenset(binding.entity_id for binding in old_bindings)
    old.connection_changed(True)
    clock["now"] += 10
    new_report = {**value["accepted"][1], "observed_at_ms": clock["now"], "session": str(uuid4()), "sequence": 1}
    feed(port, new_report)
    assert trusted_target_state(port.read_state(RELAY), now_ms=clock["now"]) == "on"
    feed(old, new_report)
    assert old.reason == "invalid_report" and old._reports == {}
