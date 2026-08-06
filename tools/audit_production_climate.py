#!/usr/bin/env python3
"""Read-only production climate audit against a live HausmanHub HACS.

The tool performs HTTP GET requests only.  It never creates backups, never
posts payloads and never changes the Home Assistant configuration.  Admin
access is read at runtime from a JSON file kept OUTSIDE the workspace:

    /home/ivsh/.config/hausmanhub/ha_admin_access.json
    {
      "base_url": "http://homeassistant.local:8123",
      "token": "<long-lived Home Assistant admin token>"
    }

The token is never printed, never written to the report directory and never
committed.  Full JSON responses are stored in an output directory that also
stays outside the repository (default: ``<access dir>/audit/<UTC timestamp>``)
because admin payloads contain private entity IDs.  Only the sanitized
summary without entity IDs is printed to stdout.

Exit codes: 0 success, 2 access file problem, 3 authorization failed,
4 endpoint unreachable or unexpected response.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_ACCESS_FILE = Path("/home/ivsh/.config/hausmanhub/ha_admin_access.json")

ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("core_config", "/api/config"),
    ("capabilities", "/api/hausman_hub/v1/capabilities"),
    ("climate_mode", "/api/hausman_hub/v1/admin/climate-mode"),
    ("climate_readiness", "/api/hausman_hub/v1/admin/climate-readiness"),
    ("climate_registry", "/api/hausman_hub/v1/admin/climate-registry"),
    ("climate_device_bindings", "/api/hausman_hub/v1/admin/climate-device-bindings"),
    (
        "climate_shadow_comparison",
        "/api/hausman_hub/v1/admin/climate-shadow-comparison",
    ),
    ("climate_shadow_window", "/api/hausman_hub/v1/admin/climate-shadow-window"),
)


class AccessFileError(ValueError):
    """Raised when the external access file is missing or invalid."""


class AuditAuthorizationError(PermissionError):
    """Raised when Home Assistant rejects the provided admin token."""


class AuditRequestError(RuntimeError):
    """Raised when an endpoint cannot be reached or parsed."""


@dataclass(frozen=True)
class AdminAccess:
    base_url: str
    token: str


@dataclass(frozen=True)
class EndpointResult:
    name: str
    path: str
    status: int
    payload: Any


def load_access(path: Path) -> AdminAccess:
    """Load admin access from a JSON file outside the workspace."""

    if not isinstance(path, Path):
        raise TypeError("access path must be a Path")
    if not path.is_file():
        raise AccessFileError(
            f"access file {path} does not exist; create it outside the "
            "workspace with keys base_url and token"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AccessFileError(f"access file {path} is not valid JSON") from error
    if not isinstance(payload, dict):
        raise AccessFileError(f"access file {path} must contain a JSON object")
    base_url = payload.get("base_url")
    token = payload.get("token")
    if not isinstance(base_url, str) or not base_url.strip():
        raise AccessFileError(f"access file {path} misses a non-empty base_url")
    if not isinstance(token, str) or not token.strip():
        raise AccessFileError(f"access file {path} misses a non-empty token")
    scheme = base_url.strip().split(":", 1)[0].lower()
    if scheme not in {"http", "https"}:
        raise AccessFileError(f"access file {path} base_url must be http or https")
    return AdminAccess(base_url=base_url.strip().rstrip("/"), token=token.strip())


def http_get_json(
    access: AdminAccess,
    path: str,
    *,
    timeout: float,
    opener: Any = None,
) -> EndpointResult:
    """Perform one authenticated GET request and decode the JSON body."""

    if not isinstance(access, AdminAccess):
        raise TypeError("validated admin access is required")
    request = urllib.request.Request(
        f"{access.base_url}{path}",
        method="GET",
        headers={
            "Authorization": f"Bearer {access.token}",
            "Accept": "application/json",
        },
    )
    open_url = opener if opener is not None else urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise AuditAuthorizationError(
                f"{path} answered HTTP {error.code}; the token is missing, "
                "expired or the user is not a local administrator"
            ) from error
        raise AuditRequestError(f"{path} answered HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AuditRequestError(f"{path} is unreachable: {error}") from error
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuditRequestError(f"{path} did not return a JSON document") from error
    return EndpointResult(name="", path=path, status=status, payload=payload)


def _get(mapping: Any, key: str) -> Any:
    return mapping.get(key) if isinstance(mapping, dict) else None


def _summarize_core_config(payload: Any, summary: dict[str, Any]) -> None:
    version = _get(payload, "version")
    summary["core"] = {"version": version if isinstance(version, str) else None}


def _summarize_climate_mode(payload: Any, summary: dict[str, Any]) -> None:
    rollout = _get(payload, "rollout")
    cutover = _get(payload, "cutover")
    summary["climate_mode"] = {
        "mode": _get(payload, "mode"),
        "contour_configured": _get(payload, "contour_configured"),
        "rollout": {
            "phase": _get(rollout, "phase"),
            "enable_allowed": _get(rollout, "enable_allowed"),
            "commands_enabled": _get(rollout, "commands_enabled"),
            "canary_room_id": _get(rollout, "canary_room_id"),
            "managed_room_count": _get(rollout, "managed_room_count"),
            "shadow_ready_room_count": _get(rollout, "shadow_ready_room_count"),
            "shadow_sample_count": _get(rollout, "shadow_sample_count"),
            "reasons": _get(rollout, "reasons") or [],
        },
        "cutover": {
            "phase": _get(cutover, "phase"),
            "node_red_can_be_disabled": _get(cutover, "node_red_can_be_disabled"),
            "pending_room_ids": _get(cutover, "pending_room_ids") or [],
            "reasons": _get(cutover, "reasons") or [],
        },
    }


def _summarize_readiness(payload: Any, summary: dict[str, Any]) -> None:
    summary["readiness"] = {
        "bridge_mode": _get(payload, "bridge_mode"),
        "status": _get(payload, "status"),
        "ready": _get(payload, "ready"),
        "fresh": _get(payload, "fresh"),
        "registry": _get(payload, "registry"),
        "reconciliation": _get(payload, "reconciliation"),
        "reasons": _get(payload, "reasons") or [],
    }


def _summarize_registry(payload: Any, summary: dict[str, Any]) -> None:
    rooms = _get(payload, "rooms")
    devices = _get(payload, "devices")
    room_ids = sorted(
        str(room.get("id"))
        for room in rooms or []
        if isinstance(room, dict) and isinstance(room.get("id"), str)
    )
    summary["registry"] = {
        "room_count": len(rooms) if isinstance(rooms, list) else 0,
        "device_count": len(devices) if isinstance(devices, list) else 0,
        "room_ids": room_ids,
    }


def _summarize_device_bindings(payload: Any, summary: dict[str, Any]) -> None:
    devices: list[dict[str, Any]] = []
    for room in _get(payload, "rooms") or []:
        if not isinstance(room, dict):
            continue
        for device in room.get("devices") or []:
            if isinstance(device, dict):
                devices.append(device)
    unbound = sorted(
        str(device.get("device_id"))
        for device in devices
        if device.get("current_entity_id") is None
    )
    unavailable = sorted(
        str(device.get("device_id"))
        for device in devices
        if device.get("current_entity_id") is not None
        and device.get("current_available") is not True
    )
    without_candidates = sorted(
        str(device.get("device_id"))
        for device in devices
        if not device.get("candidates")
    )
    summary["device_bindings"] = {
        "snapshot_revision": _get(payload, "snapshot_revision"),
        "device_count": len(devices),
        "bound_count": len(devices) - len(unbound),
        "unbound_device_ids": unbound,
        "unavailable_bound_device_ids": unavailable,
        "devices_without_candidates": without_candidates,
    }


def _summarize_shadow_window(payload: Any, summary: dict[str, Any]) -> None:
    window_summary = _get(payload, "summary")
    rooms = [
        {
            "room_id": _get(room, "room_id"),
            "verdict": _get(room, "verdict"),
            "reasons": _get(room, "reasons") or [],
        }
        for room in _get(payload, "rooms") or []
        if isinstance(room, dict)
    ]
    summary["shadow_window"] = {
        "collection_active": _get(_get(payload, "window"), "collection_active"),
        "sample_count": _get(window_summary, "sample_count"),
        "room_count": _get(window_summary, "room_count"),
        "ready_room_count": _get(window_summary, "ready_room_count"),
        "diverged_room_count": _get(window_summary, "diverged_room_count"),
        "insufficient_room_count": _get(window_summary, "insufficient_room_count"),
        "first_observed_at": _get(window_summary, "first_observed_at"),
        "latest_observed_at": _get(window_summary, "latest_observed_at"),
        "rooms": sorted(rooms, key=lambda item: str(item["room_id"])),
    }


def _summarize_shadow_comparison(payload: Any, summary: dict[str, Any]) -> None:
    rooms = _get(payload, "rooms")
    statuses: dict[str, int] = {}
    if isinstance(rooms, list):
        for room in rooms:
            if not isinstance(room, dict):
                continue
            status = str(room.get("status"))
            statuses[status] = statuses.get(status, 0) + 1
    summary["shadow_comparison"] = {
        "observed_at": _get(payload, "observed_at"),
        "room_statuses": dict(sorted(statuses.items())) or None,
    }


def build_summary(results: list[EndpointResult]) -> dict[str, Any]:
    """Build a sanitized summary without entity IDs or secret material."""

    summary: dict[str, Any] = {"endpoints": {}}
    handlers = {
        "core_config": _summarize_core_config,
        "climate_mode": _summarize_climate_mode,
        "climate_readiness": _summarize_readiness,
        "climate_registry": _summarize_registry,
        "climate_device_bindings": _summarize_device_bindings,
        "climate_shadow_comparison": _summarize_shadow_comparison,
        "climate_shadow_window": _summarize_shadow_window,
    }
    for result in results:
        summary["endpoints"][result.name] = result.status
        handler = handlers.get(result.name)
        if handler is not None:
            handler(result.payload, summary)
    return summary


def format_summary(summary: dict[str, Any]) -> str:
    """Render the sanitized summary as deterministic plain text."""

    return json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)


def _default_output_dir(access_file: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return access_file.parent / "audit" / timestamp


def run_audit(
    access: AdminAccess,
    *,
    output_dir: Path,
    timeout: float,
    opener: Any = None,
) -> tuple[list[EndpointResult], dict[str, Any]]:
    """Fetch every audit endpoint with GET and persist raw responses."""

    if not isinstance(output_dir, Path):
        raise TypeError("output directory must be a Path")
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    results: list[EndpointResult] = []
    for name, path in ENDPOINTS:
        fetched = http_get_json(access, path, timeout=timeout, opener=opener)
        result = EndpointResult(
            name=name,
            path=path,
            status=fetched.status,
            payload=fetched.payload,
        )
        results.append(result)
        raw_path = output_dir / f"{name}.json"
        raw_path.write_text(
            json.dumps(result.payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(raw_path, 0o600)
    summary = build_summary(results)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(format_summary(summary) + "\n", encoding="utf-8")
    os.chmod(summary_path, 0o600)
    return results, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only production climate audit. Performs GET requests only; "
            "admin access is read from a JSON file outside the workspace."
        )
    )
    parser.add_argument(
        "--access-file",
        type=Path,
        default=DEFAULT_ACCESS_FILE,
        help=f"JSON file with base_url and token (default: {DEFAULT_ACCESS_FILE})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "directory for raw JSON responses; must stay outside the "
            "repository (default: <access dir>/audit/<UTC timestamp>)"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="per-request timeout in seconds (default: 10)",
    )
    args = parser.parse_args(argv)

    try:
        access = load_access(args.access_file)
    except AccessFileError as error:
        print(f"access error: {error}", file=sys.stderr)
        return 2

    output_dir = args.output_dir or _default_output_dir(args.access_file)
    try:
        _, summary = run_audit(access, output_dir=output_dir, timeout=args.timeout)
    except AuditAuthorizationError as error:
        print(f"authorization failed: {error}", file=sys.stderr)
        return 3
    except AuditRequestError as error:
        print(f"request failed: {error}", file=sys.stderr)
        return 4

    print(format_summary(summary))
    print(f"\nraw responses saved outside the repository: {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
