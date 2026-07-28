"""Contract and grouping tests for the universal dashboard projection."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.dashboard_snapshot import (
    DashboardArea,
    DashboardDevice,
    DashboardEntity,
    DashboardScenario,
    build_dashboard_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]


class DashboardSnapshotTest(unittest.TestCase):
    """Keep the tablet projection generic and one-card-per-device."""

    def setUp(self) -> None:
        self.snapshot = build_dashboard_snapshot(
            areas=(
                DashboardArea("living", "Гостиная", "mdi:sofa"),
                DashboardArea("kitchen", "Кухня", "mdi:countertop"),
            ),
            devices=(
                DashboardDevice(
                    "ha-device-ac",
                    "Кондиционер",
                    "living",
                    "AC Demo",
                    "Example",
                ),
                DashboardDevice("ha-device-kettle", "Чайник", "kitchen"),
                DashboardDevice("ha-device-leak", "Датчик протечки", "kitchen"),
            ),
            entities=(
                DashboardEntity(
                    "climate.living",
                    "climate",
                    "cool",
                    "Кондиционер",
                    {
                        "temperature": 25,
                        "current_temperature": 25.4,
                        "fan_mode": "medium",
                        "access_token": "must-not-leak",
                    },
                    "ha-device-ac",
                    "living",
                ),
                DashboardEntity(
                    "sensor.living_temperature",
                    "sensor",
                    "25.4",
                    "Температура",
                    {"device_class": "temperature", "unit_of_measurement": "°C"},
                    "ha-device-ac",
                    "living",
                ),
                DashboardEntity(
                    "switch.kettle",
                    "switch",
                    "on",
                    "Нагрев",
                    {},
                    "ha-device-kettle",
                    "kitchen",
                ),
                DashboardEntity(
                    "sensor.kettle_power",
                    "sensor",
                    "1850",
                    "Мощность",
                    {"device_class": "power", "unit_of_measurement": "W"},
                    "ha-device-kettle",
                    "kitchen",
                ),
                DashboardEntity(
                    "binary_sensor.kitchen_leak",
                    "binary_sensor",
                    "on",
                    "Протечка под мойкой",
                    {"device_class": "moisture"},
                    "ha-device-leak",
                    "kitchen",
                ),
                DashboardEntity(
                    "light.floor_lamp",
                    "light",
                    "on",
                    "Торшер",
                    {"brightness": 180},
                    None,
                    "living",
                ),
                DashboardEntity(
                    "sensor.orphan_diagnostic",
                    "sensor",
                    "42",
                    "Диагностика",
                    {"device_class": "signal_strength"},
                ),
                DashboardEntity(
                    "weather.home",
                    "weather",
                    "partlycloudy",
                    "Погода",
                    {
                        "temperature": 29.4,
                        "humidity": 52,
                        "apparent_temperature": 30.1,
                        "wind_speed": 2.4,
                        "wind_speed_unit": "m/s",
                        "is_daytime": True,
                    },
                ),
            ),
            scenarios=(
                DashboardScenario(
                    "movie",
                    "Кино",
                    group="Комфорт",
                    favorite=True,
                ),
            ),
            generated_at_ms=1_782_225_600_000,
            local_iso="2026-06-23T20:00:00+03:00",
            state_revision=42,
        )

    def test_snapshot_matches_public_schema(self) -> None:
        schema_path = (
            ROOT
            / "custom_components"
            / "hausman_hub"
            / "contracts"
            / "v1"
            / "dashboard-snapshot.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(self.snapshot)

    def test_multiple_entities_make_one_physical_device_card(self) -> None:
        devices = self.snapshot["devices"]
        self.assertEqual(4, len(devices))
        self.assertEqual(len(devices), len({device["physicalId"] for device in devices}))

        air_conditioner = next(
            device for device in devices if device["name"] == "Кондиционер"
        )
        self.assertEqual(2, len(air_conditioner["details"]))
        self.assertEqual(
            {"climate", "sensor"},
            {detail["domain"] for detail in air_conditioner["details"]},
        )

        kettle = next(device for device in devices if device["name"] == "Чайник")
        self.assertEqual(2, len(kettle["details"]))
        self.assertNotIn(
            "sensor.orphan_diagnostic",
            {device["entityId"] for device in devices},
        )

    def test_snapshot_is_read_only_and_filters_private_attributes(self) -> None:
        serialized = json.dumps(self.snapshot, ensure_ascii=False)
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("must-not-leak", serialized)
        self.assertFalse(self.snapshot["capabilities"]["actions"])
        self.assertFalse(self.snapshot["capabilities"]["events"])
        self.assertTrue(all(not device["actions"] for device in self.snapshot["devices"]))

    def test_room_weather_alarm_and_summary_are_human_readable(self) -> None:
        living = next(room for room in self.snapshot["rooms"] if room["id"] == "living")
        self.assertEqual(25.4, living["temp"])
        self.assertEqual(25.0, living["targetTemp"])
        self.assertEqual("cool", living["climateState"])
        self.assertTrue(living["climateRunning"])

        summary = self.snapshot["summary"]
        self.assertEqual(2.4, summary["weatherWindSpeed"])
        self.assertEqual("m/s", summary["weatherWindSpeedUnit"])
        self.assertEqual(1, summary["activeLights"])
        self.assertEqual(1, summary["activeClimate"])
        self.assertEqual(1, summary["activeAlarms"])

        alarm = self.snapshot["alarms"][0]
        self.assertTrue(alarm["active"])
        self.assertEqual("bad", alarm["level"])
        self.assertEqual("kitchen", alarm["roomId"])


if __name__ == "__main__":
    unittest.main()
