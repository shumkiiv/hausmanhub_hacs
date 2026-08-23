from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ALERT = _load("smoke_alert", TOOLS / "report_live_smoke_failure.py")
REMINDER = _load("ux_reminder", TOOLS / "create_monthly_ux_audit_reminder.py")


class OperationalReadinessTests(unittest.TestCase):
    def test_alert_is_redacted_and_drops_unknown_failure_data(self) -> None:
        alert = ALERT.redacted_alert(
            {"failures": ["request_latency", "secret.entity", "request_latency"]},
            now=datetime(2026, 8, 23, tzinfo=timezone.utc),
        )
        encoded = json.dumps(alert, ensure_ascii=False)
        self.assertEqual(["request_latency"], alert["failures"])
        self.assertEqual("P1", alert["severity"])
        self.assertNotIn("secret", encoded)
        self.assertNotIn("entity", encoded)

    def test_monthly_reminder_has_no_home_identity(self) -> None:
        payload = REMINDER.reminder(datetime(2026, 8, 23, tzinfo=timezone.utc))
        self.assertEqual("2026-08", payload["month"])
        self.assertEqual("due", payload["status"])
        self.assertNotIn("token", json.dumps(payload).lower())

    def test_release_readiness_is_fail_closed(self) -> None:
        payload = json.loads((ROOT / "operations/release-readiness.json").read_text())
        self.assertEqual(0, payload["openP0"])
        self.assertEqual(0, payload["openP1"])
        self.assertEqual("green", payload["gateStatus"])
        for field in (
            "ownersKnown",
            "productDocumentationCurrent",
            "compatibilityChecked",
            "rollbackChecked",
        ):
            self.assertIs(payload[field], True)

    def test_systemd_alert_and_monthly_timer_are_connected(self) -> None:
        smoke = (ROOT / "operations/systemd/hausman-live-smoke.service").read_text()
        alert = (ROOT / "operations/systemd/hausman-live-smoke-alert@.service").read_text()
        timer = (ROOT / "operations/systemd/hausman-monthly-ux-audit.timer").read_text()
        self.assertIn("OnFailure=hausman-live-smoke-alert@%n.service", smoke)
        self.assertIn("report_live_smoke_failure.py", alert)
        self.assertIn("OnCalendar=*-*-01 09:00:00 Europe/Moscow", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
