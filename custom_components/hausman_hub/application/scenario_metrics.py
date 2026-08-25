"""Bounded in-memory latency metrics for scenario backend paths."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
import math


SCENARIO_PATH_BUDGETS_MS: Mapping[str, float] = {
    "list": 100.0,
    "catalog": 500.0,
    "dry_run": 500.0,
    "storage": 250.0,
    "last_result": 50.0,
}
MAX_LATENCY_SAMPLES = 128


def _percentile(samples: tuple[float, ...], fraction: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 3)


@dataclass(slots=True)
class ScenarioPathMetrics:
    """Keep only recent aggregate-safe samples, never request payloads."""

    _samples: dict[str, deque[float]] = field(default_factory=dict)

    def record(self, path: str, duration_ms: float) -> None:
        if path not in SCENARIO_PATH_BUDGETS_MS:
            raise ValueError(f"unknown scenario path metric: {path}")
        if not math.isfinite(duration_ms) or duration_ms < 0:
            raise ValueError("scenario path duration must be finite and non-negative")
        bucket = self._samples.setdefault(path, deque(maxlen=MAX_LATENCY_SAMPLES))
        bucket.append(float(duration_ms))

    def snapshot(self) -> dict[str, dict[str, float | int | str]]:
        result: dict[str, dict[str, float | int | str]] = {}
        for path, budget_ms in SCENARIO_PATH_BUDGETS_MS.items():
            samples = tuple(self._samples.get(path, ()))
            p50 = _percentile(samples, 0.50)
            p95 = _percentile(samples, 0.95)
            result[path] = {
                "count": len(samples),
                "p50Ms": p50,
                "p95Ms": p95,
                "budgetMs": budget_ms,
                "status": "within_budget" if not samples or p95 <= budget_ms else "over_budget",
            }
        return result
