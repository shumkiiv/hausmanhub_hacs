"""Restart-contract tests for the complete native climate setup storage."""

from __future__ import annotations

from copy import deepcopy
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
    contour_registry_to_payload,
)
from custom_components.hausman_hub.application.climate_registry import (
    registry_to_payload,
)
from custom_components.hausman_hub.domain.climate import REGISTRY_VERSION
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from custom_components.hausman_hub.domain.configuration import SafeConfiguration
from custom_components.hausman_hub.domain.contours import CONTOUR_REGISTRY_VERSION
from tests.climate_bridge_fixture import import_climate_state
from tests.test_climate_import import source_payload


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
            del item
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


class CompleteClimateStorageRestartTest(unittest.IsolatedAsyncioTestCase):
    """Prove that one saved setup survives construction of a new runtime."""

    def setUp(self) -> None:
        self.modules, self.fake_store = _fake_ha_storage_modules()
        self.fake_store.backing.clear()
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        sys.modules.pop("custom_components.hausman_hub.climate_storage", None)
        sys.modules.pop("custom_components.hausman_hub.contour_storage", None)
        sys.modules.pop("custom_components.hausman_hub.climate_operation_storage", None)
        self.hass = MagicMock()

    def tearDown(self) -> None:
        sys.modules.pop("custom_components.hausman_hub.climate_storage", None)
        sys.modules.pop("custom_components.hausman_hub.contour_storage", None)
        sys.modules.pop("custom_components.hausman_hub.climate_operation_storage", None)
        self.module_patch.stop()

    def _stores(self, entry_id: str):
        from custom_components.hausman_hub.climate_storage import (
            HomeAssistantClimateRegistryStore,
        )
        from custom_components.hausman_hub.contour_storage import (
            HomeAssistantContourStore,
        )

        return (
            HomeAssistantClimateRegistryStore(self.hass, entry_id),
            HomeAssistantContourStore(self.hass, entry_id),
        )

    @staticmethod
    def _configuration() -> SafeConfiguration:
        return SafeConfiguration(
            mode="read-only",
            climate_bridge_mode=ClimateControlMode.DISABLED,
        )

    async def test_complete_setup_survives_new_store_and_runtime_instances(self) -> None:
        snapshot = import_climate_state(source_payload())
        registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=["living", "kids"],
            source_ids=[
                "synthetic-ac-source-living",
                "synthetic-humidifier-source-kids",
            ],
            name="Климат",
            mode="observe",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        registry_store, contour_store = self._stores("entry_1")
        first_runtime = ClimateRuntime(
            entry_id="entry_1",
            configuration=self._configuration(),
            registry_store=registry_store,
            contour_store=contour_store,
        )
        await first_runtime.async_start()

        await first_runtime.async_replace_contour_setup(
            registry_to_payload(registry),
            contour_registry_to_payload(contours),
        )

        restarted_registry_store, restarted_contour_store = self._stores("entry_1")
        restarted_runtime = ClimateRuntime(
            entry_id="entry_1",
            configuration=self._configuration(),
            registry_store=restarted_registry_store,
            contour_store=restarted_contour_store,
        )
        await restarted_runtime.async_start()

        self.assertIsNone(restarted_runtime.last_error)
        self.assertEqual(len(registry.rooms), restarted_runtime.room_count)
        self.assertEqual(len(registry.devices), restarted_runtime.device_count)
        self.assertEqual(
            registry_to_payload(registry),
            await restarted_runtime.async_registry_payload(),
        )
        self.assertEqual(
            contour_registry_to_payload(contours),
            await restarted_runtime.async_contour_registry_payload(),
        )
        self.assertEqual(
            registry_to_payload(registry),
            self.fake_store.backing["hausman_hub.climate_registry.entry_1"],
        )
        self.assertEqual(
            contour_registry_to_payload(contours),
            self.fake_store.backing["hausman_hub.contours.entry_1"],
        )

    async def test_clean_install_backup_restore_preserves_stable_public_ids(self) -> None:
        """Exercise the DR sequence without touching a running Home Assistant."""

        snapshot = import_climate_state(source_payload())
        registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=["living", "kids"],
            source_ids=[
                "synthetic-ac-source-living",
                "synthetic-humidifier-source-kids",
            ],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        registry_store, contour_store = self._stores("entry_dr")
        await registry_store.async_save(registry)
        await contour_store.async_save(contours)
        backup = deepcopy(self.fake_store.backing)
        expected_room_ids = tuple(room.room_id for room in registry.rooms)
        expected_device_ids = tuple(device.device_id for device in registry.devices)

        self.fake_store.backing.clear()
        clean_registry_store, clean_contour_store = self._stores("entry_dr")
        clean_runtime = ClimateRuntime(
            entry_id="entry_dr",
            configuration=self._configuration(),
            registry_store=clean_registry_store,
            contour_store=clean_contour_store,
        )
        await clean_runtime.async_start()
        self.assertEqual(0, clean_runtime.room_count)
        self.assertEqual(0, clean_runtime.device_count)

        self.fake_store.backing.update(deepcopy(backup))
        restored_registry_store, restored_contour_store = self._stores("entry_dr")
        restored_runtime = ClimateRuntime(
            entry_id="entry_dr",
            configuration=self._configuration(),
            registry_store=restored_registry_store,
            contour_store=restored_contour_store,
        )
        await restored_runtime.async_start()
        restored_registry = await restored_registry_store.async_load()

        self.assertIsNone(restored_runtime.last_error)
        self.assertEqual(
            expected_room_ids,
            tuple(room.room_id for room in restored_registry.rooms),
        )
        self.assertEqual(
            expected_device_ids,
            tuple(device.device_id for device in restored_registry.devices),
        )
        self.assertEqual(
            registry_to_payload(registry),
            await restored_runtime.async_registry_payload(),
        )
        self.assertEqual(
            contour_registry_to_payload(contours),
            await restored_runtime.async_contour_registry_payload(),
        )

    async def test_tablet_operation_ledger_survives_store_reconstruction(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import (
            HomeAssistantClimateOperationStore,
        )

        payload = {
            "version": 1,
            "records": [
                {
                    "request_id": "tablet.climate.0001",
                    "fingerprint": "a" * 64,
                    "receipt": {"operation_id": "b" * 32},
                }
            ],
        }
        first = HomeAssistantClimateOperationStore(self.hass, "entry_1")
        await first.async_save(payload)

        restarted = HomeAssistantClimateOperationStore(self.hass, "entry_1")

        self.assertEqual(payload, await restarted.async_load())
        self.assertEqual(1, restarted._store.version)
        self.assertEqual(
            payload,
            self.fake_store.backing["hausman_hub.climate_operations.entry_1"],
        )

    async def test_storage_is_isolated_by_entry_and_keeps_schema_versions(self) -> None:
        registry_store, contour_store = self._stores("entry_1")
        other_registry_store, other_contour_store = self._stores("entry_2")
        snapshot = import_climate_state(source_payload())
        registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="observe",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )

        await registry_store.async_save(registry)
        await contour_store.async_save(contours)

        self.assertEqual(REGISTRY_VERSION, registry_store._store.version)
        self.assertEqual(CONTOUR_REGISTRY_VERSION, contour_store._store.version)
        self.assertEqual(REGISTRY_VERSION, registry_store._store.max_readable_version)
        self.assertEqual(
            CONTOUR_REGISTRY_VERSION,
            contour_store._store.max_readable_version,
        )
        self.assertEqual(
            registry_to_payload(registry),
            registry_to_payload(await registry_store.async_load()),
        )
        self.assertEqual(
            contour_registry_to_payload(contours),
            contour_registry_to_payload(await contour_store.async_load()),
        )
        self.assertEqual(0, len((await other_registry_store.async_load()).rooms))
        self.assertEqual(0, len((await other_contour_store.async_load()).contours))


if __name__ == "__main__":
    unittest.main()
