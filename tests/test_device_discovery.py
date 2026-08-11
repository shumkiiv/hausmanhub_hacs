"""Durable baseline and notification tests for new HA devices."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

from custom_components.hausman_hub.application.device_discovery import (
    DeviceDiscoveryService,
    DeviceDiscoveryViolation,
    DiscoveredDevice,
    DiscoveryArea,
)


class _Store:
    def __init__(self, loaded: dict[str, object] | None = None) -> None:
        self.loaded = loaded
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> dict[str, object] | None:
        return self.loaded

    async def async_save(self, value: dict[str, object]) -> None:
        self.saved.append(value)


def _device(index: int, **changes: object) -> DiscoveredDevice:
    values: dict[str, object] = {
        "private_device_id": f"private-ha-device-{index}",
        "device_id": f"device_{index:016x}",
        "title": f"Устройство {index}",
        "room_id": None,
        "room_name": None,
        "kind": "physical",
        "status": "available",
        "domains": ("sensor",),
        "manufacturer": "Example",
        "model": "T-1",
        "energy_eligible": False,
        "climate_eligible": True,
    }
    values.update(changes)
    return DiscoveredDevice(**values)  # type: ignore[arg-type]


class DeviceDiscoveryServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = _Store()
        self.service = DeviceDiscoveryService(
            self.store,
            now=lambda: datetime(2026, 8, 11, 2, 0, tzinfo=timezone.utc),
        )
        await self.service.async_load()

    async def test_first_reconciliation_creates_a_baseline_without_a_flood(self) -> None:
        changed = await self.service.async_reconcile([_device(1), _device(2)])
        document = self.service.document([DiscoveryArea("office", "Кабинет")])
        self.assertTrue(changed)
        self.assertTrue(document["initialized"])
        self.assertEqual(0, document["pendingCount"])
        self.assertEqual(1, document["revision"])

    async def test_later_device_creates_one_private_id_free_notification(self) -> None:
        await self.service.async_reconcile([_device(1)])
        await self.service.async_reconcile(
            [_device(1), _device(2, energy_eligible=True)]
        )
        document = self.service.document([DiscoveryArea("office", "Кабинет")])
        self.assertEqual(1, document["pendingCount"])
        notice = document["notifications"][0]
        self.assertTrue(any(item["kind"] == "assign_area" for item in notice["suggestedPlacements"]))
        self.assertTrue(any(item["kind"] == "add_to_energy" for item in notice["suggestedPlacements"]))
        encoded = json.dumps(document, ensure_ascii=False)
        self.assertNotIn("private-ha-device", encoded)
        self.assertNotIn("entity_id", encoded)

    async def test_acknowledge_is_optimistically_locked_and_durable(self) -> None:
        await self.service.async_reconcile([_device(1)])
        await self.service.async_reconcile([_device(1), _device(2)])
        document = self.service.document([])
        notice_id = document["notifications"][0]["id"]
        with self.assertRaises(DeviceDiscoveryViolation) as raised:
            await self.service.async_complete(1, notice_id)
        self.assertEqual("revision_conflict", raised.exception.code)
        await self.service.async_complete(2, notice_id)
        self.assertEqual(0, self.service.document([])["pendingCount"])
        restored = DeviceDiscoveryService(_Store(self.store.saved[-1]))
        await restored.async_load()
        self.assertEqual(0, restored.document([])["pendingCount"])

    async def test_known_ids_survive_restart_and_only_a_third_device_is_new(self) -> None:
        await self.service.async_reconcile([_device(1), _device(2)])
        restored_store = _Store(self.store.saved[-1])
        restored = DeviceDiscoveryService(restored_store)
        await restored.async_load()
        await restored.async_reconcile([_device(1), _device(2), _device(3)])
        document = restored.document([])
        self.assertEqual(1, document["pendingCount"])
        self.assertEqual("device_0000000000000003", document["notifications"][0]["deviceId"])
