"""Measure a representative dashboard projection without Home Assistant I/O."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.hausman_hub.application.dashboard_snapshot import (  # noqa: E402
    DashboardArea,
    DashboardDevice,
    DashboardEntity,
    DashboardScenario,
    build_dashboard_snapshot,
)
from custom_components.hausman_hub.domain.hub_settings import (  # noqa: E402
    HausmanHubSettings,
)


def _representative_snapshot() -> dict[str, object]:
    return build_dashboard_snapshot(
        areas=(
            DashboardArea("living", "Гостиная", "mdi:sofa"),
            DashboardArea("kitchen", "Кухня", "mdi:countertop"),
        ),
        devices=(
            DashboardDevice("ac", "Кондиционер", "living", "AC Demo", "Example"),
            DashboardDevice("kettle", "Чайник", "kitchen"),
            DashboardDevice("leak", "Датчик протечки", "kitchen"),
        ),
        entities=(
            DashboardEntity(
                "climate.living",
                "climate",
                "cool",
                "Кондиционер",
                {"temperature": 25, "current_temperature": 25.4, "fan_mode": "medium"},
                "ac",
                "living",
            ),
            DashboardEntity(
                "sensor.living_temperature",
                "sensor",
                "25.4",
                "Температура",
                {"device_class": "temperature", "unit_of_measurement": "°C"},
                "ac",
                "living",
            ),
            DashboardEntity(
                "switch.kettle", "switch", "on", "Нагрев", {}, "kettle", "kitchen"
            ),
            DashboardEntity(
                "sensor.kettle_power",
                "sensor",
                "1850",
                "Мощность",
                {"device_class": "power", "unit_of_measurement": "W"},
                "kettle",
                "kitchen",
            ),
            DashboardEntity(
                "sensor.kettle_current",
                "sensor",
                "8.04",
                "Ток",
                {"device_class": "current", "unit_of_measurement": "A"},
                "kettle",
                "kitchen",
            ),
            DashboardEntity(
                "sensor.kettle_voltage",
                "sensor",
                "230.1",
                "Напряжение",
                {"device_class": "voltage", "unit_of_measurement": "V"},
                "kettle",
                "kitchen",
            ),
            DashboardEntity(
                "binary_sensor.kitchen_leak",
                "binary_sensor",
                "on",
                "Протечка под мойкой",
                {"device_class": "moisture"},
                "leak",
                "kitchen",
            ),
            DashboardEntity(
                "light.floor_lamp",
                "light",
                "on",
                "Торшер",
                {"brightness": 180},
                None,
                "living",
            ),
        ),
        scenarios=(DashboardScenario("movie", "Кино", favorite=True),),
        generated_at_ms=1_786_379_200_000,
        local_iso="2026-08-14T12:00:00+03:00",
        state_revision=42,
        energy_settings=HausmanHubSettings(energy_use_all_devices=True),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1000)
    args = parser.parse_args()
    if not 10 <= args.runs <= 100_000:
        parser.error("runs must be between 10 and 100000")

    durations_ms: list[float] = []
    payload = _representative_snapshot()
    for _ in range(args.runs):
        started = time.perf_counter_ns()
        payload = _representative_snapshot()
        durations_ms.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(durations_ms)
    p95 = ordered[max(0, int(args.runs * 0.95) - 1)]
    compact_size = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    )
    print(
        json.dumps(
            {
                "runs": args.runs,
                "medianMs": round(statistics.median(durations_ms), 3),
                "p95Ms": round(p95, 3),
                "maxMs": round(max(durations_ms), 3),
                "compactBytes": compact_size,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
