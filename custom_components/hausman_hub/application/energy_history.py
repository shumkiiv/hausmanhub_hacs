"""Pure projection of Home Assistant recorder rows into the energy API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EnergySeriesDescriptor:
    entity_id: str
    source_id: str
    device_id: str
    name: str
    room_id: str | None
    unit: str
    value_key: str
    scale: float = 1.0
    metric: str | None = None


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _points(
    descriptor: EnergySeriesDescriptor,
    rows: object,
    interval: str,
) -> list[dict[str, object]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    normalized: list[tuple[datetime, float]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        at = _timestamp(row.get("start"))
        value = _number(row.get(descriptor.value_key))
        if value is None and descriptor.value_key == "sum":
            value = _number(row.get("state"))
        if value is None:
            value = _number(row.get("mean"))
        if at is not None and value is not None:
            normalized.append((at, value * descriptor.scale))
    normalized.sort(key=lambda item: item[0])
    is_energy = descriptor.metric == "energy" or descriptor.unit == "kWh"
    if is_energy:
        deltas: list[tuple[datetime, float]] = []
        previous: float | None = None
        for at, value in normalized:
            if previous is not None:
                # Recorder statistics expose a cumulative total. A counter
                # reset starts a new base instead of producing a negative use.
                deltas.append((at, value - previous if value >= previous else max(value, 0.0)))
            previous = value
        normalized = deltas
    if interval != "15m":
        return [
            {"at": at.isoformat(), "value": round(value, 3)}
            for at, value in normalized
        ]
    buckets: dict[datetime, list[float]] = {}
    for at, value in normalized:
        bucket = at.replace(minute=(at.minute // 15) * 15, second=0, microsecond=0)
        buckets.setdefault(bucket, []).append(value)
    return [
        {
            "at": at.isoformat(),
            "value": round(
                sum(values) if is_energy else sum(values) / len(values),
                3,
            ),
        }
        for at, values in sorted(buckets.items())
    ]


def build_energy_history(
    *,
    start: datetime,
    end: datetime,
    interval: str,
    descriptors: Sequence[EnergySeriesDescriptor],
    rows_by_entity: Mapping[str, object],
) -> dict[str, object]:
    """Build one bounded response without exposing recorder/entity identifiers."""

    physical_series = [
        {
            "sourceId": descriptor.source_id,
            "deviceId": descriptor.device_id,
            "name": descriptor.name,
            "roomId": descriptor.room_id,
            "unit": descriptor.unit,
            "metric": descriptor.metric or _metric_for_unit(descriptor.unit),
            "scope": "device",
            "aggregation": "delta" if descriptor.unit == "kWh" else "mean",
            "points": _points(
                descriptor,
                rows_by_entity.get(descriptor.entity_id, []),
                interval,
            ),
        }
        for descriptor in descriptors[:124]
    ]
    return {
        "contract": {"name": "hausman-hub-energy-history", "version": 1},
        "from": start.isoformat(),
        "to": end.isoformat(),
        "interval": interval,
        "series": physical_series + _selection_series(physical_series),
    }


def _metric_for_unit(unit: str) -> str:
    return {"W": "power", "A": "current", "V": "voltage", "kWh": "energy"}[unit]


def _selection_series(series: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Aggregate already-bucketed public rows for the selected devices."""

    result: list[dict[str, object]] = []
    labels = {
        "power": ("Мощность", "W", "sum"),
        "current": ("Ток", "A", "sum"),
        "voltage": ("Напряжение", "V", "mean"),
        "energy": ("Энергия", "kWh", "sum"),
    }
    for metric, (label, unit, reduction) in labels.items():
        matching = [item for item in series if item.get("metric") == metric]
        if not matching:
            continue
        values_by_at: dict[str, list[float]] = {}
        for item in matching:
            points = item.get("points", [])
            if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
                continue
            for point in points:
                if not isinstance(point, Mapping) or not isinstance(point.get("at"), str):
                    continue
                value = _number(point.get("value"))
                if value is not None:
                    values_by_at.setdefault(point["at"], []).append(value)
        result.append(
            {
                "sourceId": f"selection:{metric}",
                "deviceId": "selection",
                "name": f"Выбранные устройства · {label}",
                "roomId": None,
                "unit": unit,
                "metric": metric,
                "scope": "selection",
                "aggregation": reduction,
                "points": [
                    {
                        "at": at,
                        "value": round(
                            sum(values) / len(values) if reduction == "mean" else sum(values),
                            3,
                        ),
                    }
                    for at, values in sorted(values_by_at.items())
                ],
            }
        )
    return result
