#!/usr/bin/env python3
"""Audit persisted climate control routes against Home Assistant registries.

The tool is read-only.  It accepts explicit storage paths so an administrator
can run it in the Home Assistant SSH app without granting network access or
copying the home configuration into the repository.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _load_storage(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ValueError(f"{path} is not a Home Assistant storage document")
    return payload


def audit_routes(
    climate_storage: dict[str, object],
    entity_storage: dict[str, object],
) -> list[dict[str, str]]:
    """Return deterministic route issues without changing either document."""

    climate_data = climate_storage["data"]
    entity_data = entity_storage["data"]
    if not isinstance(climate_data, dict) or not isinstance(entity_data, dict):
        raise ValueError("storage data must be objects")
    devices = climate_data.get("devices")
    entities = entity_data.get("entities")
    if not isinstance(devices, list) or not isinstance(entities, list):
        raise ValueError("storage registries must contain device and entity lists")
    entity_by_id = {
        item.get("entity_id"): item
        for item in entities
        if isinstance(item, dict) and isinstance(item.get("entity_id"), str)
    }
    issues: list[dict[str, str]] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        device_id = str(device.get("id") or "unknown")
        room_id = str(device.get("room_id") or "unknown")
        channel = device.get("control_channel")
        endpoints = device.get("endpoints")
        if channel is None or not isinstance(endpoints, list):
            continue
        control = next(
            (
                endpoint
                for endpoint in endpoints
                if isinstance(endpoint, dict) and endpoint.get("role") == "control"
            ),
            None,
        )
        if control is None or not isinstance(control.get("entity_id"), str):
            issues.append(_issue(device_id, room_id, str(channel), "missing_control_endpoint"))
            continue
        entity_id = control["entity_id"]
        entity = entity_by_id.get(entity_id)
        if not isinstance(entity, dict):
            issues.append(_issue(device_id, room_id, str(channel), "entity_not_registered"))
            continue
        domain = entity_id.split(".", 1)[0]
        platform = entity.get("platform")
        if channel == "yandex_remote" and platform != "yandex_station":
            issues.append(_issue(device_id, room_id, channel, "yandex_platform_mismatch"))
        elif channel == "universal_ir" and not (
            domain == "remote" or platform == "smartir"
        ):
            issues.append(_issue(device_id, room_id, channel, "universal_ir_platform_mismatch"))
        elif channel == "direct_wifi" and platform in {"smartir", "yandex_station"}:
            issues.append(_issue(device_id, room_id, channel, "direct_platform_mismatch"))
    return sorted(
        issues,
        key=lambda item: (item["room_id"], item["device_id"], item["code"]),
    )


def _issue(device_id: str, room_id: str, channel: str, code: str) -> dict[str, str]:
    return {
        "device_id": device_id,
        "room_id": room_id,
        "channel": channel,
        "code": code,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--climate-registry", type=Path, required=True)
    parser.add_argument("--entity-registry", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        issues = audit_routes(
            _load_storage(args.climate_registry),
            _load_storage(args.entity_registry),
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Climate route audit failed: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"issues": issues}, ensure_ascii=False, indent=2))
    elif issues:
        for issue in issues:
            print(
                f"{issue['room_id']} / {issue['device_id']}: "
                f"{issue['channel']} -> {issue['code']}"
            )
    else:
        print("Climate control routes are consistent.")
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
