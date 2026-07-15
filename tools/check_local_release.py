#!/usr/bin/env python3
"""Run HASC's local synthetic checks before a commit or publication.

This command works only with the repository's tests, synthetic fixtures, and
local Git index. It does not start Home Assistant or connect to a home,
devices, Node-RED, or the internet.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
Check = tuple[str, tuple[str, ...]]
CommandRunner = Callable[[tuple[str, ...]], int]


def local_checks(python_executable: str) -> tuple[Check, ...]:
    """Return the complete fixed set of local checks in publication order."""

    return (
        (
            "local tests",
            (python_executable, "-m", "unittest", "discover", "-s", "tests", "-v"),
        ),
        (
            "common synthetic fixture",
            (
                python_executable,
                "tools/validate_fixture.py",
                "common",
                "fixtures/common_contract/valid_minimal.json",
            ),
        ),
        (
            "shadow synthetic fixture",
            (
                python_executable,
                "tools/validate_fixture.py",
                "shadow",
                "fixtures/shadow_evidence/valid_unresolved.json",
            ),
        ),
        (
            "diagnostics synthetic fixture",
            (
                python_executable,
                "tools/validate_fixture.py",
                "diagnostics",
                "fixtures/diagnostics/valid_redacted.json",
            ),
        ),
        (
            "staged release version",
            (python_executable, "tools/check_staged_release_version.py"),
        ),
        ("HACS installation package", (python_executable, "tools/check_hacs_package.py")),
        ("published-file safety", (python_executable, "tools/check_repository_boundary.py")),
        (
            "staged-file safety",
            (python_executable, "tools/check_repository_boundary.py", "--staged"),
        ),
    )


def run_local_command(command: tuple[str, ...]) -> int:
    """Run one fixed local command without a shell."""

    completed = subprocess.run(command, cwd=REPOSITORY_ROOT, check=False)
    return completed.returncode


def run_checks(checks: tuple[Check, ...], runner: CommandRunner) -> int:
    """Run checks in order and stop at the first unsuccessful result."""

    for label, command in checks:
        print(f"Checking {label}...", flush=True)
        result = runner(command)
        if result != 0:
            print(f"Local release check stopped at {label}.", file=sys.stderr)
            return result
    print("Local release check passed.")
    return 0


def main() -> int:
    """Run the fixed local publication checks with this Python interpreter."""

    return run_checks(local_checks(sys.executable), run_local_command)


if __name__ == "__main__":
    raise SystemExit(main())
