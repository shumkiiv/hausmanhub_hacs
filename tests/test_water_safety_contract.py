from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "custom_components/hausman_hub/contracts/v1/water-safety.schema.json").read_text(
        encoding="utf-8"
    )
)
FIXTURE = json.loads(
    (ROOT / "fixtures/hausmanhub_water_safety_v1/water-safety.json").read_text(
        encoding="utf-8"
    )
)


class WaterSafetyContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = Draft202012Validator(SCHEMA)

    def test_golden_document_is_valid(self) -> None:
        self.validator.validate(FIXTURE)

    def test_auto_close_requires_recipient_and_verified_direction(self) -> None:
        document = copy.deepcopy(FIXTURE)
        document["configuration"]["recipientServices"] = []
        document["configuration"]["directionVerified"] = False

        with self.assertRaises(ValidationError):
            self.validator.validate(document)

    def test_clear_latch_is_explicit_and_sends_no_open_command(self) -> None:
        request = {
            "action": "clear_latch",
            "expectedRevision": 4,
            "confirmation": True,
        }
        self.validator.evolve(schema=SCHEMA["$defs"]["actionRequest"]).validate(
            request
        )

        serialized = json.dumps(SCHEMA, ensure_ascii=False)
        self.assertIn('"automaticOpenAllowed": {"const": false}', serialized)
        self.assertNotIn('"openAction"', serialized)

    def test_direction_test_receipt_proves_no_command_was_sent(self) -> None:
        receipt = {
            "contract": {
                "name": "hausman-hub-water-direction-test",
                "version": 1,
            },
            "entityId": "switch.example_cold_water_reducer",
            "commandSent": False,
            "readBack": "open",
            "observedState": "on",
            "observedAt": 1787389200000,
            "safeToConfirm": True,
        }

        self.validator.evolve(
            schema=SCHEMA["$defs"]["directionTestReceipt"]
        ).validate(receipt)


if __name__ == "__main__":
    unittest.main()
