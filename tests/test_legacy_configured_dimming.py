"""Saved dimming settings work without migrating the legacy time modes."""
from dataclasses import replace

import pytest

from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightAction, SensorState, evaluate_curve_room,
)
from tests.test_room_lighting_curve_params_runtime import _payload, TestHoldCommandPlan as HoldFixture


def configured(*, enabled=True, on_absence=True, target=5, fade=20):
    payload = _payload()
    payload.update(profile='day_curve', curveParams={'eveningMaxPercent': 40,
        'neutralKelvin': 3278, 'eveningWarmKelvin': 3003, 'presenceConfirmSeconds': 3})
    payload['dimming'] = {'enabled': enabled, 'onAbsence': on_absence,
                          'targetPercent': target, 'fadeSeconds': fade}
    return config_from_payload(payload)


def context(*, hour=14, actual=100):
    ctx = HoldFixture()._room_context(chandelier=SensorState.ON,
                                            mirror=SensorState.OFF, hour=hour)
    return replace(ctx, lights=tuple(replace(light, brightness=actual,
        color_temperature=3278) if light.target_id == 'light_demo_main' else light
        for light in ctx.lights))


def plan(config, *, absence=0, presence=SensorState.OFF, confirmed=False,
         hour=14, actual=100, **flags):
    return evaluate_curve_room(config, context(hour=hour, actual=actual),
        presence=presence, presence_confirmed=confirmed, absence_seconds=absence, **flags)


def levels(decision):
    return [command for command in decision.commands
            if command.action is LightAction.SET_BRIGHTNESS]


@pytest.mark.parametrize('absence', [0, 299, 300, 599])
def test_no_early_decline_before_ten_minutes(absence):
    assert not levels(plan(configured(), absence=absence))


@pytest.mark.parametrize('absence', [600, 601, 610, 620, 1200])
def test_one_final_goal_with_configured_transition_not_intermediate_percent(absence):
    commands = levels(plan(configured(), absence=absence))
    assert len(commands) == 1
    assert (commands[0].brightness, commands[0].fade_seconds) == (5, 20)


@pytest.mark.parametrize('enabled,on_absence', [(False, True), (True, False), (False, False)])
def test_disabled_flags_keep_current_mode(enabled, on_absence):
    assert not levels(plan(configured(enabled=enabled, on_absence=on_absence), absence=1200))


@pytest.mark.parametrize('target,fade', [(3, 0), (10, 5), (40, 30)])
def test_saved_goal_and_duration_are_not_hardcoded(target, fade):
    command = levels(plan(configured(target=target, fade=fade), absence=700))[0]
    assert (command.brightness, command.fade_seconds) == (target, fade)


def test_unconfirmed_return_does_not_raise_reduced_light():
    assert not levels(plan(configured(), absence=720, presence=SensorState.ON, actual=5))


def test_confirmed_return_uses_existing_mode_and_return_duration():
    command = levels(plan(configured(), absence=720, presence=SensorState.ON,
                          confirmed=True, actual=5))[0]
    assert (command.brightness, command.fade_seconds) == (100, 10)


@pytest.mark.parametrize('presence', [SensorState.UNKNOWN, SensorState.UNAVAILABLE])
def test_unknown_presence_is_not_absence(presence):
    assert not levels(plan(configured(), absence=1200, presence=presence))


def test_decline_cannot_raise_above_lower_evening_mode():
    from datetime import time
    from custom_components.hausman_hub.domain.light_curve import time_mode_brightness
    from custom_components.hausman_hub.domain.room_lighting_engine import curve_profile_from_config
    command = levels(plan(configured(target=40), absence=700, hour=21.5, actual=100))[0]
    mode = time_mode_brightness(curve_profile_from_config(configured(target=40)),
                                time(21, 30), time(7), time(20))
    assert command.brightness == min(40, mode)


@pytest.mark.parametrize('hour', [8, 9.5, 12, 14, 19.5, 21.5, 22.5, 23.5])
def test_no_absence_keeps_existing_time_mode_commands(hour):
    from custom_components.hausman_hub.domain.light_curve import evaluate_light_curve
    from custom_components.hausman_hub.domain import room_lighting_engine as engine
    config = configured()
    ctx = context(hour=hour)
    original = engine.evaluate_light_curve
    calls = []

    def capture(profile, snapshot, **kwargs):
        value = evaluate_light_curve(profile, snapshot, **kwargs)
        calls.append((profile, snapshot, value))
        return value

    # Independent pure default and the new legacy adapter must retain the
    # exact ordinary mode away from the configured-absence branch.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(engine, 'evaluate_light_curve', capture)
        evaluate_curve_room(config, ctx, presence=SensorState.ON,
                            presence_confirmed=True, absence_seconds=0)
    profile, snapshot, value = calls[0]
    assert value == original(profile, snapshot)


async def test_transition_report_after_eight_seconds_keeps_strict_confirmation(monkeypatch):
    from tests.test_light_command_readback import harness, command, LightAction
    clock, _, _, calls, hass, executor, report = harness(monkeypatch, window=8)
    cmd = replace(command(LightAction.SET_BRIGHTNESS), brightness=5,
                  color_temperature=None, fade_seconds=20)
    clock.on_sleep = lambda: report(attributes={'brightness': 13}) if clock.seconds >= 20 else None
    result = await executor.execute(hass, cmd)
    assert result.confirmed
    assert 20 <= clock.seconds <= 28
    assert len(calls) == 1 and calls[0][2]['transition'] == 20


@pytest.mark.parametrize('change', ['none', 'on', 'unknown', 'config', 'gap', 'stop'])
@pytest.mark.parametrize('prior_seen_seconds', [0, 590])
async def test_one_absence_deadline_wakes_normal_evaluation_and_invalidates(change, prior_seen_seconds):
    import asyncio
    from custom_components.hausman_hub.application.room_lighting_runtime import RoomLightingRuntime
    from custom_components.hausman_hub.domain.room_lighting_engine import SensorSnapshot, SensorKind
    cfg = configured()
    cfg = replace(cfg, commands_enabled=True,
                  curve=replace(cfg.curve, presence_confirm_seconds=None))
    ctx = context()
    ctx = replace(ctx, sensors=(SensorSnapshot('sensor_demo_presence', SensorKind.PRESENCE,
                  SensorState.OFF, ctx.now, observed_at_ms=ctx.now),))
    clock = {'now': ctx.now}
    runtime = object.__new__(RoomLightingRuntime)
    runtime._curve_presence = ({cfg.room_id: {'since': ctx.now - prior_seen_seconds * 1000,
        'last_seen': ctx.now - prior_seen_seconds * 1000}} if prior_seen_seconds else {})
    runtime._legacy_curve_absence_tasks = {}
    runtime._legacy_curve_absence_owned = set()
    runtime._running = True
    runtime._configs = {cfg.room_id: cfg}
    runtime._now_ms = lambda: clock['now']
    runtime._create_task = asyncio.create_task
    finish = asyncio.Event()
    sleeps, processed = [], []

    async def sleep(seconds):
        sleeps.append(seconds)
        await finish.wait()

    async def process(rooms):
        processed.extend(rooms)

    runtime._legacy_absence_sleep = sleep
    runtime.async_process = process
    for _ in range(5):
        runtime._observe_curve_presence(cfg, ctx, ctx.now)
    await asyncio.sleep(0)
    tasks = tuple(item[2] for item in runtime._legacy_curve_absence_tasks.values())
    try:
        assert sleeps == [600]
        assert len(tasks) == 1
        if change in {'on', 'unknown'}:
            state = SensorState.ON if change == 'on' else SensorState.UNKNOWN
            newer = replace(ctx, now=ctx.now + 1000,
                sensors=(replace(ctx.sensors[0], state=state,
                                 last_changed=ctx.now + 1000, observed_at_ms=ctx.now + 1000),))
            runtime._observe_curve_presence(cfg, newer, newer.now)
        elif change == 'config':
            runtime._configs[cfg.room_id] = replace(cfg, version=cfg.version + 1)
        elif change == 'gap':
            runtime._curve_presence.clear()
        elif change == 'stop':
            runtime._running = False
        clock['now'] += 600_000
        finish.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert processed == ([cfg.room_id] if change == 'none' else [])
        assert not runtime._legacy_curve_absence_tasks
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize('window,fade,expected', [(0, 20, 0), (8, 20, 28),
                                                (8, 300, 30), (30, 20, 30)])
async def test_transition_wait_stays_bounded_and_rejects_unrelated_report(monkeypatch, window, fade, expected):
    from types import SimpleNamespace
    from tests.test_light_command_readback import harness, command
    clock, _, _, calls, hass, executor, report = harness(monkeypatch, window=window)
    cmd = replace(command(LightAction.SET_BRIGHTNESS), brightness=5,
                  color_temperature=None, fade_seconds=fade)
    clock.on_sleep = lambda: report(context=SimpleNamespace(id='unrelated', user_id=None),
                                    attributes={'brightness': 13})
    result = await executor.execute(hass, cmd)
    assert not result.confirmed
    assert clock.seconds == expected and len(calls) == 1


async def test_manual_hold_during_extended_transition_wait_stops_confirmation(monkeypatch):
    from types import SimpleNamespace
    from tests.test_light_command_readback import harness, command
    blocked = [False]
    authority = SimpleNamespace(blocks=lambda _entity: blocked[0])
    clock, _, _, calls, hass, executor, report = harness(monkeypatch, window=8, authority=authority)
    cmd = replace(command(LightAction.SET_BRIGHTNESS), brightness=5,
                  color_temperature=None, fade_seconds=20)

    def late_manual():
        if clock.seconds >= 10:
            blocked[0] = True
            report(attributes={'brightness': 13})

    clock.on_sleep = late_manual
    result = await executor.execute(hass, cmd)
    assert not result.confirmed
    assert clock.seconds == 10 and len(calls) == 1


async def test_real_runtime_stop_cancels_and_drains_owned_absence_task():
    import asyncio
    from tests.test_stale_auto_refresh import auto_case
    from custom_components.hausman_hub.domain.room_lighting_engine import SensorSnapshot, SensorKind
    runtime, _, clock, *_ = await auto_case()
    cfg = replace(configured(), commands_enabled=True)
    runtime._configs[cfg.room_id] = cfg
    ctx = replace(context(), now=clock['now'], sensors=(SensorSnapshot('sensor_demo_presence',
        SensorKind.PRESENCE, SensorState.OFF, clock['now'], observed_at_ms=clock['now']),))
    finish = asyncio.Event()

    async def wait(_seconds):
        await finish.wait()

    runtime._legacy_absence_sleep = wait
    runtime._observe_curve_presence(cfg, ctx, ctx.now)
    await asyncio.sleep(0)
    tasks = [item[2] for item in runtime._legacy_curve_absence_tasks.values()]
    try:
        assert tasks
        await runtime.stop()
        assert all(task.done() for task in tasks)
        assert not runtime._legacy_curve_absence_tasks
    finally:
        await runtime.stop()


async def test_stop_drains_due_deadline_already_inside_normal_process():
    import asyncio
    from tests.test_stale_auto_refresh import auto_case
    from custom_components.hausman_hub.domain.room_lighting_engine import SensorSnapshot, SensorKind
    runtime, _, clock, *_ = await auto_case()
    cfg = replace(configured(), commands_enabled=True)
    runtime._configs[cfg.room_id] = cfg
    ctx = replace(context(), now=clock['now'], sensors=(SensorSnapshot('sensor_demo_presence',
        SensorKind.PRESENCE, SensorState.OFF, clock['now'], observed_at_ms=clock['now']),))
    due, entered, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    writes = []

    async def wait(_seconds):
        await due.wait()

    async def process(_rooms):
        entered.set()
        await finish.wait()
        writes.append('late ownership write')

    runtime._legacy_absence_sleep = wait
    runtime.async_process = process
    runtime._observe_curve_presence(cfg, ctx, ctx.now)
    tasks = [item[2] for item in runtime._legacy_curve_absence_tasks.values()]
    try:
        clock['now'] += 600_000
        due.set()
        await asyncio.wait_for(entered.wait(), 1)
        await runtime.stop()
        assert all(task.done() for task in tasks)
        finish.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert not writes
    finally:
        finish.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.stop()
