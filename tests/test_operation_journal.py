from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.operation_journal import (
    MAX_OPERATION_JOURNAL_RECORDS,
    OperationJournalService,
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
        await service.async_append(receipt("scenario-3", "scenario_run", confirmed=True))
        await service.async_append(
            receipt("voice-4", "voice.yandexGreeting.test", accepted=False)
        )

        snapshot = service.snapshot()
        self.assertEqual(4, snapshot["sequence"])
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
