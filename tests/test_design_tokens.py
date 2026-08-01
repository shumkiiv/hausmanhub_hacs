"""Protect the shared Figma, Android and HACS design-token contract."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / "docs" / "design-tokens.json"
CSS = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "frontend"
    / "hausman-hub-tokens.css"
)


class DesignTokensTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(TOKENS.read_text(encoding="utf-8"))

    def test_figma_collections_and_counts_are_explicit(self) -> None:
        self.assertEqual(
            {"name": "hausmanhub-design-tokens", "version": 1},
            {
                "name": self.payload["contract"]["name"],
                "version": self.payload["contract"]["version"],
            },
        )
        collections = self.payload["collections"]
        self.assertEqual(27, collections["primitives"]["variable_count"])
        self.assertEqual(18, collections["semantic"]["variable_count"])
        self.assertEqual(17, collections["metrics"]["variable_count"])
        self.assertEqual(["Light", "Dark"], collections["semantic"]["modes"])

    def test_every_semantic_color_has_cross_surface_names(self) -> None:
        colors = self.payload["semantic_colors"]
        self.assertEqual(18, len(colors))
        css_names = set()
        android_names = set()
        for token in colors.values():
            self.assertRegex(token["light"], r"^#[0-9A-F]{6}$")
            self.assertRegex(token["dark"], r"^#[0-9A-F]{6}$")
            self.assertRegex(token["css"], r"^--hmh-[a-z-]+$")
            self.assertRegex(token["android"], r"^[a-z][A-Za-z]+$")
            css_names.add(token["css"])
            android_names.add(token["android"])
        self.assertEqual(len(colors), len(css_names))
        self.assertEqual(len(colors), len(android_names))

    def test_hacs_defines_every_semantic_css_variable_in_both_modes(self) -> None:
        css = CSS.read_text(encoding="utf-8")
        dark_block, light_tail = css.split(":host(.theme-light)", 1)
        light_block = light_tail.split("}", 1)[0]
        for token in self.payload["semantic_colors"].values():
            name = re.escape(token["css"])
            self.assertRegex(dark_block, rf"{name}\s*:\s*{token['dark']}")
            self.assertRegex(light_block, rf"{name}\s*:\s*{token['light']}")


if __name__ == "__main__":
    unittest.main()
