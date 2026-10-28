"""Virtual morning through the real bounded physical observation port."""

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import LightingMqttRefresh, READ_CHANNELS
from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction, evaluate_curve_room
from tests.test_evening_physical_replay import replay
from tests.test_lighting_mqtt_observations import ROLLOUT_BINDINGS, report, feed
from tests.test_room_lighting_force_auto import Store, seed_participants
from tests.test_room_lighting_runtime import _make_runtime, _dt, AWAY_ENTITY_ID


async def morning_case(*, mirror_on=False):
    _, config, hass, clock, port, native = replay()
    clock["now"] = int(datetime(2026, 10, 17, 9, 30, tzinfo=timezone.utc).timestamp() * 1000)
    hass.states.values.update(native)
    port._native_reader = hass.states.get
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock["now"]))
    hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_dt(clock["now"]))
    authority = ManualLightAuthority(Store())
    await authority.async_load()
    priority, off = await seed_participants(hass)
    runtime = _make_runtime(hass, payload=config.to_dict(), commands_enabled=True,
        now_ms=lambda: clock["now"], manual_light_authority=authority,
        light_priority=priority, ownership_store=Store())
    runtime._state_reader = port.read_state
    runtime._state_provider._state_reader = port.read_state
    await runtime.start(hass, "morning-refresh")
    config = runtime._configs[config.room_id]
    runtime._dispatch_failures[(config.room_id, "light_main")] = (
        "turn_off", "untrusted:off", clock["now"] - 600_000)
    calls, processed = [], []
    session = port.read_state(ROLLOUT_BINDINGS[3].entity_id).observation.session
    initial_sequence = port._sequence

    async def publish(topic, payload):
        calls.append((topic, json.loads(payload)))
        entity, _, _ = next(row for row in READ_CHANNELS
                           if (row[1], {row[2]: ""}) == calls[-1])
        channel = next(i for i, b in enumerate(ROLLOUT_BINDINGS) if b.entity_id == entity)
        value = "on" if channel == 1 or (channel == 2 and mirror_on) else "off"
        native[entity].state = value
        clock["now"] += 10
        feed(port, report(clock, channel=channel, session=session,
                          sequence=initial_sequence + len(calls), value=value.upper()))

    async def evaluate(room_id):
        fresh = await runtime._build_context(runtime._configs[room_id])
        decision = evaluate_curve_room(runtime._configs[room_id], fresh,
                                      presence_confirmed=True, absence_seconds=0)
        blocked = runtime._target_attempt_blocked((room_id, "light_main"),
            ROLLOUT_BINDINGS[3].entity_id, LightAction.TURN_ON)
        processed.append((decision, blocked))

    runtime._process_room_locked = evaluate
    runtime._observation_refresher = LightingMqttRefresh(port, publish, now_ms=lambda: clock["now"])
    return runtime, config, clock, port, native, calls, processed, authority


async def test_real_morning_reads_once_then_rebuilds_plan_and_uses_original_guard():
    runtime, config, clock, _, _, calls, processed, _ = await morning_case()
    try:
        assert runtime._target_attempt_blocked((config.room_id, "light_main"),
            ROLLOUT_BINDINGS[3].entity_id, LightAction.TURN_ON)
        await runtime.async_process([config.room_id])
        assert len(calls) == 4
        assert processed and not processed[0][1]
        assert any(c.target_id == "light_main" and c.action is LightAction.TURN_ON
                   for c in processed[0][0].commands)
        for _ in range(3):
            clock["now"] += 60_000
            await runtime.async_process([config.room_id])
        assert len(calls) == 4
        assert not runtime._ownership.snapshots_for(config.room_id, config.devices.light_target_ids)
    finally:
        await runtime.stop()


@pytest.mark.parametrize("change", ["manual", "config", "stop", "gap", "time", "presence", "false", "error"])
async def test_changed_or_failed_read_does_not_process_old_intent(change):
    runtime, config, clock, port, _, calls, processed, authority = await morning_case()
    async def refresh(_entities, current):
        calls.append("attempt")
        assert current()
        if change == "manual":
            await authority.async_capture(ROLLOUT_BINDINGS[3].entity_id, "off", "service", "gesture")
        elif change == "config":
            runtime._configs[config.room_id] = replace(config, version=config.version + 1)
        elif change == "stop":
            await runtime.stop()
        elif change == "gap":
            runtime._unobserved_since = clock["now"]
        elif change == "time":
            clock["now"] += 3 * 3_600_000
        elif change == "presence":
            runtime._hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(clock["now"]))
        elif change == "error":
            raise OSError("offline")
        if change not in {"false", "error"}:
            assert not current()
        return change != "false"
    runtime._observation_refresher = refresh
    try:
        await runtime.async_process([config.room_id])
        assert calls == ["attempt"] and not processed
        assert (config.room_id, "light_main") in runtime._dispatch_failures
    finally:
        await runtime.stop()


async def test_parallel_ticks_do_not_dispatch_partial_observations_or_repeat_read():
    runtime, config, _, _, _, calls, processed, _ = await morning_case()
    entered, finish = asyncio.Event(), asyncio.Event()
    async def refresh(_entities, current):
        calls.append("attempt")
        entered.set()
        await finish.wait()
        return False
    runtime._observation_refresher = refresh
    try:
        task = asyncio.create_task(runtime.async_process([config.room_id]))
        await entered.wait()
        await runtime.async_process([config.room_id])
        assert calls == ["attempt"] and not processed
        finish.set()
        await task
        await runtime.async_process([config.room_id])
        assert calls == ["attempt"]
    finally:
        finish.set()
        await runtime.stop()


@pytest.mark.parametrize("condition", ["shadow", "manual", "restart", "early", "late", "fresh"])
async def test_ineligible_morning_never_requests_get(condition):
    runtime, config, clock, port, native, calls, _, authority = await morning_case()
    if condition == "shadow":
        runtime._configs[config.room_id] = replace(config, commands_enabled=False)
    elif condition == "manual":
        await authority.async_capture(ROLLOUT_BINDINGS[3].entity_id, "on", "service", "gesture")
    elif condition == "restart":
        authority.begin_startup_observation()
    elif condition in {"early", "late"}:
        clock["now"] += (-2 if condition == "early" else 4) * 3_600_000
    else:
        native[ROLLOUT_BINDINGS[3].entity_id].state = "off"
        feed(port, report(clock, channel=3, value="OFF"))
    try:
        await runtime.async_process([config.room_id])
        assert not calls
    finally:
        await runtime.stop()


@pytest.mark.parametrize("presence", ["off", "unknown"])
async def test_legacy_time_mode_does_not_require_occupied_room(presence):
    runtime, config, clock, _, _, calls, processed, _ = await morning_case()
    runtime._hass.states.set("binary_sensor.demo_presence", presence, last_changed=_dt(clock["now"]))
    try:
        await runtime.async_process([config.room_id])
        assert len(calls) == 4 and processed and not processed[0][1]
        assert any(c.target_id == "light_main" and c.action is LightAction.TURN_ON
                   for c in processed[0][0].commands)
    finally:
        await runtime.stop()


@pytest.mark.parametrize("change", ["config", "lifecycle", "manual", "priority", "off"])
async def test_boundary_is_captured_before_build_context_await(change, caplog):
    runtime, config, clock, _, _, calls, processed, authority = await morning_case()
    build = runtime._build_context
    async def changed(*args):
        context = await build(*args)
        await asyncio.sleep(0)
        if change == "config":
            runtime._configs[config.room_id] = replace(config, version=config.version + 1)
        elif change == "lifecycle":
            runtime._started_at += 1
        elif change == "manual":
            await authority.async_capture(ROLLOUT_BINDINGS[3].entity_id, "off", "service", "gesture")
        elif change == "priority":
            runtime._light_priority._manual_records[ROLLOUT_BINDINGS[3].entity_id] = {"confirmedAt": clock["now"]}
        else:
            from custom_components.hausman_hub.application.room_lighting_ha_executor import room_manual_off_protection
            off = room_manual_off_protection(runtime._hass)
            off._protections[config.room_id] = {
                "lightIds": [ROLLOUT_BINDINGS[3].entity_id], "roomId": config.room_id,
                "effectivePolicy": {"allowManualRelease": True}}
        return context
    runtime._build_context = changed
    try:
        await runtime.async_process([config.room_id])
        assert not calls and not processed and not runtime._morning_refresh_active
        assert "morning observation preflight failed" not in caplog.text
    finally:
        await runtime.stop()


@pytest.mark.parametrize("transitioned", [False, True])
async def test_presence_gated_morning_does_not_spend_read_before_arrival(transitioned):
    from custom_components.hausman_hub.domain.room_lighting import MorningBoundary
    runtime, config, clock, _, _, calls, _, _ = await morning_case()
    config = replace(config, morning=MorningBoundary(enabled=True, time="09:00", presence_gate=True))
    runtime._configs[config.room_id] = config
    runtime._hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(clock["now"]))
    if transitioned:
        runtime._ownership.mark_morning_transition(config.room_id, "2026-10-17", clock["now"])
    try:
        await runtime.async_process([config.room_id])
        assert len(calls) == (4 if transitioned else 0)
        if not transitioned:
            assert config.room_id not in runtime._morning_refresh_attempts
            clock["now"] += 600_000
            runtime._hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock["now"]))
            runtime._hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_dt(clock["now"]))
            await runtime.async_process([config.room_id])
            assert len(calls) == 4
    finally:
        await runtime.stop()


async def test_preflight_failure_is_isolated_from_other_rooms():
    runtime, config, _, _, _, calls, processed, _ = await morning_case()
    other = replace(config, room_id="other-room", profile=None)
    runtime._configs[other.room_id] = other
    async def broken(*_args):
        raise ValueError("bad context")
    async def record(room_id):
        processed.append(room_id)
    runtime._build_context = broken
    runtime._process_room_locked = record
    try:
        await runtime.async_process([config.room_id, other.room_id])
        assert processed == [other.room_id] and not calls
        assert not runtime._morning_refresh_active
    finally:
        await runtime.stop()


@pytest.mark.parametrize("handoff", [False, True])
async def test_mirror_on_requires_real_scoped_handoff_not_refresh_created_ownership(handoff):
    from custom_components.hausman_hub.domain.room_lighting_ownership import ExplicitHandoffProof, OwnershipSource
    runtime, config, clock, port, _, calls, processed, _ = await morning_case(mirror_on=True)
    if handoff:
        session = port.read_state(ROLLOUT_BINDINGS[2].entity_id).observation.session
        proof = ExplicitHandoffProof(ROLLOUT_BINDINGS[2].entity_id, config.version,
            1, runtime._ownership.manual_return_boundary(), clock["now"] - 1000, session)
        record = runtime._ownership.stage_handoff(config, "light_mirror", proof, clock["now"])
        runtime._ownership.activate_handoffs(config, [record],
            getattr(runtime._service, "configuration_generation", None))
    try:
        await runtime.async_process([config.room_id])
        assert len(calls) == 4
        main_on = any(c.target_id == "light_main" and c.action is LightAction.TURN_ON
                      for c in processed[0][0].commands)
        assert main_on is handoff
        assert not any(record.source is OwnershipSource.AUTO
                       for record in runtime._ownership.snapshots())
    finally:
        await runtime.stop()
