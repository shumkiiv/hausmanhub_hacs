"""The real manual transaction must survive the new observation await."""

import asyncio
import weakref
from dataclasses import replace

import pytest

from tests.test_room_lighting_force_auto import make_case
from tests.test_room_lighting_runtime import _ROOM_ID, _NOW_DT, _NOW_MS


async def unknown_case():
    case = await make_case("on", protected=True)
    case.hass.states.set("light.demo_main", "unknown", last_changed=_NOW_DT)
    return case


async def test_refresh_then_original_transaction_releases_all_holds_without_auto_on_claim():
    case = await unknown_case()
    calls = []

    async def refresh(entities, current):
        calls.append(entities)
        assert current()
        case.hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
        return True

    case.runtime._observation_refresher = refresh
    try:
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        assert result["outcome"] == "returned", result
        assert "light.demo_main" in calls[0] and len(calls) == 1
        assert not case.authority.blocks("light.demo_main")
        assert not case.priority.manual_entity_ids()
        assert case.off.snapshot()["protections"] == []
        assert not case.executor.calls
        assert not case.runtime._explicit_return_active
    finally:
        await case.runtime.stop()


async def test_fresh_return_never_calls_refresh():
    case = await make_case("on")

    async def forbidden(*_args):
        pytest.fail("fresh return must not poll")

    case.runtime._observation_refresher = forbidden
    try:
        assert (await case.runtime.async_force_auto(_ROOM_ID, reason="test"))["outcome"] == "returned"
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("participant", ["priority", "off"])
async def test_refresh_retains_original_record_and_rejects_equal_replacement(participant):
    class WeakRecord(dict):
        pass

    case = await unknown_case()
    records = (case.priority._manual_records if participant == "priority"
               else case.off._protections)
    key = next(iter(records))
    records[key] = WeakRecord(records[key])
    original = weakref.ref(records[key])

    async def refresh(_entities, current):
        assert current()
        replacement = dict(records[key])
        del records[key]
        # The refresh fence, not this test, must retain the original identity.
        assert original() is not None
        records[key] = replacement
        assert not current()
        case.hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
        return True

    case.runtime._observation_refresher = refresh
    try:
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        assert result["outcome"] == "blocked"
        assert result["blockers"] == ["manual_action_or_observation_changed"]
        assert case.authority.blocks("light.demo_main")
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("change", ["manual", "ownership", "priority", "priority_replace", "off", "config", "stop"])
async def test_original_intent_cannot_consume_change_during_refresh(change):
    case = await unknown_case()

    async def refresh(entities, current):
        assert current()
        if change == "manual":
            await case.authority.async_capture("light.demo_main", "off", "service", "new")
        elif change == "ownership":
            case.runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS, turned_off=True)
        elif change == "priority":
            case.priority._manual_records["light.demo_main"]["confirmedAt"] += 1
        elif change == "priority_replace":
            case.priority._manual_records["light.demo_main"] = dict(
                case.priority._manual_records["light.demo_main"])
        elif change == "off":
            await case.off.async_note_state_transition("light.demo_main",
                type("State", (), {"state": "on"})(), type("State", (), {"state": "off"})(), None)
        elif change == "config":
            cfg = case.runtime._configs[_ROOM_ID]
            case.runtime._configs[_ROOM_ID] = replace(cfg, version=cfg.version + 1)
        else:
            await case.runtime.stop()
        assert not current()
        case.hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
        return True

    case.runtime._observation_refresher = refresh
    try:
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        assert result["outcome"] == "blocked", result
        assert result["blockers"] == ["manual_action_or_observation_changed"]
        assert case.authority.blocks("light.demo_main")
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("failure", ["false", "error", "cancel", "sensor"])
async def test_failed_refresh_preserves_holds_and_never_retries(failure):
    case = await unknown_case()
    calls = []

    async def refresh(_entities, _current):
        calls.append(1)
        if failure == "error":
            raise OSError("offline")
        if failure == "cancel":
            raise asyncio.CancelledError
        case.hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
        if failure == "sensor":
            case.hass.states.set("binary_sensor.demo_presence", "unknown", last_changed=_NOW_DT)
        return failure != "false"

    case.runtime._observation_refresher = refresh
    try:
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await case.runtime.async_force_auto(_ROOM_ID, reason="test")
        else:
            result = await case.runtime.async_force_auto(_ROOM_ID, reason="test")
            assert result["outcome"] == "blocked"
            assert result["blockers"] == ["observations_unavailable"]
        assert calls == [1] and not case.executor.calls
        assert case.authority.blocks("light.demo_main")
        assert not case.runtime._explicit_return_active
    finally:
        await case.runtime.stop()


async def test_double_click_busy_until_refresh_has_finished():
    case = await unknown_case()

    async def refresh(_entities, _current):
        second = await case.runtime.async_force_auto(_ROOM_ID, reason="second")
        assert second["blockers"] == ["return_busy"]
        case.hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
        return True

    case.runtime._observation_refresher = refresh
    try:
        assert (await case.runtime.async_force_auto(_ROOM_ID, reason="first"))["outcome"] == "returned"
    finally:
        await case.runtime.stop()
