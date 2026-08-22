"""Executable parity registry for the polished HACS interface."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "custom_components" / "hausman_hub" / "frontend"
REGISTRY_PATH = ROOT / "docs" / "design" / "HACS_ANDROID_COMPONENT_REGISTRY.json"


class HacsAndroidParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_registry_has_all_canonical_component_families(self) -> None:
        self.assertEqual(
            {"header", "card", "detail", "control", "notice", "picker", "emptyState"},
            set(self.registry["components"]),
        )
        for component in self.registry["components"].values():
            implementation = component["implementation"]
            self.assertTrue(
                any(FRONTEND.glob(f"{implementation}.*")),
                f"Missing registered implementation: {implementation}",
            )

    def test_registry_pins_shared_fixture_and_browser_matrix(self) -> None:
        fixture = ROOT / self.registry["syntheticFixture"]
        self.assertTrue(fixture.is_file())
        self.assertEqual([900, 1280, 1440, 1920], self.registry["browserWidthsPx"])
        self.assertEqual([100, 125, 150], self.registry["browserZoomPercent"])
        self.assertEqual(["light", "dark"], self.registry["themes"])
        self.assertEqual("1.0.251", self.registry["visualReference"]["version"])

    def test_semantic_tokens_cover_both_themes_and_component_groups(self) -> None:
        css = (FRONTEND / "hausman-hub-tokens.css").read_text(encoding="utf-8")
        self.assertIn(":host(.theme-light)", css)
        for token in (
            "--hmh-surface-card", "--hmh-border-default", "--hmh-text-primary",
            "--hmh-text-secondary", "--hmh-radius-control", "--hmh-radius-card",
            "--primary-font-family", "--hmh-status-success", "--hmh-status-warning",
            "--hmh-status-danger",
        ):
            self.assertIn(token, css)

    def test_browser_accessibility_is_a_required_platform_advantage(self) -> None:
        modal = (FRONTEND / "hausman-hub-modal.js").read_text(encoding="utf-8")
        feedback = (FRONTEND / "hausman-hub-feedback.js").read_text(encoding="utf-8")
        self.assertIn('event.key === "Escape"', modal)
        self.assertIn("trapModalTabKey", modal)
        self.assertIn("restoreFocusTo", modal)
        self.assertIn('"aria-live", tone === "error" ? "assertive" : "polite"', feedback)

    def test_every_physical_device_entry_point_is_registered(self) -> None:
        self.assertEqual(
            {"overview", "lighting", "climate", "rooms", "media", "security", "devices", "energy"},
            set(self.registry["physicalDeviceEntryPoints"]),
        )
        device_card = (FRONTEND / "hausman-hub-device-card.js").read_text(encoding="utf-8")
        self.assertIn("openPhysicalDeviceSheet", device_card)


if __name__ == "__main__":
    unittest.main()
