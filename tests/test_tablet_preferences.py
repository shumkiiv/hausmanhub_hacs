"""Contract tests for HA-owned tablet and energy preferences."""

from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timezone

from custom_components.hausman_hub.application.tablet_preferences import (
    TabletPreferencesService,
    TabletPreferencesViolation,
    default_tablet_settings,
    energy_settings_from_legacy,
)
from custom_components.hausman_hub.domain.hub_settings import HausmanHubSettings


class _Store:
    def __init__(self, value: object = None) -> None:
        self.value = deepcopy(value)
        self.saved: list[dict[str, object]] = []
        self.fail = False

    async def async_load(self) -> object:
        return deepcopy(self.value)

    async def async_save(self, value: dict[str, object]) -> None:
        if self.fail:
            raise RuntimeError("storage failed")
        self.value = deepcopy(value)
        self.saved.append(deepcopy(value))


class TabletPreferencesServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = _Store()
        self.service = TabletPreferencesService(
            self.store,
            now=lambda: datetime(2026, 7, 31, 2, 30, tzinfo=timezone.utc),
        )
        await self.service.async_load(
            HausmanHubSettings(
                energy_display_units="both",
                energy_show_voltage=False,
                energy_aggregation="separate",
                energy_use_all_devices=True,
            )
        )

    async def test_defaults_and_legacy_energy_seed_are_explicit(self) -> None:
        tablet = self.service.tablet
        energy = self.service.energy
        rooms = self.service.rooms

        self.assertEqual(0, tablet["revision"])
        self.assertEqual(default_tablet_settings(), tablet["settings"])
        self.assertEqual(
            {"showQuickAccess": False, "deviceId": None},
            tablet["settings"]["intercom"],
        )
        self.assertEqual("both", energy["settings"]["displayUnits"])
        self.assertFalse(energy["settings"]["showVoltage"])
        self.assertEqual("separate", energy["settings"]["aggregation"])
        self.assertTrue(energy["settings"]["useAllDevices"])
        self.assertEqual("hausman-hub-room-settings", rooms["contract"]["name"])
        self.assertEqual([], rooms["rooms"])

    async def test_room_settings_are_atomic_canonical_and_unique(self) -> None:
        rooms = [
            {
                "roomId": "living",
                "type": "living",
                "icon": "mdi:sofa",
                "order": 0,
                "visible": True,
            },
            {
                "roomId": "kitchen",
                "type": "kitchen",
                "icon": "mdi:fridge-outline",
                "order": 1,
                "visible": False,
            },
        ]
        saved = await self.service.async_replace_rooms(0, rooms)
        self.assertEqual(1, saved["revision"])
        self.assertEqual(rooms, saved["rooms"])
        self.assertEqual("living", self.service.room_presentations["living"]["type"])

        invalid = deepcopy(rooms)
        invalid[1]["order"] = 0
        with self.assertRaises(TabletPreferencesViolation):
            await self.service.async_replace_rooms(1, invalid)

        invalid = deepcopy(rooms)
        invalid[0]["icon"] = "mdi:bed"
        with self.assertRaises(TabletPreferencesViolation):
            await self.service.async_replace_rooms(1, invalid)

    async def test_room_registry_change_is_rolled_back_when_storage_fails(self) -> None:
        calls: list[str] = []
        rooms = [
            {
                "roomId": "living",
                "type": "living",
                "icon": "mdi:sofa",
                "order": 0,
                "visible": True,
            }
        ]

        async def apply(_rooms: list[dict[str, object]]) -> None:
            calls.append("apply")

        async def rollback() -> None:
            calls.append("rollback")

        self.store.fail = True
        with self.assertRaises(RuntimeError):
            await self.service.async_replace_rooms(
                0, rooms, apply=apply, rollback=rollback
            )
        self.assertEqual(["apply", "rollback"], calls)
        self.assertEqual([], self.service.rooms["rooms"])

    async def test_old_storage_is_migrated_with_empty_room_settings(self) -> None:
        await self.service.async_replace_tablet(0, default_tablet_settings())
        old_state = deepcopy(self.store.value)
        old_state.pop("rooms")
        service = TabletPreferencesService(self.store)
        self.store.value = old_state
        await service.async_load(HausmanHubSettings())
        self.assertEqual(0, service.rooms["revision"])
        self.assertEqual([], service.rooms["rooms"])

    async def test_replace_is_atomic_and_rejects_stale_revision(self) -> None:
        changed = default_tablet_settings()
        changed["startScreen"]["mode"] = "kiosk"
        saved = await self.service.async_replace_tablet(0, changed)

        self.assertEqual(1, saved["revision"])
        self.assertEqual("kiosk", saved["settings"]["startScreen"]["mode"])
        with self.assertRaises(TabletPreferencesViolation) as caught:
            await self.service.async_replace_tablet(0, default_tablet_settings())
        self.assertTrue(caught.exception.stale)
        self.assertEqual(saved, self.service.tablet)

    async def test_failed_save_does_not_change_memory(self) -> None:
        before = self.service.energy
        self.store.fail = True
        settings = energy_settings_from_legacy(HausmanHubSettings())
        settings["displayUnits"] = "amps"

        with self.assertRaises(RuntimeError):
            await self.service.async_replace_energy(0, settings)

        self.assertEqual(before, self.service.energy)

    async def test_invalid_sensor_and_device_ids_fail_closed(self) -> None:
        tablet = default_tablet_settings()
        tablet["displayAutomation"]["sensorEntityIds"] = ["bad id"]
        with self.assertRaises(TabletPreferencesViolation):
            await self.service.async_replace_tablet(0, tablet)

    async def test_pinned_entity_ids_follow_the_intercom_setting(self) -> None:
        self.assertEqual(frozenset(), self.service.tablet_pinned_entity_ids)

        changed = default_tablet_settings()
        changed["intercom"]["deviceId"] = " switch.entry_intercom "
        await self.service.async_replace_tablet(0, changed)
        self.assertEqual(
            frozenset({"switch.entry_intercom"}),
            self.service.tablet_pinned_entity_ids,
        )

        cleared = self.service.tablet
        cleared["settings"]["intercom"]["deviceId"] = None
        await self.service.async_replace_tablet(1, cleared["settings"])
        self.assertEqual(frozenset(), self.service.tablet_pinned_entity_ids)

        energy = energy_settings_from_legacy(HausmanHubSettings())
        energy["selectedDeviceIds"] = ["sensor.private_entity"]
        with self.assertRaises(TabletPreferencesViolation):
            await self.service.async_replace_energy(0, energy)


if __name__ == "__main__":
    unittest.main()
