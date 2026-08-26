"""Home Assistant device inventory maintenance tests."""

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import sys
import unittest

from custom_components.hausman_hub.device_maintenance_ha import (
    DeviceMaintenanceViolation,
    HomeAssistantDeviceMaintenanceService,
    inventory_device_id,
    inventory_entity_id,
)
from custom_components.hausman_hub.application.dashboard_snapshot import (
    stable_public_id,
)


class _Registry:
    def __init__(self, collection: str, entries: list[object]) -> None:
        self.collection = collection
        setattr(
            self,
            collection,
            {
                str(getattr(entry, "id", None) or getattr(entry, "entity_id", "")): entry
                for entry in entries
            },
        )
        self.updated: list[tuple[str, dict[str, object]]] = []
        self.removed: list[str] = []

    def async_update_device(self, item_id: str, **changes: object) -> None:
        self.updated.append((item_id, changes))
        item = getattr(self, self.collection)[item_id]
        for key, value in changes.items():
            setattr(item, key, value)

    def async_update_entity(self, item_id: str, **changes: object) -> None:
        self.updated.append((item_id, changes))
        item = getattr(self, self.collection)[item_id]
        for key, value in changes.items():
            setattr(item, key, value)

    def async_remove_device(self, item_id: str) -> None:
        self.removed.append(item_id)
        getattr(self, self.collection).pop(item_id)

    def async_remove(self, item_id: str) -> None:
        self.removed.append(item_id)
        getattr(self, self.collection).pop(item_id)


class _Services:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object], bool]] = []

    async def async_call(
        self, domain: str, service: str, data: dict[str, object], *, blocking: bool
    ) -> None:
        self.calls.append((domain, service, data, blocking))


class _States:
    def __init__(self) -> None:
        self.values = {
            "sensor.room_temperature": SimpleNamespace(
                state="22.5", attributes={"friendly_name": "Температура"}
            ),
            "button.device_identify": SimpleNamespace(state="unknown", attributes={}),
            "switch.standalone_relay": SimpleNamespace(state="off", attributes={}),
        }

    def get(self, entity_id: str) -> object | None:
        return self.values.get(entity_id)


def _registry_modules(hass: object) -> dict[str, ModuleType]:
    modules: dict[str, ModuleType] = {}
    for kind in ("area", "device", "entity"):
        module = ModuleType(f"homeassistant.helpers.{kind}_registry")
        module.async_get = lambda value, key=kind: getattr(value, f"{key}_registry")  # type: ignore[attr-defined]
        modules[module.__name__] = module
    search = ModuleType("homeassistant.components.search")

    class _ItemType:
        DEVICE = "device"
        ENTITY = "entity"

    class _Searcher:
        def __init__(self, value: object, _sources: object) -> None:
            self.hass = value

        def async_search(self, item_type: str, item_id: str):
            return getattr(self.hass, "related", {}).get((item_type, item_id), {})

    search.ItemType = _ItemType  # type: ignore[attr-defined]
    search.Searcher = _Searcher  # type: ignore[attr-defined]
    modules[search.__name__] = search
    entity_helpers = ModuleType("homeassistant.helpers.entity")
    entity_helpers.entity_sources = lambda _value: {}  # type: ignore[attr-defined]
    modules[entity_helpers.__name__] = entity_helpers
    return modules


class HomeAssistantDeviceMaintenanceServiceTests(unittest.TestCase):
    def _hass(self) -> object:
        area = _Registry(
            "areas",
            [
                SimpleNamespace(id="living", name="Гостиная"),
                SimpleNamespace(id="kids", name="Детская"),
            ],
        )
        device = _Registry(
            "devices",
            [
                SimpleNamespace(
                    id="device-one",
                    name="Старое имя",
                    name_by_user=None,
                    area_id="living",
                    config_entries={"entry-1"},
                )
            ],
        )
        entity = _Registry(
            "entities",
            [
                SimpleNamespace(
                    entity_id="sensor.room_temperature",
                    device_id="device-one",
                    name="Температура",
                    original_name=None,
                    translation_key=None,
                    disabled_by=None,
                    area_id="kids",
                ),
                SimpleNamespace(
                    entity_id="button.device_identify",
                    device_id="device-one",
                    name=None,
                    original_name="Identify",
                    translation_key="identify",
                    disabled_by=None,
                    area_id=None,
                ),
                SimpleNamespace(
                    entity_id="switch.standalone_relay",
                    device_id=None,
                    name="Отдельное реле",
                    original_name=None,
                    translation_key=None,
                    disabled_by=None,
                    area_id=None,
                ),
            ],
        )
        return SimpleNamespace(
            area_registry=area,
            device_registry=device,
            entity_registry=entity,
            services=_Services(),
            states=_States(),
            related={},
            data={"hausman_hub": {}},
        )

    @staticmethod
    def _revision(service: HomeAssistantDeviceMaintenanceService) -> str:
        return str(asyncio.run(service.async_snapshot())["snapshotRevision"])

    def test_snapshot_exposes_safe_actions_areas_entities_and_ha_link(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            payload = asyncio.run(
                HomeAssistantDeviceMaintenanceService(hass).async_snapshot()
            )
        items = {item["id"]: item for item in payload["devices"]}
        item = items[inventory_device_id("device-one")]
        self.assertEqual("living", item["areaId"])
        self.assertEqual(2, item["entityCount"])
        self.assertTrue(item["identifySupported"])
        self.assertFalse(item["deleteBlocked"])
        self.assertTrue(item["deleteEligible"])
        self.assertEqual("/config/devices/device/device-one", item["haUrl"])
        self.assertEqual(["Гостиная", "Детская"], [area["name"] for area in payload["areas"]])
        standalone = items[inventory_entity_id("switch.standalone_relay")]
        self.assertEqual("/config/entities/entity/switch.standalone_relay", standalone["haUrl"])
        self.assertFalse(standalone["identifySupported"])

    def test_update_persists_name_and_area_and_clears_entity_override(self) -> None:
        hass = self._hass()
        public_id = inventory_device_id("device-one")
        with patch.dict(sys.modules, _registry_modules(hass)):
            service = HomeAssistantDeviceMaintenanceService(hass)
            result = asyncio.run(
                service.async_update(
                    {
                        "expectedRevision": self._revision(service),
                        "deviceId": public_id,
                        "changes": {"name": "Климат гостиной", "areaId": "kids"},
                    }
                )
            )
        self.assertEqual("confirmed", result["status"])
        self.assertTrue(result["readBack"]["matched"])
        device = hass.device_registry.devices["device-one"]
        self.assertEqual("Климат гостиной", device.name_by_user)
        self.assertEqual("kids", device.area_id)
        self.assertIsNone(hass.entity_registry.entities["sensor.room_temperature"].area_id)

    def test_identify_calls_only_the_real_identify_button(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            service = HomeAssistantDeviceMaintenanceService(hass)
            result = asyncio.run(
                service.async_identify(
                    {
                        "expectedRevision": self._revision(service),
                        "deviceId": inventory_device_id("device-one"),
                        "confirmed": True,
                    }
                )
            )
        self.assertEqual("accepted", result["status"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(
            [("button", "press", {"entity_id": "button.device_identify"}, True)],
            hass.services.calls,
        )

    def test_entity_only_update_persists_name_and_area_in_entity_registry(self) -> None:
        hass = self._hass()
        public_id = inventory_entity_id("switch.standalone_relay")
        with patch.dict(sys.modules, _registry_modules(hass)):
            service = HomeAssistantDeviceMaintenanceService(hass)
            result = asyncio.run(
                service.async_update(
                    {
                        "expectedRevision": self._revision(service),
                        "deviceId": public_id,
                        "changes": {"name": "Реле подсветки", "areaId": "living"},
                    }
                )
            )
        self.assertEqual("confirmed", result["status"])
        entity = hass.entity_registry.entities["switch.standalone_relay"]
        self.assertEqual("Реле подсветки", entity.name)
        self.assertEqual("living", entity.area_id)

    def test_property_rename_updates_a_device_entity_and_keeps_its_id(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            result = asyncio.run(
                HomeAssistantDeviceMaintenanceService(hass).async_rename_entity(
                    {
                        "entityId": "sensor.room_temperature",
                        "name": "Температура у окна",
                    }
                )
            )
        entity = hass.entity_registry.entities["sensor.room_temperature"]
        self.assertEqual("sensor.room_temperature", entity.entity_id)
        self.assertEqual("Температура у окна", entity.name)
        self.assertEqual("renamed", result["result"])
        self.assertTrue(result["readBack"]["matched"])
        self.assertFalse(result["physicalCommandsSent"])

    def test_property_name_reset_restores_the_original_entity_label(self) -> None:
        hass = self._hass()
        entity = hass.entity_registry.entities["button.device_identify"]
        entity.name = "Найти реле"
        with patch.dict(sys.modules, _registry_modules(hass)):
            result = asyncio.run(
                HomeAssistantDeviceMaintenanceService(hass).async_rename_entity(
                    {"entityId": "button.device_identify", "name": None}
                )
            )
        self.assertIsNone(entity.name)
        self.assertEqual("Identify", result["effectiveName"])
        self.assertEqual("reset", result["result"])

    def test_property_rename_rejects_an_omitted_name(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            with self.assertRaises(DeviceMaintenanceViolation) as failure:
                asyncio.run(
                    HomeAssistantDeviceMaintenanceService(hass).async_rename_entity(
                        {"entityId": "sensor.room_temperature"}
                    )
                )
        self.assertEqual("invalid_request", failure.exception.code)
        self.assertEqual([], hass.entity_registry.updated)

    def test_delete_requires_confirmation_and_is_blocked_by_scenario_usage(self) -> None:
        hass = self._hass()
        target = SimpleNamespace(
            entity_id="sensor.room_temperature",
        )
        scenario = SimpleNamespace(
            title="Доброе утро",
            definition=SimpleNamespace(
                triggers=(),
                conditions=(),
                actions=(SimpleNamespace(target_id="target-temperature"),),
            ),
        )

        class _ScenarioService:
            _catalog = SimpleNamespace(devices={"target-temperature": target})

            async def async_list_scenarios(self):
                return (scenario,)

        hass.data["hausman_hub"]["scenario_service"] = _ScenarioService()
        service = HomeAssistantDeviceMaintenanceService(hass)
        with patch.dict(sys.modules, _registry_modules(hass)):
            with self.assertRaises(DeviceMaintenanceViolation) as unconfirmed:
                asyncio.run(service.async_delete({
                    "expectedRevision": self._revision(service),
                    "deviceId": inventory_device_id("device-one"),
                }))
            with self.assertRaises(DeviceMaintenanceViolation) as used:
                asyncio.run(service.async_delete({
                    "expectedRevision": self._revision(service),
                    "deviceId": inventory_device_id("device-one"), "confirmed": True,
                }))
        self.assertEqual("confirmation_required", unconfirmed.exception.code)
        self.assertEqual("device_in_use", used.exception.code)
        self.assertEqual([], hass.device_registry.removed)

    def test_delete_removes_an_unreferenced_registry_record(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            service = HomeAssistantDeviceMaintenanceService(hass)
            result = asyncio.run(
                service.async_delete(
                    {
                        "expectedRevision": self._revision(service),
                        "deviceId": inventory_device_id("device-one"),
                        "confirmed": True,
                    }
                )
            )
        self.assertEqual("deleted", result["result"])
        self.assertEqual(["device-one"], hass.device_registry.removed)

    def test_delete_removes_an_unreferenced_entity_only_record(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            service = HomeAssistantDeviceMaintenanceService(hass)
            result = asyncio.run(
                service.async_delete(
                    {
                        "expectedRevision": self._revision(service),
                        "deviceId": inventory_entity_id("switch.standalone_relay"),
                        "confirmed": True,
                    }
                )
            )
        self.assertEqual("deleted", result["result"])
        self.assertEqual(["switch.standalone_relay"], hass.entity_registry.removed)

    def test_entity_only_energy_source_is_visible_and_cannot_be_deleted(self) -> None:
        hass = self._hass()
        energy_id = stable_public_id(
            "device", "entity:switch.standalone_relay"
        )
        hass.data["hausman_hub"]["tablet_preferences_service"] = SimpleNamespace(
            energy={"settings": {"selectedDeviceIds": [energy_id]}}
        )
        service = HomeAssistantDeviceMaintenanceService(hass)

        with patch.dict(sys.modules, _registry_modules(hass)):
            snapshot = asyncio.run(service.async_snapshot())
            with self.assertRaises(DeviceMaintenanceViolation) as used:
                asyncio.run(
                    service.async_delete(
                        {
                            "expectedRevision": snapshot["snapshotRevision"],
                            "deviceId": inventory_entity_id(
                                "switch.standalone_relay"
                            ),
                            "confirmed": True,
                        }
                    )
                )

        standalone = next(
            item for item in snapshot["devices"]
            if item["id"] == inventory_entity_id("switch.standalone_relay")
        )
        self.assertTrue(standalone["deleteBlocked"])
        self.assertIn("Карточка энергии", standalone["deleteBlockers"])
        self.assertEqual("device_in_use", used.exception.code)
        self.assertEqual([], hass.entity_registry.removed)

    def test_home_assistant_automation_reference_blocks_deletion(self) -> None:
        hass = self._hass()
        hass.related[("device", "device-one")] = {
            "automation": {"automation.keep_climate_safe"}
        }
        service = HomeAssistantDeviceMaintenanceService(hass)
        with patch.dict(sys.modules, _registry_modules(hass)):
            snapshot = asyncio.run(service.async_snapshot())
            item = next(
                value for value in snapshot["devices"]
                if value["id"] == inventory_device_id("device-one")
            )
            with self.assertRaises(DeviceMaintenanceViolation) as used:
                asyncio.run(service.async_delete({
                    "expectedRevision": snapshot["snapshotRevision"],
                    "deviceId": item["id"],
                    "confirmed": True,
                }))
        self.assertFalse(item["deleteEligible"])
        self.assertEqual("ha_automation", item["uses"][0]["kind"])
        self.assertEqual("device_in_use", used.exception.code)

    def test_stale_snapshot_blocks_update_before_registry_write(self) -> None:
        hass = self._hass()
        service = HomeAssistantDeviceMaintenanceService(hass)
        with patch.dict(sys.modules, _registry_modules(hass)):
            with self.assertRaises(DeviceMaintenanceViolation) as stale:
                asyncio.run(service.async_update({
                    "expectedRevision": "0" * 64,
                    "deviceId": inventory_device_id("device-one"),
                    "changes": {"name": "Новое имя"},
                }))
        self.assertEqual("snapshot_changed", stale.exception.code)
        self.assertEqual([], hass.device_registry.updated)

    def test_missing_home_assistant_index_fails_closed(self) -> None:
        hass = self._hass()
        modules = _registry_modules(hass)
        modules.pop("homeassistant.components.search")
        modules.pop("homeassistant.helpers.entity")
        with patch.dict(sys.modules, modules):
            with patch(
                "custom_components.hausman_hub.device_maintenance_ha.importlib.import_module",
                side_effect=lambda name: modules[name]
                if name in modules
                else (_ for _ in ()).throw(ModuleNotFoundError(name)),
            ):
                snapshot = asyncio.run(
                    HomeAssistantDeviceMaintenanceService(hass).async_snapshot()
                )
        self.assertFalse(snapshot["usageIndex"]["complete"])
        self.assertTrue(all(item["deleteBlocked"] for item in snapshot["devices"]))


if __name__ == "__main__":
    unittest.main()
