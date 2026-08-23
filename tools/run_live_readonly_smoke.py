#!/usr/bin/env python3
"""Run the production read-only smoke without physical commands.

Only authenticated HTTP GET requests from ``ENDPOINTS`` are allowed. The
report contains aggregate health metrics and never stores entity IDs, names,
tokens or raw production payloads.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable

from audit_production_climate import (
    DEFAULT_ACCESS_FILE,
    AdminAccess,
    http_get_json,
)

DEFAULT_READ_ACCESS_FILE = DEFAULT_ACCESS_FILE.with_name("ha_read_access.json")


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    path: str
    role: str


ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec("core", "/api/config", "read"),
    EndpointSpec("capabilities", "/api/hausman_hub/v1/capabilities", "read"),
    EndpointSpec("dashboard", "/api/hausman_hub/v1/dashboard", "read"),
    EndpointSpec("climate_runtime", "/api/hausman_hub/v1/climate/runtime", "read"),
    EndpointSpec("upcoming", "/api/hausman_hub/v1/scenarios/upcoming", "read"),
    EndpointSpec("energy_meters", "/api/hausman_hub/v1/energy/meters", "read"),
    EndpointSpec("operation_journal", "/api/hausman_hub/v1/admin/operations?limit=100", "admin"),
    EndpointSpec("water_safety", "/api/hausman_hub/v1/admin/water-safety", "admin"),
    EndpointSpec("climate_mode", "/api/hausman_hub/v1/admin/climate-mode", "admin"),
)

FORBIDDEN_PROBE_DOMAINS: tuple[str, ...] = (
    "water_command",
    "intercom",
    "power",
    "security",
    "device_action",
    "scenario_run",
)


@dataclass(frozen=True)
class SmokeThresholds:
    request_latency_ms: int = 5_000
    snapshot_age_ms: int = 120_000
    pending_operation_age_ms: int = 120_000


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _epoch_millis(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number * 1_000 if number < 10_000_000_000 else number
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1_000)
        except ValueError:
            return None
    return None


def summarize_payloads(
    payloads: dict[str, Any],
    *,
    now_ms: int,
    latencies_ms: dict[str, int],
) -> dict[str, Any]:
    dashboard = _mapping(payloads.get("dashboard"))
    climate = _mapping(payloads.get("climate_runtime"))
    upcoming = _mapping(payloads.get("upcoming"))
    meters = _mapping(payloads.get("energy_meters"))
    journal = _mapping(payloads.get("operation_journal"))
    water = _mapping(payloads.get("water_safety"))
    climate_mode = _mapping(payloads.get("climate_mode"))
    devices = _items(dashboard.get("devices"))
    operations = _items(climate.get("active_operations"))
    operation_ages = [
        now_ms - occurred_at
        for operation in operations
        if (occurred_at := _epoch_millis(
            operation.get("created_at")
            or operation.get("createdAt")
            or operation.get("occurred_at")
        )) is not None
    ]
    generated_at = _epoch_millis(dashboard.get("generatedAt"))
    climate_generated_at = _epoch_millis(climate.get("generated_at"))
    unavailable = sum(device.get("unavailable") is True for device in devices)
    failed_records = sum(
        str(record.get("status", "")).lower() in {"failed", "error", "rejected"}
        for record in _items(journal.get("records"))
    )
    return {
        "request_latency_ms": dict(sorted(latencies_ms.items())),
        "max_request_latency_ms": max(latencies_ms.values(), default=0),
        "dashboard_snapshot_age_ms": None if generated_at is None else max(0, now_ms - generated_at),
        "climate_snapshot_age_ms": None
        if climate_generated_at is None
        else max(0, now_ms - climate_generated_at),
        "climate_fresh": climate.get("fresh") is True,
        "climate_commands_enabled": climate.get("commands_enabled") is True,
        "active_operation_count": len(operations),
        "oldest_pending_operation_age_ms": max(operation_ages, default=0),
        "device_count": len(devices),
        "unavailable_device_count": unavailable,
        "upcoming_event_count": len(_items(upcoming.get("events"))),
        "energy_meter_count": len(_items(meters.get("meters"))),
        "journal_retained_records": _mapping(journal.get("page")).get("retained_records"),
        "journal_failed_records_in_sample": failed_records,
        "water_safety_state": _mapping(water.get("state")).get("state"),
        "climate_rollout_phase": _mapping(climate_mode.get("rollout")).get("phase"),
        "climate_cutover_phase": _mapping(climate_mode.get("cutover")).get("phase"),
    }


def evaluate_metrics(metrics: dict[str, Any], thresholds: SmokeThresholds) -> list[str]:
    failures: list[str] = []
    if metrics["max_request_latency_ms"] > thresholds.request_latency_ms:
        failures.append("request_latency")
    for field in ("dashboard_snapshot_age_ms", "climate_snapshot_age_ms"):
        age = metrics.get(field)
        if age is None or age > thresholds.snapshot_age_ms:
            failures.append(field)
    if metrics.get("climate_fresh") is not True:
        failures.append("climate_not_fresh")
    if metrics.get("oldest_pending_operation_age_ms", 0) > thresholds.pending_operation_age_ms:
        failures.append("pending_operation_age")
    return failures


def load_smoke_access(path: Path) -> AdminAccess:
    """Load JSON or the legacy two-line base URL/token access file."""

    if not path.is_file():
        raise ValueError(f"access file {path} does not exist")
    source = path.read_text(encoding="utf-8").strip()
    try:
        payload = json.loads(source)
    except json.JSONDecodeError:
        lines = [line.strip() for line in source.splitlines() if line.strip()]
        if len(lines) != 2:
            raise ValueError(f"access file {path} must be JSON or two non-empty lines") from None
        base_url, token = lines
    else:
        if not isinstance(payload, dict):
            raise ValueError(f"access file {path} must contain an object")
        base_url, token = payload.get("base_url"), payload.get("token")
    if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
        raise ValueError(f"access file {path} has no valid base URL")
    if not isinstance(token, str) or not token.strip():
        raise ValueError(f"access file {path} has no token")
    return AdminAccess(base_url.rstrip("/"), token.strip())


def run_smoke(
    access_by_role: dict[str, AdminAccess],
    *,
    timeout: float,
    thresholds: SmokeThresholds,
    request_get: Callable[..., Any] = http_get_json,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    payloads: dict[str, Any] = {}
    latencies: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for endpoint in ENDPOINTS:
        began = time.perf_counter()
        result = request_get(access_by_role[endpoint.role], endpoint.path, timeout=timeout)
        latencies[endpoint.name] = round((time.perf_counter() - began) * 1_000)
        statuses[endpoint.name] = result.status
        payloads[endpoint.name] = result.payload
    metrics = summarize_payloads(
        payloads,
        now_ms=round(now() * 1_000),
        latencies_ms=latencies,
    )
    failures = evaluate_metrics(metrics, thresholds)
    return {
        "schema": "hausman-live-readonly-smoke-v1",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if not failures else "failed",
        "http_method": "GET",
        "physical_commands_sent": False,
        "forbidden_probe_domains": list(FORBIDDEN_PROBE_DOMAINS),
        "endpoints": statuses,
        "thresholds": asdict(thresholds),
        "metrics": metrics,
        "failures": failures,
    }


def default_report_dir(access_file: Path) -> Path:
    return access_file.parent / "live-smoke"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-access-file", type=Path, default=DEFAULT_READ_ACCESS_FILE)
    parser.add_argument("--admin-access-file", type=Path, default=DEFAULT_ACCESS_FILE)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    access_by_role = {
        "read": load_smoke_access(args.read_access_file),
        "admin": load_smoke_access(args.admin_access_file),
    }
    report = run_smoke(access_by_role, timeout=args.timeout, thresholds=SmokeThresholds())
    report_dir = args.report_dir or default_report_dir(args.admin_access_file)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"smoke-{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(report_path), "metrics": report["metrics"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
