"""Persistence and compare-and-swap for polling settings, without HA."""
import pytest

from custom_components.hausman_hub.application.device_poll_settings_service import (
    DevicePollSettingsService,
    PollSettingsConflict,
)
from custom_components.hausman_hub.application.device_poll_schedule import PollSettings


class Store:
    def __init__(self, value=None):
        self.value = value

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        self.value = value


async def test_defaults_are_disabled_and_saved_setting_survives_restart():
    store = Store()
    service = DevicePollSettingsService(store)
    await service.async_load()
    assert service.current == (0, PollSettings(False, 15))
    assert await service.async_replace(0, PollSettings(True, 20)) == 1
    restarted = DevicePollSettingsService(store)
    await restarted.async_load()
    assert restarted.current == (1, PollSettings(True, 20))


async def test_old_revision_cannot_overwrite_current_value():
    store = Store()
    service = DevicePollSettingsService(store)
    await service.async_load()
    await service.async_replace(0, PollSettings(True, 15))
    with pytest.raises(PollSettingsConflict):
        await service.async_replace(0, PollSettings(False, 60))
    assert service.current == (1, PollSettings(True, 15))


async def test_failed_reload_cannot_leave_previously_enabled_settings_active():
    class FailingStore(Store):
        async def async_load(self):
            raise OSError("storage unavailable")

    service = DevicePollSettingsService(Store())
    await service.async_load()
    await service.async_replace(0, PollSettings(True, 15))
    service._store = FailingStore()
    with pytest.raises(OSError):
        await service.async_load()
    with pytest.raises(RuntimeError):
        _ = service.current


@pytest.mark.parametrize("saved", [
    {"version": 1, "revision": 1, "settings": {"enabled": True, "intervalMinutes": 0}},
    {"version": 1, "revision": True, "settings": {"enabled": True, "intervalMinutes": 15}},
    {"version": 2, "revision": 1, "settings": {"enabled": True, "intervalMinutes": 15}},
    {"version": 1, "revision": 1, "settings": {"enabled": True, "intervalMinutes": 15}, "extra": 1},
])
async def test_bad_stored_settings_cannot_enable_polling(saved):
    service = DevicePollSettingsService(Store(saved))
    with pytest.raises(ValueError):
        await service.async_load()
    with pytest.raises(RuntimeError):
        _ = service.current
