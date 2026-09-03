"""Tests for the restricted GitHub project-file check."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "local-quality.yml"


class GitHubLocalQualityWorkflowTest(unittest.TestCase):
    """Keep the GitHub check limited to the fixed local publication command."""

    def test_workflow_uses_read_only_access_and_local_checks(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("uses: actions/checkout@v7", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("uses: actions/setup-python@v6", workflow)
        self.assertIn('python-version: "3.13"', workflow)
        self.assertIn(
            "python3 -m pip install --disable-pip-version-check jsonschema==4.25.1 coverage==7.10.7",
            workflow,
        )
        self.assertIn("run: python3 tools/check_local_release.py", workflow)
        self.assertIn("run: python3 tools/check_critical_coverage.py", workflow)
        self.assertIn("uses: actions/setup-node@v6", workflow)
        self.assertIn("run: npm ci", workflow)
        self.assertIn("run: npx playwright install --with-deps chromium", workflow)
        self.assertIn("run: npm run test:browser", workflow)
        self.assertEqual(4, workflow.count("\n        uses:"))
        self.assertEqual(6, workflow.count("\n        run:"))

    def test_workflow_runs_for_main_changes_and_has_no_home_target(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8").lower()

        self.assertIn("push:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("- main", workflow)
        self.assertNotIn("://", workflow)
        self.assertNotIn("homeassistant", workflow)
        self.assertNotIn("curl", workflow)
        self.assertNotIn("wget", workflow)

    def test_browser_gate_writes_runtime_evidence_to_runner_temp(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "QA_ARTIFACT_ROOT: ${{ runner.temp }}/hausman-browser-gate",
            workflow,
        )
        self.assertIn(
            "- name: Проверить интерфейс в браузере\n"
            "        env:\n"
            "          QA_ARTIFACT_ROOT: ${{ runner.temp }}/hausman-browser-gate\n"
            "        run: npm run test:browser",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
