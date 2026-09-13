"""Tests for critical sensor fault notifications.

Covers the pure contract builder, the deduplicating coordinator, the light and
climate participation extraction and the read-only HTTP boundary. The tests
never call Home Assistant or a physical device.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dt_time, timezone
import importlib
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator

import custom_components.hausman_hub.application.critical_sensor_notifications as notifications_application
import custom_components.hausman_hub.domain.critical_sensor_notification as notifications_domain
from custom_components.hausman_hub.application.climate_ha_observations import (
    ClimateHaEntityState,
)
from custom_components.hausman_hub.application.critical_sensor_notifications import (
    CriticalSensorNotificationService,
    climate_sensor_health_inputs,
    light_sensor_health_inputs,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateRegistry,
    ClimateRoom,
)
from custom_components.hausman_hub.domain.critical_sensor_notification import (
    CRITICAL_SENSOR_NOTIFICATION_CATEGORY,
    CRITICAL_SENSOR_NOTIFICATION_CODE,
    CRITICAL_SENSOR_NOTIFICATION_CONTRACT_NAME,
    CRITICAL_SENSOR_NOTIFICATION_SEVERITY,
    CriticalSensorHealth,
    CriticalSensorReason,
    CriticalSensorRole,
    build_critical_sensor_notification,
    build_critical_sensor_notifications,
)
from custom_components.hausman_hub.domain.room_lighting import (
    SensorKind,
    config_from_payload,
)
from custom_components.hausman_hub.domain.room_lighting_engine import (
    RoomLightingContext,
    SensorSnapshot,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState
from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES,
    FakeHomeAssistant,
    FakeRequest,
    fake_home_assistant_modules,
    reader_user,
)
from tests.test_room_lighting_api import _MemoryStore, _request

PACKAGE_MODULE = "custom_components.hausman_hub"
API_MODULE = f"{PACKAGE_MODULE}.critical_sensor_notification_api"

ROOM_ID = "room_demo_entry"
ROOM_NAME = "Тамбур"
_NOW_MS = int(datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
_NOW_SECONDS = _NOW_MS // 1000
_CONTRACT_SCHEMA = Path(
    os.environ.get(
        "HAUSMANHUB_CONTRACT_DIR",
        "/home/ivsh/projects/HausmanHub/worktrees/"
        "codex-room-lighting-contract-2026-09-11/schemas/v1",
    )
) / "critical-sensor-notification.schema.json"


def _load_schema() -> dict[str, object]:
    if not _CONTRACT_SCHEMA.is_file():
        # A missing contract checkout must skip, not error: the server logic
        # is still testable without the external contract worktree.
        raise unittest.SkipTest("critical sensor contract schema is not available")
    return json.loads(_CONTRACT_SCHEMA.read_text(encoding="utf-8"))


def _health(**overrides: object) -> CriticalSensorHealth:
    values: dict[str, object] = {
        "room_id": ROOM_ID,
        "room_name": ROOM_NAME,
        "role": CriticalSensorRole.LIGHT,
        "sensor_id": "sensor_demo_presence",
        "sensor_name": "Датчик присутствия",
        "entity_id": "binary_sensor.demo_presence",
        "reason": CriticalSensorReason.UNAVAILABLE,
    }
    values.update(overrides)
    return CriticalSensorHealth(**values)  # type: ignore[arg-type]


def _light_payload(
    *,
    with_lux: bool = False,
    lux_feeds_decision: bool = False,
    with_power: bool = False,
) -> dict[str, object]:
    sensors: list[dict[str, object]] = [
        {
            "id": "sensor_demo_presence",
            "name": "Датчик присутствия",
            "kind": "presence",
            "entityId": "binary_sensor.demo_presence",
            "autoAdoptOverride": None,
        }
    ]
    if with_lux:
        sensors.append(
            {
                "id": "sensor_demo_lux",
                "name": "Датчик освещённости",
                "kind": "illuminance",
                "entityId": "sensor.demo_lux",
                "autoAdoptOverride": None,
            }
        )
    payload: dict[str, object] = {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": ROOM_ID,
        "name": ROOM_NAME,
        "version": 1,
        "devices": {
            "sensors": sensors,
            "light_targets": [],
            "power_switch": (
                None
                if not with_power
                else {
                    "id": "power",
                    "name": "Питание комнаты",
                    "entityId": "switch.demo_power",
                    "autoAdoptOverride": None,
                }
            ),
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [],
        "switchBindings": [],
        "illumination": (
            {
                "sensor": "sensor.demo_lux",
                "calibration": {"offset": 0, "multiplier": 1},
                "hysteresis": 5,
                "minLux": 0,
                "maxLux": 100000,
                "thresholds": [],
                "failClosed": True,
            }
            if with_lux and lux_feeds_decision
            else None
        ),
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
    return payload


def _context(
    *,
    sensors: tuple[SensorSnapshot, ...],
    now_ms: int = _NOW_MS,
) -> RoomLightingContext:
    return RoomLightingContext(
        now=now_ms,
        timezone=timezone.utc,
        sunrise=dt_time(7, 0),
        sunset=dt_time(19, 0),
        sensors=sensors,
        lights=(),
    )


class CriticalSensorNotificationServiceTest(unittest.TestCase):
    """Dedup, reason update and confirmed-recovery clearing."""

    def test_fault_creates_one_entry_with_room_role_sensor_reason_and_message(
        self,
    ) -> None:
        service = CriticalSensorNotificationService()

        active = service.evaluate([_health()], now=_NOW_SECONDS)

        self.assertEqual(1, len(active))
        notification = active[0]
        self.assertEqual(ROOM_ID, notification.room_id)
        self.assertEqual(CriticalSensorRole.LIGHT, notification.role)
        self.assertEqual("sensor_demo_presence", notification.sensor_id)
        self.assertEqual(CriticalSensorReason.UNAVAILABLE, notification.reason)
        self.assertEqual(_NOW_SECONDS, notification.since)
        self.assertIn(ROOM_NAME, notification.message)
        self.assertIn("Датчик присутствия", notification.message)
        self.assertIn("свет", notification.message.lower())
        payload = notification.to_payload()
        self.assertEqual(CRITICAL_SENSOR_NOTIFICATION_CODE, payload["code"])
        self.assertEqual(CRITICAL_SENSOR_NOTIFICATION_CATEGORY, payload["category"])
        self.assertEqual(CRITICAL_SENSOR_NOTIFICATION_SEVERITY, payload["severity"])
        self.assertEqual("check_device", payload["recoveryAction"])

    def test_repeated_same_reason_does_not_duplicate_and_keeps_since(self) -> None:
        service = CriticalSensorNotificationService()

        first = service.evaluate([_health()], now=_NOW_SECONDS)
        second = service.evaluate([_health()], now=_NOW_SECONDS + 600)
        third = service.evaluate([_health()], now=_NOW_SECONDS + 3600)

        self.assertEqual(1, len(first))
        self.assertEqual(1, len(second))
        self.assertEqual(1, len(third))
        self.assertEqual(first[0].since, third[0].since)
        self.assertEqual(_NOW_SECONDS, third[0].since)

    def test_changed_reason_updates_same_entry_without_new_since(self) -> None:
        service = CriticalSensorNotificationService()

        service.evaluate([_health()], now=_NOW_SECONDS)
        active = service.evaluate(
            [_health(reason=CriticalSensorReason.UNKNOWN)],
            now=_NOW_SECONDS + 900,
        )

        self.assertEqual(1, len(active))
        self.assertEqual(CriticalSensorReason.UNKNOWN, active[0].reason)
        self.assertEqual(_NOW_SECONDS, active[0].since)
        self.assertEqual("wait", active[0].to_payload()["recoveryAction"])

    def test_fresh_reading_clears_entry_but_unknown_reading_does_not(self) -> None:
        service = CriticalSensorNotificationService()

        service.evaluate([_health()], now=_NOW_SECONDS)
        cleared = service.evaluate([_health(reason=None)], now=_NOW_SECONDS + 60)
        self.assertEqual((), cleared)

        service.evaluate([_health()], now=_NOW_SECONDS + 120)
        still_faulted = service.evaluate(
            [_health(reason=CriticalSensorReason.UNKNOWN)],
            now=_NOW_SECONDS + 180,
        )
        self.assertEqual(1, len(still_faulted))
        self.assertEqual(CriticalSensorReason.UNKNOWN, still_faulted[0].reason)
        self.assertEqual(_NOW_SECONDS + 120, still_faulted[0].since)

        missing = service.evaluate(
            [_health(reason=CriticalSensorReason.UNKNOWN)],
            now=_NOW_SECONDS + 240,
        )
        self.assertEqual(1, len(missing))

    def test_participants_drop_removed_sensor_but_keep_unobserved_one(self) -> None:
        service = CriticalSensorNotificationService()
        service.evaluate(
            [_health(), _health(sensor_id="sensor_demo_removed")],
            now=_NOW_SECONDS,
        )
        self.assertEqual(2, len(service.active()))

        # A missing observation must not clear the fault: the sensor still
        # exists in the configuration, so its entry stays with the same since.
        unobserved = service.evaluate(
            [],
            now=_NOW_SECONDS + 60,
            participants=[(ROOM_ID, "sensor_demo_presence")],
        )
        self.assertEqual(
            ["sensor_demo_presence"], [item.sensor_id for item in unobserved]
        )
        self.assertEqual(_NOW_SECONDS, unobserved[0].since)

        # Removing the sensor from every configuration drops its entry even
        # though no fresh reading ever arrived.
        removed = service.evaluate(
            [],
            now=_NOW_SECONDS + 120,
            participants=[],
        )
        self.assertEqual((), removed)

    def test_non_critical_sensor_produces_nothing(self) -> None:
        config = config_from_payload(_light_payload(with_lux=True))
        context = _context(
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence",
                    SensorKind.PRESENCE,
                    SensorState.OFF,
                    _NOW_MS,
                ),
                SensorSnapshot(
                    "sensor_demo_lux",
                    SensorKind.ILLUMINANCE,
                    SensorState.UNAVAILABLE,
                    _NOW_MS,
                    lux=None,
                    lux_healthy=False,
                ),
            )
        )

        inputs = light_sensor_health_inputs(config, context)
        service = CriticalSensorNotificationService()
        active = service.evaluate(list(inputs), now=_NOW_SECONDS)

        # The unused lux sensor is not part of a decision, so it is never even
        # evaluated; the healthy presence sensor clears instead of creating.
        self.assertEqual(
            ["sensor_demo_presence"], [item.sensor_id for item in inputs]
        )
        self.assertEqual((), active)

    def test_notifications_are_sorted_by_first_fault(self) -> None:
        service = CriticalSensorNotificationService()
        service.evaluate(
            [_health(sensor_id="sensor_b")], now=_NOW_SECONDS + 10
        )
        service.evaluate(
            [_health(sensor_id="sensor_a")], now=_NOW_SECONDS + 20
        )

        active = service.active()

        self.assertEqual(
            ["sensor_b", "sensor_a"], [item.sensor_id for item in active]
        )

    def test_document_validates_against_contract_schema(self) -> None:
        schema = _load_schema()
        notification = build_critical_sensor_notification(
            _health(reason=CriticalSensorReason.STALE), since=_NOW_SECONDS
        )

        Draft202012Validator(schema).validate(notification.to_payload())

        climate = build_critical_sensor_notification(
            _health(
                role=CriticalSensorRole.CLIMATE,
                sensor_id="sensor_demo_humidity",
                sensor_name="Датчик влажности",
                entity_id=None,
                reason=CriticalSensorReason.UNHEALTHY,
            ),
            since=_NOW_SECONDS,
        )
        Draft202012Validator(schema).validate(climate.to_payload())
        self.assertIsNone(climate.to_payload()["entityId"])

    def test_batch_builder_skips_confirmed_fresh_inputs(self) -> None:
        built = build_critical_sensor_notifications(
            [_health(), _health(reason=None), _health(reason=CriticalSensorReason.STALE)],
            since=_NOW_SECONDS,
        )

        self.assertEqual(2, len(built))

    def test_notification_modules_never_reference_physical_commands(self) -> None:
        for module in (notifications_domain, notifications_application):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("call_service", source)
            self.assertNotIn("homeassistant", source)


class CriticalSensorHealthExtractionTest(unittest.TestCase):
    """Only participating sensors are evaluated, with existing rules."""

    def test_light_extraction_marks_unavailable_and_stale_presence(self) -> None:
        config = config_from_payload(_light_payload())
        stale_ms = _NOW_MS - 400 * 1000
        context = _context(
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence",
                    SensorKind.PRESENCE,
                    SensorState.ON,
                    stale_ms,
                ),
            )
        )

        inputs = light_sensor_health_inputs(config, context)

        self.assertEqual(1, len(inputs))
        self.assertEqual(CriticalSensorReason.STALE, inputs[0].reason)

    def test_light_extraction_marks_unavailable_presence(self) -> None:
        config = config_from_payload(_light_payload())
        context = _context(
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence",
                    SensorKind.PRESENCE,
                    SensorState.UNAVAILABLE,
                    _NOW_MS,
                ),
            )
        )

        inputs = light_sensor_health_inputs(config, context)

        self.assertEqual(CriticalSensorReason.UNAVAILABLE, inputs[0].reason)
        self.assertEqual(CriticalSensorRole.LIGHT, inputs[0].role)

    def test_light_extraction_evaluates_the_configured_lux_sensor(self) -> None:
        config = config_from_payload(
            _light_payload(with_lux=True, lux_feeds_decision=True)
        )
        fresh_lux = _context(
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence",
                    SensorKind.PRESENCE,
                    SensorState.OFF,
                    _NOW_MS,
                ),
                SensorSnapshot(
                    "sensor_demo_lux",
                    SensorKind.ILLUMINANCE,
                    SensorState.ON,
                    _NOW_MS,
                    lux=120.0,
                    lux_healthy=True,
                ),
            )
        )
        stale_lux = _context(
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence",
                    SensorKind.PRESENCE,
                    SensorState.OFF,
                    _NOW_MS,
                ),
                SensorSnapshot(
                    "sensor_demo_lux",
                    SensorKind.ILLUMINANCE,
                    SensorState.ON,
                    _NOW_MS - 500 * 1000,
                    lux=120.0,
                    lux_healthy=True,
                ),
            )
        )

        fresh_inputs = {
            item.sensor_id: item.reason
            for item in light_sensor_health_inputs(config, fresh_lux)
        }
        stale_inputs = {
            item.sensor_id: item.reason
            for item in light_sensor_health_inputs(config, stale_lux)
        }

        self.assertIsNone(fresh_inputs["sensor_demo_lux"])
        self.assertEqual(
            CriticalSensorReason.STALE, stale_inputs["sensor_demo_lux"]
        )

    def test_light_extraction_reads_power_switch_state(self) -> None:
        config = config_from_payload(_light_payload(with_power=True))
        context = _context(
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence",
                    SensorKind.PRESENCE,
                    SensorState.OFF,
                    _NOW_MS,
                ),
            )
        )

        inputs = light_sensor_health_inputs(
            config,
            context,
            state_lookup=lambda entity_id: (
                "unavailable" if entity_id == "switch.demo_power" else None
            ),
        )

        power = next(item for item in inputs if item.sensor_id == "power")
        self.assertEqual(CriticalSensorReason.UNAVAILABLE, power.reason)

    def test_climate_extraction_marks_room_value_and_window_sensors(self) -> None:
        observed_at = _NOW_MS
        room = ClimateRoom(
            room_id=ROOM_ID,
            name=ROOM_NAME,
            window_entity_id="binary_sensor.demo_window",
        )
        temperature = ClimateDevice(
            device_id="temp_demo",
            name="Датчик температуры",
            room_id=ROOM_ID,
            kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
            source_id="source_temp_demo",
            control_scope=ClimateControlScope.OBSERVED,
            control_owner=ClimateControlOwner.OBSERVED,
            capabilities=(),
            endpoints=(
                ClimateEndpoint(
                    ClimateEndpointRole.TEMPERATURE, "sensor.demo_temp"
                ),
            ),
        )
        humidity = ClimateDevice(
            device_id="hum_demo",
            name="Датчик влажности",
            room_id=ROOM_ID,
            kind=ClimateDeviceKind.HUMIDITY_SENSOR,
            source_id="source_hum_demo",
            control_scope=ClimateControlScope.OBSERVED,
            control_owner=ClimateControlOwner.OBSERVED,
            capabilities=(),
            endpoints=(
                ClimateEndpoint(
                    ClimateEndpointRole.HUMIDITY, "sensor.demo_humidity"
                ),
            ),
        )
        states = {
            "sensor.demo_temp": ClimateHaEntityState(
                entity_id="sensor.demo_temp",
                state="22.5",
                attributes={},
                last_updated_ms=observed_at - 60_000,
            ),
            "binary_sensor.demo_window": ClimateHaEntityState(
                entity_id="binary_sensor.demo_window",
                state="unavailable",
                attributes={},
                last_updated_ms=observed_at,
            ),
        }

        class _StateView:
            def entity_state(
                self, entity_id: str
            ) -> ClimateHaEntityState | None:
                return states.get(entity_id)

        inputs = climate_sensor_health_inputs(
            ClimateRegistry(rooms=(room,), devices=(temperature, humidity)),
            _StateView(),
            observed_at=observed_at,
        )
        by_sensor = {item.sensor_id: item for item in inputs}

        self.assertIsNone(by_sensor["temp_demo"].reason)
        self.assertEqual(
            CriticalSensorReason.UNKNOWN, by_sensor["hum_demo"].reason
        )
        self.assertEqual(
            CriticalSensorReason.UNAVAILABLE,
            by_sensor[f"{ROOM_ID}_window"].reason,
        )
        service = CriticalSensorNotificationService()
        active = service.evaluate(list(inputs), now=_NOW_SECONDS)
        self.assertEqual(
            {"hum_demo", f"{ROOM_ID}_window"},
            {item.sensor_id for item in active},
        )
        self.assertTrue(all(item.role is CriticalSensorRole.CLIMATE for item in active))

    def test_maximum_length_room_id_keeps_every_window_notification(self) -> None:
        observed_at = _NOW_MS
        max_room_id = "room_" + "a" * 59
        self.assertEqual(64, len(max_room_id))
        long_room = ClimateRoom(
            room_id=max_room_id,
            name="Комната с длинным идентификатором",
            window_entity_id="binary_sensor.long_window",
        )
        short_room = ClimateRoom(
            room_id=ROOM_ID,
            name=ROOM_NAME,
            window_entity_id="binary_sensor.demo_window",
        )
        states = {
            "binary_sensor.long_window": ClimateHaEntityState(
                entity_id="binary_sensor.long_window",
                state="unavailable",
                attributes={},
                last_updated_ms=observed_at,
            ),
            "binary_sensor.demo_window": ClimateHaEntityState(
                entity_id="binary_sensor.demo_window",
                state="unknown",
                attributes={},
                last_updated_ms=observed_at,
            ),
        }

        class _StateView:
            def entity_state(
                self, entity_id: str
            ) -> ClimateHaEntityState | None:
                return states.get(entity_id)

        inputs = climate_sensor_health_inputs(
            ClimateRegistry(rooms=(long_room, short_room)),
            _StateView(),
            observed_at=observed_at,
        )
        active = CriticalSensorNotificationService().evaluate(
            list(inputs), now=_NOW_SECONDS
        )

        # Before the guard the long synthetic id raised and the API silently
        # dropped every climate notification. Both rooms must survive with a
        # stable id that fits the contract pattern.
        self.assertEqual({max_room_id, ROOM_ID}, {item.room_id for item in active})
        self.assertEqual(2, len(active))
        for item in active:
            self.assertIsNotNone(
                re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item.sensor_id),
                item.sensor_id,
            )
        by_room = {item.room_id: item for item in active}
        self.assertEqual(f"{ROOM_ID}_window", by_room[ROOM_ID].sensor_id)
        self.assertLessEqual(len(by_room[max_room_id].sensor_id), 64)
        self.assertEqual(
            CriticalSensorReason.UNAVAILABLE, by_room[max_room_id].reason
        )


class CriticalSensorNotificationsApiTest(unittest.TestCase):
    """The read-only GET evaluates live state and returns active faults."""

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
        self.service = RoomLightingService(_MemoryStore())
        self.presence_state = SensorState.UNKNOWN
        self.presence_last_changed = _NOW_MS
        self.presence_observed = True
        self.api.register_critical_sensor_notification_api(self.hass)
        self.hass.data["hausman_hub"].update(
            {
                self.api.DATA_ROOM_LIGHTING_SERVICE: self.service,
                self.api.DATA_ROOM_LIGHTING_CONTEXT: self._context,
            }
        )
        asyncio.run(self.service.async_put_config(config_from_payload(_light_payload())))
        self.view = self.api.CriticalSensorNotificationsView(self.hass)

    def _context(self, _config: object) -> RoomLightingContext:
        sensors = (
            (
                SensorSnapshot(
                    "sensor_demo_presence",
                    SensorKind.PRESENCE,
                    self.presence_state,
                    self.presence_last_changed,
                ),
            )
            if self.presence_observed
            else ()
        )
        return _context(sensors=sensors)

    def test_get_returns_active_fault_and_keeps_since_on_repeat(self) -> None:
        async def flow() -> None:
            path = self.api.CRITICAL_SENSOR_NOTIFICATIONS_PATH
            first = await self.view.get(_request(path))
            second = await self.view.get(_request(path))
            return first, second

        first, second = asyncio.run(flow())

        self.assertEqual(200, first.status)
        self.assertEqual(
            "hausman-hub-critical-sensor-notifications",
            first.payload["contract"]["name"],
        )
        notifications = first.payload["notifications"]
        self.assertEqual(1, len(notifications))
        self.assertEqual("sensor_demo_presence", notifications[0]["sensorId"])
        self.assertEqual("unknown", notifications[0]["reason"])
        self.assertEqual(
            notifications[0]["since"], second.payload["notifications"][0]["since"]
        )

    def test_get_clears_only_after_a_fresh_reading(self) -> None:
        async def flow() -> None:
            path = self.api.CRITICAL_SENSOR_NOTIFICATIONS_PATH
            first = await self.view.get(_request(path))
            self.presence_state = SensorState.ON
            self.presence_last_changed = _NOW_MS
            second = await self.view.get(_request(path))
            return first, second

        first, second = asyncio.run(flow())

        self.assertEqual(1, len(first.payload["notifications"]))
        self.assertEqual([], second.payload["notifications"])

    def test_get_drops_a_removed_sensor_but_keeps_an_unobserved_one(self) -> None:
        # An empty climate runtime completes the participant picture, so the
        # view can prove that a configured key disappeared.
        self.api.register_critical_sensor_notification_api(
            self.hass,
            climate_runtime=SimpleNamespace(registry=ClimateRegistry()),
        )

        async def flow() -> tuple[object, object, object]:
            path = self.api.CRITICAL_SENSOR_NOTIFICATIONS_PATH
            first = await self.view.get(_request(path))
            self.presence_observed = False
            unobserved = await self.view.get(_request(path))
            payload = _light_payload()
            payload["devices"]["sensors"] = []
            await self.service.async_put_config(config_from_payload(payload))
            removed = await self.view.get(_request(path))
            return first, unobserved, removed

        first, unobserved, removed = asyncio.run(flow())

        self.assertEqual(1, len(first.payload["notifications"]))
        self.assertEqual(1, len(unobserved.payload["notifications"]))
        self.assertEqual(
            first.payload["notifications"][0]["since"],
            unobserved.payload["notifications"][0]["since"],
        )
        self.assertEqual([], removed.payload["notifications"])

    def test_get_rejects_a_non_local_request(self) -> None:
        request = FakeRequest(
            "8.8.8.8",
            reader_user("admin", admin=True),
            path=self.api.CRITICAL_SENSOR_NOTIFICATIONS_PATH,
        )

        response = asyncio.run(self.view.get(request))

        self.assertEqual(403, response.status)


if __name__ == "__main__":
    unittest.main()
