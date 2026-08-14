"""Release acceptance contract for the standalone HausmanHub 1.52 line."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Release152AcceptanceTest(unittest.TestCase):
    def test_release_is_native_and_contains_no_android_artifacts(self) -> None:
        manifest = json.loads(
            (ROOT / "custom_components" / "hausman_hub" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        configuration = (
            ROOT
            / "custom_components"
            / "hausman_hub"
            / "application"
            / "configuration.py"
        ).read_text(encoding="utf-8")
        panel = (
            ROOT
            / "custom_components"
            / "hausman_hub"
            / "frontend"
            / "hausman-hub-panel.js"
        ).read_text(encoding="utf-8")

        self.assertEqual("1.52.91", manifest["version"])
        self.assertIn('CONNECTION_MODE_DEFAULT = "home_assistant"', configuration)
        self.assertNotIn('title: "Совместимый внешний API"', panel)
        self.assertNotIn('"Адрес совместимого API"', panel)
        tracked = [path.as_posix().lower() for path in ROOT.rglob("*") if path.is_file()]
        self.assertFalse(any(path.endswith((".apk", ".aab")) for path in tracked))
        self.assertFalse(any("components/android" in path for path in tracked))


if __name__ == "__main__":
    unittest.main()
