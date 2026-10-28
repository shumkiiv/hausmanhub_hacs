"""Stale explicit handoff needs actual observations, never a bypass of trust."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import (
    LIGHT_ENTITY, READ_CHANNELS, SUPPORTED_ENTITIES,
)
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction, evaluate_curve_room
from custom_components.hausman_hub.domain.room_lighting_ownership import OwnershipSource, SensorState
from tests.test_lighting_mqtt_observations import ROLLOUT_BINDINGS, feed, report
from tests.test_scheduled_morning_refresh import morning_case


async def stale_case(*, prepare=None):
    runtime, config, clock, port, native, calls, processed, authority = await morning_case()
    clock['now'] = int(datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc).timestamp() * 1000)
    session = port.read_state(LIGHT_ENTITY).observation.session

    async def publish(topic, payload):
        import json
        calls.append((topic, json.loads(payload)))
        entity = next(row[0] for row in READ_CHANNELS
                      if (row[1], {row[2]: ''}) == calls[-1])
        channel = next(i for i, binding in enumerate(ROLLOUT_BINDINGS) if binding.entity_id == entity)
        native[entity].state = 'on'
        clock['now'] += 10
        feed(port, report(clock, channel=channel, session=session,
                          sequence=port._sequence + 1, value='ON'))

    runtime._observation_refresher._publish = publish
    assert await runtime._observation_refresher(SUPPORTED_ENTITIES, lambda: True)
    runtime._hass.states.set('binary_sensor.demo_presence', 'off',
        last_changed=datetime.fromtimestamp(clock['now'] / 1000, timezone.utc))
    if prepare is not None:
        prepare(runtime, config, clock)
    result = await runtime.async_force_auto(config.room_id, reason='test')
    assert result['outcome'] == 'returned', result
    assert any(record.source is OwnershipSource.EXPLICIT_HANDOFF
               for record in runtime._control_ownership_for(config))
    clock['now'] += 300_001
    runtime._hass.states.set('binary_sensor.demo_presence', 'off',
        last_changed=datetime.fromtimestamp(clock['now'] / 1000, timezone.utc))
    calls.clear()
    processed.clear()
    return runtime, config, clock, port, native, calls, processed, authority


async def test_stale_handoff_gets_fresh_physical_report_before_evaluation():
    runtime, config, clock, _, _, calls, processed, _ = await stale_case()
    try:
        ownership = runtime._control_ownership_for(config)
        assert not runtime._target_trust(LIGHT_ENTITY)[0]
        await runtime.async_process([config.room_id])
        assert len(calls) == 4
        assert runtime._target_trust(LIGHT_ENTITY)[0]
        assert processed
        assert runtime._control_ownership_for(config) == ownership
        context = await runtime._build_context(config)
        decision = evaluate_curve_room(config, context, presence=SensorState.OFF,
            presence_confirmed=False, absence_seconds=600)
        adjustment = next(c for c in decision.commands
                          if c.action is LightAction.SET_BRIGHTNESS and c.target_id == 'light_main')
        assert adjustment.brightness == config.dimming.target_percent
        assert runtime._adjustment_dispatch_block_reason(config, adjustment, LIGHT_ENTITY,
            runtime._target_evidence_revision(LIGHT_ENTITY), dependencies=None,
            exact_active=False, operation_moment=clock['now']) is None
        for _ in range(3):
            await runtime.async_process([config.room_id])
        assert len(calls) == 4
    finally:
        await runtime.stop()


@pytest.mark.parametrize('change', ['manual', 'config', 'stop', 'gap', 'false', 'error'])
async def test_invalidated_or_failed_refresh_never_evaluates_old_intent(change):
    runtime, config, clock, _, _, calls, processed, authority = await stale_case()

    async def refresh(_entities, current):
        calls.append('attempt')
        assert current()
        if change == 'manual':
            await authority.async_capture(LIGHT_ENTITY, 'off', 'service', 'new-gesture')
        elif change == 'config':
            runtime._configs[config.room_id] = replace(config, version=config.version + 1)
        elif change == 'stop':
            await runtime.stop()
        elif change == 'gap':
            runtime._unobserved_since = clock['now']
        elif change == 'error':
            raise OSError('offline')
        if change not in {'false', 'error'}:
            assert not current()
        return change != 'false'

    runtime._observation_refresher = refresh
    try:
        await runtime.async_process([config.room_id])
        assert calls == ['attempt']
        assert not processed
    finally:
        await runtime.stop()


async def test_failed_read_is_not_retried_by_each_tick():
    runtime, config, _, _, _, calls, processed, _ = await stale_case()

    async def refresh(*_args):
        calls.append('attempt')
        return False

    runtime._observation_refresher = refresh
    try:
        for _ in range(5):
            await runtime.async_process([config.room_id])
        assert calls == ['attempt']
        assert not processed
    finally:
        await runtime.stop()


async def test_parallel_tick_cannot_use_partial_reports_or_start_another_read():
    runtime, config, _, _, _, calls, processed, _ = await stale_case()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def refresh(*_args):
        calls.append('attempt')
        entered.set()
        await finish.wait()
        return False

    runtime._observation_refresher = refresh
    try:
        first = asyncio.create_task(runtime.async_process([config.room_id]))
        await asyncio.wait_for(entered.wait(), 1)
        await runtime.async_process([config.room_id])
        assert calls == ['attempt'] and not processed
        finish.set()
        await first
    finally:
        finish.set()
        await runtime.stop()


@pytest.mark.parametrize('condition', ['manual', 'shadow', 'fresh', 'missing_owner', 'restart'])
async def test_ineligible_state_does_not_request_physical_read(condition):
    runtime, config, clock, port, _, calls, _, authority = await stale_case()
    if condition == 'manual':
        await authority.async_capture(LIGHT_ENTITY, 'on', 'service', 'new-gesture')
    elif condition == 'shadow':
        runtime._configs[config.room_id] = replace(config, commands_enabled=False)
    elif condition == 'fresh':
        feed(port, report(clock, channel=3, session=port.read_state(LIGHT_ENTITY).observation.session,
            sequence=port._sequence + 1, value='ON'))
    elif condition == 'missing_owner':
        runtime._ownership.invalidate_handoffs(config.room_id)
    elif condition == 'restart':
        authority.begin_startup_observation()
    try:
        await runtime.async_process([config.room_id])
        assert not calls
    finally:
        await runtime.stop()


async def test_new_fresh_report_rearms_read_after_later_expiry():
    runtime, config, clock, _, _, calls, _, _ = await stale_case()
    try:
        await runtime.async_process([config.room_id])
        assert len(calls) == 4
        clock['now'] += 300_001
        runtime._hass.states.set('binary_sensor.demo_presence', 'off',
            last_changed=datetime.fromtimestamp(clock['now'] / 1000, timezone.utc))
        await runtime.async_process([config.room_id])
        assert len(calls) == 8
    finally:
        await runtime.stop()
