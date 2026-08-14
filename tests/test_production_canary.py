from __future__ import annotations

import copy
import unittest

from tools.check_production_canary import CanaryArtifactError, evaluate_canary


def _artifact() -> dict[str, object]:
    return {
        "backupId": "backup-before-1.53.0",
        "configCheckPassed": True,
        "healthProbe": {
            "method": "GET",
            "path": "/api/config",
            "status": 200,
            "latencyMs": 12.5,
        },
        "baseline": {"unavailableCount": 2},
        "canary": {
            "durationSeconds": 60,
            "samples": [
                {
                    "method": "GET",
                    "path": "/api/hausman_hub/v1/dashboard",
                    "status": 200,
                    "latencyMs": 40 + index,
                    "unavailableCount": 2,
                    "pendingAgeMs": None,
                }
                for index in range(12)
            ],
        },
    }


class ProductionCanaryTest(unittest.TestCase):
    def test_clean_command_free_window_may_proceed(self) -> None:
        result = evaluate_canary(_artifact())
        self.assertEqual("proceed", result["decision"])
        self.assertEqual([], result["reasons"])

    def test_each_runtime_threshold_requests_rollback(self) -> None:
        cases: tuple[tuple[str, object, str], ...] = (
            ("durationSeconds", 59, "canary_window_too_short"),
            ("latencyMs", 1_001, "p95_latency_exceeded"),
            ("pendingAgeMs", 60_001, "pending_age_exceeded"),
            ("unavailableCount", 3, "unavailable_count_increased"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                artifact = _artifact()
                canary = artifact["canary"]
                assert isinstance(canary, dict)
                if field == "durationSeconds":
                    canary[field] = value
                else:
                    samples = canary["samples"]
                    assert isinstance(samples, list)
                    for sample in samples:
                        assert isinstance(sample, dict)
                        sample[field] = value
                result = evaluate_canary(artifact)
                self.assertEqual("rollback", result["decision"])
                self.assertIn(reason, result["reasons"])

    def test_one_error_in_small_window_exceeds_one_percent(self) -> None:
        artifact = _artifact()
        canary = artifact["canary"]
        assert isinstance(canary, dict)
        samples = canary["samples"]
        assert isinstance(samples, list)
        assert isinstance(samples[0], dict)
        samples[0]["status"] = 503
        result = evaluate_canary(artifact)
        self.assertIn("error_rate_exceeded", result["reasons"])

    def test_physical_or_extra_probe_is_rejected_as_invalid_evidence(self) -> None:
        artifact = _artifact()
        probe = artifact["healthProbe"]
        assert isinstance(probe, dict)
        probe["method"] = "POST"
        with self.assertRaises(CanaryArtifactError):
            evaluate_canary(artifact)

        artifact = _artifact()
        probe = artifact["healthProbe"]
        assert isinstance(probe, dict)
        probe["physicalCommand"] = False
        with self.assertRaises(CanaryArtifactError):
            evaluate_canary(artifact)

    def test_missing_metric_fails_closed(self) -> None:
        artifact = copy.deepcopy(_artifact())
        canary = artifact["canary"]
        assert isinstance(canary, dict)
        samples = canary["samples"]
        assert isinstance(samples, list)
        assert isinstance(samples[0], dict)
        samples[0]["unavailableCount"] = None
        result = evaluate_canary(artifact)
        self.assertEqual("rollback", result["decision"])
        self.assertIn("unavailable_count_missing", result["reasons"])

    def test_backup_and_config_check_are_mandatory(self) -> None:
        for field, value in (("backupId", ""), ("configCheckPassed", False)):
            with self.subTest(field=field):
                artifact = _artifact()
                artifact[field] = value
                with self.assertRaises(CanaryArtifactError):
                    evaluate_canary(artifact)
