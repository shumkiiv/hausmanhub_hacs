"""Offline-only polling schedule. No device transport is attached."""
import asyncio

import pytest

from custom_components.hausman_hub.application.device_poll_schedule import (
    DevicePollSchedule,
    PollSettings,
)


async def test_disabled_does_not_poll_and_enabled_respects_interval():
    calls = []

    async def poll(device):
        calls.append(device)
        return True

    scheduler = DevicePollSchedule(poll)
    scheduler.configure(PollSettings(False, 15), ("device_a",))
    assert await scheduler.tick(0) == {}
    scheduler.configure(PollSettings(True, 15), ("device_a",))
    assert await scheduler.tick(0) == {"device_a": "confirmed"}
    assert await scheduler.tick(899_999) == {}
    assert await scheduler.tick(900_000) == {"device_a": "confirmed"}
    assert calls == ["device_a", "device_a"]


async def test_only_registered_devices_polled_and_no_parallel_duplicate():
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def poll(device):
        calls.append(device)
        started.set()
        await release.wait()
        return False

    scheduler = DevicePollSchedule(poll)
    scheduler.configure(PollSettings(True, 15), ("device_a",))
    first = asyncio.create_task(scheduler.tick(0))
    await started.wait()
    second = asyncio.create_task(scheduler.tick(0))
    release.set()
    assert await first == {"device_a": "unconfirmed"}
    assert await second == {}
    assert calls == ["device_a"]


async def test_backward_clock_is_rejected_without_starting_poll():
    calls = []

    async def poll(device):
        calls.append(device)
        return True

    scheduler = DevicePollSchedule(poll)
    scheduler.configure(PollSettings(True, 15), ("device_a",))
    await scheduler.tick(1000)
    with pytest.raises(ValueError):
        await scheduler.tick(999)
    assert calls == ["device_a"]


async def test_disabling_during_poll_prevents_next_device():
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def poll(device):
        calls.append(device)
        started.set()
        await release.wait()
        return True

    scheduler = DevicePollSchedule(poll)
    devices = tuple(f"device_{n}" for n in range(8))
    scheduler.configure(PollSettings(True, 15), devices)
    run = asyncio.create_task(scheduler.tick(0))
    await started.wait()
    scheduler.configure(PollSettings(False, 15), devices)
    release.set()
    assert await run == {}
    assert 1 <= len(calls) <= 4
    assert all(device in devices[:4] for device in calls)


async def test_failures_are_not_treated_as_confirmed_and_other_device_continues():
    async def poll(device):
        if device == "device_a":
            raise RuntimeError("unexpected transport failure")
        return True

    scheduler = DevicePollSchedule(poll)
    scheduler.configure(PollSettings(True, 15), ("device_a", "device_b"))
    assert await scheduler.tick(0) == {"device_a": "failed", "device_b": "confirmed"}


async def test_slow_device_does_not_prevent_other_device_from_starting():
    slow_started = asyncio.Event()
    fast_started = asyncio.Event()
    release = asyncio.Event()

    async def poll(device):
        if device == "device_a":
            slow_started.set()
            await release.wait()
        else:
            fast_started.set()
        return True

    scheduler = DevicePollSchedule(poll)
    scheduler.configure(PollSettings(True, 15), ("device_a", "device_b"))
    run = asyncio.create_task(scheduler.tick(0))
    try:
        await slow_started.wait()
        await asyncio.wait_for(fast_started.wait(), timeout=0.05)
    finally:
        release.set()
        await run


@pytest.mark.parametrize("enabled,minutes", [(True, 0), (True, 1441), (True, True), (1, 15)])
def test_settings_reject_invalid_values(enabled, minutes):
    with pytest.raises(ValueError):
        PollSettings(enabled, minutes)


def test_scope_rejects_unhashable_invalid_device_without_changing_settings():
    async def poll(_device):
        return True
    scheduler = DevicePollSchedule(poll)
    with pytest.raises(ValueError):
        scheduler.configure(PollSettings(True, 15), (["invalid"],))
