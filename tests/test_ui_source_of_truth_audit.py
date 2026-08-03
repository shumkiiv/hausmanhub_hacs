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

    def test_figma_pages_separate_canon_from_archive_without_deletion(self) -> None:
        payload = json.loads(AUDIT.read_text(encoding="utf-8"))
        structure = payload["hacs_figma_structure"]
        self.assertEqual(51, structure["total_top_level_nodes"])
        self.assertEqual(0, structure["nodes_deleted"])
        pages = {page["purpose"]: page for page in structure["pages"]}
        self.assertEqual(
            {"shared_canon", "archive"},
            set(pages),
        )
        shared = set(pages["shared_canon"]["node_ids"])
        self.assertTrue(shared)
        self.assertEqual(18, pages["shared_canon"]["top_level_node_count"])
        self.assertEqual(
            structure["total_top_level_nodes"],
            len(shared) + pages["archive"]["top_level_node_count"],
        )
        self.assertEqual([1280, 720], structure["canonical_sizes"]["290:64"])
        self.assertEqual([1280, 988], structure["canonical_sizes"]["294:64"])


if __name__ == "__main__":
    unittest.main()
