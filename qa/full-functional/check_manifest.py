#!/usr/bin/env python3
"""Fail closed unless the runtime report proves the exact prepared content."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from release_pin import MANIFEST, is_release_provenance


HARNESS_ORIGIN = "http://127.0.0.1:8765"
EXPECTED_BLOCKED_EXTERNAL_IMAGES = {
    "https://www.zigbee2mqtt.io/images/devices/AC211.png",
    "https://www.zigbee2mqtt.io/images/devices/TO-Q-SY1-JZT.png",
    "https://www.zigbee2mqtt.io/images/devices/TRVZB.png",
    "https://www.zigbee2mqtt.io/images/devices/TS0505B_1.png",
}
KNOWN_FRONTEND_ASSET_NAMES = (
    "area-binding", "buttons", "catalog", "climate-overview", "climate-side", "command-feedback", "control-channel", "correlation",
    "device-actions", "device-bindings", "device-card", "device-controls", "device-discovery", "device-features", "device-inventory",
    "device-maintenance", "device-property-names", "devices-overview", "diagnostics", "energy-chart", "energy-meter", "energy",
    "error-taxonomy", "feedback", "first-run-draft", "harness-intents", "hero-room-navigation", "home-sections", "intercom",
    "inventory-duplicates", "kiosk", "library-hero", "light-protection", "lighting-side", "lighting", "media-device", "media-overview",
    "media-side", "modal", "navigation", "notice", "overview-events-modal", "overview-hero-state", "overview-side", "overview-utility-cards",
    "overview", "pagination", "panel", "power-links", "rollout", "room-climate-sources", "room-device-groups", "room-icons", "room-setup",
    "rooms-side", "rooms", "scenario-ai", "scenario-badges", "scenario-bulk", "scenario-catalog", "scenario-device-picker",
    "scenario-editor-scroll", "scenario-extensions", "scenario-fields", "scenario-icons", "scenario-node-red", "scenario-rooms", "scenario-state",
    "scenarios", "security-overview", "settings-profile", "settings-rooms", "settings", "switch", "technical-log", "tokens", "ui-state",
    "weather-sources", "wizard-validation",
)


def allowed_local_paths() -> set[str]:
    paths = {
        "/api/hausman_hub/panel/assets/hero_living_room_night.png",
        "/api/hausman_hub/panel/assets/hero_premium_kitchen_night_v2.png",
        "/api/hausman_hub/panel/assets/hero_room_bedroom_night.webp",
        "/api/hausman_hub/panel/assets/hero_room_office_night.webp",
        "/api/hausman_hub/panel/hausman-hub-panel.css",
        "/tests/visual/hausman-hub-panel-harness.html",
    }
    for name in KNOWN_FRONTEND_ASSET_NAMES:
        for suffix in (".js", ".css"):
            pathname = f"/custom_components/hausman_hub/frontend/hausman-hub-{name}{suffix}"
            if (Path(__file__).parents[2] / pathname.lstrip("/")).is_file():
                paths.add(pathname)
    return paths


def valid_request_record(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"method", "resource_type", "url"}
        and all(isinstance(value.get(field), str) and value[field] for field in ("method", "resource_type", "url"))
        and not urlsplit(value["url"]).query
        and not urlsplit(value["url"]).fragment
    )


def is_allowed_continued_request(value: object) -> bool:
    if not valid_request_record(value) or value["method"] != "GET":
        return False
    parsed = urlsplit(value["url"])
    return f"{parsed.scheme}://{parsed.netloc}" == HARNESS_ORIGIN and parsed.path in allowed_local_paths()


def is_expected_blocked_image(value: object) -> bool:
    return valid_request_record(value) and value == {
        "method": "GET", "resource_type": "image", "url": value["url"],
    } and value["url"] in EXPECTED_BLOCKED_EXTERNAL_IMAGES


data = json.loads(MANIFEST.read_text(encoding="utf-8"))
errors: list[str] = []
if not is_release_provenance(data.get("provenance")):
    errors.append("manifest content provenance mismatch")
items = data.get("interactions", [])
expected = [row.get("source_id") for row in items]
if not items or len(expected) != len(set(expected)):
    errors.append("empty or duplicate manifest source IDs")
for item in items:
    evidence = item.get("evidence", {})
    if not all((item.get("source_id"), item.get("source_ref"), item.get("screen"), item.get("module"), evidence.get("source"), evidence.get("runtime"))):
        errors.append(f"invalid entry: {item}")
if data.get("missing_evidence"):
    errors.append("manifest declares missing evidence")
report_path = os.environ.get("HACS_RUNTIME_REPORT")
if not report_path:
    errors.append("HACS_RUNTIME_REPORT is required")
else:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not is_release_provenance(report.get("provenance")):
        errors.append("runtime report content provenance mismatch")
    observed = report.get("observed_source_ids", [])
    if set(expected) != set(observed) or len(observed) != len(set(observed)):
        errors.append("source manifest is not an exact runtime-stack set")
    signatures = report.get("signatures", [])
    attempted = report.get("attempted_signatures", [])
    clicked = report.get("clicked_signatures", [])
    blocked = report.get("blocked_signatures", [])
    signature_set = set(signatures)
    attempted_set = set(attempted)
    clicked_set = set(clicked)
    blocked_set = set(blocked)
    if (
        not signatures
        or any(len(items) != len(set(items)) for items in (signatures, attempted, clicked, blocked))
        or signature_set != attempted_set
        or signature_set != clicked_set | blocked_set
        or clicked_set & blocked_set
    ):
        errors.append("signature action coverage is incomplete")
    latency = report.get("safe_action_latency_ms")
    latency_values = [latency.get(key) for key in ("p50_ms", "p95_ms", "max_ms")] if isinstance(latency, dict) else []
    if (
        not isinstance(latency, dict)
        or not isinstance(latency.get("count"), int)
        or isinstance(latency.get("count"), bool)
        or latency.get("count") != len(clicked)
        or len(latency_values) != 3
        or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0 for value in latency_values)
        or (len(latency_values) == 3 and latency_values != sorted(latency_values))
    ):
        errors.append("safe action latency telemetry is incomplete")
    route_fields = (
        "continued_requests",
        "mutation_escape_requests",
        "unexpected_local_requests",
        "blocked_external_requests",
        "unexpected_external_requests",
    )
    route_telemetry = {name: report.get(name) for name in route_fields}
    if any(not isinstance(values, list) or not all(valid_request_record(value) for value in values) for values in route_telemetry.values()):
        errors.append("route telemetry is incomplete")
    else:
        if not all(is_allowed_continued_request(value) for value in route_telemetry["continued_requests"]):
            errors.append("route telemetry includes an unapproved continued request")
        if not all(is_expected_blocked_image(value) for value in route_telemetry["blocked_external_requests"]):
            errors.append("route telemetry includes an unexpected blocked external request")
        if route_telemetry["mutation_escape_requests"] or route_telemetry["unexpected_local_requests"] or route_telemetry["unexpected_external_requests"]:
            errors.append("route telemetry records an unsafe request")
        if (
            not isinstance(report.get("external_network"), bool)
            or not isinstance(report.get("mutation_escape"), bool)
            or report["external_network"] != bool(route_telemetry["unexpected_external_requests"])
            or report["mutation_escape"] != bool(route_telemetry["mutation_escape_requests"])
        ):
            errors.append("route telemetry booleans disagree with request records")
    if (
        report.get("missing")
        or report.get("errors")
        or report.get("unrecorded_signatures")
        or report.get("unclassified")
        or report.get("unrecorded_commands")
        or report.get("unexpected_calls")
        or report.get("failed_effects")
    ):
        errors.append("runtime report records an escape, missing control, or error")
if errors:
    print("FAIL\n" + "\n".join(errors))
    raise SystemExit(1)
print(f"PASS: {len(items)} observed source sites and exact runtime control coverage")
