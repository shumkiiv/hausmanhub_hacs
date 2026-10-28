"""Owner-authorized paired zero repair; virtual clock, no HA or MQTT access."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from custom_components.hausman_hub.application.lighting_observation import (
    ObservedLightState, PhysicalLightObservation, PhysicalBrightnessObservation,
)
from test_room_lighting_runtime import (
    _FakeHass, _make_runtime, _config_payload, _service_context_factory,
    POWER_ENTITY, AWAY_ENTITY_ID, RoomLightingHaExecutor, _event,
)

LIGHT = "light.demo_main"


async def make_case(local_time="12:00", state="on", level=0):
    moment = datetime.fromisoformat("2026-09-11T" + local_time).replace(tzinfo=ZoneInfo("Asia/Omsk"))
    clock = [int(moment.timestamp() * 1000)]
    hass, reports, levels = _FakeHass(), {}, {}
    hass.config = SimpleNamespace(time_zone="Asia/Omsk")
    for entity, value in ((AWAY_ENTITY_ID, "off"), (POWER_ENTITY, "on"),
                          ("binary_sensor.demo_presence", "off")):
        hass.states.set(entity, value, last_changed=moment)
    hass.states.set(LIGHT, state, {"brightness": level}, last_changed=moment)
    payload = _config_payload(power_switch_entity=POWER_ENTITY)
    payload["profile"] = "day_curve"
    payload["curveParams"] = {"eveningMaxPercent": 40, "neutralKelvin": 3278,
        "eveningWarmKelvin": 3003, "presenceConfirmSeconds": 3}
    reserved = []
    runtime = _make_runtime(hass, commands_enabled=True, payload=payload,
        executor=RoomLightingHaExecutor(), now_ms=lambda: clock[0],
        reserved_entity_ids_provider=lambda: tuple(reserved))
    await runtime.start(hass, "entry")
    sequence = [0]

    def report(entity, value, brightness=None, *, session="live-session", stamp=None):
        sequence[0] += 1
        stamp = clock[0] if stamp is None else stamp
        hass.states.set(entity, value, {} if brightness is None else {"brightness": brightness},
                        last_changed=datetime.fromtimestamp(stamp / 1000, ZoneInfo("Asia/Omsk")))
        hass.states.get(entity).entity_id = entity
        reports[entity] = PhysicalLightObservation(entity, value, stamp, session, sequence[0], "ieee", 1)
        if brightness is not None:
            sequence[0] += 1
            levels[entity] = PhysicalBrightnessObservation(entity, 0, stamp, session, sequence[0], "ieee", 1)

    def reader(entity):
        native = hass.states.get(entity)
        return None if native is None else ObservedLightState(native, reports.get(entity), levels.get(entity))

    runtime._state_reader = reader
    report(LIGHT, state, level)
    runtime._target_last_state[LIGHT] = state
    clock[0] += 1
    report(POWER_ENTITY, "on")
    runtime._observe_power_transition(POWER_ENTITY)
    gate, entered = asyncio.Event(), asyncio.Event()
    refresh_calls, changes = [], []

    async def pause(seconds):
        assert seconds == 5
        entered.set()
        await gate.wait()

    class Refresh:
        zero_pair_entities = frozenset({LIGHT, POWER_ENTITY})

        async def refresh_zero_pair(self, entities, current):
            refresh_calls.append(entities)
            assert entities == self.zero_pair_entities
            if not current():
                return False
            clock[0] += 100
            report(POWER_ENTITY, "on")
            report(LIGHT, hass.states.get(LIGHT).state, hass.states.get(LIGHT).attributes.get("brightness"))
            for change in changes:
                change()
            return current()

    runtime._observation_refresher = Refresh()
    runtime._zero_recovery_sleep = pause
    hass.services.calls.clear()
    return SimpleNamespace(**locals())


async def cycle(case, *, origin="physical"):
    for value in ("off", "on"):
        case.clock[0] += 100
        if origin == "external":
            case.runtime._service_event(_event("switch", "turn_" + value, POWER_ENTITY))
        case.report(POWER_ENTITY, value)
        case.runtime._observe_power_transition(POWER_ENTITY)


async def finish(case):
    await asyncio.wait_for(case.entered.wait(), timeout=1)
    case.clock[0] += 5000
    case.gate.set()
    await asyncio.gather(*tuple(case.runtime._zero_recovery_tasks.values()), return_exceptions=True)


@pytest.mark.parametrize("local_time,expected", [("12:00", 100), ("21:00", 14), ("23:00", 50)])
@pytest.mark.parametrize("state,zero", [("on", 0), ("off", 0.0)])
@pytest.mark.parametrize("origin", ["physical", "external"])
async def test_zero_cycle_waits_then_sends_one_time_level_without_new_ownership(local_time, expected, state, zero, origin):
    case = await make_case(local_time, state, zero)
    try:
        await cycle(case, origin=origin)
        before = case.runtime._ownership.to_payload()
        assert case.hass.services.calls == []
        await finish(case)
        assert case.hass.services.calls == [("light", "turn_on", {"entity_id": LIGHT, "brightness_pct": expected})]
        assert case.runtime._ownership.to_payload() == before
        case.runtime._observe_power_transition(POWER_ENTITY)
        assert len(case.hass.services.calls) == 1
    finally:
        await case.runtime.stop()
    assert not case.runtime._zero_recovery_tasks


@pytest.mark.parametrize("level", [168, 1, None, "0", False, -1, float("nan")])
async def test_not_exact_zero_never_arms(level):
    case = await make_case(state="on", level=level)
    try:
        await cycle(case)
        assert not case.runtime._zero_recoveries
        assert not case.runtime._zero_recovery_tasks
        assert case.hass.services.calls == []
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("change", ["off_service", "brightness_service", "power_off", "positive", "target_off", "epoch", "config", "writer", "deadline", "session", "cached", "away", "disabled"])
async def test_new_action_or_lifecycle_cancels_during_wait(change):
    case = await make_case()
    try:
        await cycle(case)
        await case.entered.wait()
        if change == "off_service":
            case.runtime._service_event(_event("light", "turn_off", LIGHT))
        elif change == "brightness_service":
            case.runtime._service_event(_event("light", "turn_on", LIGHT))
        elif change == "power_off":
            case.report(POWER_ENTITY, "off")
            case.runtime._observe_power_transition(POWER_ENTITY)
        elif change in {"positive", "target_off"}:
            case.report(LIGHT, "off" if change == "target_off" else "on", 0 if change == "target_off" else 10)
            case.runtime._observe_target_transition(LIGHT)
        elif change == "epoch":
            case.runtime._return_intent_epoch += 1
        elif change == "config":
            key = case.payload["roomId"]
            case.runtime._configs[key] = replace(case.runtime._configs[key], version=2)
        elif change == "writer":
            case.reserved.append(LIGHT)
        elif change == "deadline":
            case.clock[0] += 15_000
        elif change == "session":
            case.report(POWER_ENTITY, "on", session="new-session")
        elif change == "cached":
            case.hass.states.get(POWER_ENTITY).attributes["restored"] = True
        elif change == "away":
            case.hass.states.set(AWAY_ENTITY_ID, "on")
        elif change == "disabled":
            key = case.payload["roomId"]
            case.runtime._configs[key] = replace(case.runtime._configs[key], commands_enabled=False)
        case.gate.set()
        await asyncio.gather(*tuple(case.runtime._zero_recovery_tasks.values()), return_exceptions=True)
        assert case.hass.services.calls == []
    finally:
        await case.runtime.stop()


async def test_stop_cancels_and_drains_owned_delay():
    case = await make_case()
    await cycle(case)
    await case.entered.wait()
    await case.runtime.stop()
    assert not case.runtime._zero_recovery_tasks
    assert not case.runtime._zero_recoveries
    assert case.hass.services.calls == []


@pytest.mark.parametrize("change", ["epoch", "new_off", "positive", "session"])
async def test_final_context_await_cannot_send_stale_command(change):
    case = await make_case()
    original = case.runtime._state_provider.build_context

    async def changed(*args):
        context = await original(*args)
        if change == "epoch":
            case.runtime._return_intent_epoch += 1
        elif change == "new_off":
            case.runtime._service_event(_event("light", "turn_off", LIGHT))
        else:
            case.report(LIGHT, "on", 1 if change == "positive" else 0,
                        session="changed" if change == "session" else "live-session")
        return context

    case.runtime._state_provider.build_context = changed
    try:
        await cycle(case)
        await finish(case)
        assert case.hass.services.calls == []
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("order", ["before", "after"])
async def test_same_cycle_relay_authority_delivery_order_does_not_clear_any_guard(order):
    case = await make_case()
    records = {LIGHT: SimpleNamespace(generation=10, action="unknown")}
    authority = SimpleNamespace(record=records.get, blocking_evidence=lambda entity: records.get(entity),
                                barrier_reason_for=lambda entity: "restart_uncertain", generation=10)
    case.runtime._manual_light_authority = authority
    try:
        case.clock[0] += 100
        case.report(POWER_ENTITY, "off")
        case.runtime._observe_power_transition(POWER_ENTITY)
        case.clock[0] += 100
        case.report(POWER_ENTITY, "on")
        def shared_handler():
            records[POWER_ENTITY] = SimpleNamespace(generation=11, action="on")
            authority.generation = 11
        if order == "before":
            shared_handler()
        case.runtime._observe_power_transition(POWER_ENTITY)
        if order == "after":
            shared_handler()
        guard = records[LIGHT]
        await finish(case)
        assert len(case.hass.services.calls) == 1
        assert records[LIGHT] is guard
    finally:
        await case.runtime.stop()


async def test_new_shared_light_action_during_wait_cancels():
    case = await make_case()
    records = {}
    case.runtime._manual_light_authority = SimpleNamespace(
        record=records.get, blocking_evidence=records.get, barrier_reason_for=lambda entity: None)
    try:
        await cycle(case)
        records[LIGHT] = SimpleNamespace(generation=12, action="off")
        await finish(case)
        assert case.hass.services.calls == []
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("order", ["before", "after"])
async def test_real_authority_graph_preserves_existing_light_guard(order):
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
    case = await make_case()
    authority = ManualLightAuthority()
    graph = RelatedLightSources.from_configs(case.runtime._configs.values())
    assert POWER_ENTITY not in graph.related(LIGHT)
    authority.set_related_sources(graph)
    authority.capture(LIGHT, "unknown", "restart", "initial", observed_at_ms=case.clock[0])
    guard = authority.record(LIGHT)
    case.runtime._manual_light_authority = authority
    try:
        case.clock[0] += 100
        case.report(POWER_ENTITY, "off")
        authority.capture(POWER_ENTITY, "off", "external", "power-off", observed_at_ms=case.clock[0])
        case.runtime._observe_power_transition(POWER_ENTITY)
        case.clock[0] += 100
        case.report(POWER_ENTITY, "on")
        def capture_on():
            authority.capture(POWER_ENTITY, "on", "external", "power-on", observed_at_ms=case.clock[0])
        if order == "before":
            capture_on()
        case.runtime._observe_power_transition(POWER_ENTITY)
        if order == "after":
            capture_on()
        await finish(case)
        assert len(case.hass.services.calls) == 1
        assert authority.record(LIGHT) is guard
    finally:
        await case.runtime.stop()


async def test_startup_on_without_fresh_off_never_stages_recovery():
    case = await make_case()
    try:
        case.runtime._target_last_state[POWER_ENTITY] = "off"
        case.report(POWER_ENTITY, "on")
        case.runtime._observe_power_transition(POWER_ENTITY)
        assert not case.runtime._zero_recoveries
        assert case.hass.services.calls == []
    finally:
        await case.runtime.stop()


def corrupt_target_proof(case, unproven):
    if unproven == 'old_session':
        case.report(LIGHT, 'on', 0, session='old')
    elif unproven == 'stale':
        case.levels[LIGHT] = replace(case.levels[LIGHT], observed_at_ms=case.clock[0] - 300_001)
    elif unproven == 'missing_level':
        case.levels.clear()
    elif unproven == 'contradictory_zero_report':
        case.levels[LIGHT] = replace(case.levels[LIGHT], value=1)


@pytest.mark.parametrize('unproven', ['old_session', 'stale', 'missing_level', 'contradictory_zero_report'])
@pytest.mark.parametrize('bad_after_get', [False, True])
async def test_unproven_old_target_only_stages_read_and_requires_new_valid_proof(unproven, bad_after_get):
    case = await make_case()
    try:
        corrupt_target_proof(case, unproven)
        if bad_after_get:
            case.changes.append(lambda: corrupt_target_proof(case, unproven))
        await cycle(case)
        assert case.runtime._zero_recoveries
        assert case.hass.services.calls == []
        await finish(case)
        assert len(case.refresh_calls) == 1
        assert not case.runtime._zero_recoveries
        if bad_after_get:
            assert case.hass.services.calls == []
        else:
            assert case.hass.services.calls == [('light', 'turn_on', {
                'entity_id': LIGHT, 'brightness_pct': 100})]
    finally:
        await case.runtime.stop()
