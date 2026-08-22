"""Independent meter collection and legacy-primary compatibility tests."""

from datetime import date, datetime, timezone
import unittest

from custom_components.hausman_hub.application.energy_meter import (
    EnergyMeterService,
    EnergyMeterViolation,
)
from custom_components.hausman_hub.application.energy_meters import EnergyMetersService


class _Store:
    def __init__(self, loaded: dict[str, object] | None = None) -> None:
        self.loaded = loaded
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> dict[str, object] | None:
        return self.loaded

    async def async_save(self, value: dict[str, object]) -> None:
        self.saved.append(value)


class EnergyMetersServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
        self.primary_store = _Store()
        self.primary = EnergyMeterService(
            self.primary_store,
            now=lambda: self.now,
            local_today=lambda: date(2026, 8, 22),
        )
        await self.primary.async_load()
        self.collection_store = _Store()
        self.service = EnergyMetersService(
            self.collection_store,
            self.primary,
            now=lambda: self.now,
            local_today=lambda: date(2026, 8, 22),
        )
        await self.service.async_load()

    @staticmethod
    def _projection(device_id: str, total: float = 100.0):
        return (
            total,
            device_id,
            device_id,
            "Источник",
            [{"deviceId": device_id, "name": "Источник", "available": True, "currentTotalKwh": total}],
        )

    async def test_primary_legacy_projection_and_two_independent_meters(self) -> None:
        primary_id = "device_0000000000000001"
        await self.primary.async_action(
            {
                "expectedRevision": 0,
                "action": "configure",
                "settings": {
                    "enabled": True,
                    "submissionDayOfMonth": 25,
                    "reminderDaysBefore": 3,
                    "sourceDeviceIds": [primary_id],
                },
            },
            *self._projection(primary_id),
        )
        extra_id = "device_0000000000000002"
        projections = {
            "meter_main": self._projection(primary_id),
            "meter_workshop": self._projection(extra_id, 50.0),
        }
        result = await self.service.async_action(
            {
                "expectedRevision": 0,
                "action": "upsert",
                "meterId": "meter_workshop",
                "name": "Мастерская",
                "primary": False,
                "settings": {
                    "enabled": True,
                    "submissionDayOfMonth": 20,
                    "reminderDaysBefore": 2,
                    "sourceDeviceIds": [extra_id],
                },
            },
            projections,
        )
        self.assertEqual(["meter_main", "meter_workshop"], [item["meterId"] for item in result["meters"]])
        self.assertTrue(result["meters"][0]["primary"])
        self.assertEqual([extra_id], self.service.source_bindings["meter_workshop"])
        self.assertEqual([primary_id], self.primary.source_device_ids)

    async def test_ten_meter_fixture_shape_restart_and_stale_write(self) -> None:
        projections = {"meter_main": (None, None, None, None, None)}
        revision = 0
        for index in range(1, 10):
            meter_id = f"meter_{index:02d}"
            device_id = f"device_{index:016x}"
            projections[meter_id] = self._projection(device_id, float(index))
            await self.service.async_action(
                {
                    "expectedRevision": revision,
                    "action": "upsert",
                    "meterId": meter_id,
                    "name": f"Счётчик {index}",
                    "settings": {
                        "enabled": True,
                        "submissionDayOfMonth": 25,
                        "reminderDaysBefore": 3,
                        "sourceDeviceIds": [device_id],
                    },
                },
                projections,
            )
            revision += 1
        document = await self.service.async_document(projections)
        self.assertEqual(10, len(document["meters"]))
        restored = EnergyMetersService(
            _Store(self.collection_store.saved[-1]),
            self.primary,
            local_today=lambda: date(2026, 8, 22),
        )
        await restored.async_load()
        self.assertEqual(10, len((await restored.async_document(projections))["meters"]))
        with self.assertRaises(EnergyMeterViolation) as raised:
            await restored.async_action(
                {"expectedRevision": 0, "action": "delete", "meterId": "meter_01"},
                projections,
            )
        self.assertTrue(raised.exception.stale)

    async def test_primary_mutation_fails_closed(self) -> None:
        with self.assertRaises(EnergyMeterViolation):
            await self.service.async_action(
                {"expectedRevision": 0, "action": "delete", "meterId": "meter_main"},
                {"meter_main": (None, None, None, None, None)},
            )
