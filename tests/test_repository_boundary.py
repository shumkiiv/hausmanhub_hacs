"""Tests for the local repository safety check."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_repository_boundary as boundary  # noqa: E402

from check_repository_boundary import (  # noqa: E402
    RepositoryFile,
    checked_relative_path,
    find_boundary_violations,
    forbidden_path_finding,
    staged_files,
    tracked_files,
)


class RepositoryBoundaryCheckTest(unittest.TestCase):
    """Keep the public repository free of common accidental disclosures."""

    def test_current_tracked_files_pass_the_boundary_check(self) -> None:
        self.assertEqual((), find_boundary_violations(tracked_files(ROOT)))

    def test_forbidden_runtime_and_credential_file_names_are_rejected(self) -> None:
        paths = (
            ".env",
            "secrets.yaml",
            "configuration.yaml",
            "automations.yaml",
            "credentials/local.json",
            ".storage/core.config_entries",
            "node-red/flows.json",
            "flow-backups/house-flow.json",
            "config_entry-hausman_hub.json",
            "local-access.token",
            "certificate.pem",
            "restore.backup",
        )
        for raw_path in paths:
            with self.subTest(path=raw_path):
                self.assertIsNotNone(forbidden_path_finding(PurePosixPath(raw_path)))

    def test_unrelated_json_names_are_not_misread_as_node_red_flows(self) -> None:
        for raw_path in ("workflow.json", "overflow.json", "home-information.json"):
            with self.subTest(path=raw_path):
                self.assertIsNone(forbidden_path_finding(PurePosixPath(raw_path)))

    def test_credential_shaped_content_is_rejected_with_a_line_number(self) -> None:
        access_token = b"access" + b"_token: " + b"x" * 24
        private_key = b"-----BEGIN " + b"PRIVATE KEY-----"
        hosted_token = b"gh" + b"p_" + b"x" * 32
        bearer_token = b"Authorization: Bearer " + b"x" * 24
        json_web_token = b"eyJ" + b"x" * 12 + b"." + b"y" * 12 + b"." + b"z" * 12
        findings = find_boundary_violations(
            (
                RepositoryFile(PurePosixPath("first.txt"), access_token),
                RepositoryFile(PurePosixPath("second.txt"), private_key),
                RepositoryFile(PurePosixPath("third.txt"), hosted_token),
                RepositoryFile(PurePosixPath("fourth.txt"), bearer_token),
                RepositoryFile(PurePosixPath("fifth.txt"), json_web_token),
            )
        )

        self.assertEqual(
            (
                "fifth.txt:1: possible JSON web token",
                "first.txt:1: possible assigned secret",
                "fourth.txt:1: possible bearer token",
                "second.txt:1: possible private key",
                "third.txt:1: possible hosted-service token",
            ),
            findings,
        )

    def test_binary_assets_are_not_misread_as_text_credentials(self) -> None:
        findings = find_boundary_violations(
            (RepositoryFile(PurePosixPath("brand/icon.png"), b"\x89PNG\x00ghp_" + b"x" * 32),)
        )

        self.assertEqual((), findings)

    def test_malformed_git_paths_are_rejected_before_file_access(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unsafe path"):
            checked_relative_path(b"../outside")

    def test_tracked_files_reads_the_git_blob_not_a_worktree_symbolic_link(self) -> None:
        """A checked path must never cause this tool to follow a local link."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside_file = root / "outside-secret.txt"
            outside_file.write_text("must not be read", encoding="utf-8")
            try:
                (root / "linked.txt").symlink_to(outside_file)
            except OSError as error:
                if getattr(error, "winerror", None) == 1314:
                    self.skipTest("Windows does not grant symbolic-link privileges")
                raise

            index_content = b"safe indexed content"
            completed = subprocess.CompletedProcess(
                ("git", "show", ":linked.txt"),
                0,
                stdout=index_content,
                stderr=b"",
            )
            with ExitStack() as stack:
                file_list = stack.enter_context(
                    patch.object(
                        boundary,
                        "git_file_list",
                        return_value=(PurePosixPath("linked.txt"),),
                    )
                )
                run_git = stack.enter_context(
                    patch.object(boundary, "run_git", return_value=completed)
                )
                files = tracked_files(root)

        self.assertEqual(
            (RepositoryFile(PurePosixPath("linked.txt"), index_content),),
            files,
        )
        file_list.assert_called_once_with(root, "ls-files")
        run_git.assert_called_once_with(root, "show", ":linked.txt")

    def test_staged_files_reads_only_the_indexed_changed_paths(self) -> None:
        staged_path = PurePosixPath("docs/new-file.txt")
        completed = subprocess.CompletedProcess(
            ("git", "show", ":docs/new-file.txt"),
            0,
            stdout=b"safe staged content",
            stderr=b"",
        )
        with ExitStack() as stack:
            file_list = stack.enter_context(
                patch.object(boundary, "git_file_list", return_value=(staged_path,))
            )
            run_git = stack.enter_context(
                patch.object(boundary, "run_git", return_value=completed)
            )
            files = staged_files(ROOT)

        self.assertEqual((RepositoryFile(staged_path, b"safe staged content"),), files)
        file_list.assert_called_once_with(
            ROOT,
            "diff",
            "--cached",
            "--name-only",
            "--diff-filter=ACMR",
        )
        run_git.assert_called_once_with(ROOT, "show", ":docs/new-file.txt")


if __name__ == "__main__":
    unittest.main()
