#!/usr/bin/env python3
"""Create a redacted local alert from the latest failed production smoke."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path


REPORT_DIR = Path.home() / ".config" / "hausmanhub" / "live-smoke"
ALLOWED_FAILURES = {
    "request_latency",
    "dashboard_snapshot_age_ms",
    "climate_snapshot_age_ms",
    "climate_not_fresh",
    "pending_operation_age",
}


def redacted_alert(report: object, *, now: datetime | None = None) -> dict[str, object]:
    source = report if isinstance(report, dict) else {}
    raw_failures = source.get("failures")
    failures = (
        sorted({item for item in raw_failures if isinstance(item, str) and item in ALLOWED_FAILURES})
        if isinstance(raw_failures, list)
        else []
    )
    return {
        "schema": "hausman-live-smoke-alert-v1",
        "occurred_at": (now or datetime.now(timezone.utc)).isoformat(),
        "severity": "P1" if failures else "P2",
        "owner": "Hausman operations",
        "failures": failures,
        "action": "Open the latest redacted smoke report and follow docs/OPERATIONS_SUPPORT_SLO_DOD.md",
    }


def latest_report(report_dir: Path) -> object:
    candidates = sorted(report_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    reports = [path for path in candidates if path.name != "alert.json"]
    if not reports:
        return {}
    return json.loads(reports[-1].read_text(encoding="utf-8"))


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    alert = redacted_alert(latest_report(REPORT_DIR))
    temporary = REPORT_DIR / ".alert.json.tmp"
    temporary.write_text(json.dumps(alert, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(REPORT_DIR / "alert.json")
    print(f"Hausman smoke alert: {alert['severity']}; failures={','.join(alert['failures']) or 'unknown'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
