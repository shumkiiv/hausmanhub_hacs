#!/usr/bin/env python3
"""Evaluate a command-free production canary and fail closed.

The input is a sanitized JSON artifact collected by the release operator.  This
tool performs no network requests and no rollback itself.  Exit code 0 means
that the full deployment may continue, 10 requests rollback, and 2 reports an
invalid or incomplete artifact.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


MIN_CANARY_SECONDS = 60
MIN_CANARY_SAMPLES = 12
MAX_ERROR_RATE = 0.01
MAX_P95_LATENCY_MS = 1_000.0
MAX_PENDING_AGE_MS = 60_000
MAX_UNAVAILABLE_INCREASE = 0
HEALTH_PATH = "/api/config"
SAMPLE_PATH = "/api/hausman_hub/v1/dashboard"


class CanaryArtifactError(ValueError):
    """Raised when a canary artifact cannot prove a safe deployment."""


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CanaryArtifactError(f"{label} must be an object")
    return value


def _non_negative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise CanaryArtifactError(f"{label} must be a non-negative integer")
    return value


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise CanaryArtifactError(f"{label} must be a positive number")
    return float(value)


def _nearest_rank_p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    rank = max(1, (95 * len(ordered) + 99) // 100)
    return ordered[rank - 1]


def evaluate_canary(artifact: object) -> dict[str, object]:
    """Return a redacted proceed/rollback decision for one release canary."""

    root = _mapping(artifact, "artifact")
    backup_id = root.get("backupId")
    if not isinstance(backup_id, str) or not backup_id.strip():
        raise CanaryArtifactError("backupId must prove a completed backup")
    if root.get("configCheckPassed") is not True:
        raise CanaryArtifactError("configCheckPassed must be true")

    probe = _mapping(root.get("healthProbe"), "healthProbe")
    if set(probe) != {"method", "path", "status", "latencyMs"}:
        raise CanaryArtifactError("healthProbe must contain exactly one GET result")
    if probe.get("method") != "GET" or probe.get("path") != HEALTH_PATH:
        raise CanaryArtifactError("healthProbe must be GET /api/config")
    probe_status = _non_negative_int(probe.get("status"), "healthProbe.status")
    _positive_number(probe.get("latencyMs"), "healthProbe.latencyMs")

    baseline = _mapping(root.get("baseline"), "baseline")
    baseline_unavailable = _non_negative_int(
        baseline.get("unavailableCount"), "baseline.unavailableCount"
    )
    canary = _mapping(root.get("canary"), "canary")
    duration = _non_negative_int(canary.get("durationSeconds"), "canary.durationSeconds")
    samples = canary.get("samples")
    if not isinstance(samples, list):
        raise CanaryArtifactError("canary.samples must be an array")

    latencies: list[float] = []
    unavailable_counts: list[int] = []
    pending_ages: list[int] = []
    errors = 0
    for index, raw_sample in enumerate(samples):
        sample = _mapping(raw_sample, f"canary.samples[{index}]")
        if set(sample) != {
            "method",
            "path",
            "status",
            "latencyMs",
            "unavailableCount",
            "pendingAgeMs",
        }:
            raise CanaryArtifactError(
                f"canary.samples[{index}] has unexpected or missing fields"
            )
        if sample.get("method") != "GET" or sample.get("path") != SAMPLE_PATH:
            raise CanaryArtifactError(
                f"canary.samples[{index}] must be a read-only dashboard GET"
            )
        status = _non_negative_int(sample.get("status"), f"canary.samples[{index}].status")
        latencies.append(
            _positive_number(
                sample.get("latencyMs"), f"canary.samples[{index}].latencyMs"
            )
        )
        if not 200 <= status < 300:
            errors += 1
        unavailable = sample.get("unavailableCount")
        pending_age = sample.get("pendingAgeMs")
        if unavailable is not None:
            unavailable_counts.append(
                _non_negative_int(unavailable, f"canary.samples[{index}].unavailableCount")
            )
        if pending_age is not None:
            pending_ages.append(
                _non_negative_int(pending_age, f"canary.samples[{index}].pendingAgeMs")
            )

    sample_count = len(samples)
    error_rate = errors / sample_count if sample_count else 1.0
    p95_latency = _nearest_rank_p95(latencies) if latencies else 0.0
    max_pending_age = max(pending_ages, default=0)
    max_unavailable = max(unavailable_counts, default=baseline_unavailable)
    reasons: list[str] = []
    if probe_status < 200 or probe_status >= 300:
        reasons.append("health_probe_failed")
    if duration < MIN_CANARY_SECONDS:
        reasons.append("canary_window_too_short")
    if sample_count < MIN_CANARY_SAMPLES:
        reasons.append("too_few_samples")
    if error_rate > MAX_ERROR_RATE:
        reasons.append("error_rate_exceeded")
    if p95_latency > MAX_P95_LATENCY_MS:
        reasons.append("p95_latency_exceeded")
    if max_pending_age > MAX_PENDING_AGE_MS:
        reasons.append("pending_age_exceeded")
    if max_unavailable - baseline_unavailable > MAX_UNAVAILABLE_INCREASE:
        reasons.append("unavailable_count_increased")
    if len(unavailable_counts) != sample_count:
        reasons.append("unavailable_count_missing")

    decision = "rollback" if reasons else "proceed"
    return {
        "decision": decision,
        "reasons": reasons,
        "metrics": {
            "durationSeconds": duration,
            "sampleCount": sample_count,
            "errorCount": errors,
            "errorRate": round(error_rate, 6),
            "p95LatencyMs": round(p95_latency, 3),
            "maxPendingAgeMs": max_pending_age,
            "baselineUnavailableCount": baseline_unavailable,
            "maxUnavailableCount": max_unavailable,
        },
        "thresholds": {
            "minDurationSeconds": MIN_CANARY_SECONDS,
            "minSamples": MIN_CANARY_SAMPLES,
            "maxErrorRate": MAX_ERROR_RATE,
            "maxP95LatencyMs": MAX_P95_LATENCY_MS,
            "maxPendingAgeMs": MAX_PENDING_AGE_MS,
            "maxUnavailableIncrease": MAX_UNAVAILABLE_INCREASE,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    try:
        artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
        result = evaluate_canary(artifact)
    except (OSError, json.JSONDecodeError, CanaryArtifactError) as error:
        print(json.dumps({"decision": "invalid", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["decision"] == "proceed" else 10


if __name__ == "__main__":
    raise SystemExit(main())
