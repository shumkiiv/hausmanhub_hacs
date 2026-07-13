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

    def assert_invalid(self, relative_path: str, validator: object) -> None:
        self.assertTrue(validator(load(relative_path)), relative_path)  # type: ignore[operator]

    def test_common_valid_fixtures(self) -> None:
        self.assert_valid("fixtures/common_contract/valid_minimal.json", validate_common_inventory)
        self.assert_valid("fixtures/common_contract/valid_owner_boundaries.json", validate_common_inventory)

    def test_common_rejects_boundary_violations(self) -> None:
        for fixture in (
            "invalid_unknown_contour.json",
            "invalid_missing_room.json",
            "invalid_service_path.json",
            "invalid_direct_execution.json",
            "invalid_executed_audit.json",
            "invalid_common_owner.json",
        ):
            self.assert_invalid(f"fixtures/common_contract/{fixture}", validate_common_inventory)

    def test_shadow_fixture_stays_unresolved_and_read_only(self) -> None:
        self.assert_valid("fixtures/shadow_evidence/valid_unresolved.json", validate_shadow_evidence)
        self.assert_invalid("fixtures/shadow_evidence/invalid_parity_claim.json", validate_shadow_evidence)
        self.assert_invalid("fixtures/shadow_evidence/invalid_service_path.json", validate_shadow_evidence)

    def test_diagnostics_fixture_stays_redacted_and_manual_only(self) -> None:
        self.assert_valid("fixtures/diagnostics/valid_redacted.json", validate_diagnostics_contract)
        self.assert_invalid(
            "fixtures/diagnostics/invalid_blocked_without_repair.json", validate_diagnostics_contract
        )
        self.assert_invalid("fixtures/diagnostics/invalid_service_path.json", validate_diagnostics_contract)

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

    def test_hacs_runtime_skeleton_is_still_absent(self) -> None:
        self.assertFalse((ROOT / "hacs.json").exists())
        self.assertFalse((ROOT / "custom_components").exists())


if __name__ == "__main__":
    unittest.main()
