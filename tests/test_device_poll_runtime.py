"""Lifecycle contract for periodic read-only polls; no HA or devices."""
import asyncio

import pytest

from custom_components.hausman_hub.application.device_poll_runtime import DevicePollRuntime
from custom_components.hausman_hub.application.device_poll_schedule import PollSettings


async def test_stop_cancels_and_waits_for_inflight_read():
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def poll(_device):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    runtime = DevicePollRuntime(poll, now_ms=lambda: 0)
    await runtime.async_configure(PollSettings(True, 15), ("approved",))
    await asyncio.wait_for(entered.wait(), 1)
    await runtime.async_stop()
    assert stopped.is_set()
    assert not runtime.running


async def test_disabling_cancels_inflight_read_and_does_not_start_another():
    entered, stopped, calls = asyncio.Event(), asyncio.Event(), []

    async def poll(device):
        calls.append(device)
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    runtime = DevicePollRuntime(poll, now_ms=lambda: 0)
    await runtime.async_configure(PollSettings(True, 15), ("approved",))
    await asyncio.wait_for(entered.wait(), 1)
    await runtime.async_configure(PollSettings(False, 15), ("approved",))
    assert stopped.is_set()
    await asyncio.sleep(0)
    assert calls == ["approved"]
    assert not runtime.running
    await runtime.async_stop()


async def test_bad_new_configuration_does_not_stop_existing_poll():
    entered, release = asyncio.Event(), asyncio.Event()

    async def poll(_device):
        entered.set()
        await release.wait()
        return True

    runtime = DevicePollRuntime(poll, now_ms=lambda: 0)
    await runtime.async_configure(PollSettings(True, 15), ("approved",))
    await asyncio.wait_for(entered.wait(), 1)
    with pytest.raises(ValueError):
        await runtime.async_configure(PollSettings(True, 15), ("",))
    assert runtime.running
    release.set()
    await runtime.async_stop()


async def test_same_configuration_does_not_restart_or_repeat_poll():
    entered, release, calls = asyncio.Event(), asyncio.Event(), []

    async def poll(device):
        calls.append(device)
        entered.set()
        await release.wait()
        return True

    runtime = DevicePollRuntime(poll, now_ms=lambda: 0)
    settings = PollSettings(True, 15)
    await runtime.async_configure(settings, ("approved",))
    await asyncio.wait_for(entered.wait(), 1)
    original_task = runtime._task
    await runtime.async_configure(settings, ("approved",))
    assert runtime.running and runtime._task is original_task and calls == ["approved"]
    release.set()
    await runtime.async_stop()


async def test_non_cancellable_read_blocks_replacement_without_overlapping():
    entered, release, calls = asyncio.Event(), asyncio.Event(), []

    async def poll(device):
        calls.append(device)
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release.wait()
        return False

    runtime = DevicePollRuntime(poll, now_ms=lambda: 0, stop_timeout=0.01)
    await runtime.async_configure(PollSettings(True, 15), ("old",))
    await asyncio.wait_for(entered.wait(), 1)
    with pytest.raises(TimeoutError):
        await runtime.async_configure(PollSettings(True, 15), ("new",))
    assert calls == ["old"]
    release.set()
    await asyncio.sleep(0.02)
    await runtime.async_stop()


async def test_results_do_not_fabricate_confirmation_and_clear_on_stop():
    completed = asyncio.Event()

    async def poll(_device):
        completed.set()
        return False

    runtime = DevicePollRuntime(poll, now_ms=lambda: 1234)
    await runtime.async_configure(PollSettings(True, 15), ("approved",))
    await asyncio.wait_for(completed.wait(), 1)
    await asyncio.sleep(0.01)
    assert runtime.last_result == (1234, {"approved": "unconfirmed"})
    await runtime.async_stop()
    assert runtime.last_result is None
