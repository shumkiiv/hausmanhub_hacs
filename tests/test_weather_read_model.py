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


if __name__ == "__main__":
    unittest.main()
