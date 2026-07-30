"""Contract tests for HA-owned tablet and energy preferences."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

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

        self.assertEqual(0, tablet["revision"])
        self.assertEqual(default_tablet_settings(), tablet["settings"])
        self.assertEqual("both", energy["settings"]["displayUnits"])
        self.assertFalse(energy["settings"]["showVoltage"])
        self.assertEqual("separate", energy["settings"]["aggregation"])
        self.assertTrue(energy["settings"]["useAllDevices"])

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

        energy = energy_settings_from_legacy(HausmanHubSettings())
        energy["selectedDeviceIds"] = ["sensor.private_entity"]
        with self.assertRaises(TabletPreferencesViolation):
            await self.service.async_replace_energy(0, energy)


if __name__ == "__main__":
    unittest.main()
