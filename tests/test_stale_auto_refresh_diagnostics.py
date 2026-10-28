"""Bounded refresh outcomes are visible without changing control decisions."""

import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timezone
import logging

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import (
    LIGHT_ENTITY, READ_CHANNELS, SUPPORTED_ENTITIES,
)
from tests.test_lighting_mqtt_refresh import fixture
from tests.test_stale_auto_refresh import auto_case
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState


ADAPTER_LOG = 'custom_components.hausman_hub.adapters.lighting_mqtt_refresh'
RUNTIME_LOG = 'custom_components.hausman_hub.application.room_lighting_runtime'
SECRET = 'private-token-DO-NOT-LOG\nforged-log-entry'


def messages(caplog, logger):
    return [record.getMessage() for record in caplog.records
            if record.name == logger and 'refresh:' in record.getMessage()]


def control_snapshot(runtime, config, authority):
    from custom_components.hausman_hub.application.room_lighting_ha_executor import room_manual_off_protection
    entities = frozenset(target.entity_id for target in config.devices.light_targets)
    return copy.deepcopy((runtime._control_ownership_for(config), authority._records,
        authority.generation, runtime._light_priority.explicit_return_snapshot(entities),
        room_manual_off_protection(runtime._hass).explicit_return_snapshot(config.room_id, entities)))


async def test_real_second_channel_timeout_is_visible_once_without_retry_or_authority_change(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    runtime, config, _, _, native, calls, processed, authority = await auto_case()
    caplog.clear()
    adapter = runtime._observation_refresher
    publish = adapter._publish

    async def second_silent(topic, payload):
        if calls:
            calls.append((topic, payload))
        else:
            await publish(topic, payload)

    adapter._publish = second_silent
    monkeypatch.setattr('custom_components.hausman_hub.adapters.lighting_mqtt_refresh.REFRESH_TIMEOUT_SECONDS', 0.01)
    before = control_snapshot(runtime, config, authority)
    try:
        for _ in range(5):
            await runtime.async_process([config.room_id])
        assert len(calls) == 2
        assert all(topic != READ_CHANNELS[3][1] for topic, _ in calls)
        assert not processed
        assert native[LIGHT_ENTITY].state == 'on'
        assert not runtime._target_trust(LIGHT_ENTITY)[0]
        assert config.room_id in runtime._adjustment_refresh_attempts
        assert config.room_id not in runtime._adjustment_refresh_active
        assert control_snapshot(runtime, config, authority) == before
        adapter_messages = messages(caplog, ADAPTER_LOG)
        runtime_messages = messages(caplog, RUNTIME_LOG)
        assert len(adapter_messages) == len(runtime_messages) == 1
        assert 'scope=tambur phase=waiting_report channelIndex=2 completedReads=1 outcome=timeout bulbSkipped=False' in adapter_messages[0]
        assert 'outcome=observations_unavailable attemptRetained=True' in runtime_messages[0]
        for message in adapter_messages + runtime_messages:
            assert not any(value in message for value in ('zigbee2mqtt/', '0x', 'binary_sensor.', 'light.', 'switch.', 'session', SECRET))
    finally:
        await runtime.stop()


@pytest.mark.parametrize('mode,phase,outcome', [
    ('error', 'publishing', 'io_error'),
    ('cancel', 'publishing', 'cancelled'),
    ('manual', 'waiting_report', 'current_guard_failed'),
    ('disconnect', 'waiting_report', 'current_guard_failed'),
])
async def test_adapter_logs_only_safe_finite_failure_fields(mode, phase, outcome, caplog):
    caplog.set_level(logging.INFO)
    adapter, _, _, _, calls, current = fixture(mode=mode)
    if mode == 'error':
        async def fail(topic, payload):
            calls.append((topic, payload))
            raise OSError(SECRET)
        adapter._publish = fail
    if mode == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await adapter(SUPPORTED_ENTITIES, lambda: current['value'])
    else:
        assert not await adapter(SUPPORTED_ENTITIES, lambda: current['value'])
    lines = messages(caplog, ADAPTER_LOG)
    assert len(lines) == 1
    assert f'phase={phase} channelIndex=1 completedReads=0 outcome={outcome}' in lines[0]
    assert len(calls) == 1
    assert SECRET not in caplog.text
    assert 'forged-log-entry' not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


async def test_hung_publish_is_distinct_from_waiting_report(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    adapter, _, _, _, calls, _ = fixture()
    async def hang(topic, payload):
        calls.append((topic, payload))
        await asyncio.Future()
    adapter._publish = hang
    monkeypatch.setattr('custom_components.hausman_hub.adapters.lighting_mqtt_refresh.REFRESH_TIMEOUT_SECONDS', 0.01)
    assert not await adapter(SUPPORTED_ENTITIES, lambda: True)
    assert len(calls) == 1
    assert len(messages(caplog, ADAPTER_LOG)) == 1
    assert 'phase=publishing channelIndex=1 completedReads=0 outcome=timeout' in messages(caplog, ADAPTER_LOG)[0]


@pytest.mark.parametrize('power,count,skipped', [('on', 4, False), ('off', 3, True)])
async def test_success_and_unpowered_bulb_skip_are_not_conflated(power, count, skipped, caplog):
    caplog.set_level(logging.INFO)
    adapter, _, _, _, calls, _ = fixture(power=power)
    assert await adapter(SUPPORTED_ENTITIES, lambda: True)
    assert len(calls) == count
    lines = messages(caplog, ADAPTER_LOG)
    assert len(lines) == 1
    assert f'phase=complete channelIndex={count} completedReads={count} outcome=completed bulbSkipped={skipped}' in lines[0]


@pytest.mark.parametrize('unready,outcome', [(False, 'unsupported_scope'), (True, 'receiver_not_ready')])
async def test_preflight_failure_has_no_get_and_one_safe_outcome(unready, outcome, caplog):
    caplog.set_level(logging.INFO)
    adapter, port, _, _, calls, _ = fixture()
    entities = SUPPORTED_ENTITIES if unready else frozenset({SECRET})
    if unready:
        port._enabled = frozenset()
    assert not await adapter(entities, lambda: True)
    assert calls == []
    lines = messages(caplog, ADAPTER_LOG)
    assert len(lines) == 1
    assert f'phase=preflight channelIndex=0 completedReads=0 outcome={outcome}' in lines[0]
    assert SECRET not in caplog.text


async def test_unexpected_error_still_propagates_without_exception_data(caplog):
    caplog.set_level(logging.INFO)
    adapter, _, _, _, calls, _ = fixture()
    async def fail(topic, payload):
        calls.append((topic, payload))
        raise ValueError(SECRET)
    adapter._publish = fail
    with pytest.raises(ValueError, match='private-token'):
        await adapter(SUPPORTED_ENTITIES, lambda: True)
    assert len(calls) == 1
    assert len(messages(caplog, ADAPTER_LOG)) == 1
    assert 'outcome=unexpected_error' in messages(caplog, ADAPTER_LOG)[0]
    assert SECRET not in caplog.text and not any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize('change', ['manual', 'config', 'gap'])
async def test_boundary_changes_remain_blocked_and_do_not_leak_data(change, caplog):
    caplog.set_level(logging.INFO)
    runtime, config, clock, _, _, calls, processed, authority = await auto_case()
    caplog.clear()
    publish = runtime._observation_refresher._publish
    async def changed(topic, payload):
        await publish(topic, payload)
        if change == 'manual':
            await authority.async_capture(LIGHT_ENTITY, 'off', 'service', SECRET)
        elif change == 'config':
            runtime._configs[config.room_id] = replace(config, version=config.version + 1)
        elif change == 'gap':
            runtime._unobserved_since = clock['now']
    runtime._observation_refresher._publish = changed
    try:
        await runtime.async_process([config.room_id])
        assert len(calls) == 1 and not processed
        assert config.room_id in runtime._adjustment_refresh_attempts
        assert len(messages(caplog, ADAPTER_LOG)) == len(messages(caplog, RUNTIME_LOG)) == 1
        assert 'outcome=current_guard_failed' in messages(caplog, ADAPTER_LOG)[0]
        assert 'outcome=manual_action_or_observation_changed attemptRetained=True' in messages(caplog, RUNTIME_LOG)[0]
        assert SECRET not in caplog.text
    finally:
        await runtime.stop()


async def test_fresh_sensor_during_read_rebuilds_context_without_changing_control(caplog):
    caplog.set_level(logging.INFO)
    runtime, config, clock, _, _, calls, processed, authority = await auto_case()
    caplog.clear()
    before = control_snapshot(runtime, config, authority)
    publish = runtime._observation_refresher._publish
    presence = next(sensor for sensor in config.devices.sensors if sensor.kind.value == 'presence')

    async def refreshed(topic, payload):
        await publish(topic, payload)
        runtime._hass.states.set(
            presence.entity_id, 'on',
            last_changed=datetime.fromtimestamp(clock['now'] / 1000, timezone.utc),
        )

    runtime._observation_refresher._publish = refreshed
    try:
        await runtime.async_process([config.room_id])
        context = await runtime._build_context(config)
        sensor = next(item for item in context.sensors if item.sensor_id == presence.id)
        assert sensor.state is SensorState.ON
        assert len(calls) == 4 and processed
        assert control_snapshot(runtime, config, authority) == before
        assert config.room_id not in runtime._adjustment_refresh_attempts
        adapter_messages = messages(caplog, ADAPTER_LOG)
        runtime_messages = messages(caplog, RUNTIME_LOG)
        assert len(adapter_messages) == len(runtime_messages) == 1
        assert 'outcome=completed' in adapter_messages[0]
        assert 'outcome=fresh_reports attemptRetained=False' in runtime_messages[0]
        assert SECRET not in caplog.text
    finally:
        await runtime.stop()


@pytest.mark.parametrize('mode', ['normal', 'error', 'cancel', 'manual', 'disconnect'])
async def test_logging_level_does_not_change_calls_returns_or_guard_checks(mode, caplog):
    outcomes = []
    for level in (logging.CRITICAL, logging.INFO):
        caplog.set_level(level)
        adapter, _, _, _, calls, current = fixture(mode=mode)
        checks = []
        def guard():
            checks.append(current['value'])
            return current['value']
        try:
            result = await adapter(SUPPORTED_ENTITIES, guard)
        except asyncio.CancelledError:
            result = 'cancelled'
        outcomes.append((copy.deepcopy(calls), result, checks))
    assert outcomes[0] == outcomes[1]


async def test_runtime_success_logs_freshness_but_does_not_claim_new_ownership(caplog):
    caplog.set_level(logging.INFO)
    runtime, config, _, _, _, calls, processed, authority = await auto_case()
    caplog.clear()
    before = control_snapshot(runtime, config, authority)
    try:
        await runtime.async_process([config.room_id])
        assert len(calls) == 4 and processed
        assert config.room_id not in runtime._adjustment_refresh_attempts
        assert control_snapshot(runtime, config, authority) == before
        lines = messages(caplog, RUNTIME_LOG)
        assert len(lines) == 1
        assert 'outcome=fresh_reports attemptRetained=False' in lines[0]
    finally:
        await runtime.stop()


@pytest.mark.parametrize('cancelled', [False, True])
async def test_runtime_cached_success_or_cancellation_retains_attempt_and_authority(cancelled, caplog):
    caplog.set_level(logging.INFO)
    runtime, config, _, _, _, calls, processed, authority = await auto_case()
    caplog.clear()
    async def refresh(*_):
        calls.append('attempt')
        if cancelled:
            raise asyncio.CancelledError
        return True  # No new physical evidence, so this cannot release the guard.
    runtime._observation_refresher = refresh
    before = control_snapshot(runtime, config, authority)
    try:
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await runtime.async_process([config.room_id])
        else:
            await runtime.async_process([config.room_id])
        for _ in range(4):
            await runtime.async_process([config.room_id])
        assert calls == ['attempt'] and not processed
        assert not runtime._target_trust(LIGHT_ENTITY)[0]
        assert config.room_id not in runtime._adjustment_refresh_active
        assert config.room_id in runtime._adjustment_refresh_attempts
        assert control_snapshot(runtime, config, authority) == before
        lines = messages(caplog, RUNTIME_LOG)
        assert len(lines) == 1
        expected = 'interrupted' if cancelled else 'target_still_untrusted'
        assert f'outcome={expected} attemptRetained=True' in lines[0]
    finally:
        await runtime.stop()


@pytest.mark.parametrize('success', [False, True])
async def test_runtime_logging_levels_leave_attempts_and_protection_unchanged(success, caplog):
    outcomes = []
    for level in (logging.CRITICAL, logging.INFO):
        caplog.set_level(level)
        runtime, config, _, _, _, calls, processed, authority = await auto_case()
        if not success:
            async def refuse(*_):
                calls.append('attempt')
                return False
            runtime._observation_refresher = refuse
        before = control_snapshot(runtime, config, authority)
        try:
            for _ in range(5):
                await runtime.async_process([config.room_id])
            assert control_snapshot(runtime, config, authority) == before
            outcomes.append((copy.deepcopy(calls), bool(processed),
                runtime._target_trust(LIGHT_ENTITY)[0],
                config.room_id in runtime._adjustment_refresh_attempts,
                config.room_id in runtime._adjustment_refresh_active))
        finally:
            await runtime.stop()
    assert outcomes[0] == outcomes[1]


@pytest.mark.asyncio
async def test_refresh_timeout_logs_safe_outcome_without_changing_result(caplog, monkeypatch):
    """A bounded timeout remains false and leaves one finite diagnostic record."""
    caplog.set_level(logging.INFO)
    adapter, _, _, _, calls, _ = fixture()

    async def hang_publish(*_args):
        calls.append("hung")
        await asyncio.Future()

    adapter._publish = hang_publish
    monkeypatch.setattr(
        "custom_components.hausman_hub.adapters.lighting_mqtt_refresh.REFRESH_TIMEOUT_SECONDS",
        0.01,
    )

    assert await adapter(frozenset(row[0] for row in READ_CHANNELS), lambda: True) is False

    messages = [record.getMessage() for record in caplog.records
                if record.name.endswith("lighting_mqtt_refresh")]
    assert len(calls) == 1
    assert any("scope=tambur" in message and "outcome=timeout" in message
               for message in messages)


@pytest.fixture(autouse=True)
def diagnostic_records_contain_only_safe_fields(caplog):
    yield
    forbidden = (SECRET, "forged-log-entry", "zigbee2mqtt/", "0x",
                 "binary_sensor.", "light.", "switch.", "private-token",
                 "Traceback", "payload", "172.30.", "https://")
    for record in caplog.records:
        if record.name not in {ADAPTER_LOG, RUNTIME_LOG} or "refresh:" not in record.getMessage():
            continue
        assert record.exc_info is None
        for rendered in (record.getMessage(), repr(record.args)):
            assert not any(value in rendered for value in forbidden)
