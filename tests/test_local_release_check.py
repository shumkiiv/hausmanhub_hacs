"""Tests for the one-command local HASC publication check."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_local_release as release  # noqa: E402


class LocalReleaseCheckTest(unittest.TestCase):
    """Keep the publication command limited to its fixed local checklist."""

    def test_checklist_covers_tests_fixtures_and_file_safety(self) -> None:
        checks = release.local_checks("python-for-test")

        self.assertEqual(
            tuple(label for label, _ in checks),
            (
                "local tests",
                "common synthetic fixture",
                "shadow synthetic fixture",
                "diagnostics synthetic fixture",
                "staged release version",
                "HACS installation package",
                "published-file safety",
                "staged-file safety",
            ),
        )
        self.assertEqual(
            checks[-1][1],
            ("python-for-test", "tools/check_repository_boundary.py", "--staged"),
        )

    def test_checklist_has_no_network_or_home_assistant_target(self) -> None:
        checks = release.local_checks("python-for-test")
        command_text = " ".join(
            argument for _, command in checks for argument in command[1:]
        ).lower()

        self.assertNotIn("://", command_text)
        self.assertNotIn("homeassistant", command_text)
        self.assertNotIn("curl", command_text)
        self.assertNotIn("wget", command_text)
        for _, command in checks:
            for argument in command[1:]:
                if argument.endswith(".py"):
                    self.assertTrue((ROOT / argument).is_file())

    def test_checklist_stops_at_the_first_failed_local_check(self) -> None:
        called: list[tuple[str, ...]] = []

        def runner(command: tuple[str, ...]) -> int:
            called.append(command)
            return 7 if len(called) == 2 else 0

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = release.run_checks(release.local_checks("python-for-test"), runner)

        self.assertEqual(result, 7)
        self.assertEqual(len(called), 2)


if __name__ == "__main__":
    unittest.main()
