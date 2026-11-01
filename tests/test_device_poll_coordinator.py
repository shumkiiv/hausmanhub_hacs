"""Exercise preferences and scheduler together without HA or network."""
import asyncio

import pytest

from custom_components.hausman_hub.application.device_poll_coordinator import DevicePollCoordinator
from custom_components.hausman_hub.application.device_poll_schedule import PollSettings
from custom_components.hausman_hub.application.device_poll_settings_service import DevicePollSettingsService, PollSettingsConflict
from custom_components.hausman_hub.application.device_poll_runtime import DevicePollRuntime
from tests.test_device_poll_settings_service import Store


async def test_saved_disabled_setting_never_starts_polling():
    calls = []

    async def poll(entity):
        calls.append(entity)
        return True

    runtime = DevicePollRuntime(poll, now_ms=lambda: 0)
    coordinator = DevicePollCoordinator(DevicePollSettingsService(Store()), runtime, ("approved",))
    await coordinator.async_start()
    await asyncio.sleep(0)
    assert calls == [] and not runtime.running
    await coordinator.async_stop()


async def test_failed_reload_stops_previously_running_polling():
    entered = asyncio.Event()

    async def poll(_entity):
        entered.set()
        await asyncio.Future()

    class FailingStore(Store):
        async def async_load(self):
            raise OSError("storage unavailable")

    store = Store({"version": 1, "revision": 1,
                   "settings": {"enabled": True, "intervalMinutes": 15}})
    settings = DevicePollSettingsService(store)
    runtime = DevicePollRuntime(poll, now_ms=lambda: 0)
    coordinator = DevicePollCoordinator(settings, runtime, ("approved",))
    await coordinator.async_start()
    await asyncio.wait_for(entered.wait(), 1)
    settings._store = FailingStore()
    with pytest.raises(OSError):
        await coordinator.async_start()
    assert not runtime.running


async def test_saved_replacement_is_applied_without_accepting_stale_revision():
    calls = []

    async def poll(entity):
        calls.append(entity)
        return True

    settings = DevicePollSettingsService(Store())
    runtime = DevicePollRuntime(poll, now_ms=lambda: 0)
    coordinator = DevicePollCoordinator(settings, runtime, ("approved",))
    await coordinator.async_start()
    assert await coordinator.async_replace(0, PollSettings(True, 15)) == 1
    await asyncio.sleep(0.01)
    assert calls == ["approved"]
    await coordinator.async_stop()


async def test_cancel_during_save_cannot_leave_old_poll_running():
    entered_poll, entered_save = asyncio.Event(), asyncio.Event()

    async def poll(_entity):
        entered_poll.set()
        await asyncio.Future()

    class StalledStore(Store):
        async def async_save(self, value):
            entered_save.set()
            await asyncio.Future()

    store = StalledStore({"version": 1, "revision": 1,
                          "settings": {"enabled": True, "intervalMinutes": 15}})
    runtime = DevicePollRuntime(poll, now_ms=lambda: 0)
    coordinator = DevicePollCoordinator(DevicePollSettingsService(store), runtime, ("approved",))
    await coordinator.async_start()
    await asyncio.wait_for(entered_poll.wait(), 1)
    change = asyncio.create_task(coordinator.async_replace(1, PollSettings(False, 15)))
    await asyncio.wait_for(entered_save.wait(), 1)
    assert not runtime.running
    change.cancel()
    with pytest.raises(asyncio.CancelledError):
        await change
    await coordinator.async_stop()


async def test_stale_settings_revision_does_not_interrupt_active_poll():
    entered = asyncio.Event()

    async def poll(_entity):
        entered.set()
        await asyncio.Future()

    store = Store({"version": 1, "revision": 1,
                   "settings": {"enabled": True, "intervalMinutes": 15}})
    runtime = DevicePollRuntime(poll, now_ms=lambda: 0)
    coordinator = DevicePollCoordinator(DevicePollSettingsService(store), runtime, ("approved",))
    await coordinator.async_start()
    await asyncio.wait_for(entered.wait(), 1)
    with pytest.raises(PollSettingsConflict):
        await coordinator.async_replace(0, PollSettings(False, 15))
    assert runtime.running
    await coordinator.async_stop()
