"""Confirmed AUTO keeps bounded physical recovery without gaining authority."""

import asyncio
from dataclasses import replace

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import LIGHT_ENTITY
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction, evaluate_curve_room
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    OwnershipSource, SensorState, latest_ownership,
)
from tests.test_lighting_mqtt_observations import feed, report
from tests.test_stale_handoff_refresh import stale_case


async def auto_case(*, prepare=None):
    case = await stale_case(prepare=prepare)
    runtime, config, clock, *_ = case
    runtime._ownership.record_auto(config.room_id, 'light_main', at=clock['now'] - 300_000)
    key = (config.room_id, 'light_main')
    runtime._dispatch_failures[key] = ('set_brightness', 'untrusted:on', clock['now'] - 1000)
    assert latest_ownership(runtime._control_ownership_for(config), 'light_main').source is OwnershipSource.AUTO
    assert runtime._target_attempt_blocked(key, LIGHT_ENTITY, LightAction.SET_BRIGHTNESS)
    return case


@pytest.mark.parametrize('absence_seconds,brightness', [(0, 100), (600, 15)])
async def test_auto_genuine_reports_release_original_guard_and_recalculate(absence_seconds, brightness):
    runtime, config, clock, _, _, calls, processed, _ = await auto_case()
    try:
        records = runtime._control_ownership_for(config)
        await runtime.async_process([config.room_id])
        assert len(calls) == 4
        assert processed
        assert runtime._control_ownership_for(config) == records
        context = await runtime._build_context(config)
        decision = evaluate_curve_room(config, context, presence=SensorState.OFF,
            presence_confirmed=not absence_seconds, absence_seconds=absence_seconds)
        command = next(c for c in decision.commands
            if c.action is LightAction.SET_BRIGHTNESS and c.target_id == 'light_main')
        expected = config.dimming.target_percent if absence_seconds else brightness
        assert command.brightness == expected
        assert not runtime._target_attempt_blocked((config.room_id, 'light_main'),
            LIGHT_ENTITY, LightAction.SET_BRIGHTNESS)
        assert runtime._adjustment_dispatch_block_reason(config, command, LIGHT_ENTITY,
            runtime._target_evidence_revision(LIGHT_ENTITY), dependencies=None,
            exact_active=False, operation_moment=clock['now']) is None
        for _ in range(4):
            await runtime.async_process([config.room_id])
        assert len(calls) == 4
    finally:
        await runtime.stop()


@pytest.mark.parametrize('change', ['manual_on', 'manual_off', 'config', 'stop', 'gap',
                                   'dependencies', 'false', 'error'])
async def test_auto_refresh_cancels_when_original_conditions_change(change):
    runtime, config, clock, _, _, calls, processed, authority = await auto_case()

    async def refresh(entities, current):
        calls.append('read')
        assert current()
        if change.startswith('manual_'):
            await authority.async_capture(LIGHT_ENTITY, change.removeprefix('manual_'),
                                          'service', 'new-manual-action')
        elif change == 'config':
            runtime._configs[config.room_id] = replace(config, version=config.version + 1)
        elif change == 'stop':
            await runtime.stop()
        elif change == 'gap':
            runtime._unobserved_since = clock['now']
        elif change == 'dependencies':
            runtime._power_dependencies = lambda: {'changed': object()}
        elif change == 'error':
            raise OSError('offline')
        if change != 'false':
            assert not current()
        return change != 'false'

    runtime._observation_refresher = refresh
    try:
        await runtime.async_process([config.room_id])
        assert calls == ['read']
        assert not processed
        assert runtime._target_attempt_blocked((config.room_id, 'light_main'),
            LIGHT_ENTITY, LightAction.SET_BRIGHTNESS)
    finally:
        await runtime.stop()


@pytest.mark.parametrize('result', ['false', 'error', 'cached_success'])
async def test_auto_failed_or_nonphysical_read_keeps_guard_and_is_bounded(result):
    runtime, config, _, _, _, calls, _, _ = await auto_case()

    async def refresh(*_):
        calls.append('read')
        if result == 'error':
            raise OSError('offline')
        return result == 'cached_success'

    runtime._observation_refresher = refresh
    try:
        for _ in range(5):
            await runtime.async_process([config.room_id])
        assert runtime._target_attempt_blocked((config.room_id, 'light_main'),
            LIGHT_ENTITY, LightAction.SET_BRIGHTNESS)
        # A dishonest success cannot make evidence fresh. The real port only
        # succeeds after physical reports; this test also checks the retry cap.
        assert calls == ['read']
    finally:
        await runtime.stop()


async def test_auto_parallel_processing_does_not_use_partial_reports():
    runtime, config, _, _, _, calls, processed, _ = await auto_case()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def refresh(*_):
        calls.append('read')
        entered.set()
        await finish.wait()
        return False

    runtime._observation_refresher = refresh
    task = asyncio.create_task(runtime.async_process([config.room_id]))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await runtime.async_process([config.room_id])
        assert calls == ['read'] and not processed
    finally:
        finish.set()
        await task
        await runtime.stop()


@pytest.mark.parametrize('condition', ['manual', 'shadow', 'fresh', 'unconfirmed', 'unknown',
                                      'missing_owner', 'restart'])
async def test_auto_ineligible_state_never_requests_read(condition):
    runtime, config, clock, port, _, calls, _, authority = await auto_case()
    if condition == 'manual':
        await authority.async_capture(LIGHT_ENTITY, 'on', 'service', 'manual-before-read')
    elif condition == 'shadow':
        runtime._configs[config.room_id] = replace(config, commands_enabled=False)
    elif condition == 'fresh':
        feed(port, report(clock, channel=3, session=port.read_state(LIGHT_ENTITY).observation.session,
                          sequence=port._sequence + 1, value='ON'))
    elif condition == 'unconfirmed':
        runtime._ownership.record_auto(config.room_id, 'light_main', at=clock['now'], confirmed=False)
    elif condition == 'unknown':
        runtime._ownership._records[(config.room_id, 'light_main')] = [replace(
            latest_ownership(runtime._control_ownership_for(config), 'light_main'),
            source=OwnershipSource.UNKNOWN)]
    elif condition == 'missing_owner':
        runtime._ownership._records[(config.room_id, 'light_main')] = []
    elif condition == 'restart':
        authority.begin_startup_observation()
    try:
        await runtime.async_process([config.room_id])
        assert not calls
    finally:
        await runtime.stop()


async def test_auto_new_report_rearms_after_later_expiry():
    runtime, config, clock, _, _, calls, _, _ = await auto_case()
    try:
        await runtime.async_process([config.room_id])
        assert len(calls) == 4
        clock['now'] += 300_001
        runtime._hass.states.set('binary_sensor.demo_presence', 'off')
        await runtime.async_process([config.room_id])
        assert len(calls) == 8
    finally:
        await runtime.stop()
