"""Tests for the local Git-only HACS installation package check."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_hacs_package as package  # noqa: E402
from check_repository_boundary import RepositoryCheckError, RepositoryFile, tracked_files  # noqa: E402


class HacsPackageCheckTest(unittest.TestCase):
    """Keep the HACS installation surface complete and local-only."""

    def setUp(self) -> None:
        self.files = tracked_files(ROOT)
        self.file_modes = package.indexed_file_modes(ROOT)

    def test_current_prepared_git_package_passes(self) -> None:
        self.assertEqual(
            (),
            package.find_hacs_package_violations(self.files, self.file_modes),
        )

    def test_missing_file_or_link_is_rejected(self) -> None:
        missing_icon = without_file(self.files, package.ICON_PATH)
        missing_findings = package.find_hacs_package_violations(
            missing_icon,
            self.file_modes,
        )
        self.assertIn(
            f"{package.ICON_PATH}: missing required HACS package file",
            missing_findings,
        )

        linked_modes = dict(self.file_modes)
        linked_modes[package.ICON_PATH] = "120000"
        linked_findings = package.find_hacs_package_violations(
            self.files,
            linked_modes,
        )
        self.assertIn(
            f"{package.ICON_PATH}: must be a regular Git file",
            linked_findings,
        )

    def test_metadata_and_manifest_stay_within_the_approved_package_shape(self) -> None:
        changed_hacs = replace_file(
            self.files,
            package.HACS_METADATA_PATH,
            b'{"name":"HausMan Hub HASC","homeassistant":"2026.6.4",'
            b'"render_readme":true}',
        )
        hacs_findings = package.find_hacs_package_violations(
            changed_hacs,
            self.file_modes,
        )
        self.assertIn(
            f"{package.HACS_METADATA_PATH}: must keep the approved minimal HACS metadata",
            hacs_findings,
        )

        manifest = json.loads(file_content(self.files, package.MANIFEST_PATH))
        manifest["requirements"] = ["not-approved"]
        changed_manifest = replace_file(
            self.files,
            package.MANIFEST_PATH,
            json.dumps(manifest).encode("utf-8"),
        )
        manifest_findings = package.find_hacs_package_violations(
            changed_manifest,
            self.file_modes,
        )
        self.assertIn(
            f"{package.MANIFEST_PATH}: must keep the approved manifest fields",
            manifest_findings,
        )

    def test_json_translation_icon_and_version_errors_are_rejected(self) -> None:
        invalid_translation = replace_file(
            self.files,
            package.TRANSLATION_PATHS[1],
            b'{"config": {"step": {"user": {"title": "x"}}}}',
        )
        translation_findings = package.find_hacs_package_violations(
            invalid_translation,
            self.file_modes,
        )
        self.assertIn(
            "translations: English and Russian files must have the same key shape",
            translation_findings,
        )

        invalid_icon = replace_file(self.files, package.ICON_PATH, b"not a PNG")
        icon_findings = package.find_hacs_package_violations(
            invalid_icon,
            self.file_modes,
        )
        self.assertIn(
            f"{package.ICON_PATH}: must be a 512px transparent PNG",
            icon_findings,
        )

        manifest = json.loads(file_content(self.files, package.MANIFEST_PATH))
        manifest["version"] = "0.3"
        invalid_version = replace_file(
            self.files,
            package.MANIFEST_PATH,
            json.dumps(manifest).encode("utf-8"),
        )
        version_findings = package.find_hacs_package_violations(
            invalid_version,
            self.file_modes,
        )
        self.assertIn(
            f"{package.MANIFEST_PATH}: version must use three non-negative numbers",
            version_findings,
        )

        missing_changelog_version = "999.999.999"
        manifest["version"] = missing_changelog_version
        missing_note = replace_file(
            self.files,
            package.MANIFEST_PATH,
            json.dumps(manifest).encode("utf-8"),
        )
        note_findings = package.find_hacs_package_violations(
            missing_note,
            self.file_modes,
        )
        self.assertIn(
            f"{package.CHANGELOG_PATH}: must describe manifest version {missing_changelog_version}",
            note_findings,
        )

    def test_duplicate_json_key_is_rejected(self) -> None:
        duplicate_key = replace_file(
            self.files,
            package.HACS_METADATA_PATH,
            b'{"name":"first","name":"second","homeassistant":"2026.6.4"}',
        )

        findings = package.find_hacs_package_violations(duplicate_key, self.file_modes)

        self.assertIn(
            f"{package.HACS_METADATA_PATH}: must contain a valid JSON object without duplicate keys",
            findings,
        )

    def test_index_modes_reject_unresolved_conflicts(self) -> None:
        completed = subprocess.CompletedProcess(
            ("git", "ls-files", "--stage"),
            0,
            stdout=b"100644 abcdef 2\tREADME.md\0",
            stderr=b"",
        )
        with patch.object(package, "run_git", return_value=completed):
            with self.assertRaisesRegex(RepositoryCheckError, "unresolved conflict"):
                package.indexed_file_modes(ROOT)

    def test_main_reports_only_a_local_package_result(self) -> None:
        output = io.StringIO()
        with (
            patch.object(package, "tracked_files", return_value=self.files),
            patch.object(package, "indexed_file_modes", return_value=self.file_modes),
            redirect_stdout(output),
            redirect_stderr(io.StringIO()),
        ):
            result = package.main()

        self.assertEqual(0, result)
        self.assertIn("prepared Git package", output.getvalue())


def without_file(
    files: tuple[RepositoryFile, ...],
    path: PurePosixPath,
) -> tuple[RepositoryFile, ...]:
    """Return synthetic indexed files with one selected path removed."""

    return tuple(file for file in files if file.path != path)


def replace_file(
    files: tuple[RepositoryFile, ...],
    path: PurePosixPath,
    content: bytes,
) -> tuple[RepositoryFile, ...]:
    """Return synthetic indexed files with one selected blob replaced."""

    return tuple(
        RepositoryFile(file.path, content) if file.path == path else file for file in files
    )


def file_content(files: tuple[RepositoryFile, ...], path: PurePosixPath) -> str:
    """Return one known UTF-8 Git blob for a focused synthetic change."""

    return next(file.content for file in files if file.path == path).decode("utf-8")


if __name__ == "__main__":
    unittest.main()
