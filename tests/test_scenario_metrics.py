"""Tests for bounded scenario backend latency budgets."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.scenario_metrics import (
    MAX_LATENCY_SAMPLES,
    SCENARIO_PATH_BUDGETS_MS,
    ScenarioPathMetrics,
)


class ScenarioPathMetricsTest(unittest.TestCase):
    def test_performance_gate_locks_backend_path_budgets(self) -> None:
        self.assertEqual(
            {
                "list": 100.0,
                "catalog": 500.0,
                "dry_run": 500.0,
                "storage": 250.0,
                "last_result": 50.0,
            },
            dict(SCENARIO_PATH_BUDGETS_MS),
        )

    def test_snapshot_reports_percentiles_and_budget_status(self) -> None:
        metrics = ScenarioPathMetrics()
        for duration in (10, 20, 30, 40, 600):
            metrics.record("catalog", duration)

        snapshot = metrics.snapshot()["catalog"]

        self.assertEqual(5, snapshot["count"])
        self.assertEqual(30.0, snapshot["p50Ms"])
        self.assertEqual(600.0, snapshot["p95Ms"])
        self.assertEqual("over_budget", snapshot["status"])

    def test_samples_are_bounded_and_contain_no_request_data(self) -> None:
        metrics = ScenarioPathMetrics()
        for duration in range(MAX_LATENCY_SAMPLES + 20):
            metrics.record("list", duration)

        snapshot = metrics.snapshot()

        self.assertEqual(MAX_LATENCY_SAMPLES, snapshot["list"]["count"])
        self.assertEqual(
            {"count", "p50Ms", "p95Ms", "budgetMs", "status"},
            set(snapshot["list"]),
        )

    def test_rejects_unknown_metric_and_invalid_duration(self) -> None:
        metrics = ScenarioPathMetrics()
        with self.assertRaises(ValueError):
            metrics.record("unknown", 1)
        with self.assertRaises(ValueError):
            metrics.record("list", -1)


if __name__ == "__main__":
    unittest.main()
