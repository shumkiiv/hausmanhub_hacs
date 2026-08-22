"""Pure projection of Home Assistant recorder rows into the energy API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ENERGY_HISTORY_MAX_WINDOW_DAYS = 31
ENERGY_HISTORY_MAX_SERIES = 128
ENERGY_HISTORY_MAX_POINTS_PER_SERIES = 8928
ENERGY_HISTORY_WINDOWS = frozenset({"day", "week", "month"})


def resolve_energy_history_window(
    window: str,
    timezone_name: str,
    *,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Resolve one local calendar window as a DST-aware half-open interval."""

    if window not in ENERGY_HISTORY_WINDOWS or not isinstance(timezone_name, str):
        raise ValueError("energy history calendar window is invalid")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("energy history timezone is invalid") from error
    aware_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    local_now = aware_now.astimezone(zone)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "week":
        start -= timedelta(days=start.weekday())
        end = start + timedelta(days=7)
    elif window == "month":
        start = start.replace(day=1)
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
    else:
        end = start + timedelta(days=1)
    return start, end


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
        return value if value.tzinfo is not None else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else None
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
    start: datetime,
    end: datetime,
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
        if at is not None and start <= at < end and value is not None:
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
        ][:ENERGY_HISTORY_MAX_POINTS_PER_SERIES]
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
    ][:ENERGY_HISTORY_MAX_POINTS_PER_SERIES]


def build_energy_history(
    *,
    start: datetime,
    end: datetime,
    interval: str,
    descriptors: Sequence[EnergySeriesDescriptor],
    rows_by_entity: Mapping[str, object],
    window: str = "explicit",
    timezone_name: str | None = None,
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
                start,
                end,
            ),
        }
        for descriptor in descriptors[:124]
    ]
    return {
        "contract": {"name": "hausman-hub-energy-history", "version": 1},
        "from": start.isoformat(),
        "to": end.isoformat(),
        "interval": interval,
        "window": window,
        **({"timezone": timezone_name} if timezone_name is not None else {}),
        "page": {
            "strategy": "time_window",
            "order": "timestamp_asc",
            "fromInclusive": True,
            "toExclusive": True,
            "maxWindowDays": ENERGY_HISTORY_MAX_WINDOW_DAYS,
            "maxSeries": ENERGY_HISTORY_MAX_SERIES,
            "maxPointsPerSeries": ENERGY_HISTORY_MAX_POINTS_PER_SERIES,
        },
        "retention": {
            "mode": "source_bound",
            "source": "home_assistant_recorder",
            "separateCopy": False,
            "missingPointsAllowed": True,
        },
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
