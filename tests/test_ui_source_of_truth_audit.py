"""Keep the audited UI source-of-truth matrix exact and implementation-safe."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "ui-source-of-truth-audit.json"
NAVIGATION = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "frontend"
    / "hausman-hub-navigation.js"
)
EXPECTED_MAIN = (
    "overview",
    "lighting",
    "climate",
    "rooms",
    "media",
    "security",
    "devices",
    "energy",
    "scenarios",
    "settings",
)


class UiSourceOfTruthAuditTest(unittest.TestCase):
    def test_matrix_covers_the_exact_panel_navigation(self) -> None:
        payload = json.loads(AUDIT.read_text(encoding="utf-8"))
        self.assertEqual(
            {"name": "hausman-hub-ui-source-of-truth-audit", "version": 1},
            payload["contract"],
        )
        surfaces = {item["id"]: item for item in payload["surfaces"]}
        self.assertEqual(set(EXPECTED_MAIN) | {"kiosk"}, set(surfaces))
        for surface_id in EXPECTED_MAIN:
            self.assertEqual("implemented", surfaces[surface_id]["android"])
            self.assertTrue(surfaces[surface_id]["android_figma"])

        navigation = NAVIGATION.read_text(encoding="utf-8")
        panel_ids = tuple(
            re.findall(r'\{ id: "([a-z]+)", label:', navigation.split("];", 1)[0])
        )
        self.assertEqual(EXPECTED_MAIN, panel_ids)

    def test_archive_is_explicitly_non_implementable(self) -> None:
        payload = json.loads(AUDIT.read_text(encoding="utf-8"))
        rules = payload["rules"]
        self.assertEqual("ARCHIVE /", rules["archive_prefix"])
        self.assertIs(False, rules["archive_is_implementable"])
        for item in payload["surfaces"]:
            self.assertNotIn("archive", item["hacs"])


if __name__ == "__main__":
    unittest.main()
