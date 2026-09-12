"""HTTP boundary tests for room lighting config, status, templates and live tests.

Responses are validated against the contract schemas from the contract worktree.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import sys
import unittest

import pytest
from jsonschema import Draft202012Validator

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
_CONTRACT_DIR = Path(
    "/home/ivsh/projects/HausmanHub/worktrees/codex-room-lighting-contract-2026-09-11/schemas/v1"
)


def _validate(schema_name: str, payload: object) -> None:
    path = _CONTRACT_DIR / schema_name
    if not path.is_file():
        pytest.skip(f"room lighting contract schema is not available: {schema_name}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)


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
    request = FakeRequest("127.0.0.1", reader_user("admin", admin=True), path=path)
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
            self.api.DATA_ROOM_LIGHTING_LIVE_TEST_SLEEP: lambda _seconds: asyncio.sleep(0),
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

    def test_config_round_trip_and_revision_conflict(self) -> None:
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
            _validate("room-lighting-config.schema.json", put.payload)
            self.assertEqual(1, put.payload["version"])

            got = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            self.assertEqual(200, got.status)
            _validate("room-lighting-config.schema.json", got.payload)
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

    def test_invalid_config_returns_readable_400_without_details(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            payload = _config_payload(room_id)
            payload["schedule"][0]["how"]["brightness"] = 101
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            self.assertEqual(400, response.status)
            self.assertEqual("invalid_request", response.payload["code"])
            self.assertIn("Конфигурация освещения", response.payload["message"])
            details = response.payload.get("details", {})
            self.assertNotIn("violations", details)

        asyncio.run(flow())

    def test_status_matches_contract_schema(self) -> None:
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
            _validate("room-lighting-status.schema.json", status.payload)
            self.assertNotIn("commandsEnabled", status.payload)
            self.assertNotIn("shadowDecision", status.payload)

        asyncio.run(flow())

    def test_status_uses_the_running_runtime_context(self) -> None:
        from datetime import datetime, timezone

        now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
        calls: list[object] = []

        class _Runtime:
            running = True

            async def async_context_for(self, config):
                calls.append(config.room_id)
                return RoomLightingApiTest._context(now)

        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_RUNTIME
            ] = _Runtime()
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            _validate("room-lighting-status.schema.json", status.payload)
            self.assertTrue(status.payload["fresh"])
            self.assertEqual("idle", status.payload["phase"])
            self.assertEqual([room_id], calls)

        asyncio.run(flow())

    def test_status_away_uses_runtime_flag(self) -> None:
        from datetime import datetime, timezone

        now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)

        class _Runtime:
            running = True
            away = True

            async def async_context_for(self, config):
                return RoomLightingApiTest._context(now)

        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_RUNTIME
            ] = _Runtime()
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            self.assertTrue(status.payload["away"])

            # Without a running runtime the config fallback applies.
            self.hass.data["hausman_hub"].pop(
                self.api.DATA_ROOM_LIGHTING_RUNTIME, None
            )
            fallback = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, fallback.status)
            self.assertFalse(fallback.payload["away"])

        asyncio.run(flow())

    def test_templates_are_full_contract_documents(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            templates = await self.templates_view.get(
                _request(
                    self.api.ROOM_LIGHTING_TEMPLATES_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, templates.status)
            self.assertTrue(templates.payload["templates"])
            for document in templates.payload["templates"]:
                _validate("room-lighting-template.schema.json", document)

        asyncio.run(flow())

    def test_apply_template_returns_valid_config_without_null_entity(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            response = await self.apply_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_TEMPLATE_APPLY_PATH.format(room_id=room_id),
                    {"templateId": "day_profile", "keepDevices": True},
                    room_id=room_id,
                )
            )
            self.assertEqual(200, response.status)
            _validate("room-lighting-config.schema.json", response.payload)
            for target in response.payload["devices"]["light_targets"]:
                self.assertNotIn(None, [target.get("entityId")])

        asyncio.run(flow())

    def test_live_test_safe_is_background_and_command_free(self) -> None:
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
                    {"mode": "safe", "correlationId": "live-api-safe-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, response.status)
            _validate("room-lighting-live-test.schema.json", response.payload)
            self.assertEqual("request", response.payload["kind"])

            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            await registry["live-api-safe-1"]["task"]

            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-safe-1"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-safe-1")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            self.assertEqual("result", result.payload["kind"])
            self.assertEqual(0, result.payload["result"]["commands_sent"])
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
            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            await registry["live-api-real-2"]["task"]
            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-real-2"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-real-2")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            self.assertTrue(result.payload["result"]["commands_sent"] > 0)
            self.assertTrue(spy.calls)

        asyncio.run(flow())

    def test_live_test_cancel(self) -> None:
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
            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            entry = registry["live-api-cancel-1"]
            await asyncio.sleep(0)

            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-cancel-1"
            )
            cancelled = await self.live_result_view.post(
                _request(path, room_id=room_id, correlation_id="live-api-cancel-1")
            )
            self.assertEqual(200, cancelled.status)
            _validate("room-lighting-live-test.schema.json", cancelled.payload)
            self.assertEqual("cancelled", cancelled.payload["result"]["status"])

            await entry["task"]
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-cancel-1")
            )
            self.assertEqual("cancelled", result.payload["result"]["status"])

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

    def test_config_omits_absent_entity_id(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            payload = _config_payload(room_id)
            payload["devices"]["sensors"] = []
            payload["devices"]["light_targets"][0].pop("entityId")
            put = await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            self.assertEqual(200, put.status)
            _validate("room-lighting-config.schema.json", put.payload)
            self.assertNotIn("entityId", put.payload["devices"]["light_targets"][0])

            got = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            _validate("room-lighting-config.schema.json", got.payload)
            self.assertNotIn("entityId", got.payload["devices"]["light_targets"][0])

        asyncio.run(flow())

    def test_status_sensor_state_enum_and_since_seconds(self) -> None:
        from datetime import datetime, time as dt_time, timezone

        from custom_components.hausman_hub.domain.room_lighting import SensorKind
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightSnapshot,
            ProtectionSnapshot,
            RoomLightingContext,
            SensorSnapshot,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            SensorState,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            now_ms = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
            payload = _config_payload(room_id)
            payload["devices"]["sensors"].append(
                {
                    "id": "sensor_demo_lux",
                    "name": "Освещённость",
                    "kind": "illuminance",
                    "entityId": "sensor.demo_lux",
                    "autoAdoptOverride": None,
                }
            )
            payload["illumination"] = {
                "sensor": "sensor.demo_lux",
                "calibration": {"offset": 0, "multiplier": 1},
                "hysteresis": 5,
                "minLux": 0,
                "maxLux": 20000,
                "thresholds": [],
                "failClosed": True,
            }
            await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            status_path = self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id)

            def provider(lux_healthy: bool, state: SensorState):
                def factory(_config):
                    return RoomLightingContext(
                        now=now_ms,
                        timezone=timezone.utc,
                        sunrise=dt_time(7, 0),
                        sunset=dt_time(19, 0),
                        sensors=(
                            SensorSnapshot(
                                "sensor_demo_lux",
                                SensorKind.ILLUMINANCE,
                                state,
                                now_ms,
                                lux=120.0,
                                lux_healthy=lux_healthy,
                            ),
                        ),
                        lights=(LightSnapshot("light_main", SensorState.OFF, now_ms),),
                        protection=ProtectionSnapshot(
                            active=True,
                            started_at=now_ms,
                            minimum_interval_seconds=600,
                        ),
                    )

                return factory

            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_CONTEXT
            ] = provider(True, SensorState.OFF)
            response = await self.status_view.get(
                _request(status_path, room_id=room_id)
            )
            _validate("room-lighting-status.schema.json", response.payload)
            self.assertEqual("ok", response.payload["illumination"]["sensorState"])
            self.assertEqual(
                now_ms // 1000, response.payload["manual_protection"]["since"]
            )

            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_CONTEXT
            ] = provider(False, SensorState.OFF)
            stale = await self.status_view.get(_request(status_path, room_id=room_id))
            _validate("room-lighting-status.schema.json", stale.payload)
            self.assertEqual("stale", stale.payload["illumination"]["sensorState"])

        asyncio.run(flow())

    def test_config_rejects_entity_shared_with_another_room(self) -> None:
        async def flow() -> None:
            first = await self.config_view.put(
                _json_request(
                    self._config_path("room_a"),
                    _config_payload("room_a"),
                    room_id="room_a",
                )
            )
            self.assertEqual(200, first.status)
            second = await self.config_view.put(
                _json_request(
                    self._config_path("room_b"),
                    _config_payload("room_b"),
                    room_id="room_b",
                )
            )
            self.assertEqual(400, second.status)
            self.assertEqual("invalid_request", second.payload["code"])

        asyncio.run(flow())

    def test_status_remaining_seconds_is_the_actual_remainder(self) -> None:
        from datetime import datetime, time as dt_time, timezone

        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightSnapshot,
            ProtectionSnapshot,
            RoomLightingContext,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            SensorState,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            now_ms = int(
                datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000
            )
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id),
                    _config_payload(room_id),
                    room_id=room_id,
                )
            )

            def provider(_config):
                return RoomLightingContext(
                    now=now_ms,
                    timezone=timezone.utc,
                    sunrise=dt_time(7, 0),
                    sunset=dt_time(19, 0),
                    lights=(LightSnapshot("light_main", SensorState.OFF, now_ms),),
                    protection=ProtectionSnapshot(
                        active=True,
                        started_at=now_ms - 100_000,
                        minimum_interval_seconds=600,
                    ),
                )

            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_CONTEXT
            ] = provider
            response = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, response.status)
            _validate("room-lighting-status.schema.json", response.payload)
            self.assertEqual(
                500, response.payload["manual_protection"]["remaining_seconds"]
            )

        asyncio.run(flow())

if __name__ == "__main__":
    unittest.main()
