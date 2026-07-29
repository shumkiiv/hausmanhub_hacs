"""Atomic lifecycle tests for the native settings service."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.settings_service import (
    HausmanHubSettingsService,
    SettingsServiceUnavailable,
)
from custom_components.hausman_hub.domain.hub_settings import HausmanHubSettings


class MemorySettingsStore:
    def __init__(self, value: object = None) -> None:
        self.value = value
        self.saved: list[HausmanHubSettings] = []

    async def async_load(self) -> object:
        return self.value

    async def async_save(self, value: HausmanHubSettings) -> None:
        self.saved.append(value)
        self.value = value


class FailingSettingsStore(MemorySettingsStore):
    async def async_save(self, value: HausmanHubSettings) -> None:
        raise OSError("synthetic storage failure")


class HausmanHubSettingsServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_load_exposes_the_exact_saved_model(self) -> None:
        saved = HausmanHubSettings(light_on_entities=("light.living",))
        service = HausmanHubSettingsService("entry_1", MemorySettingsStore(saved))

        await service.async_load()

        self.assertEqual(saved, service.current)
        self.assertEqual("entry_1", service.entry_id)

    async def test_unloaded_or_invalid_store_fails_closed(self) -> None:
        service = HausmanHubSettingsService("entry_1", MemorySettingsStore(None))
        with self.assertRaises(SettingsServiceUnavailable):
            _ = service.current
        with self.assertRaises(SettingsServiceUnavailable):
            await service.async_load()

    async def test_replace_persists_before_changing_memory(self) -> None:
        original = HausmanHubSettings()
        replacement = HausmanHubSettings(tv_off_entities=("media_player.tv",))
        store = MemorySettingsStore(original)
        service = HausmanHubSettingsService("entry_1", store)
        await service.async_load()

        await service.async_replace(replacement)

        self.assertEqual([replacement], store.saved)
        self.assertEqual(replacement, service.current)

    async def test_failed_save_keeps_the_previous_settings(self) -> None:
        original = HausmanHubSettings(light_on_entities=("light.living",))
        service = HausmanHubSettingsService("entry_1", FailingSettingsStore(original))
        await service.async_load()

        with self.assertRaises(OSError):
            await service.async_replace(HausmanHubSettings())

        self.assertEqual(original, service.current)


if __name__ == "__main__":
    unittest.main()
