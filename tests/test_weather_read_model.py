"""Weather read-model unit and HA adapter tests."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.hausman_hub import dashboard_ha_snapshot
from custom_components.hausman_hub.application.weather_read_model import (
    build_weather_read_model,
)


class WeatherReadModelTest(unittest.TestCase):
    def test_units_forecast_bounds_and_climate_load_are_normalized(self) -> None:
        daily = [
            {
                "datetime": f"2026-08-{day:02d}T00:00:00+06:00",
                "condition": "cloudy",
                "temperature": 68,
                "templow": 50,
                "wind_speed": 36,
                "precipitation_probability": 60,
            }
            for day in range(1, 13)
        ]
        payload = build_weather_read_model(
            condition="cloudy",
            attributes={
                "temperature": 68,
                "temperature_unit": "°F",
                "humidity": 82,
                "wind_speed": 36,
                "wind_speed_unit": "km/h",
                "pressure": 101325,
                "pressure_unit": "Pa",
            },
            daily=daily,
            target_temperature_c=25,
        )

        self.assertEqual(20.0, payload["temperatureC"])
        self.assertEqual(10.0, payload["windSpeedMps"])
        self.assertEqual(1013.2, payload["pressureHpa"])
        self.assertEqual(10, len(payload["dailyForecast"]))
        self.assertEqual("strong", payload["climateLoad"]["level"])
        self.assertIn("wind", payload["climateLoad"]["factors"])

    def test_outdoor_sensor_fields_report_an_honest_reading(self) -> None:
        payload = build_weather_read_model(
            condition="sunny",
            attributes={"temperature": 29.4, "temperature_unit": "°C"},
            outdoor_sensor_value="86",
            outdoor_sensor_unit="°F",
            outdoor_sensor_entity_id="sensor.ac_outdoor_temperature",
            outdoor_sensor_updated_at=1782225585,
            outdoor_sensor_available=True,
        )

        self.assertEqual(30.0, payload["outdoorSensorTemperatureC"])
        self.assertEqual(
            "sensor.ac_outdoor_temperature", payload["outdoorSensorEntityId"]
        )
        self.assertEqual(1782225585, payload["outdoorSensorUpdatedAt"])
        self.assertIs(payload["outdoorSensorAvailable"], True)

    def test_outdoor_sensor_fields_stay_null_without_a_sensor(self) -> None:
        payload = build_weather_read_model(condition="sunny", attributes={})

        self.assertIsNone(payload["outdoorSensorTemperatureC"])
        self.assertIsNone(payload["outdoorSensorEntityId"])
        self.assertIsNone(payload["outdoorSensorUpdatedAt"])
        self.assertIs(payload["outdoorSensorAvailable"], False)

    def test_unavailable_sensor_never_invents_a_value(self) -> None:
        payload = build_weather_read_model(
            condition=None,
            attributes=None,
            outdoor_sensor_value="not-a-number",
            outdoor_sensor_entity_id="sensor.ac_outdoor_temperature",
            outdoor_sensor_updated_at=1782225585,
            outdoor_sensor_available=False,
        )

        self.assertIsNone(payload["outdoorSensorTemperatureC"])
        self.assertIsNone(payload["outdoorSensorEntityId"])
        self.assertIsNone(payload["outdoorSensorUpdatedAt"])
        self.assertIs(payload["outdoorSensorAvailable"], False)


class _States:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, entity_id: str) -> object | None:
        return self._values.get(entity_id)


class _Services:
    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) == ("weather", "get_forecasts")

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, object],
        *,
        blocking: bool,
        return_response: bool,
    ) -> dict[str, object]:
        forecast_type = data["type"]
        count = 12 if forecast_type == "daily" else 30
        return {
            "weather.home": {
                "forecast": [
                    {
                        "datetime": f"2026-07-31T{index % 24:02d}:00:00+06:00",
                        "condition": "sunny",
                        "temperature": 25,
                    }
                    for index in range(count)
                ]
            }
        }


class DashboardWeatherAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_uses_weather_response_service_and_mps(self) -> None:
        entry = SimpleNamespace(
            entity_id="weather.home",
            device_id=None,
            area_id=None,
            disabled_by=None,
            hidden_by=None,
            entity_category=None,
            name=None,
            original_name="Погода",
        )
        updated = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
        state = SimpleNamespace(
            entity_id="weather.home",
            state="sunny",
            last_updated=updated,
            attributes={
                "friendly_name": "Погода",
                "temperature": 25,
                "temperature_unit": "°C",
                "wind_speed": 18,
                "wind_speed_unit": "km/h",
            },
        )
        hass = SimpleNamespace(
            config=SimpleNamespace(location_name="Дом"),
            states=_States({"weather.home": state}),
            services=_Services(),
        )
        registries = (
            SimpleNamespace(areas={}),
            SimpleNamespace(devices={}),
            SimpleNamespace(entities={entry.entity_id: entry}),
        )
        with (
            patch.object(dashboard_ha_snapshot, "_registry_snapshot", return_value=registries),
            patch.object(dashboard_ha_snapshot, "_local_now", return_value=updated),
        ):
            payload = await dashboard_ha_snapshot.async_dashboard_snapshot(hass)

        self.assertEqual(5.0, payload["summary"]["weatherWindSpeed"])
        self.assertEqual("m/s", payload["summary"]["weatherWindSpeedUnit"])
        self.assertEqual(10, len(payload["weather"]["dailyForecast"]))
        self.assertEqual(24, len(payload["weather"]["hourlyForecast"]))
        self.assertTrue(payload["capabilities"]["weatherDetails"])
        self.assertIsNone(payload["weather"]["outdoorSensorTemperatureC"])
        self.assertIs(payload["weather"]["outdoorSensorAvailable"], False)

    async def test_dashboard_reports_a_configured_outdoor_sensor(self) -> None:
        entry = SimpleNamespace(
            entity_id="weather.home",
            device_id=None,
            area_id=None,
            disabled_by=None,
            hidden_by=None,
            entity_category=None,
            name=None,
            original_name="Погода",
        )
        updated = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
        weather_state = SimpleNamespace(
            entity_id="weather.home",
            state="sunny",
            last_updated=updated,
            attributes={"friendly_name": "Погода", "temperature": 25},
        )
        sensor_state = SimpleNamespace(
            entity_id="sensor.ac_outdoor_temperature",
            state="86",
            last_updated=updated,
            attributes={"unit_of_measurement": "°F"},
        )
        hass = SimpleNamespace(
            config=SimpleNamespace(location_name="Дом"),
            states=_States(
                {
                    "weather.home": weather_state,
                    "sensor.ac_outdoor_temperature": sensor_state,
                }
            ),
            services=_Services(),
        )
        registries = (
            SimpleNamespace(areas={}),
            SimpleNamespace(devices={}),
            SimpleNamespace(entities={entry.entity_id: entry}),
        )
        with (
            patch.object(dashboard_ha_snapshot, "_registry_snapshot", return_value=registries),
            patch.object(dashboard_ha_snapshot, "_local_now", return_value=updated),
        ):
            payload = await dashboard_ha_snapshot.async_dashboard_snapshot(
                hass,
                outdoor_sensor_entity_ids=(
                    "sensor.ac_outdoor_temperature",
                    "weather.home",
                ),
            )

        weather = payload["weather"]
        self.assertEqual(30.0, weather["outdoorSensorTemperatureC"])
        self.assertEqual(
            "sensor.ac_outdoor_temperature", weather["outdoorSensorEntityId"]
        )
        self.assertEqual(int(updated.timestamp()), weather["outdoorSensorUpdatedAt"])
        self.assertIs(weather["outdoorSensorAvailable"], True)

    async def test_dashboard_sensor_fields_stay_null_when_the_sensor_is_silent(
        self,
    ) -> None:
        updated = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
        sensor_state = SimpleNamespace(
            entity_id="sensor.ac_outdoor_temperature",
            state="unavailable",
            last_updated=updated,
            attributes={"unit_of_measurement": "°C"},
        )
        hass = SimpleNamespace(
            config=SimpleNamespace(location_name="Дом"),
            states=_States({"sensor.ac_outdoor_temperature": sensor_state}),
            services=_Services(),
        )
        registries = (
            SimpleNamespace(areas={}),
            SimpleNamespace(devices={}),
            SimpleNamespace(entities={}),
        )
        with (
            patch.object(dashboard_ha_snapshot, "_registry_snapshot", return_value=registries),
            patch.object(dashboard_ha_snapshot, "_local_now", return_value=updated),
        ):
            payload = await dashboard_ha_snapshot.async_dashboard_snapshot(
                hass,
                outdoor_sensor_entity_ids=("sensor.ac_outdoor_temperature",),
            )

        weather = payload["weather"]
        self.assertIs(weather["available"], False)
        self.assertIsNone(weather["outdoorSensorTemperatureC"])
        self.assertIsNone(weather["outdoorSensorEntityId"])
        self.assertIsNone(weather["outdoorSensorUpdatedAt"])
        self.assertIs(weather["outdoorSensorAvailable"], False)


if __name__ == "__main__":
    unittest.main()
