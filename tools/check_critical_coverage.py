#!/usr/bin/env python3
"""Block releases when branch coverage of safety-critical runtime regresses."""

from __future__ import annotations

import subprocess
import sys


SOURCE = ",".join(
    (
        "custom_components.hausman_hub.application.water_safety",
        "custom_components.hausman_hub.application.scenario_executor",
        "custom_components.hausman_hub.application.scenario_light_priority",
        "custom_components.hausman_hub.application.intercom_release_obligation",
        "custom_components.hausman_hub.application.scenario_service",
        "custom_components.hausman_hub.application.managed_switch_migration",
        "custom_components.hausman_hub.application.managed_switch_binding_migration",
        "custom_components.hausman_hub.application.climate_deviation_guard",
        "custom_components.hausman_hub.verified_safety_storage",
    )
)
# The safety paths include pytest-style asynchronous scenarios as well as
# unittest cases.  A hand-maintained unittest subset can omit either group, so
# measure the complete local pytest collection required before a release.
TEST_SUITE = ("tests",)
MINIMUM_BRANCH_COVERAGE = 75


def run(*args: str) -> None:
    subprocess.run((sys.executable, "-m", "coverage", *args), check=True)


def main() -> int:
    run("erase")
    run("run", "--branch", f"--source={SOURCE}", "-m", "pytest", "-q", *TEST_SUITE)
    run("report", "--show-missing", f"--fail-under={MINIMUM_BRANCH_COVERAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
