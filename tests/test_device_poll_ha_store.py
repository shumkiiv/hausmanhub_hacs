"""Exercise the HA settings adapter against an in-memory fake Store."""
import sys
import types


async def test_settings_store_is_scoped_per_entry_and_survives_recreation(monkeypatch):
    saved = {}
    storage = types.ModuleType("homeassistant.helpers.storage")

    class FakeStore:
        def __init__(self, _hass, version, key):
            assert version == 1
            self.key = key

        async def async_load(self):
            return saved.get(self.key)

        async def async_save(self, value):
            saved[self.key] = value

    storage.Store = FakeStore
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    helpers.storage = storage
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    ha.helpers = helpers
    monkeypatch.setitem(sys.modules, "homeassistant", ha)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.storage", storage)

    from custom_components.hausman_hub.device_poll_storage import HomeAssistantDevicePollStore

    one = HomeAssistantDevicePollStore(object(), "entry-a")
    other = HomeAssistantDevicePollStore(object(), "entry-b")
    await one.async_save({"version": 1, "revision": 1, "settings": {"enabled": True, "intervalMinutes": 15}})
    assert await other.async_load() is None
    assert (await HomeAssistantDevicePollStore(object(), "entry-a").async_load())["revision"] == 1
