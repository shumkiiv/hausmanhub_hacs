"""Read-only HA adapter tests for the universal dashboard endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.hausman_hub import dashboard_ha_snapshot


class _States:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, entity_id: str) -> object | None:
        return self._values.get(entity_id)


class _ScenarioService:
    def __init__(self) -> None:
        action = SimpleNamespace(action_id="turn_on", title="Включить")
        device = SimpleNamespace(
            target_id="entity_0123456789abcdef",
            entity_id="climate.living",
            name="Кондиционер",
            actions=(action,),
        )
        self._catalog = SimpleNamespace(devices={device.target_id: device})

    async def async_list_scenarios(self) -> tuple[object, ...]:
        return (
            SimpleNamespace(
                id="welcome",
                title="Добро пожаловать",
                group="Комфорт",
                description="Приветствие",
                icon="mdi:hand-wave",
                enabled=True,
                favorite=True,
                danger=False,
                requires_confirmation=False,
            ),
            SimpleNamespace(
                id="disabled",
                title="Выключен",
                group="custom",
                description="",
                icon="mdi:script",
                enabled=False,
                favorite=False,
                danger=False,
                requires_confirmation=False,
            ),
        )


class DashboardHaSnapshotTest(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_reads_registries_without_mutating_home_assistant(self) -> None:
        area = SimpleNamespace(id="living", name="Гостиная", icon="mdi:sofa")
        device = SimpleNamespace(
            id="physical-ac",
            name_by_user="Кондиционер",
            name="AC",
            area_id="living",
            model="AC Demo",
            manufacturer="Example",
        )
        climate_entry = SimpleNamespace(
            entity_id="climate.living",
            device_id="physical-ac",
            area_id=None,
            disabled_by=None,
            name=None,
            original_name="Climate",
        )
        sensor_entry = SimpleNamespace(
            entity_id="sensor.living_temperature",
            device_id="physical-ac",
            area_id=None,
            disabled_by=None,
            name=None,
            original_name="Temperature",
        )
        hass = SimpleNamespace(
            config=SimpleNamespace(location_name="Тестовый дом"),
            states=_States(
                {
                    "climate.living": SimpleNamespace(
                        entity_id="climate.living",
                        state="cool",
                        attributes={
                            "friendly_name": "Кондиционер",
                            "temperature": 25,
                            "current_temperature": 25.3,
                        },
                    ),
                    "sensor.living_temperature": SimpleNamespace(
                        entity_id="sensor.living_temperature",
                        state="25.3",
                        attributes={
                            "friendly_name": "Температура",
                            "device_class": "temperature",
                            "unit_of_measurement": "°C",
                        },
                    ),
                }
            ),
        )
        areas = SimpleNamespace(areas={"living": area})
        devices = SimpleNamespace(devices={"physical-ac": device})
        entities = SimpleNamespace(
            entities={
                "climate.living": climate_entry,
                "sensor.living_temperature": sensor_entry,
            }
        )
        now = datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc)

        with (
            patch.object(
                dashboard_ha_snapshot,
                "_registry_snapshot",
                return_value=(areas, devices, entities),
            ),
            patch.object(dashboard_ha_snapshot, "_local_now", return_value=now),
        ):
            payload = await dashboard_ha_snapshot.async_dashboard_snapshot(
                hass, _ScenarioService()
            )

        self.assertEqual("Тестовый дом", payload["summary"]["homeName"])
        self.assertEqual(1, len(payload["devices"]))
        self.assertEqual(2, len(payload["devices"][0]["details"]))
        self.assertEqual(1, len(payload["devices"][0]["actions"]))
        self.assertEqual(
            "entity_0123456789abcdef",
            payload["devices"][0]["actions"][0]["payload"]["targetId"],
        )
        self.assertTrue(payload["capabilities"]["actions"])
        self.assertEqual(["welcome"], [item["id"] for item in payload["scenarios"]])
        self.assertEqual(int(now.timestamp() * 1000), payload["generatedAt"])

if __name__ == "__main__":
    unittest.main()
