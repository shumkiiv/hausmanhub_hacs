"""Synthetic compatibility checks for the audited read-only Android models."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_android_compatibility as android_check  # noqa: E402


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class AndroidModelCompatibilityTest(unittest.TestCase):
    """Keep HASC v12 representable by existing Kotlin model primitives."""

    def setUp(self) -> None:
        self.home = load_json(android_check.HOME_FIXTURE)
        self.home_schema = load_json(android_check.HOME_SCHEMA)
        self.action_schema = load_json(android_check.ACTION_SCHEMA)

    def check(self, payload: object) -> android_check.AndroidHomeModel:
        return android_check.check_android_payload(
            payload,
            home_schema=self.home_schema,
            action_schema=self.action_schema,
        )

    def test_current_fixture_maps_to_rooms_devices_actions_and_requests(self) -> None:
        model = self.check(self.home)

        self.assertEqual(1, len(model.rooms))
        self.assertEqual("living", model.rooms[0].room_id)
        self.assertEqual(("living_ac",), model.rooms[0].device_ids)
        self.assertEqual("climate", model.devices[0].domain)
        self.assertEqual(
            ("set_room_target", "turn_room_off"),
            tuple(action.action_id for action in model.rooms[0].actions),
        )
        self.assertEqual(
            {
                "request_id": "android-check-1",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 18.0,
            },
            model.rooms[0].actions[0].request,
        )
        self.assertEqual(
            ("Включена только проверка без команд",),
            model.rooms[0].actions[0].blocked_reason_labels,
        )

    def test_android_long_and_exact_json_number_boundaries_are_enforced(self) -> None:
        too_late = copy.deepcopy(self.home)
        too_late["generated_at"] = android_check.ANDROID_LONG_MAXIMUM + 1  # type: ignore[index]
        with self.assertRaisesRegex(
            android_check.AndroidCompatibilityError,
            "Android Long",
        ):
            self.check(too_late)

        imprecise_revision = copy.deepcopy(self.home)
        imprecise_revision["state_revision"] = (  # type: ignore[index]
            android_check.JSON_SAFE_INTEGER_MAXIMUM + 1
        )
        with self.assertRaises(android_check.AndroidCompatibilityError):
            self.check(imprecise_revision)

    def test_actions_require_russian_reasons_and_valid_hasc_requests(self) -> None:
        missing_reason = copy.deepcopy(self.home)
        missing_reason["display_names"]["blocked_reasons"].pop("shadow_only")  # type: ignore[index]
        with self.assertRaises(android_check.AndroidCompatibilityError):
            self.check(missing_reason)

        invalid_input = copy.deepcopy(self.home)
        invalid_input["rooms"][0]["control"]["action_inputs"][  # type: ignore[index]
            "set_room_target"
        ]["target_temperature"]["minimum"] = 17.0
        with self.assertRaises(android_check.AndroidCompatibilityError):
            self.check(invalid_input)

    def test_every_hasc_device_kind_requires_an_android_domain_mapping(self) -> None:
        expanded_schema = copy.deepcopy(self.home_schema)
        expanded_schema["$defs"]["device"]["properties"]["kind"][  # type: ignore[index]
            "enum"
        ].append("air_purifier")

        with self.assertRaisesRegex(
            android_check.AndroidCompatibilityError,
            "domain mappings",
        ):
            android_check.check_android_payload(
                self.home,
                home_schema=expanded_schema,
                action_schema=self.action_schema,
            )


if __name__ == "__main__":
    unittest.main()
