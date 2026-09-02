#!/usr/bin/env python3
"""Fail closed unless the runtime report proves the exact prepared content."""
from __future__ import annotations

import json
import os
from pathlib import Path

from release_pin import MANIFEST, is_release_provenance


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
    signatures, attempted, clicked = report.get("signatures", []), report.get("attempted_signatures", []), report.get("clicked_signatures", [])
    if not signatures or set(signatures) != set(attempted) or set(signatures) != set(clicked):
        errors.append("signature action coverage is incomplete")
    if report.get("missing") or report.get("external_network") or report.get("mutation_escape") or report.get("errors"):
        errors.append("runtime report records an escape, missing control, or error")
if errors:
    print("FAIL\n" + "\n".join(errors))
    raise SystemExit(1)
print(f"PASS: {len(items)} observed source sites and exact runtime control coverage")
