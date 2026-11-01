"""Configured return is a proof of expiry, not a physical device command."""
from dataclasses import replace
import pytest
from custom_components.hausman_hub.application.timed_manual_return import ManualClaim, timed_manual_return
from tests.test_room_lighting_domain import _payload
from custom_components.hausman_hub.domain.room_lighting import config_from_payload


async def make_timed_case(action, *, protected=False):
    from tests.test_room_lighting_force_auto import make_case, _ROOM_ID, _NOW_MS
    from custom_components.hausman_hub.domain.room_lighting import ReleaseMode
    case = await make_case(action, protected=protected)
    await case.authority.async_capture("light.demo_main", action, "service", "timed-fixture",
                                       observed_at_ms=_NOW_MS)
    cfg = case.runtime._configs[_ROOM_ID]
    case.runtime._configs[_ROOM_ID] = replace(cfg, manual_off_protection=replace(
        cfg.manual_off_protection, release_mode=ReleaseMode.TIMER_ONLY))
    return case


@pytest.mark.parametrize("action,seconds", [("off", 600), ("on", 3600)])
def test_exact_boundary_and_custom_duration(action, seconds):
    config = config_from_payload(_payload())
    claims = [ManualClaim("light.demo", action, 100000)]
    assert not timed_manual_return(config, claims, 100000 + seconds * 1000 - 1).due
    assert timed_manual_return(config, claims, 100000 + seconds * 1000).due
    config = replace(config, manual_control_return=replace(config.manual_control_return, priority_seconds=15),
                     manual_off_protection=replace(config.manual_off_protection, minimum_interval_seconds=15))
    assert timed_manual_return(config, claims, 115000).due


def test_latest_direction_wins_but_linked_newer_on_holds_group():
    config = config_from_payload(_payload())
    claims = [ManualClaim("light.demo", "on", 100000), ManualClaim("light.demo", "off", 200000)]
    assert timed_manual_return(config, claims, 800000).due
    claims.append(ManualClaim("light.peer", "on", 300000))
    assert not timed_manual_return(config, claims, 800000).due
    assert timed_manual_return(config, claims, 3900000).due


@pytest.mark.parametrize("claims", [[], [ManualClaim("light.demo", "unknown", 100000)],
    [ManualClaim("light.demo", "on", 0)], [ManualClaim("light.demo", "on", 9999999)],
    [ManualClaim("light.demo", "on", 100000), ManualClaim("light.demo", "off", 100000)]])
def test_unknown_empty_future_or_opposed_claims_never_grant_return(claims):
    assert not timed_manual_return(config_from_payload(_payload()), claims, 4000000).due


def test_away_respects_setting_and_rejects_new_gesture_after_boundary():
    config = config_from_payload(_payload())
    claims = [ManualClaim("light.demo", "on", 100000)]
    assert timed_manual_return(config, claims, 100001, away_boundary_ms=100000).due
    assert not timed_manual_return(config, claims, 100001, away_boundary_ms=99999).due
    config = replace(config, manual_control_return=replace(config.manual_control_return, clear_on_away=False))
    assert not timed_manual_return(config, claims, 100001, away_boundary_ms=100000).due


def test_disabled_away_clear_does_not_suspend_the_regular_expiry_timer():
    config = config_from_payload(_payload())
    config = replace(config, manual_control_return=replace(config.manual_control_return, clear_on_away=False))
    claims = [ManualClaim("light.demo", "off", 100000)]
    assert timed_manual_return(config, claims, 700000, away_boundary_ms=200000).due
    # A gesture after departure still has its ordinary deadline.
    config = replace(config, manual_control_return=replace(config.manual_control_return, clear_on_away=True))
    assert timed_manual_return(config, claims, 700000, away_boundary_ms=99999).due


def test_disabling_manual_off_protection_does_not_hold_off_for_ten_minutes():
    config = config_from_payload(_payload())
    config = replace(config, manual_off_protection=replace(config.manual_off_protection, enabled=False))
    assert timed_manual_return(config, [ManualClaim("light.demo", "off", 100000)], 100001).due
    assert not timed_manual_return(config, [ManualClaim("light.demo", "on", 100000)], 100001).due
    assert not timed_manual_return(config, [ManualClaim("light.demo", "unknown", 100000)], 100001).due
    assert not timed_manual_return(config, [ManualClaim("light.demo", "off", 100000),
                                             ManualClaim("light.other", "on", 100000)], 100001).due


async def test_disabled_off_protection_releases_all_manual_stores_without_a_command():
    from datetime import datetime, timezone
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS
    case = await make_timed_case("off", protected=True)
    try:
        for record in case.off._protections.values():
            record["startedAt"] = datetime.fromtimestamp(_NOW_MS / 1000, timezone.utc).isoformat()
        cfg = case.runtime._configs[_ROOM_ID]
        case.runtime._configs[_ROOM_ID] = replace(cfg, manual_off_protection=replace(
            cfg.manual_off_protection, enabled=False))
        now = _NOW_MS + 1000
        case.runtime._now_ms = lambda: now
        case.hass.states.set("light.demo_main", "off", last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))
        assert await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert not case.authority.record("light.demo_main")
        assert not case.off.snapshot()["protections"]
        assert not case.priority.manual_entity_ids()
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("action,seconds", [("off", 600), ("on", 3600)])
async def test_runtime_timer_returns_all_histories_without_recursive_evaluation(action, seconds):
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS
    case = await make_timed_case(action)
    try:
        now = [_NOW_MS + (seconds - 1) * 1000]
        case.runtime._now_ms = lambda: now[0]
        assert not await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert case.authority.record("light.demo_main") is not None
        now[0] += 1000
        # A fresh light report is enough for the handoff; old PIR isn't absence.
        from datetime import datetime, timezone
        case.hass.states.set("light.demo_main", "on" if action == "on" else "off",
                             last_changed=datetime.fromtimestamp(now[0]/1000, timezone.utc))
        assert await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert case.authority.record("light.demo_main") is None
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


async def test_new_gesture_while_timer_persists_never_loses_priority():
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS
    case = await make_timed_case("off")
    try:
        now = _NOW_MS + 600000
        case.runtime._now_ms = lambda: now
        from datetime import datetime, timezone
        case.hass.states.set("light.demo_main", "off", last_changed=datetime.fromtimestamp(now/1000, timezone.utc))
        fired = False
        async def intervene(_payload):
            nonlocal fired
            if not fired:
                fired = True
                case.authority.capture("light.demo_main", "on", "service", "new", observed_at_ms=now)
        case.store.hook = intervene
        assert not await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert fired and case.authority.record("light.demo_main").fingerprint == "new"
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


async def test_sensor_loss_during_off_return_keeps_manual_priority():
    from datetime import datetime, timezone
    from custom_components.hausman_hub.domain.room_lighting import ReleaseMode
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS
    case = await make_timed_case("off")
    try:
        now = _NOW_MS + 600_000
        case.runtime._now_ms = lambda: now
        cfg = case.runtime._configs[_ROOM_ID]
        cfg = replace(cfg, manual_off_protection=replace(
            cfg.manual_off_protection, release_mode=ReleaseMode.TIMER_AND_ABSENCE))
        case.runtime._configs[_ROOM_ID] = cfg
        case.runtime._absence_release_latch.add(_ROOM_ID)
        stamp = datetime.fromtimestamp(now / 1000, timezone.utc)
        case.hass.states.set("light.demo_main", "off", last_changed=stamp)
        case.hass.states.set("binary_sensor.demo_presence", "off", last_changed=stamp)
        fired = False

        async def lose_sensor(_payload):
            nonlocal fired
            if not fired:
                fired = True
                case.hass.states.set("binary_sensor.demo_presence", "unavailable", last_changed=stamp)

        case.store.hook = lose_sensor
        assert not await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert fired
        assert case.authority.record("light.demo_main") is not None
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


async def test_fresh_sensor_allows_off_return_after_proven_absence():
    from datetime import datetime, timezone
    from custom_components.hausman_hub.domain.room_lighting import ReleaseMode
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS
    case = await make_timed_case("off")
    try:
        now = _NOW_MS + 600_000
        case.runtime._now_ms = lambda: now
        cfg = case.runtime._configs[_ROOM_ID]
        case.runtime._configs[_ROOM_ID] = replace(cfg, manual_off_protection=replace(
            cfg.manual_off_protection, release_mode=ReleaseMode.TIMER_AND_ABSENCE))
        case.runtime._absence_release_latch.add(_ROOM_ID)
        stamp = datetime.fromtimestamp(now / 1000, timezone.utc)
        case.hass.states.set("light.demo_main", "off", last_changed=stamp)
        case.hass.states.set("binary_sensor.demo_presence", "off", last_changed=stamp)
        assert await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert case.authority.record("light.demo_main") is None
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("clear_on_away", [True, False])
async def test_confirmed_away_clears_only_enabled_rooms_and_home_preserves_false(clear_on_away):
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS
    case = await make_timed_case("on")
    try:
        config = case.runtime._configs[_ROOM_ID]
        case.runtime._configs[_ROOM_ID] = replace(config, manual_control_return=replace(
            config.manual_control_return, clear_on_away=clear_on_away))
        case.runtime._away_debounce_seconds = 0
        case.runtime._current_away_state = lambda *_: True
        await case.runtime._async_away_after_debounce(case.runtime._away_generation)
        assert (case.authority.record("light.demo_main") is None) is clear_on_away
        case.runtime._release_away()
        import asyncio
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert (case.authority.record("light.demo_main") is None) is clear_on_away
    finally:
        await case.runtime.stop()


async def test_off_expiry_clears_all_four_histories_with_old_generic_priority_marker():
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS
    from datetime import datetime, timezone
    case = await make_timed_case("off", protected=True)
    try:
        for record in case.off._protections.values():
            record["startedAt"] = datetime.fromtimestamp(_NOW_MS/1000, timezone.utc).isoformat()
        now = _NOW_MS + 600000
        case.runtime._now_ms = lambda: now
        case.hass.states.set("light.demo_main", "off", last_changed=datetime.fromtimestamp(now/1000, timezone.utc))
        assert await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert not case.priority.manual_entity_ids()
        assert not case.off.snapshot()["protections"]
        assert not case.authority.record("light.demo_main")
    finally:
        await case.runtime.stop()


async def test_off_profile_supplies_direction_when_only_generic_priority_marker_remains():
    from tests.test_room_lighting_force_auto import make_case, _ROOM_ID, _NOW_MS
    from datetime import datetime, timezone
    case = await make_case(None, protected=True)
    try:
        for record in case.off._protections.values():
            record["startedAt"] = datetime.fromtimestamp(_NOW_MS/1000, timezone.utc).isoformat()
        config = case.runtime._configs[_ROOM_ID]
        claims = case.runtime._timed_manual_claims(config)
        assert claims and {claim.action for claim in claims} == {"off"}
        assert timed_manual_return(config, claims, _NOW_MS + 600000).due
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("action,seconds", [("off", 600), ("on", 3600)])
async def test_saved_gesture_keeps_original_deadline_across_restart(monkeypatch, action, seconds):
    import time
    from datetime import datetime, timezone
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS, _make_runtime
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    now = [_NOW_MS]
    monkeypatch.setattr(time, "time", lambda: now[0]/1000)
    case = await make_timed_case(action)
    await case.runtime.stop()
    now[0] += (seconds-1)*1000
    restored = ManualLightAuthority(case.store)
    await restored.async_load()
    restored.begin_startup_observation()
    runtime = _make_runtime(case.hass, ownership_store=case.ownership_store,
        manual_light_authority=restored, light_priority=case.priority, executor=case.executor)
    await runtime.start(case.hass, "restarted-timer")
    restored.finish_startup_observation(frozenset({"light.demo_main"}))
    try:
        config = runtime._configs[_ROOM_ID]
        runtime._configs[_ROOM_ID] = replace(config, manual_off_protection=case.runtime._configs[_ROOM_ID].manual_off_protection)
        runtime._now_ms = lambda: now[0]
        assert not await runtime._async_maybe_return_manual(_ROOM_ID)
        assert restored.record("light.demo_main").observed_at_ms == _NOW_MS
        now[0] += 1000
        case.hass.states.set("light.demo_main", action, last_changed=datetime.fromtimestamp(now[0]/1000, timezone.utc))
        assert await runtime._async_maybe_return_manual(_ROOM_ID)
        assert restored.record("light.demo_main") is None
        assert not case.executor.calls
    finally:
        await runtime.stop()


@pytest.mark.parametrize("change", ["new_priority", "config", "storage", "pending"])
async def test_timer_refuses_changed_frontier_or_failed_storage(change):
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS
    from datetime import datetime, timezone
    case = await make_timed_case("off")
    try:
        now = _NOW_MS + 600000; case.runtime._now_ms = lambda: now
        case.hass.states.set("light.demo_main", "off", last_changed=datetime.fromtimestamp(now/1000, timezone.utc))
        async def intervene(_):
            if change == "new_priority":
                case.priority._manual_records["light.demo_main"] = {"entityId":"light.demo_main", "confirmedAt":now, "ownership":"manual"}
            elif change == "config":
                config = case.runtime._configs[_ROOM_ID]
                case.runtime._configs[_ROOM_ID] = replace(config, version=config.version+1)
            elif change == "storage":
                raise OSError("synthetic store failure")
        if change == "pending":
            case.authority._explicit_return_pending = True
        else:
            case.store.hook = intervene
        assert not await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert not case.executor.calls
        if change in {"storage", "pending"}:
            assert case.authority.barrier_reason in {"storage_unavailable", "return_pending"}
            assert not await case.runtime._async_maybe_return_manual(_ROOM_ID)
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("light", ["unknown", "unavailable"])
async def test_expiry_never_treats_unknown_light_as_off(light):
    from tests.test_room_lighting_force_auto import _ROOM_ID, _NOW_MS
    from datetime import datetime, timezone
    case = await make_timed_case("off")
    try:
        now = _NOW_MS + 600000; case.runtime._now_ms = lambda: now
        case.hass.states.set("light.demo_main", light, last_changed=datetime.fromtimestamp(now/1000, timezone.utc))
        assert not await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert case.authority.record("light.demo_main") is not None
    finally:
        await case.runtime.stop()
