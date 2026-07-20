"""Tests for the frozen, command-free climate migration reference."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from custom_components.hausman_hub.domain import climate_reference
from custom_components.hausman_hub.domain.climate_reference import (
    CLIMATE_REFERENCE_PATH,
    CLIMATE_REFERENCE_SHA256,
    ClimateReferenceViolation,
    REFERENCE_CATEGORIES,
    REFERENCE_POLICY_PRIORITY,
    REFERENCE_PROTECTION_CODES,
    load_climate_reference_suite,
    validate_climate_reference_suite,
)


class ClimateReferenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = load_climate_reference_suite()

    def test_packaged_suite_is_frozen_and_never_enables_commands(self) -> None:
        self.assertTrue(CLIMATE_REFERENCE_PATH.is_relative_to(Path.cwd()))
        self.assertEqual(
            CLIMATE_REFERENCE_SHA256,
            hashlib.sha256(CLIMATE_REFERENCE_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            {"mode": "reference_only", "commands_enabled": False},
            self.suite["execution"],
        )
        source = self.suite["source"]
        self.assertTrue(source["read_only"])  # type: ignore[index]
        self.assertEqual(
            "0bf681c4278f14f1ad7808b5fe0726b199bcdccc",
            source["repository_revision"],  # type: ignore[index]
        )
        self.assertEqual(30, len(self.suite["cases"]))
        self.assertEqual(31, len(self.suite["protections"]))

    def test_suite_covers_each_algorithm_area_and_fixed_policy_priority(self) -> None:
        self.assertEqual(
            REFERENCE_CATEGORIES,
            {item["category"] for item in self.suite["cases"]},  # type: ignore[index]
        )
        self.assertEqual(
            REFERENCE_POLICY_PRIORITY,
            tuple(self.suite["policy_priority"]),  # type: ignore[arg-type]
        )
        self.assertEqual(
            REFERENCE_PROTECTION_CODES,
            {item["code"] for item in self.suite["protections"]},  # type: ignore[index]
        )

    def test_anchor_cases_preserve_working_decisions_and_safety_guards(self) -> None:
        cases = {item["id"]: item for item in self.suite["cases"]}  # type: ignore[index]

        self.assertEqual(
            ("cool", 26, "low"),
            self._decision(cases["stopped_ac_starts_at_default_gap"]),
        )
        self.assertEqual(
            ("maintain", 26, "medium"),
            self._decision(cases["weak_cooling_raises_fan_first"]),
        )
        self.assertEqual(
            ["minimum_off_pause"],
            cases["minimum_off_pause_blocks_restart"]["expected"]["blockers"],
        )
        self.assertEqual(
            "safe_off",
            cases["open_window_forces_safe_off"]["expected"]["action"],
        )
        self.assertEqual(
            "humidifier_on",
            cases["dry_closed_room_starts_humidifier"]["expected"][
                "auxiliary_action"
            ],
        )
        self.assertEqual(
            19.5,
            cases["winter_trv_uses_cold_weather_target"]["expected"][
                "auxiliary_target"
            ],
        )

    def test_semantic_validator_rejects_weakened_or_private_reference_data(self) -> None:
        changed_priority = copy.deepcopy(self.suite)
        changed_priority["policy_priority"][0:2] = [  # type: ignore[index]
            "safety_lockout",
            "away",
        ]
        with self.assertRaisesRegex(ClimateReferenceViolation, "priority"):
            validate_climate_reference_suite(changed_priority)

        duplicate_case = copy.deepcopy(self.suite)
        duplicate_case["cases"].append(duplicate_case["cases"][0])  # type: ignore[index]
        with self.assertRaisesRegex(ClimateReferenceViolation, "case ids"):
            validate_climate_reference_suite(duplicate_case)

        private_value = copy.deepcopy(self.suite)
        private_value["cases"][0]["input"]["entity_id"] = "climate.private"  # type: ignore[index]
        with self.assertRaisesRegex(ClimateReferenceViolation, "private runtime"):
            validate_climate_reference_suite(private_value)

    def test_loader_rejects_an_unreviewed_file_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "climate-reference-suite.json"
            payload = copy.deepcopy(self.suite)
            payload["cases"][0]["name"] += " Изменено."  # type: ignore[index]
            changed.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with (
                patch.object(climate_reference, "CLIMATE_REFERENCE_PATH", changed),
                self.assertRaisesRegex(ClimateReferenceViolation, "fingerprint"),
            ):
                load_climate_reference_suite()

    @staticmethod
    def _decision(case: dict[str, object]) -> tuple[object, object, object]:
        expected = case["expected"]
        return (
            expected["action"],  # type: ignore[index]
            expected["setpoint"],  # type: ignore[index]
            expected["fan_mode"],  # type: ignore[index]
        )


if __name__ == "__main__":
    unittest.main()
