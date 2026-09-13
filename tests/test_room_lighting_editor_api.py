"""HTTP boundary tests for the room lighting editor catalog and preview."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
import importlib
import sys
import unittest

from custom_components.hausman_hub.application.room_lighting_editor import (
    EditorDevice,
)
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightSnapshot,
    RoomLightingContext,
    SensorSnapshot,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    SensorState,
)
from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES,
    FakeHomeAssistant,
    fake_home_assistant_modules,
)
from tests.test_room_lighting_api import _MemoryStore, _json_request, _request
from tests.test_room_lighting_editor import _payload

PACKAGE_MODULE = "custom_components.hausman_hub"
API_MODULE = f"{PACKAGE_MODULE}.room_lighting_editor_api"

_NOW = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _context(_config: object) -> RoomLightingContext:
    return RoomLightingContext(
        now=_NOW,
        timezone=timezone.utc,
        sunrise=time(7, 0),
        sunset=time(19, 0),
        sensors=(
            SensorSnapshot(
                "sensor_demo_presence",
                config_from_payload(_payload()).devices.sensors[0].kind,
                SensorState.ON,
                _NOW,
            ),
        ),
        lights=(LightSnapshot("light_main", SensorState.OFF, _NOW),),
    )


class RoomLightingEditorApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        importlib.import_module(PACKAGE_MODULE)
        cls.api = importlib.import_module(API_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {
                name: module
                for name, module in cls.previous_modules.items()
                if module is not None
            }
        )

    def setUp(self) -> None:
        from custom_components.hausman_hub.application.room_lighting_service import (
            RoomLightingService,
        )

        self.hass = FakeHomeAssistant()
        self.store = _MemoryStore()
        self.service = RoomLightingService(self.store)
        self.hass.data["hausman_hub"] = {
            self.api.DATA_ROOM_LIGHTING_SERVICE: self.service,
            self.api.DATA_ROOM_LIGHTING_EDITOR_DEVICES: lambda _config: (
                EditorDevice(
                    kind="light",
                    entity_id="light.demo_main",
                    user_label="Люстра",
                    physical_device_label="Люстра тамбур",
                    channel_label="основной канал",
                ),
                EditorDevice(
                    kind="sensor",
                    entity_id="binary_sensor.demo_presence",
                    user_label="Датчик присутствия",
                ),
            ),
            self.api.DATA_ROOM_LIGHTING_CONTEXT: _context,
        }
        asyncio.run(self.service.async_put_config(config_from_payload(_payload())))
        self.catalog_view = self.api.RoomLightingEditorCatalogView(self.hass)
        self.preview_view = self.api.RoomLightingPreviewView(self.hass)

    def _catalog_path(self, room_id: str = "room_demo_entry") -> str:
        return self.api.ROOM_LIGHTING_EDITOR_CATALOG_PATH.format(room_id=room_id)

    def _preview_path(self, room_id: str = "room_demo_entry") -> str:
        return self.api.ROOM_LIGHTING_PREVIEW_PATH.format(room_id=room_id)

    def test_catalog_route_returns_only_room_devices_with_friendly_labels(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            response = await self.catalog_view.get(
                _request(self._catalog_path(room_id), room_id=room_id)
            )
            self.assertEqual(200, response.status)
            self.assertEqual(
                "hausman-hub-room-lighting-editor-catalog",
                response.payload["contract"]["name"],
            )
            labels = [device["label"] for device in response.payload["devices"]]
            self.assertEqual(["Люстра", "Датчик присутствия"], labels)
            self.assertEqual(
                ["light.demo_main", "binary_sensor.demo_presence"],
                [device["entityId"] for device in response.payload["devices"]],
            )

        asyncio.run(flow())

    def test_preview_route_returns_400_section_issues_and_never_dispatches(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            malformed = await self.preview_view.post(
                _json_request(self._preview_path(room_id), [1, 2], room_id=room_id)
            )
            self.assertEqual(400, malformed.status)

            invalid = _payload()
            invalid["devices"]["sensors"][0]["id"] = "Bad ID"
            response = await self.preview_view.post(
                _json_request(self._preview_path(room_id), invalid, room_id=room_id)
            )
            self.assertEqual(200, response.status)
            self.assertFalse(response.payload["safe"])
            issues = response.payload["sectionIssues"]
            self.assertTrue(issues)
            self.assertEqual("inputs", issues[0]["section"])
            # The preview has no executor: nothing may be dispatched.
            self.assertEqual([], self.hass.executor_jobs)

        asyncio.run(flow())

    def test_preview_route_returns_503_when_runtime_is_unavailable(self) -> None:
        async def flow() -> None:
            self.hass.data["hausman_hub"] = {}
            response = await self.preview_view.post(
                _json_request(
                    self._preview_path("room_demo_entry"),
                    _payload(),
                    room_id="room_demo_entry",
                )
            )
            self.assertEqual(503, response.status)

        asyncio.run(flow())
