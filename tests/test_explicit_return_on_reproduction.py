"""HH-014.b explicit ON handoff regression, no physical IO."""
import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from custom_components.hausman_hub.domain.room_lighting_ownership import (
    has_proven_auto_ownership,
    has_automatic_control, OwnershipSource, latest_ownership,
    resolve_manual_ownership,
)
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction
from tests.test_room_lighting_force_auto import make_case
from tests.test_room_lighting_runtime import _ROOM_ID, _NOW_MS, AWAY_ENTITY_ID


async def power_case(*, exact, target="on", power="off"):
    from custom_components.hausman_hub.domain.room_lighting import config_from_payload
    from custom_components.hausman_hub.domain.device_power_dependencies import DevicePowerDependency
    case = await make_case("on", protected=True)
    payload = case.runtime._configs[_ROOM_ID].to_dict()
    payload["devices"]["power_switch"] = {"id": "power", "name": "Питание", "entityId": "switch.demo_power", "autoAdoptOverride": None}
    config = config_from_payload(payload)
    case.runtime._configs[_ROOM_ID] = config
    case.runtime._service._configs[_ROOM_ID] = config
    stamp = datetime.fromtimestamp(_NOW_MS / 1000, timezone.utc)
    case.hass.states.set("light.demo_main", target, last_changed=stamp)
    case.hass.states.set("switch.demo_power", power, last_changed=stamp)
    if exact:
        manifest = {"light_main": "switch.demo_power"}
        dependency = DevicePowerDependency(dependent_entity_id="light.demo_main", power_source_entity_id="switch.demo_power", policy="auto_turn_on", warmup_seconds=0)
        case.runtime._power_dependency_resolver = lambda: {"light.demo_main": dependency}
        case.runtime._power_migration_manifest = manifest
        case.runtime._state_provider._power_dependency_resolver = case.runtime._power_dependency_resolver
        case.runtime._state_provider._power_migration_manifest = manifest
    return case, config


@pytest.mark.parametrize("exact", [False, True])
async def test_conflicting_target_on_and_upstream_off_never_reports_returned(exact):
    case, config = await power_case(exact=exact)
    original = case.authority.record("light.demo_main")
    history = case.runtime._ownership.snapshots()
    priority = copy.deepcopy(case.priority.explicit_return_snapshot({"light.demo_main"}))
    off = copy.deepcopy(case.off.explicit_return_snapshot(_ROOM_ID, frozenset({"light.demo_main"})))
    try:
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="owner")
        assert result["outcome"] == "blocked", result
        assert case.authority.record("light.demo_main") is original
        assert case.authority.blocks("light.demo_main")
        assert case.runtime._ownership.snapshots() == history
        assert case.priority.explicit_return_snapshot({"light.demo_main"}) == priority
        assert case.off.explicit_return_snapshot(_ROOM_ID, frozenset({"light.demo_main"})) == off
        assert not has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize("target,power", [("on", "on"), ("unknown", "off"), ("unavailable", "off")])
async def test_noncontradictory_power_observations_keep_return_path(exact, target, power):
    case, config = await power_case(exact=exact, target=target, power=power)
    try:
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="owner")
        assert result["outcome"] == "returned", result
        assert has_automatic_control(case.runtime._control_ownership_for(config), "light_main") is (target == "on")
        assert not has_proven_auto_ownership(case.runtime._control_ownership_for(config), "light_main")
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize("participant", ["authority", "ownership", "priority", "off", "commit"])
async def test_power_contradiction_during_persistence_keeps_return_fenced(exact, participant):
    case, config = await power_case(exact=exact, power="on")
    original = case.authority.record("light.demo_main")

    async def conflict(_payload):
        case.hass.states.set("switch.demo_power", "off", last_changed=datetime.fromtimestamp(_NOW_MS / 1000, timezone.utc))
        await asyncio.sleep(0)

    stores = {"authority": case.store, "ownership": case.ownership_store, "priority": case.priority._store, "off": case.off._store, "commit": case.store}
    setattr(stores[participant], "prepare_hook" if participant == "commit" else "hook", conflict)
    try:
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="owner")
        assert result["outcome"] != "returned", result
        assert case.authority.record("light.demo_main") is original
        assert case.authority.blocks("light.demo_main")
        assert not has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        assert not case.executor.calls
    finally:
        for store in stores.values():
            store.hook = store.prepare_hook = None
        await case.runtime.stop()


async def test_explicit_return_delegates_on_without_fabricating_auto_on():
    case = await make_case("on")
    try:
        case.runtime._configs[_ROOM_ID] = replace(
            case.runtime._configs[_ROOM_ID], commands_enabled=True,
        )
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="explicit owner return")
        assert result["outcome"] == "returned", result
        assert result["evaluation"] == "requested"
        assert case.authority.record("light.demo_main") is None
        assert not case.authority.blocks("light.demo_main")
        records = case.runtime._control_ownership_for(case.runtime._configs[_ROOM_ID])
        assert records[-1].source is OwnershipSource.EXPLICIT_HANDOFF
        assert records[-1].handoff.operation_id == result["operationId"]
        assert records[-1].handoff.entity_id == "light.demo_main"
        assert has_automatic_control(records, "light_main")
        assert not has_proven_auto_ownership(records, "light_main")
        assert not resolve_manual_ownership(records, "light_main", light_on=True)
        assert all(c.action is not LightAction.TURN_ON for c in case.executor.calls)
    finally:
        await case.runtime.stop()


def test_unattributed_on_stays_protected_without_explicit_handoff():
    assert resolve_manual_ownership([], "light_main", light_on=True)
    assert not has_proven_auto_ownership([], "light_main")
    assert not resolve_manual_ownership([], "light_main", light_on=False)


async def enabled_case():
    case = await make_case("on", protected=True)
    config = replace(case.runtime._configs[_ROOM_ID], commands_enabled=True, profile="day_curve")
    case.runtime._configs[_ROOM_ID] = config
    case.runtime._service._configs[_ROOM_ID] = config
    return case, config


async def test_manual_change_during_first_handoff_adjustment_stops_later_commands():
    case, config = await enabled_case()
    execute = case.executor.execute

    async def intervene(hass, command):
        receipt = await execute(hass, command)
        case.authority.capture("light.demo_main", "on", "service", "new-brightness")
        case.runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS)
        await asyncio.sleep(0)
        return receipt

    case.executor.execute = intervene
    try:
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="owner")
        assert result["outcome"] == "returned"
        assert len(case.executor.calls) == 1
        assert not has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        assert case.authority.blocks("light.demo_main")
        receipts = [r for entry in case.runtime._shadow.journal_payload()["entries"] for r in entry.get("dispatch", [])]
        assert any(r.get("reason") == "manual_superseded" for r in receipts)
        assert not any(r.get("ownershipChanged") == "auto" for r in receipts)
    finally:
        await case.runtime.stop()


async def test_return_on_adjusts_then_scheduled_off_without_redundant_on():
    case, config = await enabled_case()
    try:
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="owner")
        assert result["outcome"] == "returned", result
        assert case.executor.calls
        assert all(c.action in (LightAction.SET_BRIGHTNESS, LightAction.SET_COLOR_TEMPERATURE)
                   for c in case.executor.calls)
        records = case.runtime._control_ownership_for(config)
        assert has_automatic_control(records, "light_main")
        assert not has_proven_auto_ownership(records, "light_main")
        now = _NOW_MS + 13 * 3600_000
        case.runtime._now_ms = lambda: now
        stamp = datetime.fromtimestamp(now / 1000, timezone.utc)
        for entity, value in (("light.demo_main", "on"), ("binary_sensor.demo_presence", "off"), (AWAY_ENTITY_ID, "off")):
            case.hass.states.set(entity, value, last_changed=stamp)
        await case.runtime.async_process([_ROOM_ID])
        assert case.executor.calls[-1].action is LightAction.TURN_OFF
        assert not any(c.action is LightAction.TURN_ON for c in case.executor.calls)
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("presence", ["off", "unknown", "unavailable"])
@pytest.mark.parametrize("fade", [False, True])
async def test_explicit_on_handoff_allows_off_only_after_observed_absence(presence, fade):
    case = await make_case("on")
    old = case.runtime._configs[_ROOM_ID]
    config = replace(old, commands_enabled=True, dimming=replace(old.dimming, enabled=fade))
    case.runtime._configs[_ROOM_ID] = config
    case.runtime._service._configs[_ROOM_ID] = config
    now = [_NOW_MS]
    case.runtime._now_ms = lambda: now[0]
    try:
        assert (await case.runtime.async_force_auto(_ROOM_ID, reason="owner"))["outcome"] == "returned"
        case.executor.calls.clear()
        for delta in (1000, 601_000):
            now[0] = _NOW_MS + delta
            stamp = datetime.fromtimestamp(now[0] / 1000, timezone.utc)
            for entity, value in (("light.demo_main", "on"), ("binary_sensor.demo_presence", presence), (AWAY_ENTITY_ID, "off")):
                changed = datetime.fromtimestamp((_NOW_MS + 1000) / 1000, timezone.utc) if entity == "binary_sensor.demo_presence" else stamp
                case.hass.states.set(entity, value, last_changed=changed)
                case.hass.states.get(entity).last_reported = stamp
            await case.runtime.async_process([_ROOM_ID])
        from custom_components.hausman_hub.domain.room_lighting_engine import DecisionReason
        offs = [c for c in case.executor.calls if c.reason is DecisionReason.DIMMING]
        assert bool(offs) is (presence == "off")
        if offs:
            assert offs[-1].action is (LightAction.SET_BRIGHTNESS if fade else LightAction.TURN_OFF)
            if fade:
                assert offs[-1].brightness == 0
            receipts = case.runtime._shadow.journal_payload()["entries"][-1]["dispatch"]
            assert receipts[-1]["sent"] and receipts[-1]["confirmed"]
        assert not any(c.action is LightAction.TURN_ON for c in case.executor.calls)
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("change", ["session", "cached", "unknown", "gap"])
async def test_physical_handoff_invalidates_permanently_on_observation_break(change):
    from custom_components.hausman_hub.application.lighting_observation import ObservedLightState, PhysicalLightObservation
    case, config = await enabled_case()
    native = case.hass.states.get("light.demo_main")
    native.entity_id = "light.demo_main"
    observation = PhysicalLightObservation("light.demo_main", "on", _NOW_MS, "session-a", 1, "synthetic", 1)
    state = [ObservedLightState(native, observation)]
    reader = lambda entity: state[0] if entity == "light.demo_main" else case.hass.states.get(entity)
    case.runtime._state_reader = reader
    case.runtime._state_provider._state_reader = reader
    try:
        assert (await case.runtime.async_force_auto(_ROOM_ID, reason="owner"))["outcome"] == "returned"
        assert has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        assert latest_ownership(case.runtime._control_ownership_for(config), "light_main").handoff.observer_session == "session-a"
        if change == "session":
            state[0] = replace(state[0], observation=replace(observation, session="session-b"))
        elif change == "cached":
            native.attributes["cached"] = True
        elif change == "unknown":
            native.state = "unknown"
        else:
            case.runtime._unobserved_since = _NOW_MS + 1
        assert not has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        state[0] = ObservedLightState(native, observation)
        native.state = "on"
        native.attributes.clear()
        case.runtime._unobserved_since = None
        assert not has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("participant", ["authority", "ownership", "priority", "off", "commit"])
@pytest.mark.parametrize("action", ["on", "off", "brightness", "color"])
async def test_new_manual_during_on_handoff_wins_at_every_store(participant, action):
    case, config = await enabled_case()
    fired = False

    async def intervene(_payload):
        nonlocal fired
        if fired:
            return
        fired = True
        case.authority.capture("light.demo_main", "off" if action == "off" else "on", "service", "new-" + action,
                               observed_at_ms=_NOW_MS)
        case.runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS, turned_off=action == "off")
        await asyncio.sleep(0)

    stores = {"authority": case.store, "ownership": case.ownership_store,
              "priority": case.priority._store, "off": case.off._store, "commit": case.store}
    setattr(stores[participant], "prepare_hook" if participant == "commit" else "hook", intervene)
    try:
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="owner")
        assert fired and result["outcome"] != "returned", result
        assert case.authority.blocks("light.demo_main")
        assert not has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        assert not case.executor.calls
    finally:
        for store in stores.values():
            store.hook = store.prepare_hook = None
        await case.runtime.stop()


@pytest.mark.parametrize("change", ["manual", "config", "service", "restart", "unknown", "gap"])
async def test_committed_handoff_cannot_survive_new_intent_or_gap(change):
    case, config = await enabled_case()
    try:
        assert (await case.runtime.async_force_auto(_ROOM_ID, reason="owner"))["outcome"] == "returned"
        assert has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        if change == "manual":
            case.runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS)
        elif change == "config":
            case.runtime._configs[_ROOM_ID] = replace(config, version=config.version + 1)
        elif change == "service":
            case.runtime._service._configs[_ROOM_ID] = replace(config, version=config.version + 1)
        elif change == "restart":
            case.runtime._ownership.restore(copy.deepcopy(case.ownership_store.payload))
        elif change == "unknown":
            case.hass.states.set("light.demo_main", "unknown", last_changed=datetime.fromtimestamp(_NOW_MS / 1000, timezone.utc))
        else:
            case.runtime._unobserved_since = _NOW_MS + 1
        assert not has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        if change == "restart":
            assert latest_ownership(case.runtime._control_ownership_for(config), "light_main").source is OwnershipSource.UNKNOWN
        # Status reads rebuild equal documents, but must never resurrect a
        # delegation cancelled by manual intent, generation or observation.
        calls_before = len(case.executor.calls)
        for _ in range(2):
            context = await case.runtime.async_context_for(copy.deepcopy(case.runtime._configs[_ROOM_ID]))
            assert not has_automatic_control(context.ownership, "light_main")
        assert len(case.executor.calls) == calls_before
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("participant", ["ownership", "priority", "off", "commit"])
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_pending_on_handoff_never_grants_control_on_storage_failure(participant, failure):
    case, config = await enabled_case()

    async def fail(_payload):
        assert not has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        raise asyncio.CancelledError() if failure == "cancel" else OSError("synthetic failure")

    stores = {"ownership": case.ownership_store, "priority": case.priority._store,
              "off": case.off._store, "commit": case.store}
    setattr(stores[participant], "prepare_hook" if participant == "commit" else "hook", fail)
    try:
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await case.runtime.async_force_auto(_ROOM_ID, reason="owner")
        else:
            assert (await case.runtime.async_force_auto(_ROOM_ID, reason="owner"))["outcome"] == "incomplete"
        assert not has_automatic_control(case.runtime._control_ownership_for(config), "light_main")
        assert case.authority.blocks("light.demo_main")
        assert not case.executor.calls
    finally:
        for store in stores.values():
            store.hook = store.prepare_hook = None
        await case.runtime.stop()
