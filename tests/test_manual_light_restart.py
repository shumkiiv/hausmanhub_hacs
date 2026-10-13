"""Existing-store startup through real authority, listener, runtime and executor."""

import asyncio
import copy
import importlib
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import test_manual_light_authority as authority_fixture
import test_room_lighting_runtime as runtime_fixture

from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
from custom_components.hausman_hub.manual_light_authority_events import ManualLightAuthorityEvents

pytestmark = pytest.mark.asyncio
ENTITY = "light.demo_main"
SENSOR = "binary_sensor.demo_presence"


def payload(action=None, *, observed=None, duration=3_600_000, legacy=False):
    records = []
    if action is not None:
        records.append({
            "entityId": ENTITY, "action": action, "source": "service",
            "fingerprint": "before-restart", "observedAtMs": observed,
            "deadlineMs": observed + duration, "generation": 1, "legacy": legacy,
        })
    return {"version": 2, "records": records, "recentFingerprints": ["before-restart"]}


async def boot(monkeypatch, *, saved=None, pair=False, mixed_import=False):
    clock = {"now": runtime_fixture._NOW_MS}
    monkeypatch.setattr(time, "time", lambda: clock["now"] / 1000)
    monkeypatch.setattr(
        importlib.import_module("custom_components.hausman_hub.application.room_lighting_ha_executor"),
        "_new_service_context",
        lambda: SimpleNamespace(id="restart-test-auto"),
    )
    store = authority_fixture._Store()
    store.payload = copy.deepcopy(saved if saved is not None else payload())
    authority = ManualLightAuthority(store)
    hass = runtime_fixture._FakeHass()
    room_payload = runtime_fixture._config_payload(
        commands_enabled=True, power_switch_entity="switch.demo_power" if pair else None,
    )
    ownership_store = runtime_fixture._MemoryOwnershipStore()
    if mixed_import:
        alternate = copy.deepcopy(room_payload["devices"]["light_targets"][0])
        alternate.update(id="light_alternative", entityId="light.alternative", role="other")
        room_payload["devices"]["light_targets"].append(alternate)
        journal = runtime_fixture.RoomLightingOwnershipJournal()
        journal.record_manual(runtime_fixture._ROOM_ID, "light_main", clock["now"] - 7_200_000,
                              confirmed=True, turned_off=True)
        journal.record_manual(runtime_fixture._ROOM_ID, "light_alternative", clock["now"] - 7_200_000,
                              confirmed=True)
        ownership_store.payload = journal.to_payload()
    runtime = runtime_fixture._make_runtime(
        hass, payload=room_payload, manual_light_authority=authority,
        now_ms=lambda: clock["now"], ownership_store=ownership_store,
    )
    listener = ManualLightAuthorityEvents(
        hass, authority, lambda: authority_fixture.room_light_entity_ids(runtime.configs()),
        None, lambda: runtime._executor,
    )
    listener.start(buffer_startup=True)
    await authority.async_load()
    stamp = runtime_fixture._dt(clock["now"])
    hass.states.set(ENTITY, "unavailable" if pair else "off", last_changed=stamp)
    hass.states.set(SENSOR, "off", last_changed=stamp)
    if mixed_import:
        hass.states.set("light.alternative", "off", last_changed=stamp)
    if pair:
        hass.states.set("switch.demo_power", "off", last_changed=stamp)
    await runtime.start(hass, "restart-test")
    await listener.async_complete_startup()
    config = runtime.configs()[0]
    assert authority.barrier_reason is None
    assert authority.barrier_reason_for(ENTITY) == "restart_uncertain"
    assert not hass.services.calls
    return authority, store, runtime, hass, config, listener, clock


async def close(runtime, listener, hass):
    listener.cancel()
    await runtime.stop()
    await asyncio.sleep(0)
    await asyncio.gather(*hass.tasks, return_exceptions=True)


@pytest.mark.parametrize("saved_kind", ["empty", "expired_off"])
@pytest.mark.parametrize("pair", [False, True])
async def test_existing_store_full_startup_recovers_without_new_manual_gesture(
    monkeypatch, saved_kind, pair,
):
    saved = (payload("off", observed=runtime_fixture._NOW_MS - 7_200_000)
             if saved_kind == "expired_off" else payload())
    authority, store, runtime, hass, config, listener, clock = await boot(
        monkeypatch, saved=saved, pair=pair,
    )
    try:
        before = copy.deepcopy(store.payload)
        clock["now"] += 29_999
        await runtime._build_context(config)
        assert not await runtime._reconcile_manual_restart(config)
        clock["now"] += 1
        await runtime._build_context(config)
        assert await runtime._reconcile_manual_restart(config)
        assert not authority.blocks(ENTITY)
        assert authority.blocks("light.unobserved_other_room")
        assert store.payload == before  # no invented manual record or deadline
        assert not hass.services.calls  # reconciliation itself never commands
        if not pair:
            hass.states.set(SENSOR, "on", last_changed=runtime_fixture._dt(clock["now"]))
            runtime._state_event(SimpleNamespace(data={"entity_id": SENSOR}))
            await asyncio.gather(*hass.tasks)
            assert any(call[1] == "turn_on" for call in hass.services.calls)
            assert hass.states.get(ENTITY).state == "on"
        else:
            # The real power executor now accepts the same protected scope.
            receipt = await runtime._executor.async_power_on(
                hass, "switch.demo_power", protected_entities=(ENTITY,),
            )
            assert receipt["sent"] and len(hass.services.calls) == 1
    finally:
        await close(runtime, listener, hass)


@pytest.mark.parametrize("case", [
    "active_off", "long_deadline", "legacy", "unknown_record", "on", "unavailable",
    "unknown", "cached", "restored", "stale", "before_subscription", "future",
    "sensor_unknown", "sensor_stale", "changed_config", "selector", "storage_health",
])
async def test_recovery_retains_every_unsafe_condition(monkeypatch, case):
    now = runtime_fixture._NOW_MS
    saved = payload()
    if case in {"active_off", "long_deadline", "legacy", "unknown_record"}:
        saved = payload(
            "unknown" if case in {"legacy", "unknown_record"} else "off",
            observed=now - 7_200_000 if case != "active_off" else now - 1000,
            duration=10_800_000 if case == "long_deadline" else 3_600_000,
            legacy=case == "legacy",
        )
    authority, _, runtime, hass, config, listener, clock = await boot(monkeypatch, saved=saved)
    try:
        clock["now"] += 30_000
        if case in {"on", "unavailable", "unknown"}:
            hass.states.set(ENTITY, case, last_changed=runtime_fixture._dt(clock["now"]))
        elif case in {"cached", "restored"}:
            hass.states.set(ENTITY, "off", {case: True}, runtime_fixture._dt(clock["now"]))
        elif case in {"stale", "before_subscription", "future"}:
            offset = {"stale": -900_000, "before_subscription": -1, "future": 900_000}[case]
            hass.states.set(ENTITY, "off", last_changed=runtime_fixture._dt(now + offset))
        elif case in {"sensor_unknown", "sensor_stale"}:
            hass.states.set(SENSOR, "unknown" if case == "sensor_unknown" else "off",
                            last_changed=runtime_fixture._dt(now - 900_000))
        elif case == "changed_config":
            runtime._configs[config.room_id] = replace(config, version=config.version + 1)
            runtime._rebuild_index()
        elif case == "selector":
            authority.mark_selector_uncertain("light")
        elif case == "storage_health":
            authority.mark_unhealthy()
        await runtime._build_context(config)
        assert not await runtime._reconcile_manual_restart(config)
        assert authority.blocks(ENTITY)
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


@pytest.mark.parametrize("race", ["manual_on", "manual_off", "graph", "config", "state", "failure", "cancel"])
async def test_recovery_save_is_a_fenced_transition(monkeypatch, race):
    authority, store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        clock["now"] += 30_000
        await runtime._build_context(config)
        original = store.async_save

        async def save(value):
            if race.startswith("manual_"):
                authority.capture(ENTITY, race.removeprefix("manual_"), "service", "new-action")
            elif race == "graph":
                authority.set_related_sources(RelatedLightSources(False, {}))
            elif race == "config":
                runtime._configs[config.room_id] = replace(config, version=2)
                runtime._rebuild_index()
            elif race == "state":
                hass.states.set(ENTITY, "on", last_changed=runtime_fixture._dt(clock["now"]))
            elif race == "failure":
                raise OSError("store unavailable")
            elif race == "cancel":
                raise asyncio.CancelledError()
            await original(value)

        store.async_save = save
        if race in {"failure", "cancel"}:
            with pytest.raises(OSError if race == "failure" else asyncio.CancelledError):
                await runtime._reconcile_manual_restart(config)
            assert authority.barrier_reason == "storage_unavailable"
        else:
            assert not await runtime._reconcile_manual_restart(config)
        assert authority.blocks(ENTITY)
        assert not hass.services.calls
        store.async_save = original
        await authority.async_drain()
        if race.startswith("manual_"):
            assert store.payload["records"][0]["fingerprint"] == "new-action"
    finally:
        await close(runtime, listener, hass)


async def test_new_gesture_keeps_original_time_and_duplicate_does_not_extend(monkeypatch):
    authority, store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        event = SimpleNamespace(
            data={"domain": "light", "service": "turn_off", "service_data": {"entity_id": ENTITY}},
            context=SimpleNamespace(id="new-manual-off"), time_fired="stable-event-id",
        )
        listener.service_event(event)
        await authority.async_drain()
        deadline = authority.record(ENTITY).deadline_ms
        clock["now"] += 3_599_999
        listener.service_event(event)
        await authority.async_drain()
        assert authority.record(ENTITY).deadline_ms == deadline
        hass.states.set(ENTITY, "off", last_changed=runtime_fixture._dt(clock["now"]))
        hass.states.set(SENSOR, "off", last_changed=runtime_fixture._dt(clock["now"] - 30_000))
        await runtime._build_context(config)
        assert not await runtime._reconcile_manual_restart(config)
        clock["now"] += 1
        assert await runtime._reconcile_manual_restart(config)
        assert authority.record(ENTITY).deadline_ms == deadline
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


@pytest.mark.parametrize("returned_at,recovered", [(29_000, False), (29_999, False), (30_000, True)])
async def test_restart_absence_uses_live_return_moment(monkeypatch, returned_at, recovered):
    authority, store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        start = clock["now"]
        clock["now"] += 30_000
        await runtime._build_context(config)
        hass.states.set(SENSOR, "on", last_changed=runtime_fixture._dt(start + returned_at))
        assert await runtime._reconcile_manual_restart(config) is recovered
        assert authority.blocks(ENTITY) is not recovered
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_restart_new_off_starts_a_new_absence_window(monkeypatch):
    authority, store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        start = clock["now"]
        clock["now"] += 30_000
        await runtime._build_context(config)
        hass.states.set(SENSOR, "off", last_changed=runtime_fixture._dt(start + 29_000))
        assert not await runtime._reconcile_manual_restart(config)
        assert authority.blocks(ENTITY)
        clock["now"] = start + 59_000
        await runtime._build_context(config)
        assert await runtime._reconcile_manual_restart(config)
        assert not authority.blocks(ENTITY)
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


@pytest.mark.parametrize("value", ["on", "off", "unknown"])
@pytest.mark.parametrize("second_sensor", [False, True])
async def test_restart_fences_all_sensor_changes_during_save(monkeypatch, value, second_sensor):
    authority, store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        entity = SENSOR
        if second_sensor:
            sensor = next(item for item in config.devices.sensors if item.entity_id == SENSOR)
            entity = "binary_sensor.other_presence"
            config = replace(config, version=config.version + 1,
                             devices=replace(config.devices, sensors=(*config.devices.sensors,
                                 replace(sensor, id="other_presence", entity_id=entity))))
            runtime._configs[config.room_id] = config
            runtime._rebuild_index()
            hass.states.set(entity, "off", last_changed=runtime_fixture._dt(clock["now"]))
        start = clock["now"]
        clock["now"] += 30_000
        await runtime._build_context(config)
        original = store.async_save

        async def save(payload):
            hass.states.set(entity, value, last_changed=runtime_fixture._dt(start + 29_000))
            await original(payload)

        store.async_save = save
        assert not await runtime._reconcile_manual_restart(config)
        assert authority.blocks(ENTITY)
        assert not hass.services.calls
        store.async_save = original
        await runtime._build_context(config)
        assert not await runtime._reconcile_manual_restart(config)
        assert authority.blocks(ENTITY)
    finally:
        await close(runtime, listener, hass)


async def test_restart_changed_valid_return_retries_on_next_normal_pass(monkeypatch):
    authority, store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        clock["now"] += 30_000
        await runtime._build_context(config)
        original = store.async_save

        async def save(payload):
            hass.states.set(SENSOR, "on", last_changed=runtime_fixture._dt(clock["now"]))
            await original(payload)

        store.async_save = save
        assert not await runtime._reconcile_manual_restart(config)
        assert authority.blocks(ENTITY)
        store.async_save = original
        await runtime._build_context(config)
        assert await runtime._reconcile_manual_restart(config)
        assert not authority.blocks(ENTITY)
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_explicit_returns_do_not_bypass_unresolved_restart(monkeypatch):
    authority, _, runtime, hass, config, listener, _ = await boot(
        monkeypatch, saved=payload("on", observed=runtime_fixture._NOW_MS - 1000),
    )
    try:
        assert await authority.async_begin_explicit_return((ENTITY,)) is None
        await authority.async_release_manual_claims_through(authority.generation)
        assert authority.blocks(ENTITY)
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_recovered_room_and_uncertain_neighbour_have_consistent_projection(monkeypatch):
    authority, _, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        clock["now"] += 30_000
        await runtime._build_context(config)
        assert await runtime._reconcile_manual_restart(config)
        other_entity = "light.other_room"
        other = replace(
            config, room_id="other_room", schedule=(), devices=replace(
                config.devices, light_targets=(replace(
                    config.devices.light_targets[0], id="other_light", entity_id=other_entity,
                ),),
            ),
        )
        runtime._configs[other.room_id] = other
        runtime._rebuild_index()
        assert not authority.unhealthy
        assert not authority.blocks(ENTITY)
        assert authority.blocks(other_entity)
        holds = runtime.core_manual_holds()
        assert ENTITY not in holds and other_entity in holds
        assert not await runtime._reconcile_manual_restart(other)
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_buffered_manual_action_keeps_observed_time_not_load_completion(monkeypatch):
    clock = {"now": runtime_fixture._NOW_MS}
    monkeypatch.setattr(time, "time", lambda: clock["now"] / 1000)
    store = authority_fixture._Store()
    store.payload = payload()
    authority = ManualLightAuthority(store)
    hass = runtime_fixture._FakeHass()
    listener = ManualLightAuthorityEvents(hass, authority, lambda: {ENTITY}, None, lambda: None)
    listener.start(buffer_startup=True)
    event = SimpleNamespace(
        data={"domain": "light", "service": "turn_off", "service_data": {"entity_id": ENTITY}},
        context=SimpleNamespace(id="during-load"), time_fired="stable-id",
    )
    listener.service_event(event)
    clock["now"] += 30_000
    await authority.async_load()
    await listener.async_complete_startup()
    record = authority.record(ENTITY)
    assert record.observed_at_ms == runtime_fixture._NOW_MS
    assert record.deadline_ms == runtime_fixture._NOW_MS + 3_600_000
    listener.service_event(event)
    await authority.async_drain()
    assert authority.record(ENTITY) == record
    listener.cancel()
    await asyncio.sleep(0)
    await asyncio.gather(*hass.tasks)


@pytest.mark.parametrize("action", ["on", "off"])
async def test_second_restart_never_reuses_prior_recovery(monkeypatch, action):
    authority, store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        clock["now"] += 30_000
        await runtime._build_context(config)
        assert await runtime._reconcile_manual_restart(config)
        await authority.async_capture(ENTITY, action, "service", "last-action")
        original_deadline = authority.record(ENTITY).deadline_ms
        restarted = ManualLightAuthority(store)
        restarted.begin_startup_observation()
        await restarted.async_load()
        restarted.set_related_sources(authority._related_sources)
        restarted.register_recovery_scope((ENTITY,), config.version)
        restarted.finish_startup_observation((ENTITY,))
        assert restarted.blocks(ENTITY)
        assert restarted.record(ENTITY).deadline_ms == original_deadline
        clock["now"] += 7_200_000
        assert restarted.blocks(ENTITY)  # time is not a fresh OFF/absence proof
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_mixed_legacy_import_preserves_expired_proof_across_two_full_starts(monkeypatch):
    saved = payload("off", observed=runtime_fixture._NOW_MS - 7_200_000)
    original = copy.deepcopy(saved["records"][0])
    for _ in range(2):
        authority, store, runtime, hass, _, listener, clock = await boot(
            monkeypatch, saved=saved, mixed_import=True,
        )
        try:
            assert authority.barrier_reason is None
            assert authority.has_durable_import((ENTITY, "light.alternative"))
            records = {record["entityId"]: record for record in store.payload["records"]}
            assert records[ENTITY] == original
            assert not authority._priority_active(authority.record(ENTITY), clock["now"])
            assert records["light.alternative"]["legacy"] is True
            assert authority.blocks("light.alternative")
            saved = copy.deepcopy(store.payload)
        finally:
            await close(runtime, listener, hass)
