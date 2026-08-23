"""Executable guardrails for the live, soak, fault and canary program."""

from __future__ import annotations

from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("live_smoke", TOOLS / "run_live_readonly_smoke.py")
assert SPEC and SPEC.loader
LIVE_SMOKE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LIVE_SMOKE
SPEC.loader.exec_module(LIVE_SMOKE)


class _Result:
    def __init__(self, path: str, payload: object) -> None:
        self.status = 200
        self.path = path
        self.payload = payload


class ProductReadinessProgramTests(unittest.TestCase):
    def test_live_smoke_allowlist_is_get_only_and_excludes_dangerous_routes(self) -> None:
        paths = [endpoint.path for endpoint in LIVE_SMOKE.ENDPOINTS]
        source = (TOOLS / "run_live_readonly_smoke.py").read_text(encoding="utf-8")
        self.assertEqual(len(paths), len(set(paths)))
        self.assertTrue(all(path.startswith("/api/") for path in paths))
        self.assertNotIn("request(\"POST\"", source)
        self.assertNotIn("request(\"PUT\"", source)
        self.assertNotIn("request(\"DELETE\"", source)
        forbidden_segments = {"run", "action", "apply", "test", "cancel"}
        for path in paths:
            self.assertTrue(forbidden_segments.isdisjoint(path.split("/")))

    def test_redacted_summary_passes_a_healthy_home_without_identity_fields(self) -> None:
        now_seconds = 1_800_000_000.0
        payload_by_path = {
            "/api/config": {"version": "2026.8.2"},
            "/api/hausman_hub/v1/capabilities": {"contract": {"version": 1}},
            "/api/hausman_hub/v1/dashboard": {
                "generatedAt": 1_800_000_000_000,
                "devices": [{"id": "private", "name": "private", "unavailable": False}],
            },
            "/api/hausman_hub/v1/climate/runtime": {
                "generated_at": 1_800_000_000_000,
                "fresh": True,
                "commands_enabled": False,
                "active_operations": [],
            },
            "/api/hausman_hub/v1/scenarios/upcoming": {"events": [{}]},
            "/api/hausman_hub/v1/energy/meters": {"meters": [{}]},
            "/api/hausman_hub/v1/admin/operations?limit=100": {
                "page": {"retained_records": 4},
                "records": [{"status": "confirmed"}],
            },
            "/api/hausman_hub/v1/admin/water-safety": {"state": {"state": "idle"}},
            "/api/hausman_hub/v1/admin/climate-mode": {
                "rollout": {"phase": "shadow"},
                "cutover": {"phase": "not_ready"},
            },
        }

        def fake_get(_access: object, path: str, *, timeout: float) -> _Result:
            self.assertEqual(15.0, timeout)
            return _Result(path, payload_by_path[path])

        report = LIVE_SMOKE.run_smoke(
            {
                "read": LIVE_SMOKE.AdminAccess("https://example.invalid", "secret"),
                "admin": LIVE_SMOKE.AdminAccess("https://example.invalid", "secret-admin"),
            },
            timeout=15.0,
            thresholds=LIVE_SMOKE.SmokeThresholds(),
            request_get=fake_get,
            now=lambda: now_seconds,
        )
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertEqual("passed", report["status"])
        self.assertFalse(report["physical_commands_sent"])
        self.assertNotIn("private", encoded)
        self.assertNotIn("secret", encoded)
        self.assertEqual(1, report["metrics"]["device_count"])
        self.assertEqual(asdict(LIVE_SMOKE.SmokeThresholds()), report["thresholds"])

    def test_thresholds_fail_stale_slow_and_old_pending_state(self) -> None:
        metrics = {
            "max_request_latency_ms": 5_001,
            "dashboard_snapshot_age_ms": 120_001,
            "climate_snapshot_age_ms": None,
            "climate_fresh": False,
            "oldest_pending_operation_age_ms": 120_001,
        }
        self.assertEqual(
            [
                "request_latency",
                "dashboard_snapshot_age_ms",
                "climate_snapshot_age_ms",
                "climate_not_fresh",
                "pending_operation_age",
            ],
            LIVE_SMOKE.evaluate_metrics(metrics, LIVE_SMOKE.SmokeThresholds()),
        )

    def test_fault_matrix_is_complete_and_bound_to_real_tests(self) -> None:
        matrix = json.loads(
            (ROOT / "tests" / "product_readiness_fault_matrix.json").read_text(encoding="utf-8")
        )
        self.assertFalse(matrix["physicalCommandsAllowed"])
        self.assertEqual(
            {"latency", "http_401", "http_409", "http_500", "dropped_sse", "ha_restart", "unavailable_entity", "stale_recorder"},
            {fault["id"] for fault in matrix["faults"]},
        )
        for fault in matrix["faults"]:
            path = ROOT / fault["evidence"]
            self.assertTrue(path.is_file(), fault)
            self.assertIn("test", path.read_text(encoding="utf-8").lower())

    def test_daily_timer_is_persistent_and_runs_the_read_only_tool(self) -> None:
        service = (ROOT / "operations/systemd/hausman-live-smoke.service").read_text(encoding="utf-8")
        timer = (ROOT / "operations/systemd/hausman-live-smoke.timer").read_text(encoding="utf-8")
        self.assertIn("tools/run_live_readonly_smoke.py", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("OnCalendar=*-*-* 04:20:00 Europe/Moscow", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
