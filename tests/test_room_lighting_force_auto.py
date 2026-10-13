"""Explicit return uses the real shared authority; no physical house calls."""

import copy
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
from custom_components.hausman_hub.application.manual_light_off_protection import ManualLightOffProtectionCoordinator
from custom_components.hausman_hub.application.scenario_light_priority import LightAutomationPriority
from tests.test_room_lighting_runtime import (
    _FakeHass, _MemoryOwnershipStore, _NOW_DT, _NOW_MS, _ROOM_ID,
    _SpyExecutor, _make_runtime, _now_ms,
)


class Store(_MemoryOwnershipStore):
    recovered_previous = False
    hook = None
    prepare_hook = None

    async def async_save(self, payload):
        if self.hook is not None:
            await self.hook(payload)
        await super().async_save(copy.deepcopy(payload))

    async def async_save_if(self, payload, is_current):
        if self.prepare_hook is not None:
            await self.prepare_hook(payload)
        return await super().async_save_if(payload, is_current)


async def seed_participants(hass):
    off_protection = ManualLightOffProtectionCoordinator(Store())
    await off_protection.async_load()
    hass.data = {"hausman_hub": {"manual_light_off_protection": off_protection}}
    priority = LightAutomationPriority(Store())
    await priority.async_load()
    return priority, off_protection


async def assert_pending_survives_startup(authority, store):
    from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
    scope = frozenset({"light.demo_main"})
    assert authority.blocks("light.demo_main")
    before = copy.deepcopy(store.payload["returnOperation"])
    authority.begin_startup_observation()
    authority.set_related_sources(RelatedLightSources(True, {"light.demo_main": scope}))
    authority.register_recovery_scope(scope, 1)
    authority.finish_startup_observation(scope)
    assert authority.barrier_reason == "return_pending"
    now = _now_ms(60_000)
    assert not await authority.async_reconcile_restart(
        scope, version=1, observe_off=lambda: {"light.demo_main": now}, now_ms=lambda: now,
    )
    assert store.payload["returnOperation"] == before


async def make_case(action="off", *, protected=False):
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on" if action == "on" else "off", last_changed=_NOW_DT)
    store, ownership_store = Store(), Store()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    priority, off = await seed_participants(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, ownership_store=ownership_store,
                            manual_light_authority=authority, light_priority=priority,
                            executor=executor)
    await runtime.start(hass, "entry")
    if action:
        runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS, turned_off=action == "off")
        if action == "legacy":
            authority.capture_legacy("light.demo_main", source="room")
            await authority.async_drain()
        else:
            await authority.async_capture("light.demo_main", action, "service", "old", observed_at_ms=_now_ms())
    if protected:
        from tests.test_manual_light_off_protection import _settings
        settings = _settings()
        settings["profiles"][0].update(roomId=_ROOM_ID, lightIds=["light.demo_main"], presenceSensorIds=[])
        await off.async_replace_settings("settings", 0, settings)
        await off.async_note_state_transition("light.demo_main", SimpleNamespace(state="on"),
                                             SimpleNamespace(state="off"), None)
        priority._manual_records["light.demo_main"] = {
            "entityId": "light.demo_main", "targetId": "light_main", "ownership": "manual",
            "evidenceRevision": None, "confirmedAt": _NOW_MS,
        }
        await priority._store.async_save(priority._payload())
    return SimpleNamespace(hass=hass, runtime=runtime, authority=authority, store=store,
                           ownership_store=ownership_store, priority=priority, off=off, executor=executor)


@pytest.mark.parametrize("action", [None, "legacy", "on", "off"])
async def test_return_coordinates_all_stores_and_preserves_other_room(action):
    case = await make_case(action, protected=True)
    try:
        await case.authority.async_capture("light.other", "on", "service", "other")
        before_other = case.authority.record("light.other")
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        assert result["outcome"] == "returned", result
        assert case.store.payload["version"] == 2
        assert case.off.snapshot()["protections"] == []
        assert not case.priority.manual_entity_ids()
        assert case.authority.record("light.other") is before_other
        assert case.authority.blocks("light.other")
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


async def test_force_auto_off_reaches_ordinary_dispatch_when_conditions_allow():
    case = await make_case("off", protected=True)
    try:
        case.runtime._configs[_ROOM_ID] = replace(case.runtime._configs[_ROOM_ID], commands_enabled=True)
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        assert result["outcome"] == "returned", result
        assert case.executor.calls
        assert all(command.action.value == "turn_on" for command in case.executor.calls)
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("participant", ["authority", "ownership", "priority", "off", "commit"])
@pytest.mark.parametrize("action", ["on", "off"])
async def test_new_manual_during_each_store_keeps_durable_pending(participant, action):
    case = await make_case("off", protected=True)
    try:
        fired = False

        async def intervene(_payload):
            nonlocal fired
            if fired:
                return
            fired = True
            case.authority.capture("light.demo_main", action, "service", "new", observed_at_ms=_now_ms())
            case.runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS, turned_off=action == "off")
            await asyncio.sleep(0)

        stores = {"authority": case.store, "ownership": case.ownership_store,
                  "priority": case.priority._store, "off": case.off._store, "commit": case.store}
        setattr(stores[participant], "prepare_hook" if participant == "commit" else "hook", intervene)
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        assert fired
        assert result["outcome"] == "incomplete", result
        assert case.store.payload["version"] == 3
        assert case.authority.record("light.demo_main").fingerprint == "new"
        restored = ManualLightAuthority(case.store)
        await restored.async_load()
        await assert_pending_survives_startup(restored, case.store)
        assert restored.blocks("light.demo_main")
        assert not case.executor.calls
        previous_id = case.store.payload["returnOperation"]["operationId"]
        await case.authority.async_drain()
        # A distinct later click is a new intent, not automatic recovery.
        second = await case.runtime.async_force_auto(_ROOM_ID, reason="new explicit click")
        assert second["outcome"] == "returned", second
        assert second["operationId"] > previous_id
        assert not case.authority.blocks("light.demo_main")
        assert not case.executor.calls
    finally:
        case.ownership_store.hook = None
        await case.runtime.stop()


@pytest.mark.parametrize("participant", ["authority", "ownership", "priority", "off", "commit"])
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_storage_failure_or_cancel_never_reports_success(participant, failure):
    case = await make_case("off", protected=True)
    try:
        async def fail(_payload):
            raise asyncio.CancelledError() if failure == "cancel" else OSError("test failure")

        stores = {"authority": case.store, "ownership": case.ownership_store,
                  "priority": case.priority._store, "off": case.off._store, "commit": case.store}
        setattr(stores[participant], "prepare_hook" if participant == "commit" else "hook", fail)
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        else:
            result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
            assert result["outcome"] == "incomplete", result
        assert case.authority.blocks("light.demo_main")
        restored = ManualLightAuthority(case.store)
        await restored.async_load()
        assert restored.blocks("light.demo_main")
        if participant != "authority":
            await assert_pending_survives_startup(restored, case.store)
        retry = await case.runtime.async_force_auto(_ROOM_ID, reason="new explicit click")
        assert retry["outcome"] == "blocked", retry
        assert retry["blockers"] == ["storage_unavailable"]
        assert not case.executor.calls
    finally:
        case.ownership_store.hook = None
        await case.runtime.stop()


@pytest.mark.parametrize("change", ["selector", "graph", "version", "sensor", "priority", "off_health"])
async def test_commit_rechecks_independent_guards(change):
    case = await make_case("off", protected=True)
    try:
        async def change_guard(_payload):
            if change == "selector":
                case.authority.mark_selector_uncertain("light")
            elif change == "graph":
                case.authority.invalidate_related_sources()
            elif change == "version":
                case.runtime._configs[_ROOM_ID] = replace(case.runtime._configs[_ROOM_ID], version=2)
            elif change == "sensor":
                case.hass.states.set("binary_sensor.demo_presence", "unknown", last_changed=_NOW_DT)
            elif change == "priority":
                case.priority._manual_records["light.demo_main"] = {"entityId": "light.demo_main", "ownership": "manual"}
            else:
                case.off.mark_unhealthy()

        case.store.prepare_hook = change_guard
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        assert result["outcome"] == "incomplete", result
        assert case.store.payload["version"] == 3
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("guard", ["forbidden", "shared", "unhealthy", "sensor", "reserved"])
async def test_preflight_guards_do_not_partially_release(guard):
    case = await make_case("off", protected=True)
    try:
        record = next(iter(case.off._protections.values()))
        if guard == "forbidden":
            record["effectivePolicy"]["allowManualRelease"] = False
        elif guard == "shared":
            record["lightIds"].append("light.other")
        elif guard == "unhealthy":
            case.authority.mark_unhealthy()
        elif guard == "sensor":
            case.hass.states.set("binary_sensor.demo_presence", "unknown", last_changed=_NOW_DT)
        else:
            case.runtime._reserved_target_ids = frozenset({"light_main"})
        before = copy.deepcopy(case.store.payload)
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        assert result["outcome"] == "blocked", result
        assert case.store.payload == before
        assert case.runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == _NOW_MS
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("action", ["on", "off"])
async def test_explicit_return_after_real_startup_requires_new_observations(monkeypatch, action):
    from tests.test_manual_light_restart import boot, close, payload
    authority, store, runtime, hass, config, listener, clock = await boot(
        monkeypatch, saved=payload(action, observed=_NOW_MS - 10_000),
    )
    try:
        priority, _off = await seed_participants(hass)
        runtime._light_priority = priority
        # A still-recent but pre-subscription report cannot close the gap.
        from tests.test_room_lighting_runtime import _dt
        hass.states.set("light.demo_main", action, last_changed=_dt(clock["now"] - 1))
        blocked = await runtime.async_force_auto(_ROOM_ID, reason="test")
        assert blocked["outcome"] == "blocked", blocked
        hass.states.set("light.demo_main", action, last_changed=_dt(clock["now"]))
        runtime._configs[_ROOM_ID] = replace(config, commands_enabled=False)
        result = await runtime.async_force_auto(_ROOM_ID, reason="test")
        assert result["outcome"] == "returned", result
        assert not authority.blocks("light.demo_main")
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_no_manual_hold_preserves_proven_auto_history():
    case = await make_case(None)
    try:
        old = case.runtime._ownership.record_auto(_ROOM_ID, "light_main", _NOW_MS)
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        assert result["outcome"] == "returned", result
        assert old in case.runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})
    finally:
        await case.runtime.stop()


async def interrupt_return(case, fingerprint="interrupted"):
    async def interrupt(_payload):
        case.store.prepare_hook = None
        case.authority.capture("light.demo_main", "on", "service", fingerprint, observed_at_ms=_now_ms())
    case.store.prepare_hook = interrupt
    result = await case.runtime.async_force_auto(_ROOM_ID, reason="first click")
    assert result["outcome"] == "incomplete", result
    await case.authority.async_drain()
    return case.store.payload["returnOperation"]["operationId"]


async def test_new_manual_during_second_intent_still_wins():
    case = await make_case("off", protected=True)
    try:
        first_id = await interrupt_return(case, "first-interruption")
        second_id = await interrupt_return(case, "second-interruption")
        assert second_id > first_id
        assert case.authority.record("light.demo_main").fingerprint == "second-interruption"
        assert case.authority.barrier_reason == "return_pending"
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("scope", [(), ("light.demo_main", "light.other"), ("light.other",)])
async def test_new_intent_cannot_replace_a_different_pending_scope(scope):
    case = await make_case("off", protected=True)
    try:
        await interrupt_return(case)
        before = copy.deepcopy(case.store.payload)
        assert await case.authority.async_begin_explicit_return(
            scope, all_manual=True, config_version=1, observations_current=lambda: True,
        ) is None
        assert case.store.payload == before
        assert case.authority.barrier_reason == "return_pending"
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("guard", ["version", "selector", "recovery"])
async def test_new_intent_keeps_independent_guards(guard):
    case = await make_case("off", protected=True)
    try:
        await interrupt_return(case)
        before = copy.deepcopy(case.store.payload)
        if guard == "version":
            case.runtime._configs[_ROOM_ID] = replace(case.runtime._configs[_ROOM_ID], version=2)
        elif guard == "selector":
            case.authority.mark_selector_uncertain("light")
        else:
            case.authority._recovery = True
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="new explicit click")
        assert result["outcome"] == "blocked", result
        assert case.store.payload == before
        assert case.authority.blocks("light.demo_main")
    finally:
        await case.runtime.stop()


async def test_replacement_save_failure_keeps_prior_durable_pending():
    case = await make_case("off", protected=True)
    try:
        await interrupt_return(case)
        before = copy.deepcopy(case.store.payload)
        async def fail(_payload):
            raise OSError("cannot replace pending")
        case.store.hook = fail
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="new explicit click")
        assert result["outcome"] == "incomplete", result
        assert case.store.payload == before
        assert case.authority.blocks("light.demo_main")
    finally:
        await case.runtime.stop()


async def test_parallel_button_is_rejected_instead_of_queued():
    case = await make_case("off", protected=True)
    started, release = asyncio.Event(), asyncio.Event()
    async def hold(_payload):
        started.set()
        await release.wait()
    case.store.hook = hold
    first = asyncio.create_task(case.runtime.async_force_auto(_ROOM_ID, reason="first"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        second = await asyncio.wait_for(case.runtime.async_force_auto(_ROOM_ID, reason="parallel"), timeout=2)
        assert second["outcome"] == "blocked"
        assert second["blockers"] == ["return_busy"]
        release.set()
        assert (await first)["outcome"] == "returned"
        assert not case.executor.calls
    finally:
        release.set()
        await first
        await case.runtime.stop()


@pytest.mark.parametrize("lock_kind", ["room", "runtime"])
async def test_manual_after_click_while_waiting_for_writer_is_not_consumed(lock_kind):
    case = await make_case("off", protected=True)
    lock = case.runtime._room_lock(_ROOM_ID) if lock_kind == "room" else case.runtime._lock
    await lock.acquire()
    task = asyncio.create_task(case.runtime.async_force_auto(_ROOM_ID, reason="click before new action"))
    try:
        await asyncio.sleep(0)
        await case.authority.async_capture("light.demo_main", "on", "service", "after-click", observed_at_ms=_now_ms())
        lock.release()
        result = await task
        assert result["outcome"] == "blocked", result
        assert case.authority.record("light.demo_main").fingerprint == "after-click"
        assert case.store.payload["version"] == 2
        assert not case.executor.calls
    finally:
        if lock.locked():
            lock.release()
        await task
        await case.runtime.stop()


async def test_new_explicit_intent_after_healthy_pending_restart(monkeypatch):
    import time
    monkeypatch.setattr(time, "time", lambda: _NOW_MS / 1000)
    case = await make_case("off", protected=True)
    try:
        previous_id = await interrupt_return(case)
        await case.runtime.stop()
        restarted = ManualLightAuthority(case.store)
        await restarted.async_load()
        await assert_pending_survives_startup(restarted, case.store)
        runtime = _make_runtime(case.hass, ownership_store=case.ownership_store,
            manual_light_authority=restarted, light_priority=case.priority, executor=case.executor)
        await runtime.start(case.hass, "restart-entry")
        try:
            result = await runtime.async_force_auto(_ROOM_ID, reason="new click after restart")
            assert result["outcome"] == "returned", result
            assert result["operationId"] > previous_id
            assert not restarted.blocks("light.demo_main")
            assert not case.executor.calls
        finally:
            await runtime.stop()
    finally:
        await case.runtime.stop()


async def test_rejected_replacement_after_writer_wait_keeps_old_pending():
    case = await make_case("off", protected=True)
    try:
        await interrupt_return(case)
        before = copy.deepcopy(case.store.payload)
        await case.authority._writer.acquire()
        task = asyncio.create_task(case.authority.async_begin_explicit_return(
            ("light.demo_main",), all_manual=True, config_version=1, observations_current=lambda: True,
        ))
        await asyncio.sleep(0)
        case.authority.invalidate_related_sources()
        case.authority._writer.release()
        assert await task is None
        assert case.store.payload == before
        assert case.authority.barrier_reason == "return_pending"
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("participant", ["ownership", "priority", "off", "sensor"])
async def test_every_intent_snapshot_precedes_runtime_writer_wait(participant):
    case = await make_case("off", protected=True)
    await case.runtime._lock.acquire()
    task = asyncio.create_task(case.runtime.async_force_auto(_ROOM_ID, reason="before change"))
    try:
        await asyncio.sleep(0)
        if participant == "ownership":
            case.runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS, turned_off=False)
        elif participant == "priority":
            old = case.priority._manual_records["light.demo_main"]
            case.priority._manual_records["light.demo_main"] = {**old, "confirmedAt": _NOW_MS + 1}
        elif participant == "off":
            key, old = next(iter(case.off._protections.items()))
            case.off._protections[key] = {**old, "revision": old["revision"] + 1}
        else:
            case.hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
        case.runtime._lock.release()
        result = await task
        assert result["outcome"] == "blocked", result
        assert case.store.payload["version"] == 2
        assert case.authority.blocks("light.demo_main")
        assert not case.executor.calls
    finally:
        if case.runtime._lock.locked():
            case.runtime._lock.release()
        await task
        await case.runtime.stop()


@pytest.mark.parametrize("action", ["on", "off"])
async def test_force_auto_releases_real_authority_and_room_history(action):
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", action, last_changed=_NOW_DT)
    store = Store()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    ownership_store = Store()
    priority, off_protection = await seed_participants(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, ownership_store=ownership_store, manual_light_authority=authority,
        executor=executor,
        light_priority=priority,
    )
    await runtime.start(hass, "entry")
    try:
        runtime._ownership.record_manual(
            _ROOM_ID, "light_main", _NOW_MS, turned_off=action == "off",
        )
        await authority.async_capture(
            "light.demo_main", action, "service", "before-return",
            observed_at_ms=_now_ms(),
        )
        result = await runtime.async_force_auto(_ROOM_ID, reason="test")
        assert authority.record("light.demo_main") is None, result
        assert not authority.blocks("light.demo_main"), result
        assert not runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})
        assert runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) is None
        assert executor.calls == []  # commandsEnabled=False remains a separate gate.
        assert result["outcome"] == "returned"
    finally:
        await runtime.stop()
