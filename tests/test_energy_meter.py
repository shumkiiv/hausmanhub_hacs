"""Durability and projection tests for the utility energy meter."""

from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from custom_components.hausman_hub.application.energy_meter import (
    EnergyMeterService,
    EnergyMeterViolation,
)


class _Store:
    def __init__(self, loaded: dict[str, object] | None = None) -> None:
        self.loaded = loaded
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> dict[str, object] | None:
        return self.loaded

    async def async_save(self, value: dict[str, object]) -> None:
        self.saved.append(value)


class EnergyMeterServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = _Store()
        self.today = date(2026, 8, 11)
        self.service = EnergyMeterService(
            self.store,
            now=lambda: datetime(2026, 8, 11, 1, 2, tzinfo=timezone.utc),
            local_today=lambda: self.today,
        )
        await self.service.async_load()

    async def test_submission_resets_only_the_calculated_monthly_cycle(self) -> None:
        configured = await self.service.async_action(
            {
                "expectedRevision": 0,
                "action": "configure",
                "settings": {
                    "enabled": True,
                    "submissionDayOfMonth": 25,
                    "reminderDaysBefore": 3,
                },
            },
            276.46,
        )
        self.assertEqual("none", configured["submission"]["status"])
        submitted = await self.service.async_action(
            {"expectedRevision": 1, "action": "submit", "readingKwh": 18342.4},
            276.46,
        )
        self.assertEqual(0.0, submitted["cycle"]["consumptionKwh"])
        self.assertEqual(276.46, submitted["source"]["currentTotalKwh"])
        projected = self.service.document(281.96)
        self.assertEqual(18347.9, projected["reading"]["currentKwh"])
        self.assertEqual(5.5, projected["cycle"]["consumptionKwh"])
        self.assertTrue(projected["reading"]["estimated"])

    async def test_correction_moves_reading_without_starting_a_new_cycle(self) -> None:
        await self.service.async_action(
            {"expectedRevision": 0, "action": "submit", "readingKwh": 1000.0},
            50.0,
        )
        corrected = await self.service.async_action(
            {"expectedRevision": 1, "action": "correct", "readingKwh": 1007.0},
            55.0,
        )
        self.assertEqual(7.0, corrected["cycle"]["consumptionKwh"])
        self.assertEqual("correction", corrected["history"][0]["kind"])
        self.assertEqual("submission", corrected["history"][1]["kind"])
        self.assertEqual(
            corrected["history"][1]["recordedAt"],
            corrected["cycle"]["startedAt"],
        )

    async def test_source_counter_drop_is_explicit_and_not_fabricated(self) -> None:
        await self.service.async_action(
            {"expectedRevision": 0, "action": "submit", "readingKwh": 500.0},
            100.0,
        )
        projected = self.service.document(2.0)
        self.assertEqual("reset_detected", projected["source"]["state"])
        self.assertIsNone(projected["cycle"]["consumptionKwh"])
        self.assertEqual(500.0, projected["reading"]["currentKwh"])

    async def test_source_selection_change_requires_a_new_anchor(self) -> None:
        await self.service.async_action(
            {"expectedRevision": 0, "action": "submit", "readingKwh": 500.0},
            100.0,
            "device_one|device_two",
        )
        projected = self.service.document(140.0, "device_one|device_three")
        self.assertEqual("reset_detected", projected["source"]["state"])
        self.assertEqual(500.0, projected["reading"]["currentKwh"])
        self.assertIsNone(projected["cycle"]["consumptionKwh"])

    async def test_selected_device_is_persisted_and_stamped_on_history(self) -> None:
        source_id = "device_0123456789abcdef"
        configured = await self.service.async_action(
            {
                "expectedRevision": 0,
                "action": "configure",
                "settings": {
                    "enabled": True,
                    "submissionDayOfMonth": 25,
                    "reminderDaysBefore": 3,
                    "sourceDeviceId": source_id,
                },
            },
            51.03,
            source_id,
            source_id,
            "Вводной автомат",
        )
        self.assertEqual(source_id, configured["settings"]["sourceDeviceId"])
        self.assertEqual(source_id, configured["source"]["deviceId"])
        self.assertEqual("Вводной автомат", configured["source"]["name"])
        submitted = await self.service.async_action(
            {"expectedRevision": 1, "action": "submit", "readingKwh": 1000.0},
            51.03,
            source_id,
            source_id,
            "Вводной автомат",
        )
        self.assertEqual(source_id, submitted["history"][0]["sourceDeviceId"])

    async def test_legacy_document_migrates_nullable_source_fields(self) -> None:
        legacy = {
            "revision": 1,
            "updatedAt": "2026-08-11T01:02:00Z",
            "settings": {
                "enabled": True,
                "submissionDayOfMonth": 25,
                "reminderDaysBefore": 3,
            },
            "anchor": None,
            "cycle": None,
            "lastSubmissionDate": None,
            "history": [
                {
                    "id": "reading_1",
                    "kind": "correction",
                    "readingKwh": 100.0,
                    "sourceTotalKwh": 10.0,
                    "recordedAt": "2026-08-11T01:02:00Z",
                }
            ],
        }
        restored = EnergyMeterService(_Store(legacy), local_today=lambda: self.today)
        await restored.async_load()
        document = restored.document(10.0)
        self.assertIsNone(document["settings"]["sourceDeviceId"])
        self.assertIsNone(document["history"][0]["sourceDeviceId"])

    async def test_month_end_reminder_is_clamped_and_writes_are_locked(self) -> None:
        self.today = date(2026, 2, 26)
        result = await self.service.async_action(
            {
                "expectedRevision": 0,
                "action": "configure",
                "settings": {
                    "enabled": True,
                    "submissionDayOfMonth": 31,
                    "reminderDaysBefore": 3,
                },
            },
            None,
        )
        self.assertEqual("2026-02-28", result["submission"]["nextDate"])
        self.assertEqual("upcoming", result["submission"]["status"])
        with self.assertRaises(EnergyMeterViolation) as raised:
            await self.service.async_action(
                {"expectedRevision": 0, "action": "correct", "readingKwh": 1.0},
                None,
            )
        self.assertTrue(raised.exception.stale)

    async def test_restart_restores_anchor_and_history(self) -> None:
        await self.service.async_action(
            {"expectedRevision": 0, "action": "submit", "readingKwh": 42.0},
            10.0,
        )
        restored = EnergyMeterService(
            _Store(self.store.saved[-1]),
            local_today=lambda: self.today,
        )
        await restored.async_load()
        self.assertEqual(43.25, restored.document(11.25)["reading"]["currentKwh"])
        self.assertEqual(1, restored.document(11.25)["revision"])
