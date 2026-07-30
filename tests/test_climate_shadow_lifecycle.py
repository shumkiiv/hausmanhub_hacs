"""Lifecycle tests for command-free climate shadow collection."""

from __future__ import annotations

import asyncio
import importlib
import sys
from unittest.mock import patch
import unittest

from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES,
    FakeEntry,
    FakeHomeAssistant,
    fake_home_assistant_modules,
)


PACKAGE_MODULE = "custom_components.hausman_hub"


class Runtime:
    async def async_native_climate_comparison(self):
        return None


class UnreadableStore:
    async def async_load(self):
        from custom_components.hausman_hub.climate_shadow_storage import (
            ClimateShadowStorageError,
        )

        raise ClimateShadowStorageError("synthetic invalid evidence")

    async def async_save(self, state) -> None:
        return None


class ClimateShadowLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        cls.shadow = importlib.import_module("custom_components.hausman_hub.climate_shadow")

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in cls.previous_modules.items() if module is not None}
        )

    def test_invalid_evidence_does_not_abort_integration_startup(self) -> None:
        hass = FakeHomeAssistant()
        entry = FakeEntry({}, {})

        with patch.object(
            self.shadow,
            "HomeAssistantClimateShadowStore",
            lambda *_: UnreadableStore(),
        ):
            service = asyncio.run(
                self.shadow.async_start_climate_shadow(hass, entry, Runtime())
            )
            payload = asyncio.run(service.async_snapshot(generated_at=1))

        self.assertTrue(payload["window"]["collection_active"])
        self.assertEqual(0, payload["summary"]["sample_count"])
        self.assertEqual(1, len(entry.unload_callbacks))


if __name__ == "__main__":
    unittest.main()
