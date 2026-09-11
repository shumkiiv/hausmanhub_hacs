"""Storage and service tests for room lighting configurations."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from custom_components.hausman_hub.application.room_lighting_service import (
    DEFAULT_TEMPLATE_ID,
    RoomLightingService,
)
from custom_components.hausman_hub.domain.room_lighting import (
    ROOM_LIGHTING_STORAGE_VERSION,
    config_from_payload,
)


def _fake_ha_storage_modules() -> tuple[dict[str, types.ModuleType], type]:
    modules: dict[str, types.ModuleType] = {}
    if "homeassistant" not in sys.modules:
        homeassistant = types.ModuleType("homeassistant")
        homeassistant.__path__ = []
        modules["homeassistant"] = homeassistant
    if "homeassistant.core" not in sys.modules:
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = object  # type: ignore[attr-defined]
        modules["homeassistant.core"] = core
    if "homeassistant.helpers" not in sys.modules:
        helpers = types.ModuleType("homeassistant.helpers")
        helpers.__path__ = []
        modules["homeassistant.helpers"] = helpers
    storage = types.ModuleType("homeassistant.helpers.storage")

    class FakeStore:
        backing: dict[str, dict[str, object]] = {}

        def __class_getitem__(cls, item: object) -> type:
            return cls

        def __init__(self, hass: object, version: int, key: str, **kwargs: object) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self.max_readable_version = kwargs.get("max_readable_version")

        async def async_load(self) -> dict[str, object] | None:
            return self.backing.get(self.key)

        async def async_save(self, value: dict[str, object]) -> None:
            self.backing[self.key] = value

    storage.Store = FakeStore  # type: ignore[attr-defined]
    modules["homeassistant.helpers.storage"] = storage
    return modules, FakeStore


def _payload(room_id: str = "room_demo_entry", name: str = "Тамбур") -> dict[str, object]:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": name,
        "version": 1,
        "devices": {
            "sensors": [],
            "light_targets": [
                {
                    "id": "light_demo_a",
                    "name": "Свет A",
                    "kind": "switch",
                    "entityId": "switch.demo_a",
                    "role": "main",
                    "groupId": None,
                    "brightness": False,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [],
        "switchBindings": [],
        "dimming": {
            "enabled": False,
            "onAbsence": False,
            "fadeSeconds": 0,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": False,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_only",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "none"},
        "autoAdopt": False,
        "updatedAt": 1,
        "overrides": {},
    }


class RoomLightingStorageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.modules, self.fake_store = _fake_ha_storage_modules()
        self.fake_store.backing.clear()
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        self.hass = MagicMock()

    def tearDown(self) -> None:
        self.module_patch.stop()

    def make_store(self):
        from custom_components.hausman_hub.application.room_lighting_storage import (
            HomeAssistantRoomLightingStore,
        )

        return HomeAssistantRoomLightingStore(self.hass, "entry_1")

    async def test_empty_store_loads_empty(self) -> None:
        store = self.make_store()
        self.assertEqual((), await store.async_load_all())
        self.assertIsNone(await store.async_get("room_demo_entry"))

    async def test_save_and_reload_round_trip(self) -> None:
        store = self.make_store()
        config = config_from_payload(_payload())
        await store.async_upsert(config)

        restored = await self.make_store().async_get("room_demo_entry")
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual("Тамбур", restored.name)
        self.assertEqual(config.to_dict(), restored.to_dict())
        self.assertEqual(
            ROOM_LIGHTING_STORAGE_VERSION,
            self.fake_store.backing["hausman_hub.room_lighting.entry_1"]["version"],
        )

    async def test_upsert_replaces_existing_room(self) -> None:
        store = self.make_store()
        await store.async_upsert(config_from_payload(_payload()))
        await store.async_upsert(config_from_payload(_payload(name="Новое имя")))

        all_configs = await store.async_load_all()
        self.assertEqual(1, len(all_configs))
        self.assertEqual("Новое имя", all_configs[0].name)

    async def test_delete_reports_existence(self) -> None:
        store = self.make_store()
        await store.async_upsert(config_from_payload(_payload()))

        self.assertTrue(await store.async_delete("room_demo_entry"))
        self.assertFalse(await store.async_delete("room_demo_entry"))
        self.assertEqual((), await store.async_load_all())

    async def test_damaged_payload_fails_closed(self) -> None:
        key = "hausman_hub.room_lighting.entry_1"
        store = self.make_store()
        self.fake_store.backing[key] = {"version": 1, "rooms": [{"bad": True}]}
        self.assertEqual((), await store.async_load_all())
        self.assertIsNone(await store.async_get("room_demo_entry"))

        self.fake_store.backing[key] = {"version": 1}
        self.assertEqual((), await store.async_load_all())

        self.fake_store.backing[key] = "not-an-object"  # type: ignore[assignment]
        self.assertEqual((), await store.async_load_all())

    async def test_pre_version_storage_migrates_to_empty(self) -> None:
        store = self.make_store()
        migrated = await store._store._async_migrate_func(0, 3, {"unsafe": True})
        self.assertEqual({"version": ROOM_LIGHTING_STORAGE_VERSION, "rooms": []}, migrated)


class RoomLightingServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.modules, self.fake_store = _fake_ha_storage_modules()
        self.fake_store.backing.clear()
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        self.hass = MagicMock()

        from custom_components.hausman_hub.application.room_lighting_storage import (
            HomeAssistantRoomLightingStore,
        )

        self.service = RoomLightingService(
            HomeAssistantRoomLightingStore(self.hass, "entry_1")
        )

    def tearDown(self) -> None:
        self.module_patch.stop()

    async def test_put_bumps_version_only_on_change(self) -> None:
        initial = config_from_payload(_payload(room_id="room_demo_entry"))
        stored = await self.service.async_put_config(initial)
        self.assertEqual(1, stored.version)

        changed = config_from_payload(_payload(room_id="room_demo_entry", name="Другое"))
        updated = await self.service.async_put_config(changed)
        self.assertEqual(2, updated.version)

        same = await self.service.async_put_config(changed)
        self.assertEqual(2, same.version)

    async def test_apply_template_builds_full_config(self) -> None:
        config = await self.service.async_apply_template(
            DEFAULT_TEMPLATE_ID,
            {"name": "Шаблон для тамбура"},
            room_id="room_demo_entry",
        )
        self.assertEqual("room_demo_entry", config.room_id)
        self.assertEqual("Шаблон для тамбура", config.name)
        self.assertEqual(DEFAULT_TEMPLATE_ID, config.template_id)
        self.assertTrue(config.schedule)
        self.assertEqual("room_off", config.away_behavior.mode.value)

    async def test_operations_never_call_home_assistant_services(self) -> None:
        config = config_from_payload(_payload(room_id="room_demo_entry"))
        await self.service.async_put_config(config)
        await self.service.async_get_config("room_demo_entry")
        await self.service.async_list_configs()
        await self.service.async_apply_template(DEFAULT_TEMPLATE_ID)
        await self.service.async_delete_config("room_demo_entry")

        self.assertFalse(self.hass.services.async_call.called)


if __name__ == "__main__":
    unittest.main()
