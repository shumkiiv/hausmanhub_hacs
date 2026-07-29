"""Home Assistant storage adapter tests for native HausmanHub settings."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from custom_components.hausman_hub.domain.hub_settings import (
    HUB_SETTINGS_VERSION,
    HausmanHubSettings,
    hub_settings_to_payload,
)


def _fake_ha_storage_modules() -> tuple[dict[str, types.ModuleType], type]:
    modules: dict[str, types.ModuleType] = {}
    if "homeassistant" not in sys.modules:
        homeassistant = types.ModuleType("homeassistant")
        homeassistant.__path__ = []
        modules["homeassistant"] = homeassistant
    if "homeassistant.core" not in sys.modules:
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = object  # type: ignore[attr-defined]
        modules["homeassistant.core"] = core
    if "homeassistant.helpers" not in sys.modules:
        helpers = types.ModuleType("homeassistant.helpers")
        helpers.__path__ = []
        modules["homeassistant.helpers"] = helpers
    storage = types.ModuleType("homeassistant.helpers.storage")

    class FakeStore:
        backing: dict[str, dict[str, object]] = {}

        def __class_getitem__(cls, item: object) -> type:
            return cls

        def __init__(self, hass: object, version: int, key: str, **kwargs: object) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self.max_readable_version = kwargs.get("max_readable_version")

        async def async_load(self) -> dict[str, object] | None:
            return self.backing.get(self.key)

        async def async_save(self, value: dict[str, object]) -> None:
            self.backing[self.key] = value

    storage.Store = FakeStore  # type: ignore[attr-defined]
    modules["homeassistant.helpers.storage"] = storage
    return modules, FakeStore


class HomeAssistantSettingsStoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.modules, self.fake_store = _fake_ha_storage_modules()
        self.fake_store.backing.clear()
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        self.hass = MagicMock()

    def tearDown(self) -> None:
        self.module_patch.stop()

    def make_store(self):
        from custom_components.hausman_hub.settings_storage import HomeAssistantSettingsStore

        return HomeAssistantSettingsStore(self.hass, "entry_1")

    async def test_empty_store_loads_safe_defaults(self) -> None:
        self.assertEqual(HausmanHubSettings(), await self.make_store().async_load())

    async def test_save_survives_a_new_adapter_instance(self) -> None:
        saved = HausmanHubSettings(
            light_on_entities=("light.living",),
            tv_off_entities=("media_player.tv",),
            climate_reports_enabled=False,
            curtain_holidays=("2026-01-01",),
        )
        await self.make_store().async_save(saved)

        restored = await self.make_store().async_load()

        self.assertEqual(saved, restored)
        self.assertEqual(
            hub_settings_to_payload(saved),
            self.fake_store.backing["hausman_hub.settings.entry_1"],
        )

    async def test_damaged_document_fails_closed(self) -> None:
        from custom_components.hausman_hub.settings_storage import SettingsStorageError

        self.fake_store.backing["hausman_hub.settings.entry_1"] = {
            "version": HUB_SETTINGS_VERSION,
            "light_on_entities": ["light.ok"],
        }
        with self.assertRaises(SettingsStorageError):
            await self.make_store().async_load()

    async def test_pre_version_storage_migrates_only_to_defaults(self) -> None:
        store = self.make_store()

        migrated = await store._store._async_migrate_func(0, 7, {"unsafe": True})

        self.assertEqual(hub_settings_to_payload(HausmanHubSettings()), migrated)
        self.assertNotIn("unsafe", migrated)


if __name__ == "__main__":
    unittest.main()
