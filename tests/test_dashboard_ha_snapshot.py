"""Read-only HA adapter tests for the universal dashboard endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.hausman_hub import dashboard_ha_snapshot
from custom_components.hausman_hub.application.operation_journal import (
    OperationJournalService,
)


class _States:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, entity_id: str) -> object | None:
        return self._values.get(entity_id)


class _JournalStore:
    async def async_load(self) -> object | None:
        return None

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload


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
    async def test_durable_journal_becomes_redacted_dashboard_activity(self) -> None:
        journal = OperationJournalService(
            _JournalStore(),
            now_ms=lambda: 1_786_379_619_981,
        )
        await journal.async_append(
            {
                "request_id": "private-run-id",
                "operation": "scenario_run",
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
                "reason": "Сценарий выполнен и подтверждён.",
            }
        )
        hass = SimpleNamespace(
            data={"hausman_hub": {"operation_journal": journal}}
        )

        events = dashboard_ha_snapshot._dashboard_operation_events(hass)

        self.assertEqual(1, len(events))
        self.assertEqual("operation-1", events[0].event_id)
        self.assertEqual("Сценарий", events[0].title)
        self.assertEqual("scenario", events[0].kind)
        self.assertNotIn("private-run-id", repr(events[0]))

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

    async def test_adapter_hides_maintenance_entities_and_prefers_z2m_image(self) -> None:
        area = SimpleNamespace(id="living", name="Гостиная", icon=None)
        device = SimpleNamespace(
            id="zigbee-switch",
            name_by_user="Выключатель гостиная",
            name="TS0012",
            area_id="living",
            model="TS0012",
            model_id="TS0012",
            manufacturer="Tuya",
            identifiers=(("mqtt", "zigbee2mqtt_0x00124b"),),
            entry_type=None,
            disabled_by=None,
        )

        def entry(
            entity_id: str,
            *,
            category: str | None = None,
            hidden_by: str | None = None,
        ) -> object:
            return SimpleNamespace(
                entity_id=entity_id,
                device_id=device.id,
                area_id=None,
                disabled_by=None,
                hidden_by=hidden_by,
                entity_category=category,
                name=None,
                original_name=entity_id,
            )

        state_values = {
            "switch.living_left": SimpleNamespace(
                entity_id="switch.living_left",
                state="on",
                attributes={"friendly_name": "Левая клавиша"},
            ),
            "switch.living_indicator": SimpleNamespace(
                entity_id="switch.living_indicator",
                state="off",
                attributes={"friendly_name": "Индикатор"},
            ),
            "select.living_power_outage_memory": SimpleNamespace(
                entity_id="select.living_power_outage_memory",
                state="restore",
                attributes={"friendly_name": "Память питания"},
            ),
            "sensor.living_linkquality": SimpleNamespace(
                entity_id="sensor.living_linkquality",
                state="72",
                attributes={
                    "friendly_name": "Качество связи",
                    "device_class": "signal_strength",
                },
            ),
            "sensor.living_battery": SimpleNamespace(
                entity_id="sensor.living_battery",
                state="55",
                attributes={
                    "friendly_name": "Заряд",
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                },
            ),
        }
        entries = {
            "switch.living_left": entry("switch.living_left"),
            "switch.living_indicator": entry(
                "switch.living_indicator", category="config"
            ),
            "select.living_power_outage_memory": entry(
                "select.living_power_outage_memory", category="config"
            ),
            "sensor.living_linkquality": entry(
                "sensor.living_linkquality", category="diagnostic"
            ),
            "sensor.living_battery": entry(
                "sensor.living_battery", category="diagnostic"
            ),
        }
        hass = SimpleNamespace(
            config=SimpleNamespace(location_name="Дом"),
            states=_States(state_values),
        )
        now = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
        with (
            patch.object(
                dashboard_ha_snapshot,
                "_registry_snapshot",
                return_value=(
                    SimpleNamespace(areas={area.id: area}),
                    SimpleNamespace(devices={device.id: device}),
                    SimpleNamespace(entities=entries),
                ),
            ),
            patch.object(dashboard_ha_snapshot, "_local_now", return_value=now),
        ):
            payload = await dashboard_ha_snapshot.async_dashboard_snapshot(hass)

        self.assertEqual(1, len(payload["devices"]))
        card = payload["devices"][0]
        self.assertEqual(
            "https://www.zigbee2mqtt.io/images/devices/TS0012.png",
            card["imageUrl"],
        )
        self.assertEqual(
            {"switch.living_left", "sensor.living_battery"},
            {detail["entityId"] for detail in card["details"]},
        )
        self.assertEqual(2, payload["inventory"]["devices"][0]["entityCount"])


class _RefreshingScenarioService:
    def __init__(self, *, with_pinned: bool) -> None:
        self.refresh_calls = 0
        self._with_pinned = with_pinned
        self._catalog = SimpleNamespace(devices={})

    async def async_refresh_catalog(self) -> object:
        self.refresh_calls += 1
        if self._with_pinned:
            action = SimpleNamespace(action_id="turn_on", title="Включить")
            device = SimpleNamespace(
                target_id="entity_intercom",
                entity_id="switch.intercom",
                name="Домофон",
                actions=(action,),
            )
            self._catalog = SimpleNamespace(devices={device.target_id: device})
        return self._catalog


class _FailingRefreshScenarioService:
    def __init__(self) -> None:
        self.refresh_calls = 0
        self._catalog = SimpleNamespace(devices={})

    async def async_refresh_catalog(self) -> object:
        self.refresh_calls += 1
        raise RuntimeError("catalog scan failed")


class RefreshCatalogForMissingPinnedTest(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_is_triggered_when_pinned_entity_is_unknown(self) -> None:
        service = _RefreshingScenarioService(with_pinned=True)

        await dashboard_ha_snapshot._refresh_catalog_for_missing_pinned(
            service, frozenset({"switch.intercom"})
        )

        self.assertEqual(1, service.refresh_calls)
        self.assertIn("switch.intercom", service._catalog.devices["entity_intercom"].entity_id)

    async def test_refresh_is_skipped_when_pinned_entity_is_known(self) -> None:
        service = _RefreshingScenarioService(with_pinned=True)
        await service.async_refresh_catalog()

        await dashboard_ha_snapshot._refresh_catalog_for_missing_pinned(
            service, frozenset({"switch.intercom"})
        )

        self.assertEqual(1, service.refresh_calls)

    async def test_refresh_is_skipped_without_pinned_entities(self) -> None:
        service = _RefreshingScenarioService(with_pinned=False)

        await dashboard_ha_snapshot._refresh_catalog_for_missing_pinned(service, None)
        await dashboard_ha_snapshot._refresh_catalog_for_missing_pinned(service, frozenset())

        self.assertEqual(0, service.refresh_calls)

    async def test_refresh_failure_keeps_the_read_path_available(self) -> None:
        service = _FailingRefreshScenarioService()

        await dashboard_ha_snapshot._refresh_catalog_for_missing_pinned(
            service, frozenset({"switch.intercom"})
        )

        self.assertEqual(1, service.refresh_calls)


if __name__ == "__main__":
    unittest.main()
