from __future__ import annotations

import asyncio
from datetime import timedelta
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


class RefreshService:
    async def async_refresh(self) -> None:
        return None


class MemoryAssistantStore:
    async def async_load(self):
        from custom_components.hausman_hub.domain.ai_assistant_state import AiAssistantState

        return AiAssistantState()

    async def async_save(self, state) -> None:
        return None


class Runtime:
    async def async_ai_evidence_snapshot(self) -> dict[str, object]:
        return {
            "version": 1,
            "rooms": [],
            "mismatch_room_ids": [],
            "outdoor_temperature": None,
        }


class AiAssistantLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        cls.schedule = importlib.import_module(
            "custom_components.hausman_hub.ai_assistant_schedule"
        )
        cls.setup = importlib.import_module("custom_components.hausman_hub.ai_assistant_setup")

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in cls.previous_modules.items() if module is not None}
        )

    def test_schedule_registers_two_hour_cancel_callback_on_entry_unload(self) -> None:
        hass = FakeHomeAssistant()
        entry = FakeEntry({}, {})
        calls: list[tuple[object, timedelta]] = []
        cancelled: list[bool] = []

        def track_time_interval(_, action, interval):
            calls.append((action, interval))
            return lambda: cancelled.append(True)

        with patch.object(self.schedule, "async_track_time_interval", track_time_interval):
            asyncio.run(
                self.schedule.async_start_ai_assistant_schedule(
                    hass,
                    entry,
                    RefreshService(),
                )
            )

        self.assertEqual(timedelta(hours=2), calls[0][1])
        self.assertEqual(1, len(entry.unload_callbacks))
        entry.process_unload_callbacks()
        self.assertEqual([True], cancelled)

    def test_setup_rereads_updated_entry_binding(self) -> None:
        hass = FakeHomeAssistant()
        entry = FakeEntry(
            {
                "ai_assistant_settings": {
                    "enabled": False,
                    "preset": "custom",
                    "base_url": "https://provider.example/v1",
                    "model": "first-model",
                },
                "ai_assistant_api_key": "test-key-123",
            },
            {},
        )

        async def no_schedule(*_) -> None:
            return None

        with (
            patch.object(self.setup, "HomeAssistantAiAssistantStore", lambda *_: MemoryAssistantStore()),
            patch.object(self.setup, "async_start_ai_assistant_schedule", no_schedule),
        ):
            first = asyncio.run(self.setup.async_start_ai_assistant(hass, entry, Runtime()))
            entry.data["ai_assistant_settings"]["model"] = "second-model"
            second = asyncio.run(self.setup.async_start_ai_assistant(hass, entry, Runtime()))

        self.assertEqual("first-model", first._settings.model)
        self.assertEqual("second-model", second._settings.model)
