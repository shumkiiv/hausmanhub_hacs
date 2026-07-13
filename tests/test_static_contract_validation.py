from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hasc_validation import (  # noqa: E402
    validate_common_inventory,
    validate_diagnostics_contract,
    validate_shadow_evidence,
)


def load(relative_path: str) -> object:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


class StaticContractValidationTest(unittest.TestCase):
    def assert_valid(self, relative_path: str, validator: object) -> None:
        self.assertEqual([], validator(load(relative_path)), relative_path)  # type: ignore[operator]

    def assert_invalid(self, relative_path: str, validator: object, expected_error: str) -> None:
        errors = validator(load(relative_path))  # type: ignore[operator]
        self.assertTrue(errors, relative_path)
        self.assertTrue(
            any(expected_error in error for error in errors),
            f"{relative_path} did not report {expected_error!r}: {errors}",
        )

    def test_common_valid_fixtures(self) -> None:
        self.assert_valid("fixtures/common_contract/valid_minimal.json", validate_common_inventory)
        self.assert_valid("fixtures/common_contract/valid_owner_boundaries.json", validate_common_inventory)

    def test_common_rejects_boundary_violations(self) -> None:
        cases = {
            "invalid_unknown_contour.json": "unknown contour_id",
            "invalid_missing_room.json": "must reference an existing room",
            "invalid_service_path.json": "forbidden execution or sensitive field",
            "invalid_direct_execution.json": "forbidden execution or sensitive field",
            "invalid_executed_audit.json": "never executed",
            "invalid_common_owner.json": "cannot make Common, facade, or integration a decision owner",
        }
        for fixture, expected_error in cases.items():
            self.assert_invalid(
                f"fixtures/common_contract/{fixture}", validate_common_inventory, expected_error
            )

    def test_shadow_fixture_stays_unresolved_and_read_only(self) -> None:
        self.assert_valid("fixtures/shadow_evidence/valid_unresolved.json", validate_shadow_evidence)
        self.assert_invalid(
            "fixtures/shadow_evidence/invalid_parity_claim.json",
            validate_shadow_evidence,
            "must remain unresolved",
        )
        self.assert_invalid(
            "fixtures/shadow_evidence/invalid_service_path.json",
            validate_shadow_evidence,
            "forbidden execution or sensitive field",
        )

    def test_diagnostics_fixture_stays_redacted_and_manual_only(self) -> None:
        self.assert_valid("fixtures/diagnostics/valid_redacted.json", validate_diagnostics_contract)
        self.assert_invalid(
            "fixtures/diagnostics/invalid_blocked_without_repair.json",
            validate_diagnostics_contract,
            "must contain a critical redaction_failure issue",
        )
        self.assert_invalid(
            "fixtures/diagnostics/invalid_service_path.json",
            validate_diagnostics_contract,
            "forbidden execution or sensitive field",
        )

    def test_diagnostics_rejects_unknown_shadow_mismatch_category(self) -> None:
        fixture = load("fixtures/diagnostics/valid_redacted.json")
        fixture["shadow_parity"]["mismatch_categories"] = ["undocumented_gap"]
        errors = validate_diagnostics_contract(fixture)
        self.assertTrue(
            any("must use a documented mismatch category" in error for error in errors), errors
        )

    def test_cli_accepts_a_valid_fixture(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/validate_fixture.py",
                "common",
                "fixtures/common_contract/valid_minimal.json",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_cli_reports_invalid_fixture(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/validate_fixture.py",
                "common",
                "fixtures/common_contract/invalid_service_path.json",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("forbidden execution or sensitive field", result.stderr)

    def test_hacs_runtime_skeleton_is_still_absent(self) -> None:
        self.assertFalse((ROOT / "hacs.json").exists())
        self.assertFalse((ROOT / "custom_components").exists())


if __name__ == "__main__":
    unittest.main()
