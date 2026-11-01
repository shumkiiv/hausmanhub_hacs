"""Real manual-return participants, with no network, devices or real waits."""

import asyncio
import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application import manual_light_authority
from custom_components.hausman_hub.application.lighting_return_intent import LightingReturnIntents
from custom_components.hausman_hub.manual_light_authority_events import ManualLightAuthorityEvents
from tests.test_room_lighting_force_auto import make_case
from tests.test_room_lighting_runtime import _ROOM_ID, _NOW_DT, _NOW_MS

ENTITY = "light.demo_main"
ACTOR = "fixture-admin"


@pytest.fixture(autouse=True)
def authority_clock(monkeypatch):
    monkeypatch.setattr(manual_light_authority.time, "time", lambda: _NOW_MS / 1000)


async def case_with_intent(*, protected=True):
    case = await make_case("off", protected=protected)
    case.clock = [100.0]
    case.runtime._return_intents.clock = lambda: case.clock[0]
    issued = case.runtime.issue_force_auto_intent(_ROOM_ID, actor_id=ACTOR)
    assert issued is not None
    case.intent = issued["intentToken"]
    return case


async def guarded(case, **kwargs):
    return await case.runtime.async_force_auto_guarded(
        kwargs.get("room", _ROOM_ID), actor_id=kwargs.get("actor", ACTOR),
        intent_token=kwargs.get("token", case.intent), reason="test",
    )


def new_manual(case, action="off"):
    # Use the real service adapter for ON/OFF, brightness and colour alike.
    listener = ManualLightAuthorityEvents(case.hass, case.authority,
        lambda: {ENTITY}, None, lambda: None)
    parameters = {"entity_id": ENTITY}
    if action == "brightness":
        parameters["brightness"] = 51
    elif action == "color":
        parameters["color_temp_kelvin"] = 3200
    listener.service_event(SimpleNamespace(
        data={"domain": "light", "service": "turn_off" if action == "off" else "turn_on",
              "service_data": parameters}, context=SimpleNamespace(id="new-" + action),
        time_fired="new-" + action,
    ))
    case.runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS,
                                          turned_off=action == "off")
    record = case.authority.record(ENTITY)
    assert record is not None and record.source == "service"
    assert "new-" + action in record.fingerprint
    return record


async def drain_events(case):
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await case.authority.async_drain()
    if case.hass.tasks:
        await asyncio.gather(*case.hass.tasks)


def saves(case):
    return tuple(store.saves for store in (
        case.store, case.ownership_store, case.priority._store, case.off._store))


@pytest.mark.parametrize("action", ["on", "off", "brightness", "color"])
async def test_new_manual_before_runtime_entry_survives(action):
    case = await case_with_intent()
    try:
        record = new_manual(case, action)
        await drain_events(case)
        before = saves(case)
        result = await guarded(case)
        assert result["outcome"] == "blocked", result
        assert result["released"] == 0 and result["evaluation"] == "not_requested"
        assert case.authority.record(ENTITY) is record
        assert case.authority.blocks(ENTITY)
        assert saves(case) == before
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


async def test_correct_intent_uses_existing_return_and_cannot_be_replayed():
    case = await case_with_intent()
    try:
        before = saves(case)
        # Issuing another intent does not persist or dispatch anything.
        assert case.runtime.issue_force_auto_intent(_ROOM_ID, actor_id=ACTOR)
        assert saves(case) == before and not case.executor.calls
        result = await guarded(case)
        assert result["outcome"] == "returned", result
        assert not case.authority.blocks(ENTITY)
        assert not case.priority.manual_entity_ids()
        assert case.off.snapshot()["protections"] == []
        assert (await guarded(case))["outcome"] == "blocked"
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("change", ["expired", "rollback", "unknown", "missing", "wrong_type",
                                    "wrong_actor", "wrong_room", "config", "service_config",
                                    "dependency", "stopped", "restarted"])
async def test_stale_or_foreign_intent_has_no_participant_writes(change):
    case = await case_with_intent()
    kwargs = {}
    try:
        if change == "expired": case.clock[0] = 400.0
        elif change == "rollback": case.clock[0] = 99.0
        elif change == "unknown": kwargs["token"] = "0" * 64
        elif change == "missing": kwargs["token"] = None
        elif change == "wrong_type": kwargs["token"] = True
        elif change == "wrong_actor": kwargs["actor"] = "other"
        elif change == "wrong_room": kwargs["room"] = "other_room"
        elif change == "config":
            cfg = case.runtime._configs[_ROOM_ID]
            case.runtime._configs[_ROOM_ID] = replace(cfg, version=cfg.version + 1)
        elif change == "service_config":
            cfg = case.runtime._service._configs[_ROOM_ID]
            case.runtime._service._configs[_ROOM_ID] = replace(cfg, version=cfg.version + 1)
        elif change == "dependency": case.runtime._power_dependency_resolver = lambda: {}
        elif change == "stopped": await case.runtime.stop()
        elif change == "restarted":
            await case.runtime.stop()
            await case.runtime.start(case.hass, "entry")
        before = saves(case)
        result = await guarded(case, **kwargs)
        assert result["outcome"] == "blocked", result
        assert saves(case) == before
        assert case.authority.blocks(ENTITY)
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("participant", ["authority", "ownership", "priority", "off", "commit"])
@pytest.mark.parametrize("action", ["on", "off", "brightness", "color"])
async def test_new_manual_at_each_persistence_boundary_is_kept(participant, action):
    case = await case_with_intent()
    fired = []
    stores = {"authority": case.store, "ownership": case.ownership_store,
              "priority": case.priority._store, "off": case.off._store, "commit": case.store}
    store = stores[participant]
    attr = "prepare_hook" if participant == "commit" else "hook"

    async def intervene(_payload):
        if not fired:
            fired.append(new_manual(case, action))
            await asyncio.sleep(0)

    setattr(store, attr, intervene)
    try:
        result = await guarded(case)
        assert fired, participant
        assert result["outcome"] != "returned", result
        assert result["evaluation"] == "not_requested"
        assert case.authority.record(ENTITY) is fired[0]
        assert case.authority.blocks(ENTITY)
        assert case.authority.barrier_reason is not None
        assert not case.executor.calls
        setattr(store, attr, None)
        await drain_events(case)
        assert any(row["fingerprint"] == fired[0].fingerprint for row in case.store.payload["records"])
        assert case.store.payload["returnOperation"]
        restored = manual_light_authority.ManualLightAuthority(case.store)
        await restored.async_load()
        assert restored.record(ENTITY).fingerprint == fired[0].fingerprint
        assert restored.blocks(ENTITY) and restored.barrier_reason is not None
    finally:
        setattr(store, attr, None)
        await drain_events(case)
        await case.runtime.stop()


@pytest.mark.parametrize("lock_name", ["room", "runtime", "authority", "priority", "off"])
@pytest.mark.parametrize("action", ["on", "off", "brightness", "color"])
async def test_new_manual_while_waiting_for_each_lock_wins(lock_name, action):
    case = await case_with_intent()
    lock = {"room": case.runtime._room_lock(_ROOM_ID), "runtime": case.runtime._lock,
            "authority": case.authority._writer, "priority": case.priority._authority_lock,
            "off": case.off._lock}[lock_name]
    await lock.acquire()
    task = asyncio.create_task(guarded(case))
    try:
        for _ in range(10): await asyncio.sleep(0)
        assert not task.done()
        record = new_manual(case, action)
        lock.release()
        result = await task
        assert result["outcome"] != "returned", result
        assert case.authority.record(ENTITY) is record and case.authority.blocks(ENTITY)
        assert not case.executor.calls
    finally:
        if lock.locked(): lock.release()
        if not task.done(): task.cancel()
        await drain_events(case)
        await case.runtime.stop()


@pytest.mark.parametrize("action", ["on", "off", "brightness", "color"])
async def test_original_intent_is_retained_across_refresh(action):
    case = await case_with_intent()
    case.hass.states.set(ENTITY, "unknown", last_changed=_NOW_DT)
    seen = []

    async def refresh(_entities, current):
        assert current()
        seen.append(new_manual(case, action))
        case.hass.states.set(ENTITY, "on", last_changed=_NOW_DT)
        assert not current()
        return True

    case.runtime._observation_refresher = refresh
    try:
        result = await guarded(case)
        assert len(seen) == 1 and result["outcome"] == "blocked", result
        assert case.authority.record(ENTITY) is seen[0] and case.authority.blocks(ENTITY)
        assert not case.executor.calls
    finally:
        await drain_events(case)
        await case.runtime.stop()


@pytest.mark.parametrize("participant,change", [
    ("priority", "replacement"), ("priority", "mutation"),
    ("off", "replacement"), ("off", "mutation"), ("off", "new_key"),
])
async def test_participant_cannot_remove_new_or_changed_record(participant, change):
    case = await case_with_intent()
    records = case.priority._manual_records if participant == "priority" else case.off._protections
    key = next(iter(records))
    original = records[key]
    store = case.priority._store if participant == "priority" else case.off._store
    seen = []

    async def intervene(_payload):
        if seen: return
        if change == "replacement": records[key] = dict(original)
        elif change == "mutation": records[key]["fixture-new-intent"] = True
        else: records["fixture-new-key"] = dict(original)
        seen.append(dict(records))

    store.hook = intervene
    try:
        result = await guarded(case)
        assert seen and result["outcome"] != "returned", result
        assert records == seen[0]
        assert case.authority.blocks(ENTITY) and not case.executor.calls
        assert case.store.payload["returnOperation"]
    finally:
        store.hook = None
        await case.runtime.stop()


async def test_expiration_in_final_commit_preserves_pending_guard():
    case = await case_with_intent()

    async def expire(_payload): case.clock[0] = 400.0

    case.store.prepare_hook = expire
    try:
        result = await guarded(case)
        assert result["outcome"] != "returned", result
        assert case.authority.blocks(ENTITY) and case.store.payload["returnOperation"]
        assert not case.executor.calls
    finally:
        case.store.prepare_hook = None
        await case.runtime.stop()


async def test_parallel_consumption_cannot_repeat_one_intent():
    case = await case_with_intent()
    try:
        results = await asyncio.gather(guarded(case), guarded(case))
        assert sorted(row["outcome"] for row in results) == ["blocked", "returned"]
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("participant", ["ownership", "priority", "off"])
async def test_real_storage_error_is_not_reclassified_as_only_stale_intent(participant):
    case = await case_with_intent()
    store = {"ownership": case.ownership_store, "priority": case.priority._store,
             "off": case.off._store}[participant]
    records = []

    async def fail(_payload):
        if not records:
            records.append(new_manual(case))
        raise OSError("fixture real storage failure")

    store.hook = fail
    try:
        result = await guarded(case)
        assert result["outcome"] == "incomplete", result
        assert case.authority._storage_failed
        assert case.authority.blocks(ENTITY) and case.store.payload["returnOperation"]
        assert case.authority.record(ENTITY) is records[0]
        assert not case.executor.calls
    finally:
        store.hook = None
        await drain_events(case)
        await case.runtime.stop()


async def test_abort_drain_failure_keeps_storage_unhealthy_and_return_pending():
    case = await case_with_intent()
    records = []

    async def fail(_payload):
        raise OSError("fixture abort drain failure")

    async def interrupt(_payload):
        if not records:
            records.append(new_manual(case))
            case.store.hook = fail

    case.priority._store.hook = interrupt
    try:
        result = await guarded(case)
        assert result["outcome"] == "incomplete", result
        assert case.authority._storage_failed and case.authority.blocks(ENTITY)
        assert not case.authority._active_return_scopes
        assert case.store.payload["returnOperation"]
        assert case.authority.record(ENTITY) is records[0]
        assert not case.executor.calls
    finally:
        case.priority._store.hook = case.store.hook = None
        # The adapter's real background task can observe this same IO error.
        await asyncio.sleep(0)
        if case.hass.tasks:
            await asyncio.gather(*case.hass.tasks, return_exceptions=True)
        await case.runtime.stop()


@pytest.mark.parametrize("lock_name", ["room", "runtime", "authority", "priority", "off"])
async def test_expiration_while_waiting_for_each_lock_cannot_return(lock_name):
    case = await case_with_intent()
    lock = {"room": case.runtime._room_lock(_ROOM_ID), "runtime": case.runtime._lock,
            "authority": case.authority._writer, "priority": case.priority._authority_lock,
            "off": case.off._lock}[lock_name]
    await lock.acquire()
    task = asyncio.create_task(guarded(case))
    try:
        for _ in range(10): await asyncio.sleep(0)
        assert not task.done()
        case.clock[0] = 400.0
        lock.release()
        result = await task
        assert result["outcome"] != "returned", result
        assert case.authority.blocks(ENTITY) and not case.executor.calls
    finally:
        if lock.locked(): lock.release()
        if not task.done(): task.cancel()
        await case.runtime.stop()


def test_bounded_registry_does_not_evict_a_live_intent():
    clock, sequence = [0.0], iter(range(100))
    registry = LightingReturnIntents(lambda: clock[0], lambda: f"{next(sequence):064x}")
    tokens = [registry.issue("room", "actor", 1, lambda: True, lambda: True) for _ in range(64)]
    assert all(tokens)
    assert registry.issue("room", "actor", 1, lambda: True, lambda: True) is None
    assert registry.consume(tokens[0], "room", "other") is None
    assert registry.consume(tokens[0], "room", "actor") is not None
    assert registry.consume(tokens[0], "room", "actor") is None
    assert registry.issue("room", "actor", 1, lambda: True, lambda: True)
    clock[0] = 300.0
    assert registry.issue("room", "actor", 1, lambda: True, lambda: True)


@pytest.mark.parametrize("stage", ["before-entry", "priority-save", "commit"])
@pytest.mark.parametrize("parameters", [{"brightness": 51}, {"color_temp_kelvin": 5000}])
async def test_direct_device_parameter_event_invalidates_original_intent(stage, parameters):
    from tests.test_manual_light_parameter_events import state
    case = await case_with_intent()
    listener = ManualLightAuthorityEvents(case.hass, case.authority, lambda: {ENTITY}, None, lambda: None)
    records = []

    def deliver():
        if records: return
        old = {"brightness": 153, "color_temp_kelvin": 4000}
        listener.state_event(SimpleNamespace(
            data={"entity_id": ENTITY, "old_state": state(old), "new_state": state(dict(old, **parameters))},
            context=SimpleNamespace(id="fixture-physical-parameters")))
        record = case.authority.record(ENTITY)
        assert record is not None and record.fingerprint != "old"
        records.append(record)

    async def intervene(_payload): deliver()

    if stage == "before-entry": deliver()
    elif stage == "priority-save": case.priority._store.hook = intervene
    else: case.store.prepare_hook = intervene
    try:
        result = await guarded(case)
        assert records and result["outcome"] != "returned", result
        assert case.authority.record(ENTITY) is records[0] and case.authority.blocks(ENTITY)
        assert not case.executor.calls
        case.priority._store.hook = case.store.prepare_hook = None
        await drain_events(case)
        restored = manual_light_authority.ManualLightAuthority(case.store)
        await restored.async_load()
        assert restored.record(ENTITY).fingerprint == records[0].fingerprint
        assert restored.blocks(ENTITY)
    finally:
        case.priority._store.hook = case.store.prepare_hook = None
        await drain_events(case)
        await case.runtime.stop()
