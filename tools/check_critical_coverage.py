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
        "custom_components.hausman_hub.application.climate_deviation_guard",
        "custom_components.hausman_hub.verified_safety_storage",
    )
)
SUITES = (
    "tests.test_water_safety",
    "tests.test_scenario_executor",
    "tests.test_scenario_light_priority_storage",
    "tests.test_intercom_release_obligation",
    "tests.test_scenario_service",
    "tests.test_managed_switch_migration_service",
    "tests.test_climate_deviation_guard",
    "tests.test_verified_safety_storage",
)
MINIMUM_BRANCH_COVERAGE = 75


def run(*args: str) -> None:
    subprocess.run((sys.executable, "-m", "coverage", *args), check=True)


def main() -> int:
    run("erase")
    run("run", "--branch", f"--source={SOURCE}", "-m", "unittest", *SUITES)
    run("report", "--show-missing", f"--fail-under={MINIMUM_BRANCH_COVERAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
