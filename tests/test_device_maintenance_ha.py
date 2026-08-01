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


def _registry_modules(hass: object) -> dict[str, ModuleType]:
    modules: dict[str, ModuleType] = {}
    for kind in ("area", "device", "entity"):
        module = ModuleType(f"homeassistant.helpers.{kind}_registry")
        module.async_get = lambda value, key=kind: getattr(value, f"{key}_registry")  # type: ignore[attr-defined]
        modules[module.__name__] = module
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
            data={"hausman_hub": {}},
        )

    def test_snapshot_exposes_safe_actions_areas_entities_and_ha_link(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            payload = asyncio.run(
                HomeAssistantDeviceMaintenanceService(hass).async_snapshot()
            )
        item = payload["devices"][inventory_device_id("device-one")]
        self.assertEqual("living", item["roomAreaId"])
        self.assertEqual(2, item["entityCount"])
        self.assertTrue(item["identifySupported"])
        self.assertFalse(item["deleteBlocked"])
        self.assertEqual("/config/devices/device/device-one", item["haUrl"])
        self.assertEqual(["Гостиная", "Детская"], [area["name"] for area in payload["areas"]])
        standalone = payload["devices"][inventory_entity_id("switch.standalone_relay")]
        self.assertEqual("entity_only", standalone["kind"])
        self.assertEqual("/config/entities/entity/switch.standalone_relay", standalone["haUrl"])
        self.assertFalse(standalone["identifySupported"])

    def test_update_persists_name_and_area_and_clears_entity_override(self) -> None:
        hass = self._hass()
        public_id = inventory_device_id("device-one")
        with patch.dict(sys.modules, _registry_modules(hass)):
            result = asyncio.run(
                HomeAssistantDeviceMaintenanceService(hass).async_update(
                    {"deviceId": public_id, "name": "Климат гостиной", "areaId": "kids"}
                )
            )
        self.assertEqual("saved", result["status"])
        device = hass.device_registry.devices["device-one"]
        self.assertEqual("Климат гостиной", device.name_by_user)
        self.assertEqual("kids", device.area_id)
        self.assertIsNone(hass.entity_registry.entities["sensor.room_temperature"].area_id)

    def test_identify_calls_only_the_real_identify_button(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            result = asyncio.run(
                HomeAssistantDeviceMaintenanceService(hass).async_identify(
                    {"deviceId": inventory_device_id("device-one")}
                )
            )
        self.assertEqual("command_sent", result["status"])
        self.assertEqual(
            [("button", "press", {"entity_id": "button.device_identify"}, True)],
            hass.services.calls,
        )

    def test_entity_only_update_persists_name_and_area_in_entity_registry(self) -> None:
        hass = self._hass()
        public_id = inventory_entity_id("switch.standalone_relay")
        with patch.dict(sys.modules, _registry_modules(hass)):
            result = asyncio.run(
                HomeAssistantDeviceMaintenanceService(hass).async_update(
                    {"deviceId": public_id, "name": "Реле подсветки", "areaId": "living"}
                )
            )
        self.assertEqual("saved", result["status"])
        entity = hass.entity_registry.entities["switch.standalone_relay"]
        self.assertEqual("Реле подсветки", entity.name)
        self.assertEqual("living", entity.area_id)

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
                asyncio.run(service.async_delete({"deviceId": inventory_device_id("device-one")}))
            with self.assertRaises(DeviceMaintenanceViolation) as used:
                asyncio.run(service.async_delete({
                    "deviceId": inventory_device_id("device-one"), "confirmed": True,
                }))
        self.assertEqual("confirmation_required", unconfirmed.exception.code)
        self.assertEqual("device_in_use", used.exception.code)
        self.assertEqual([], hass.device_registry.removed)

    def test_delete_removes_an_unreferenced_registry_record(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            result = asyncio.run(
                HomeAssistantDeviceMaintenanceService(hass).async_delete(
                    {"deviceId": inventory_device_id("device-one"), "confirmed": True}
                )
            )
        self.assertEqual("deleted", result["status"])
        self.assertEqual(["device-one"], hass.device_registry.removed)

    def test_delete_removes_an_unreferenced_entity_only_record(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _registry_modules(hass)):
            result = asyncio.run(
                HomeAssistantDeviceMaintenanceService(hass).async_delete(
                    {
                        "deviceId": inventory_entity_id("switch.standalone_relay"),
                        "confirmed": True,
                    }
                )
            )
        self.assertEqual("deleted", result["status"])
        self.assertEqual(["switch.standalone_relay"], hass.entity_registry.removed)


if __name__ == "__main__":
    unittest.main()
