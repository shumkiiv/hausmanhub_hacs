"""Pure server-side comfort score for dashboard clients."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


_TEMPERATURE_WEIGHT = 0.5
_HUMIDITY_WEIGHT = 0.3
_CO2_WEIGHT = 0.2
_MINIMUM_FRESH_COVERAGE = 0.6


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _mean(values: Iterable[float]) -> float | None:
    collected = tuple(values)
    if not collected:
        return None
    return sum(collected) / len(collected)


def _bounded_score(penalty: float) -> float:
    return max(0.0, min(100.0, 100.0 - penalty))


def _temperature_score(rooms: tuple[Mapping[str, object], ...]) -> float | None:
    scores = []
    for room in rooms:
        current = _number(room.get("temp"))
        target = _number(room.get("targetTemp"))
        if current is None or target is None:
            continue
        scores.append(_bounded_score(abs(current - target) * 15.0))
    return _mean(scores)


def _humidity_score(rooms: tuple[Mapping[str, object], ...]) -> float | None:
    scores = []
    for room in rooms:
        current = _number(room.get("humidity"))
        target = _number(room.get("targetHumidity"))
        if current is None or target is None:
            continue
        scores.append(_bounded_score(abs(current - target) * 2.0))
    return _mean(scores)


def _co2_score(value: object) -> float | None:
    co2 = _number(value)
    if co2 is None or co2 < 0:
        return None
    if co2 <= 800:
        return 100.0
    return _bounded_score((co2 - 800.0) / 12.0)


def _coverage(
    rooms: tuple[Mapping[str, object], ...],
    *,
    target_key: str,
    value_key: str,
) -> float:
    expected = tuple(room for room in rooms if _number(room.get(target_key)) is not None)
    if not expected:
        return 0.0
    observed = sum(_number(room.get(value_key)) is not None for room in expected)
    return observed / len(expected)


def _status_label(score: int) -> str:
    if score >= 90:
        return "Отлично"
    if score >= 75:
        return "Хорошо"
    if score >= 60:
        return "Нормально"
    if score >= 40:
        return "Нужна настройка"
    return "Требует внимания"


def build_dashboard_comfort(
    rooms: Iterable[Mapping[str, object]],
    *,
    co2: object,
) -> dict[str, object]:
    """Return one client-ready score without moving policy into the client."""

    room_values = tuple(rooms)
    temperature = _temperature_score(room_values)
    humidity = _humidity_score(room_values)
    carbon_dioxide = _co2_score(co2)
    if temperature is None:
        return {
            "available": False,
            "score": None,
            "statusLabel": None,
            "dataQuality": "limited",
        }

    channels = (
        (temperature, _TEMPERATURE_WEIGHT),
        (humidity, _HUMIDITY_WEIGHT),
        (carbon_dioxide, _CO2_WEIGHT),
    )
    available_channels = tuple(
        (value, weight) for value, weight in channels if value is not None
    )
    weight_total = sum(weight for _, weight in available_channels)
    score = round(
        sum(value * weight for value, weight in available_channels) / weight_total
    )
    explicit_stale = any(
        str(room.get("status") or "").casefold() in {"stale", "устарело"}
        for room in room_values
    )
    complete = (
        humidity is not None
        and carbon_dioxide is not None
        and _coverage(
            room_values, target_key="targetTemp", value_key="temp"
        ) >= _MINIMUM_FRESH_COVERAGE
        and _coverage(
            room_values, target_key="targetHumidity", value_key="humidity"
        ) >= _MINIMUM_FRESH_COVERAGE
    )
    return {
        "available": True,
        "score": score,
        "statusLabel": _status_label(score),
        "dataQuality": "stale" if explicit_stale else ("fresh" if complete else "limited"),
    }
