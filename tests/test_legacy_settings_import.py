"""Safe, deterministic preview of the retired Node-RED settings context."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.legacy_settings_import import (
    LegacySettingsImportViolation,
    preview_legacy_settings,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "hausmanhub_legacy_settings_v1" / "request.json"
SCHEMA = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "legacy-settings-preview.schema.json"
)


def fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class LegacySettingsImportTest(unittest.TestCase):
    def test_preview_separates_configuration_runtime_and_sensitive_values(self) -> None:
        payload = fixture()

        result = preview_legacy_settings(payload)

        self.assertEqual(25.0, result["migratable"]["home_target_temperature"])  # type: ignore[index]
        self.assertEqual(
            [
                {
                    "legacy_room_id": "living",
                    "target_temperature": 25.0,
                    "target_humidity": 45,
                }
            ],
            result["migratable"]["rooms"],  # type: ignore[index]
        )
        self.assertEqual(["smart_home_light_preset"], result["recognized_pending"])
        self.assertEqual(["ac_pause_until"], result["ignored_runtime"])
        self.assertEqual(["max_alert_user_ids"], result["rejected_sensitive"])
        self.assertFalse(result["write_performed"])
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(result)

    def test_sensitive_values_are_neither_returned_nor_hashed(self) -> None:
        payload = fixture()
        payload["globals"]["private_password"] = "first-private-value"  # type: ignore[index]
        first = preview_legacy_settings(payload)
        payload["globals"]["private_password"] = "second-private-value"  # type: ignore[index]
        second = preview_legacy_settings(payload)

        serialized = json.dumps(first, sort_keys=True)
        self.assertNotIn("first-private-value", serialized)
        self.assertNotIn("second-private-value", serialized)
        self.assertEqual(first["preview_id"], second["preview_id"])
        self.assertIn("private_password", first["rejected_sensitive"])

    def test_preview_is_deterministic_for_reordered_global_keys(self) -> None:
        payload = fixture()
        reordered = copy.deepcopy(payload)
        reordered["globals"] = dict(reversed(list(payload["globals"].items())))  # type: ignore[index]

        self.assertEqual(
            preview_legacy_settings(payload),
            preview_legacy_settings(reordered),
        )

    def test_invalid_contract_and_targets_fail_closed(self) -> None:
        invalid_contract = fixture()
        invalid_contract["contract"]["version"] = 2  # type: ignore[index]
        invalid_target = fixture()
        invalid_target["globals"]["home_target_temp"] = 31  # type: ignore[index]
        invalid_room = fixture()
        invalid_room["globals"]["climate_rooms"] = {"Living room": {"comfortTemp": 25}}  # type: ignore[index]

        for payload in (invalid_contract, invalid_target, invalid_room):
            with self.subTest(payload=payload):
                with self.assertRaises(LegacySettingsImportViolation):
                    preview_legacy_settings(payload)

    def test_export_is_bounded_and_unknown_values_are_not_echoed(self) -> None:
        payload = fixture()
        payload["globals"]["vendor_private_blob"] = {"nested": "private-value"}  # type: ignore[index]
        result = preview_legacy_settings(payload)

        self.assertEqual(["vendor_private_blob"], result["unknown"])
        self.assertNotIn("private-value", json.dumps(result, sort_keys=True))

        oversized = fixture()
        oversized["globals"] = {f"key_{index}": index for index in range(129)}
        with self.assertRaises(LegacySettingsImportViolation):
            preview_legacy_settings(oversized)


if __name__ == "__main__":
    unittest.main()
