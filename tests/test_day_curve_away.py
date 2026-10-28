"""Away is a bounded safe-off, never a general manual-authority bypass."""

import asyncio
from dataclasses import replace
from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import (
    DecisionReason, LightAction, LightSnapshot, PlannedCommand,
    RoomLightingContext, evaluate_curve_room,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    OwnershipSnapshot, OwnershipSource, SensorState,
)
from tests.test_room_lighting_runtime import (
    AWAY_ENTITY_ID, _FakeHass, _SpyExecutor, _config_payload, _make_runtime,
    _service_context_factory,
)

NOW = int(datetime(2026, 9, 11, 18, tzinfo=timezone.utc).timestamp() * 1000)
STAMP = datetime.fromtimestamp(NOW / 1000, timezone.utc)


def payload(*, manual_only=False):
    value = _config_payload(commands_enabled=True)
    value.update(profile="day_curve", curveParams={"eveningMaxPercent": 40,
        "neutralKelvin": 3278, "eveningWarmKelvin": 3003,
        "presenceConfirmSeconds": 3}, awayBehavior={"mode": "room_off",
        "return": {"restore": "by_current_conditions"}})
    main = value["devices"]["light_targets"][0]
    main["autoControl"] = not manual_only
    value["devices"]["light_targets"] = [main, {**main, "id": "mirror",
        "entityId": "light.demo_mirror", "role": "mirror"}, {**main,
        "id": "accent", "entityId": "light.demo_accent", "role": "accent"}]
    return value


async def test_unknown_away_gap_does_not_revive_old_off_permission():
    runtime, config, hass = await make_case()
    try:
        hass.states.set(AWAY_ENTITY_ID, "on", last_changed=STAMP)
        runtime._away = True
        runtime._capture_away_off_boundaries()
        command = PlannedCommand("light_main", LightAction.TURN_OFF, reason=DecisionReason.AWAY)
        assert runtime._away_off_is_current(config, command)
        hass.states.set(AWAY_ENTITY_ID, "unknown", last_changed=STAMP)
        runtime._away_event(None)
        hass.states.set(AWAY_ENTITY_ID, "on", last_changed=STAMP)
        runtime._away_event(None)
        assert not runtime._away_off_is_current(config, command)
        await runtime.async_process()
        assert not runtime._executor.calls
    finally:
        await runtime.stop()


@pytest.mark.parametrize("manual_only", [False, True])
@pytest.mark.parametrize("owner", ["auto", "manual", "none"])
def test_curve_away_turns_off_all_roles(owner, manual_only):
    config = config_from_payload(payload(manual_only=manual_only))
    records = () if owner == "none" else tuple(OwnershipSnapshot(t.id,
        OwnershipSource.AUTO if owner == "auto" else OwnershipSource.MANUAL,
        True, NOW - 1000) for t in config.devices.light_targets)
    context = RoomLightingContext(now=NOW, timezone=timezone.utc,
        sunrise=time(7), sunset=time(19), away=True, ownership=records,
        lights=tuple(LightSnapshot(t.id, SensorState.ON, NOW, brightness=100,
            reported_state=SensorState.ON) for t in config.devices.light_targets))
    decision = evaluate_curve_room(config, context, presence=SensorState.OFF,
        presence_confirmed=False, absence_seconds=700)
    assert {c.target_id for c in decision.commands} == set(config.devices.light_target_ids)
    assert all(c.action is LightAction.TURN_OFF and c.reason is DecisionReason.AWAY
               for c in decision.commands)


class AwaySpy(_SpyExecutor):
    async def execute_away_off(self, hass, command, *, is_current):
        assert is_current()
        return await self.execute(hass, command)


async def make_case(*, manual=False, manual_only=False):
    value = payload(manual_only=manual_only)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=STAMP)
    for target in value["devices"]["light_targets"]:
        hass.states.set(target["entityId"], "on", {"brightness": 255}, last_changed=STAMP)
    hass.states.set(AWAY_ENTITY_ID, "off", last_changed=STAMP)
    runtime = _make_runtime(hass, commands_enabled=True, executor=AwaySpy(),
        payload=value, now_ms=lambda: NOW)
    runtime._away_debounce_seconds = 0
    await runtime.start(hass, "entry")
    config = runtime.configs()[0]
    if manual:
        for target in config.devices.light_targets:
            runtime._ownership.record_manual(config.room_id, target.id, NOW - 1000)
    runtime._executor.calls.clear()
    return runtime, config, hass


@pytest.mark.parametrize("manual,manual_only", [(False, False), (True, False), (True, True)])
async def test_current_stable_away_dispatches_all_off(manual, manual_only):
    runtime, config, hass = await make_case(manual=manual, manual_only=manual_only)
    try:
        hass.states.set(AWAY_ENTITY_ID, "on", last_changed=STAMP)
        await runtime._async_away_after_debounce(runtime._away_generation)
        assert runtime.away
        assert {c.target_id for c in runtime._executor.calls
                if c.action is LightAction.TURN_OFF} == set(config.devices.light_target_ids)
    finally:
        await runtime.stop()


@pytest.mark.parametrize("change", ["manual", "home", "unknown", "config", "stop",
                                    "priority", "off_protection"])
async def test_new_choice_or_invalid_away_cancels_after_pending_save(monkeypatch, change):
    runtime, config, hass = await make_case(manual=True)
    records = {}
    source = SimpleNamespace(explicit_return_snapshot=lambda *args: dict(records),
                             manual_claim_entity_ids=lambda entities: frozenset(records))
    if change == "priority":
        runtime._light_priority = source
    if change == "off_protection":
        hass.data = {"hausman_hub": {"manual_light_off_protection": source}}
    original = runtime._persist_pending_target_dispatch

    async def save(**kwargs):
        saved = await original(**kwargs)
        if change == "manual":
            # Same millisecond is still a NEW choice, not a duplicate.
            runtime._ownership.record_manual(config.room_id, "light_main", NOW)
        elif change in {"home", "unknown"}:
            hass.states.set(AWAY_ENTITY_ID, "off" if change == "home" else "unknown",
                            last_changed=STAMP)
        elif change == "config":
            runtime._configs[config.room_id] = replace(config, version=config.version + 1)
        elif change == "stop":
            runtime.cancel()
        else:
            records["light.demo_main"] = object()
        return saved

    monkeypatch.setattr(runtime, "_persist_pending_target_dispatch", save)
    try:
        hass.states.set(AWAY_ENTITY_ID, "on", last_changed=STAMP)
        await runtime._async_away_after_debounce(runtime._away_generation)
        assert runtime._executor.calls == []
        assert hass.services.calls == []
    finally:
        await runtime.stop()


async def test_real_executor_away_then_home_uses_evening_not_cached_100(monkeypatch):
    from custom_components.hausman_hub.application.room_lighting_ha_executor import RoomLightingHaExecutor

    runtime, config, hass = await make_case(manual=True)
    clock = [NOW]
    runtime._now_ms = lambda: clock[0]
    executor = RoomLightingHaExecutor({t.id: t for t in config.devices.light_targets},
        now_ms=lambda: clock[0], light_readback_window_seconds=0)
    runtime._executor = executor
    try:
        hass.states.set(AWAY_ENTITY_ID, "on", last_changed=STAMP)
        await runtime._async_away_after_debounce(runtime._away_generation)
        assert {data["entity_id"] for _, service, data in hass.services.calls
                if service == "turn_off"} == {t.entity_id for t in config.devices.light_targets}
        assert all(hass.states.get(t.entity_id).state == "off" for t in config.devices.light_targets)
        assert all(r["confirmed"] for row in runtime._shadow._entries
                   for r in row.get("dispatch", []) if r.get("action") == "turn_off")
        hass.services.calls.clear()
        clock[0] += 5000
        stamp = datetime.fromtimestamp(clock[0] / 1000, timezone.utc)
        hass.states.set(AWAY_ENTITY_ID, "off", last_changed=stamp)
        hass.states.set("binary_sensor.demo_presence", "on", last_changed=stamp)
        runtime._release_away()
        await asyncio.gather(*hass.tasks)
        await runtime.async_process()
        sent = [data for _, service, data in hass.services.calls
                if service == "turn_on" and data["entity_id"] == "light.demo_main"]
        assert sent and 0 < sent[-1]["brightness_pct"] <= 40
        assert hass.states.get("light.demo_main").attributes["brightness"] < 255
        # A newer manual choice after return must not be overwritten.
        runtime._ownership.record_manual(config.room_id, "light_main", clock[0])
        hass.services.calls.clear()
        clock[0] += 1000
        await runtime.async_process()
        assert hass.services.calls == []
    finally:
        await runtime.stop()


@pytest.mark.parametrize("manual_only", [False, True])
async def test_real_executor_narrow_away_override_keeps_normal_manual_guard(monkeypatch, manual_only):
    from tests.test_light_command_readback import harness, state, BASE_MS
    clock, _, cache, calls, hass, executor, report = harness(monkeypatch,
        authority=SimpleNamespace(blocks=lambda entity: True,
                                  blocks_departure_off=lambda entity: False))
    executor._targets["main"] = replace(executor._targets["main"], auto_control=not manual_only)
    cache["light.main"] = state("on", at=BASE_MS - 1000)
    command = PlannedCommand("main", LightAction.TURN_OFF, reason=DecisionReason.AWAY)
    ordinary = await executor.execute(hass, command)
    assert not ordinary.sent and not calls
    clock.on_sleep = lambda: report(value="off", attributes={})
    away = await executor.execute_away_off(hass, command, is_current=lambda: True)
    assert away.sent and away.confirmed and len(calls) == 1


@pytest.mark.parametrize("change", ["before_service", "after_service", "invalid_on"])
async def test_real_executor_checks_away_intent_at_service_and_readback(monkeypatch, change):
    from tests.test_light_command_readback import harness, state, BASE_MS
    clock, _, cache, calls, hass, executor, report = harness(monkeypatch)
    cache["light.main"] = state("on", at=BASE_MS - 1000)
    current = [True]
    command = PlannedCommand("main", LightAction.TURN_OFF, reason=DecisionReason.AWAY)
    if change == "before_service":
        def read(entity):
            current[0] = False
            return cache.get(entity)
        executor._state_reader = read
    elif change == "after_service":
        def late():
            current[0] = False
            report(value="off", attributes={})
        clock.on_sleep = late
    else:
        with pytest.raises(ValueError, match="away turn_off"):
            await executor.execute_away_off(hass, replace(command, action=LightAction.TURN_ON),
                                          is_current=lambda: True)
        assert not calls
        return
    receipt = await executor.execute_away_off(hass, command, is_current=lambda: current[0])
    assert not receipt.confirmed
    assert receipt.sent is (change == "after_service")
    assert len(calls) == (1 if change == "after_service" else 0)


def close_authority(authority, cause, entity):
    if cause == "storage":
        authority._storage_failed = True
    elif cause == "restart":
        authority._restart_uncertain = True
    elif cause == "startup":
        authority.begin_startup_observation()
    elif cause == "selector":
        authority.mark_selector_uncertain("light")
    elif cause == "attribution":
        authority.invalidate_related_sources()
    elif cause == "legacy":
        authority.capture_legacy(entity, source="room")
    elif cause == "unknown":
        authority.capture(entity, "unknown", "service", "unknown-test")
    elif cause == "dispatch":
        authority.mark_unhealthy()
    elif cause == "return":
        authority._explicit_return_pending = True
    elif cause == "missing":
        authority.blocks_departure_off = None
    else:
        def fail(entity):
            raise RuntimeError("authority unavailable")
        authority.blocks_departure_off = fail


@pytest.mark.parametrize("cause", ["storage", "restart", "startup", "selector", "attribution",
                                   "legacy", "unknown", "dispatch", "return", "missing", "error"])
async def test_real_authority_gap_blocks_runtime_away(cause):
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    runtime, config, hass = await make_case()
    authority = ManualLightAuthority()
    runtime._manual_light_authority = authority
    close_authority(authority, cause, "light.demo_main")
    try:
        hass.states.set(AWAY_ENTITY_ID, "on", last_changed=STAMP)
        await runtime._async_away_after_debounce(runtime._away_generation)
        assert runtime._executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.parametrize("cause", ["storage", "restart", "selector", "attribution", "dispatch"])
async def test_real_authority_late_gap_without_new_record_revokes_room(monkeypatch, cause):
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    runtime, config, hass = await make_case()
    authority = ManualLightAuthority()
    runtime._manual_light_authority = authority
    original = runtime._persist_pending_target_dispatch

    async def save(**kwargs):
        saved = await original(**kwargs)
        before = runtime._manual_dispatch_boundary(config)
        close_authority(authority, cause, "light.demo_main")
        assert runtime._manual_dispatch_boundary(config) == before
        return saved

    monkeypatch.setattr(runtime, "_persist_pending_target_dispatch", save)
    try:
        hass.states.set(AWAY_ENTITY_ID, "on", last_changed=STAMP)
        await runtime._async_away_after_debounce(runtime._away_generation)
        assert runtime._executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.parametrize("cause", ["storage", "restart", "startup", "selector", "attribution",
                                   "legacy", "unknown", "dispatch", "return", "missing", "error"])
async def test_real_authority_gap_blocks_executor_even_if_caller_guard_is_true(monkeypatch, cause):
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    from tests.test_light_command_readback import harness
    authority = ManualLightAuthority()
    _, _, _, calls, hass, executor, _ = harness(monkeypatch, authority=authority)
    close_authority(authority, cause, "light.main")
    command = PlannedCommand("main", LightAction.TURN_OFF, reason=DecisionReason.AWAY)
    receipt = await executor.execute_away_off(hass, command, is_current=lambda: True)
    assert not receipt.sent and not receipt.confirmed and not calls


async def test_proved_old_manual_on_is_allowed_but_late_barrier_cannot_confirm(monkeypatch):
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    from tests.test_light_command_readback import harness, state, BASE_MS
    authority = ManualLightAuthority()
    authority.capture("light.main", "on", "service", "old-known-on")
    assert authority.blocks("light.main") and not authority.blocks_departure_off("light.main")
    clock, _, cache, calls, hass, executor, report = harness(monkeypatch, authority=authority)
    cache["light.main"] = state("on", at=BASE_MS - 1000)
    def late():
        authority.mark_selector_uncertain("light")
        report(value="off", attributes={})
    clock.on_sleep = late
    receipt = await executor.execute_away_off(hass,
        PlannedCommand("main", LightAction.TURN_OFF, reason=DecisionReason.AWAY),
        is_current=lambda: True)
    assert receipt.sent and not receipt.confirmed and len(calls) == 1
    assert authority.record("light.main").fingerprint == "old-known-on"


@pytest.mark.parametrize("action", ["on", "off"])
async def test_real_authority_old_known_action_allows_confirmed_departure_off(monkeypatch, action):
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    from tests.test_light_command_readback import harness, state, BASE_MS
    authority = ManualLightAuthority()
    authority.capture("light.main", action, "service", "old-known-action")
    original = authority.record("light.main")
    clock, _, cache, calls, hass, executor, report = harness(monkeypatch, authority=authority)
    cache["light.main"] = state("on", at=BASE_MS - 1000)
    clock.on_sleep = lambda: report(value="off", attributes={})
    receipt = await executor.execute_away_off(hass,
        PlannedCommand("main", LightAction.TURN_OFF, reason=DecisionReason.AWAY),
        is_current=lambda: True)
    assert receipt.sent and receipt.confirmed and len(calls) == 1
    assert authority.record("light.main") is original
