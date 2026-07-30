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
                values[-1]
                if descriptor.unit == "kWh"
                else sum(values) / len(values),
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

    return {
        "contract": {"name": "hausman-hub-energy-history", "version": 1},
        "from": start.isoformat(),
        "to": end.isoformat(),
        "interval": interval,
        "series": [
            {
                "sourceId": descriptor.source_id,
                "deviceId": descriptor.device_id,
                "name": descriptor.name,
                "roomId": descriptor.room_id,
                "unit": descriptor.unit,
                "points": _points(
                    descriptor,
                    rows_by_entity.get(descriptor.entity_id, []),
                    interval,
                ),
            }
            for descriptor in descriptors[:128]
        ],
    }
