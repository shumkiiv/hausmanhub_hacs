from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.operation_journal import (
    MAX_OPERATION_JOURNAL_RECORDS,
    OperationJournalService,
    scenario_operation_receipt,
)


class MemoryStore:
    def __init__(self, payload=None) -> None:
        self.payload = payload
        self.saved: list[dict[str, object]] = []

    async def async_load(self):
        return self.payload

    async def async_save(self, payload):
        self.payload = payload
        self.saved.append(payload)


def receipt(
    request_id: str,
    operation: str,
    *,
    accepted: bool = True,
    confirmed: bool = False,
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "operation": operation,
        "accepted": accepted,
        "confirmed": confirmed,
        "status": "confirmed" if confirmed else "accepted" if accepted else "failed",
        "reason": "Проверенный результат",
        "error_code": None,
    }


class OperationJournalTests(unittest.IsolatedAsyncioTestCase):
    def test_fixture_matches_public_contract_and_rejects_private_target(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (
                root
                / "custom_components/hausman_hub/contracts/v1/operation-journal.schema.json"
            ).read_text(encoding="utf-8")
        )
        fixture = json.loads(
            (root / "fixtures/hausmanhub_operation_journal_v1/journal.json").read_text(
                encoding="utf-8"
            )
        )
        validator = Draft202012Validator(schema)
        validator.validate(fixture)
        unsafe = json.loads(json.dumps(fixture))
        unsafe["records"][0]["target_id"] = "media_player.private"
        self.assertTrue(list(validator.iter_errors(unsafe)))

    async def test_records_all_sources_and_filters_by_correlation(self) -> None:
        store = MemoryStore()
        service = OperationJournalService(store, now_ms=lambda: 1786375200000)
        await service.async_load()

        await service.async_append(receipt("device-1", "device_action", confirmed=True))
        await service.async_append(receipt("climate-2", "climate.tablet_action"))
        await service.async_append(
            receipt("scenario-3", "scenario_run", confirmed=True)
        )
        await service.async_append(
            receipt("voice-4", "voice.yandexGreeting.test", accepted=False)
        )

        snapshot = service.snapshot()
        self.assertEqual(4, snapshot["sequence"])
        self.assertEqual(4, snapshot["page"]["returned"])
        self.assertFalse(snapshot["page"]["has_more"])
        self.assertEqual(512, snapshot["page"]["retention_limit"])
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (
                root
                / "custom_components/hausman_hub/contracts/v1/operation-journal.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(snapshot)
        self.assertEqual(
            ["voice", "scenario", "climate", "device"],
            [item["source"] for item in snapshot["records"]],
        )
        filtered = service.snapshot(correlation_id="climate-2")
        self.assertEqual(1, len(filtered["records"]))
        self.assertEqual("climate", filtered["records"][0]["source"])

        explicit = receipt("private-request", "scenario_run", confirmed=True)
        explicit["correlation_id"] = "corr.scenario.public-1"
        record = await service.async_append(explicit)
        self.assertEqual("corr.scenario.public-1", record["correlation_id"])

    async def test_restart_restores_sequence_and_records(self) -> None:
        store = MemoryStore()
        first = OperationJournalService(store, now_ms=lambda: 10)
        await first.async_append(receipt("device-1", "device_action"))

        restored = OperationJournalService(store, now_ms=lambda: 20)
        await restored.async_load()
        new_record = await restored.async_append(receipt("scenario-2", "scenario_run"))

        self.assertEqual(2, new_record["sequence"])
        self.assertEqual(
            ["scenario-2", "device-1"],
            [item["correlation_id"] for item in restored.snapshot()["records"]],
        )

    async def test_scenario_trace_is_redacted_persisted_and_schema_valid(self) -> None:
        store = MemoryStore()
        service = OperationJournalService(store, now_ms=lambda: 50)
        normalized = scenario_operation_receipt(
            {
                "run_id": "run-safety-1",
                "scenario_id": "night_light",
                "execution_mode": "queued",
                "status": "partial",
                "confirmed": False,
                "evidence_revision": "revision-1",
                "condition_results": [
                    {
                        "condition_id": "presence",
                        "passed": False,
                        "outcome": "skipped",
                        "reason": "entity binary_sensor.private is unavailable",
                    }
                ],
                "receipts": [
                    {
                        "action_id": "light_on",
                        "status": "completed",
                        "confirmed": True,
                        "entity_id": "light.private_room",
                    },
                    {
                        "action_id": "notify",
                        "status": "failed",
                        "error": "notify_failed",
                    },
                ],
            }
        )

        record = await service.async_append(normalized)

        self.assertEqual("partial", record["scenario"]["outcome"])
        self.assertNotIn("entity_id", json.dumps(record, ensure_ascii=False))
        self.assertNotIn(
            "binary_sensor.private", json.dumps(record, ensure_ascii=False)
        )
        self.assertEqual(
            "condition_not_met",
            record["scenario"]["decisions"][0]["reason"],
        )
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (
                root
                / "custom_components/hausman_hub/contracts/v1/operation-journal.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(service.snapshot())

    async def test_invalid_scenario_trace_fails_closed(self) -> None:
        store = MemoryStore()
        service = OperationJournalService(store)
        unsafe = receipt("run-unsafe", "scenario_run")
        unsafe["scenario"] = {"scenario_id": "../private"}

        with self.assertRaises(ValueError):
            await service.async_append(unsafe)

    async def test_shadow_trace_never_claims_physical_confirmation(self) -> None:
        store = MemoryStore()
        service = OperationJournalService(store, now_ms=lambda: 60)
        normalized = scenario_operation_receipt(
            {
                "run_id": "run-shadow-1",
                "scenario_id": "bathroom_shadow",
                "execution_mode": "single",
                "command_mode": "shadow",
                "status": "completed",
                "confirmed": True,
                "evidence_revision": "revision-2",
                "condition_results": [],
                "receipts": [
                    {
                        "action_id": "fan_on",
                        "status": "completed",
                        "confirmed": True,
                    }
                ],
            }
        )

        record = await service.async_append(normalized)

        self.assertEqual("accepted", record["status"])
        self.assertFalse(record["confirmed"])
        self.assertEqual("shadow", record["scenario"]["command_mode"])
        self.assertIsNone(record["scenario"]["actions"][0]["confirmed"])
        self.assertEqual("shadow_plan", record["scenario"]["actions"][0]["reason"])

    async def test_skipped_shadow_trace_is_retained_as_failed_without_confirmation(
        self,
    ) -> None:
        store = MemoryStore()
        service = OperationJournalService(store, now_ms=lambda: 70)
        normalized = scenario_operation_receipt(
            {
                "run_id": "run-shadow-skipped",
                "scenario_id": "bathroom_shadow",
                "execution_mode": "single",
                "command_mode": "shadow",
                "status": "skipped",
                "confirmed": False,
                "condition_results": [
                    {
                        "condition_id": "humidity",
                        "passed": False,
                        "outcome": "skipped",
                        "reason": "condition_not_met",
                    }
                ],
                "receipts": [],
            }
        )

        record = await service.async_append(normalized)

        self.assertEqual("failed", record["status"])
        self.assertFalse(record["accepted"])
        self.assertFalse(record["confirmed"])
        self.assertEqual("skipped", record["scenario"]["outcome"])

    async def test_journal_is_bounded_and_does_not_store_targets(self) -> None:
        store = MemoryStore()
        service = OperationJournalService(store, now_ms=lambda: 30)
        for index in range(MAX_OPERATION_JOURNAL_RECORDS + 3):
            value = receipt(f"device-{index}", "device_action")
            value["target_id"] = "private.target"
            await service.async_append(value)

        records = service.snapshot(limit=MAX_OPERATION_JOURNAL_RECORDS)["records"]
        self.assertEqual(MAX_OPERATION_JOURNAL_RECORDS, len(records))
        self.assertEqual("device-514", records[0]["correlation_id"])
        self.assertNotIn("target_id", records[0])

    async def test_keyset_pages_are_stable_and_do_not_overlap(self) -> None:
        store = MemoryStore()
        service = OperationJournalService(store, now_ms=lambda: 40)
        for index in range(5):
            await service.async_append(receipt(f"device-{index}", "device_action"))

        first = service.snapshot(limit=2)
        second = service.snapshot(
            limit=2,
            before_sequence=first["page"]["next_before_sequence"],
        )
        third = service.snapshot(
            limit=2,
            before_sequence=second["page"]["next_before_sequence"],
        )

        self.assertEqual([5, 4], [item["sequence"] for item in first["records"]])
        self.assertEqual([3, 2], [item["sequence"] for item in second["records"]])
        self.assertEqual([1], [item["sequence"] for item in third["records"]])
        self.assertTrue(first["page"]["has_more"])
        self.assertTrue(second["page"]["has_more"])
        self.assertFalse(third["page"]["has_more"])
        self.assertIsNone(third["page"]["next_before_sequence"])

        with self.assertRaises(ValueError):
            service.snapshot(before_sequence=0)
