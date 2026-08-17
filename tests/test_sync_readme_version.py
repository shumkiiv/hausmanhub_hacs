"""Tests for the README release version sync tool."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import sync_readme_version as sync  # noqa: E402


def write_layout(root: Path, manifest_version: str, readme_text: str) -> None:
    """Create a minimal manifest and README layout under `root`."""

    manifest = root / sync.MANIFEST_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"version": manifest_version}), encoding="utf-8")
    (root / sync.README_PATH).write_text(readme_text, encoding="utf-8")


class ReadmeVersionSyncTest(unittest.TestCase):
    """Keep the README status line equal to the manifest version."""

    def test_sync_rewrites_stale_readme_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_layout(
                root,
                "1.52.112",
                "Шапка\n\nТекущая версия — **1.52.32**.\n\nПодвал\n",
            )

            version = sync.manifest_version(root)
            text = sync.readme_text(root)
            synced = sync.synced_readme_text(text, version)
            (root / sync.README_PATH).write_text(synced, encoding="utf-8")

            self.assertEqual(version, "1.52.112")
            self.assertIn("Текущая версия — **1.52.112**.", synced)
            self.assertNotIn("1.52.32", synced)
            self.assertTrue(synced.endswith("Подвал\n"))

    def test_readme_version_reports_drift_and_match(self) -> None:
        drifted = "Текущая версия — **1.52.32**."
        matched = "Текущая версия — **1.52.112**."

        self.assertEqual(sync.readme_version(drifted), "1.52.32")
        self.assertEqual(sync.readme_version(matched), "1.52.112")

    def test_missing_status_line_is_an_error(self) -> None:
        with self.assertRaises(sync.ReadmeVersionSyncError):
            sync.readme_version("Нет строки версии\n")

    def test_malformed_manifest_version_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_layout(root, "latest", "Текущая версия — **1.52.32**.")

            with self.assertRaises(sync.ReadmeVersionSyncError):
                sync.manifest_version(root)

    def test_check_mode_fails_closed_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_layout(
                root, "1.52.112", "Текущая версия — **1.52.32**."
            )
            original_root = sync.REPOSITORY_ROOT
            sync.REPOSITORY_ROOT = root
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    drift = sync.main(("--check",))
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    applied = sync.main(())
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    clean = sync.main(("--check",))
            finally:
                sync.REPOSITORY_ROOT = original_root

            self.assertEqual(drift, 1)
            self.assertEqual(applied, 0)
            self.assertEqual(clean, 0)
            self.assertIn(
                "Текущая версия — **1.52.112**",
                (root / sync.README_PATH).read_text(encoding="utf-8"),
            )

    def test_repository_readme_matches_manifest(self) -> None:
        version = sync.manifest_version(ROOT)
        shown = sync.readme_version(sync.readme_text(ROOT))

        self.assertEqual(shown, version)


if __name__ == "__main__":
    unittest.main()
