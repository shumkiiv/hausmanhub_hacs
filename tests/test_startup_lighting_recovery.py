"""Bounded read-only startup recovery pass for restart-uncertain rooms.

The pass refreshes missing observations through the existing GET-only
refresher. It never releases authority, never dispatches commands and never
touches manual protections.
"""

import asyncio
from types import SimpleNamespace

import pytest
import test_manual_light_authority as authority_fixture
import test_manual_light_restart as restart_fixture
import test_room_lighting_runtime as runtime_fixture

from test_manual_light_restart import boot, close, ENTITY, SENSOR

pytestmark = pytest.mark.asyncio


async def seed_priority(runtime, hass):
    from test_room_lighting_force_auto import seed_participants
    priority, _ = await seed_participants(hass)
    runtime._light_priority = priority
    return priority


def make_pass_recorder(runtime, hass, clock, *, value="off", result=True):
    calls = []

    async def refresh(entities, is_current):
        assert is_current()
        calls.append(frozenset(entities))
        hass.states.set(ENTITY, value,
                        last_changed=runtime_fixture._dt(clock["now"]))
        return result

    runtime._observation_refresher = refresh
    return calls


async def test_pass_refreshes_restart_uncertain_unknown_room(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        await seed_priority(runtime, hass)
        hass.states.set(ENTITY, "unknown",
                        last_changed=runtime_fixture._dt(clock["now"]))
        calls = make_pass_recorder(runtime, hass, clock, value="off")
        report = await runtime.async_startup_recovery_pass()
        assert report["attempted"] == [config.room_id]
        assert report["refreshed"] == [config.room_id]
        assert calls == [frozenset({ENTITY})]
        # Read-only: no HA service command was dispatched by the pass.
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_pass_skips_room_with_known_states_only(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        await seed_priority(runtime, hass)
        calls = make_pass_recorder(runtime, hass, clock)
        report = await runtime.async_startup_recovery_pass()
        assert report["attempted"] == [] and report["refreshed"] == []
        assert calls == []
    finally:
        await close(runtime, listener, hass)


async def test_pass_skips_room_with_fresh_manual_record(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        await seed_priority(runtime, hass)
        hass.states.set(ENTITY, "unknown",
                        last_changed=runtime_fixture._dt(clock["now"]))
        calls = make_pass_recorder(runtime, hass, clock)
        await authority.async_capture(ENTITY, "on", "service", "manual-after",
                                      observed_at_ms=clock["now"])
        report = await runtime.async_startup_recovery_pass()
        assert report["attempted"] == []
        assert calls == []
    finally:
        await close(runtime, listener, hass)


async def test_pass_reselects_scope_immediately_before_refresh(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        await seed_priority(runtime, hass)
        hass.states.set(ENTITY, "unknown",
                        last_changed=runtime_fixture._dt(clock["now"]))
        calls = make_pass_recorder(runtime, hass, clock)
        original_scope = runtime._startup_recovery_scope
        scope_calls = {"count": 0}

        def scope(room_id):
            scope_calls["count"] += 1
            # Candidate listing sees the room; the pre-refresh re-selection
            # does not (a manual gesture landed in between).
            if scope_calls["count"] <= 1:
                return original_scope(room_id)
            return None

        runtime._startup_recovery_scope = scope
        report = await runtime.async_startup_recovery_pass()
        assert report["attempted"] == [] and report["refreshed"] == []
        assert calls == []
    finally:
        await close(runtime, listener, hass)


async def test_failed_refresh_is_recorded_not_retried(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        await seed_priority(runtime, hass)
        hass.states.set(ENTITY, "unknown",
                        last_changed=runtime_fixture._dt(clock["now"]))
        calls = make_pass_recorder(runtime, hass, clock, result=False)
        report = await runtime.async_startup_recovery_pass()
        assert report["attempted"] == [config.room_id]
        assert report["refreshed"] == []
        assert len(calls) == 1
    finally:
        await close(runtime, listener, hass)


async def test_pass_without_candidates_does_not_touch_refresher(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        calls = []

        async def refresh(entities, is_current):
            calls.append(entities)
            return True

        runtime._observation_refresher = refresh
        report = await runtime.async_startup_recovery_pass()
        assert report == {"attempted": [], "refreshed": [], "skipped": 0}
        assert calls == []
    finally:
        await close(runtime, listener, hass)


async def test_pass_room_limit_and_round_robin(monkeypatch):
    """Several candidate rooms: one pass takes max_rooms, the next rotates."""
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        rooms = ["room_a", "room_b", "room_c"]
        runtime._configs = {room_id: config for room_id in rooms}
        runtime._light_priority = SimpleNamespace(
            manual_claim_entity_ids=lambda scope: set())
        runtime._startup_recovery_scope = (
            lambda room_id: frozenset({ENTITY}))
        refreshed = []

        async def fake_refresh(room_id, *, intent_current=None):
            refreshed.append(room_id)
            return None

        runtime._refresh_manual_return_observations = fake_refresh
        report = await runtime.async_startup_recovery_pass(max_rooms=2)
        assert report["attempted"] == ["room_a", "room_b"]
        report = await runtime.async_startup_recovery_pass(max_rooms=2)
        assert report["attempted"] == ["room_c", "room_a"]
        assert refreshed == ["room_a", "room_b", "room_c", "room_a"]
    finally:
        await close(runtime, listener, hass)


async def test_pass_no_refresher_means_no_attempt(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        runtime._observation_refresher = None
        await seed_priority(runtime, hass)
        hass.states.set(ENTITY, "unknown",
                        last_changed=runtime_fixture._dt(clock["now"]))
        report = await runtime.async_startup_recovery_pass()
        assert report == {"attempted": [], "refreshed": [], "skipped": 0}
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


# --- R2 evidence: answers flow through the ordinary state-event path ------


async def _deliver_and_drain(runtime, hass, clock, entity=ENTITY, sensor=SENSOR):
    """Deliver device reports through the REAL runtime event handler."""
    from types import SimpleNamespace as NS
    runtime._state_event(NS(data={"entity_id": entity}))
    runtime._state_event(NS(data={"entity_id": sensor}))
    await asyncio.gather(*hass.tasks, return_exceptions=True)
    await asyncio.sleep(0)


async def test_answer_on_never_grants_ownership_or_off_command(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        await seed_priority(runtime, hass)
        hass.states.set(ENTITY, "unknown",
                        last_changed=runtime_fixture._dt(clock["now"]))
        make_pass_recorder(runtime, hass, clock, value="on")
        report = await runtime.async_startup_recovery_pass()
        assert report["refreshed"] == [config.room_id]
        await _deliver_and_drain(runtime, hass, clock)
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            has_automatic_control,
        )
        context = await runtime._build_context(config)
        assert not has_automatic_control(context.ownership, "light_main")
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_answer_off_without_absence_keeps_barrier(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        await seed_priority(runtime, hass)
        hass.states.set(ENTITY, "unknown",
                        last_changed=runtime_fixture._dt(clock["now"]))
        make_pass_recorder(runtime, hass, clock, value="off")
        report = await runtime.async_startup_recovery_pass()
        assert report["refreshed"] == [config.room_id]
        await _deliver_and_drain(runtime, hass, clock)
        assert authority.barrier_reason_for(ENTITY) == "restart_uncertain"
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_answer_off_with_proven_absence_uses_designed_recovery(monkeypatch):
    """A fresh OFF plus proven sustained absence goes through the ordinary
    restart recovery: the barrier is lifted exactly as for any other device
    report, and the pass itself still sends no command."""
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        from test_room_lighting_force_auto import seed_participants
        priority, _ = await seed_participants(hass)
        runtime._light_priority = priority
        hass.states.set(ENTITY, "off",
                        last_changed=runtime_fixture._dt(clock["now"] - 400_000))
        clock["now"] += 30_000
        make_pass_recorder(runtime, hass, clock, value="off")
        report = await runtime.async_startup_recovery_pass()
        assert report["refreshed"] == [config.room_id]
        await _deliver_and_drain(runtime, hass, clock)
        # Designed recovery outcome: the restart barrier is gone, no command.
        assert authority.barrier_reason_for(ENTITY) is None
        assert not hass.services.calls
    finally:
        await close(runtime, listener, hass)


async def test_manual_gesture_during_wait_keeps_protection(monkeypatch):
    """The human gesture lands WHILE the GET is still in flight."""
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    await seed_priority(runtime, hass)
    entered, release = asyncio.Event(), asyncio.Event()
    requested = []
    seen = {}

    async def blocking_refresh(entities, is_current):
        requested.append(frozenset(entities))
        seen["is_current_at_entry"] = is_current()
        entered.set()
        await release.wait()
        seen["is_current_after_release"] = is_current()
        hass.states.set(ENTITY, "on",
                        last_changed=runtime_fixture._dt(clock["now"]))
        return True

    runtime._observation_refresher = blocking_refresh
    hass.states.set(ENTITY, "unknown",
                    last_changed=runtime_fixture._dt(clock["now"]))
    pass_task = hass.async_create_task(runtime.async_startup_recovery_pass())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert not pass_task.done()
        assert seen["is_current_at_entry"] is True
        # The gesture happens while the refresh is still awaiting.
        captured = await authority.async_capture(
            ENTITY, "on", "service", "human-mid", observed_at_ms=clock["now"])
        assert captured is True
        record_mid = authority.blocking_evidence(ENTITY)
        assert record_mid is not None and record_mid.deadline_ms > 0
        assert not pass_task.done()

        release.set()
        report = await asyncio.wait_for(pass_task, 2)
        assert report["attempted"] == [config.room_id]
        # The late answer is NOT trusted after the mid-flight gesture.
        assert report["refreshed"] == []
        assert seen["is_current_after_release"] is False

        # The device answer still arrives through the ordinary event path.
        await _deliver_and_drain(runtime, hass, clock)
        after = authority.blocking_evidence(ENTITY)
        assert after is not None
        assert after.observed_at_ms == record_mid.observed_at_ms
        assert after.deadline_ms == record_mid.deadline_ms
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            has_automatic_control,
        )
        context = await runtime._build_context(config)
        assert not has_automatic_control(context.ownership, "light_main")
        assert not hass.services.calls
        assert requested == [frozenset({ENTITY})]
    finally:
        release.set()
        if not pass_task.done():
            pass_task.cancel()
        await asyncio.gather(pass_task, return_exceptions=True)
        await close(runtime, listener, hass)


async def test_state_event_behaviour_identical_with_and_without_pass(monkeypatch):
    """The ordinary event processing outcome must not depend on the pass."""
    outcomes = []
    for use_pass in (False, True):
        authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
        try:
            await seed_priority(runtime, hass)
            if use_pass:
                hass.states.set(ENTITY, "unknown",
                                last_changed=runtime_fixture._dt(clock["now"]))
                make_pass_recorder(runtime, hass, clock, value="off")
                await runtime.async_startup_recovery_pass()
            hass.states.set(ENTITY, "off",
                            last_changed=runtime_fixture._dt(clock["now"]))
            await _deliver_and_drain(runtime, hass, clock)
            outcomes.append((authority.barrier_reason_for(ENTITY),
                             authority.blocking_evidence(ENTITY) is not None,
                             len(hass.services.calls)))
        finally:
            await close(runtime, listener, hass)
    assert outcomes[0] == outcomes[1]


async def test_direct_pass_without_event_delivery_changes_nothing_else(monkeypatch):
    authority, _store, runtime, hass, config, listener, clock = await boot(monkeypatch)
    try:
        await seed_priority(runtime, hass)
        hass.states.set(ENTITY, "unknown",
                        last_changed=runtime_fixture._dt(clock["now"]))
        barrier_before = authority.barrier_reason_for(ENTITY)
        make_pass_recorder(runtime, hass, clock, value="off")
        report = await runtime.async_startup_recovery_pass()
        assert report["attempted"] == [config.room_id]
        # The pass itself: no release, no processing, no commands.
        assert authority.barrier_reason_for(ENTITY) == barrier_before
        assert not hass.services.calls
        assert not [t for t in hass.tasks if not t.done()]
    finally:
        await close(runtime, listener, hass)
