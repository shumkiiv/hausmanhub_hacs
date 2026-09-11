"""HTTP boundary tests for room lighting config, status, templates and live tests."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from unittest.mock import patch

from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES,
    FakeHomeAssistant,
    FakeJsonRequest,
    FakeRequest,
    fake_home_assistant_modules,
    reader_user,
)

PACKAGE_MODULE = "custom_components.hausman_hub"
API_MODULE = f"{PACKAGE_MODULE}.room_lighting_api"


def _config_payload(room_id: str = "room_demo_entry", name: str = "Тамбур") -> dict:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": name,
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_demo_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.demo_presence",
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "light_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": "light.demo_main",
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_day",
                "title": "День",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
                },
                "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
                "how": {
                    "brightness": 60,
                    "colorTemperature": 3000,
                    "fade": True,
                    "mode": "on_presence",
                    "minOnSeconds": 0,
                },
            }
        ],
        "switchBindings": [],
        "illumination": None,
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 20,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "none"},
        "autoAdopt": True,
        "updatedAt": 1,
        "overrides": {},
    }


class _MemoryStore:
    def __init__(self) -> None:
        self.configs: dict[str, object] = {}

    async def async_get(self, room_id: str):
        return self.configs.get(room_id)

    async def async_load_all(self):
        return tuple(self.configs.values())

    async def async_upsert(self, config) -> None:
        self.configs[config.room_id] = config

    async def async_delete(self, room_id: str) -> bool:
        return self.configs.pop(room_id, None) is not None


class _SpyExecutor:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def __call__(self, command: object) -> dict:
        self.calls.append(command)
        return {"confirmed": True}


def _request(path: str, **match: str) -> FakeRequest:
    request = FakeRequest(
        "127.0.0.1", reader_user("admin", admin=True), path=path
    )
    request.match_info = match
    return request


def _json_request(path: str, payload: object, **match: str) -> FakeJsonRequest:
    request = FakeJsonRequest(
        "127.0.0.1", reader_user("admin", admin=True), path, payload
    )
    request.match_info = match
    return request


class RoomLightingApiTest(unittest.TestCase):
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
        from datetime import datetime, timezone

        from custom_components.hausman_hub.application.room_lighting_service import (
            RoomLightingService,
        )

        now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)

        def context_factory(_config):
            return self._context(now)

        def live_context_factory(_stage):
            return self._context(now)

        self.hass = FakeHomeAssistant()
        self.store = _MemoryStore()
        self.hass.data["hausman_hub"] = {
            self.api.DATA_ROOM_LIGHTING_SERVICE: RoomLightingService(self.store),
            self.api.DATA_ROOM_LIGHTING_LIVE_TESTS: {},
            self.api.DATA_ROOM_LIGHTING_CONTEXT: context_factory,
            self.api.DATA_ROOM_LIGHTING_LIVE_CONTEXT: live_context_factory,
        }
        self.config_view = self.api.RoomLightingConfigView(self.hass)
        self.status_view = self.api.RoomLightingStatusView(self.hass)
        self.templates_view = self.api.RoomLightingTemplatesView(self.hass)
        self.apply_view = self.api.RoomLightingTemplateApplyView(self.hass)
        self.live_view = self.api.RoomLightingLiveTestsView(self.hass)
        self.live_result_view = self.api.RoomLightingLiveTestView(self.hass)

    @staticmethod
    def _context(now: int):
        from datetime import time, timezone

        from custom_components.hausman_hub.domain.room_lighting import SensorKind
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightSnapshot,
            RoomLightingContext,
            SensorSnapshot,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            SensorState,
        )

        return RoomLightingContext(
            now=now,
            timezone=timezone.utc,
            sunrise=time(7, 0),
            sunset=time(19, 0),
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, now
                ),
            ),
            lights=(LightSnapshot("light_main", SensorState.OFF, now),),
        )

    def _config_path(self, room_id: str = "room_demo_entry") -> str:
        return self.api.ROOM_LIGHTING_CONFIG_PATH.format(room_id=room_id)

    def test_config_round_trip(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            missing = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            self.assertEqual(404, missing.status)

            put = await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            self.assertEqual(200, put.status)
            self.assertEqual(room_id, put.payload["roomId"])
            self.assertEqual(1, put.payload["version"])

            got = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            self.assertEqual(200, got.status)
            self.assertEqual("Тамбур", got.payload["name"])

            changed = _config_payload(room_id, name="Другое")
            changed["version"] = 1
            updated = await self.config_view.put(
                _json_request(self._config_path(room_id), changed, room_id=room_id)
            )
            self.assertEqual(2, updated.payload["version"])

            conflict = _config_payload(room_id, name="Старое")
            conflict["version"] = 1
            stale = await self.config_view.put(
                _json_request(self._config_path(room_id), conflict, room_id=room_id)
            )
            self.assertEqual(409, stale.status)
            self.assertEqual("revision_conflict", stale.payload["code"])

        asyncio.run(flow())

    def test_invalid_config_returns_400_with_violations(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            payload = _config_payload(room_id)
            payload["schedule"][0]["how"]["brightness"] = 101
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            self.assertEqual(400, response.status)
            self.assertEqual("invalid_request", response.payload["code"])
            self.assertTrue(response.payload["details"]["violations"])

        asyncio.run(flow())

    def test_status_and_templates(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            self.assertIn("active_schedule", status.payload)
            self.assertIn("illumination", status.payload)
            self.assertEqual(False, status.payload["commandsEnabled"])

            templates = await self.templates_view.get(
                _request(
                    self.api.ROOM_LIGHTING_TEMPLATES_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, templates.status)
            self.assertTrue(templates.payload["templates"])

        asyncio.run(flow())

    def test_apply_template_returns_config(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            response = await self.apply_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_TEMPLATE_APPLY_PATH.format(room_id=room_id),
                    {"templateId": "day_profile", "overrides": {"name": "Шаблон"}},
                    room_id=room_id,
                )
            )
            self.assertEqual(200, response.status)
            self.assertEqual(room_id, response.payload["roomId"])
            self.assertEqual("Шаблон", response.payload["name"])

        asyncio.run(flow())

    def test_live_test_safe_never_calls_executor(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            spy = _SpyExecutor()
            self.config_view._hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_EXECUTOR
            ] = spy
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "safe", "correlationId": "live-api-safe-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, response.status)
            self.assertEqual(0, response.payload["commandsSent"])
            self.assertEqual([], spy.calls)

        asyncio.run(flow())

    def test_live_test_real_without_executor_is_capability_unavailable(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "real", "correlationId": "live-api-real-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(409, response.status)
            self.assertEqual("capability_unavailable", response.payload["code"])

        asyncio.run(flow())

    def test_live_test_real_with_executor_records_receipts(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            spy = _SpyExecutor()
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_EXECUTOR] = spy
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "real", "correlationId": "live-api-real-2"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, response.status)
            self.assertTrue(response.payload["commandsSent"] > 0)
            self.assertTrue(response.payload["receipts"])
            self.assertTrue(spy.calls)
            self.assertEqual(
                "live-api-real-2", response.payload["correlationId"]
            )

        asyncio.run(flow())

    def test_live_test_result_and_cancel(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            started = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "safe", "correlationId": "live-api-cancel-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, started.status)

            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-cancel-1"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-cancel-1")
            )
            self.assertEqual(200, result.status)
            self.assertEqual("live-api-cancel-1", result.payload["correlationId"])

            event = asyncio.Event()
            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            registry["live-api-cancel-2"] = None
            registry["live-api-cancel-2.cancel"] = event
            cancel_path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-cancel-2"
            )
            cancelled = await self.live_result_view.post(
                _request(cancel_path, room_id=room_id, correlation_id="live-api-cancel-2")
            )
            self.assertEqual(200, cancelled.status)
            self.assertEqual("cancelled", cancelled.payload["status"])
            self.assertTrue(event.is_set())

        asyncio.run(flow())

    def test_unauthorized_is_forbidden(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            path = self._config_path(room_id)
            request = FakeRequest("8.8.8.8", reader_user("guest"), path=path)
            request.match_info = {"room_id": room_id}
            response = await self.config_view.get(request)
            self.assertEqual(403, response.status)

        asyncio.run(flow())


if __name__ == "__main__":
    unittest.main()
