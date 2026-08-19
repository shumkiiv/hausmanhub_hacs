"""Bounded, unit-normalized weather read model for tablet clients."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(result, 3)


def _temperature_c(value: object, unit: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    normalized = str(unit or "°C").strip().casefold()
    if normalized in {"°f", "f", "fahrenheit"}:
        number = (number - 32.0) * 5.0 / 9.0
    elif normalized in {"k", "kelvin"}:
        number -= 273.15
    return round(number, 1)


def _wind_mps(value: object, unit: object) -> float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    normalized = str(unit or "m/s").strip().casefold().replace(" ", "")
    if normalized in {"km/h", "kmh", "kph"}:
        number /= 3.6
    elif normalized in {"mph", "mi/h"}:
        number *= 0.44704
    elif normalized in {"kn", "kt", "knot", "knots"}:
        number *= 0.514444
    return round(number, 2)


def _pressure_hpa(value: object, unit: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    normalized = str(unit or "hPa").strip().casefold()
    if normalized == "pa":
        number /= 100.0
    elif normalized in {"inhg", "in hg"}:
        number *= 33.8639
    elif normalized in {"mmhg", "mm hg"}:
        number *= 1.33322
    return round(number, 1)


def _visibility_km(value: object, unit: object) -> float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    normalized = str(unit or "km").strip().casefold()
    if normalized in {"m", "meter", "meters"}:
        number /= 1000.0
    elif normalized in {"mi", "mile", "miles"}:
        number *= 1.609344
    return round(number, 2)


def _precipitation_mm(value: object, unit: object) -> float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    if str(unit or "mm").strip().casefold() in {"in", "inch", "inches"}:
        number *= 25.4
    return round(number, 2)


def _bounded_percent(value: object) -> float | None:
    number = _number(value)
    return None if number is None else round(min(100.0, max(0.0, number)), 1)


def _forecast_item(
    item: Mapping[str, object], units: Mapping[str, object]
) -> dict[str, object] | None:
    timestamp = item.get("datetime")
    if not isinstance(timestamp, str) or not timestamp:
        return None
    return {
        "at": timestamp,
        "condition": (
            item.get("condition")
            if isinstance(item.get("condition"), str)
            else None
        ),
        "temperatureC": _temperature_c(
            item.get("temperature"), units.get("temperature_unit")
        ),
        "lowTemperatureC": _temperature_c(
            item.get("templow"), units.get("temperature_unit")
        ),
        "feelsLikeC": _temperature_c(
            item.get("apparent_temperature"), units.get("temperature_unit")
        ),
        "humidityPercent": _bounded_percent(item.get("humidity")),
        "precipitationProbabilityPercent": _bounded_percent(item.get("precipitation_probability")),
        "precipitationMm": _precipitation_mm(
            item.get("precipitation"), units.get("precipitation_unit")
        ),
        "windSpeedMps": _wind_mps(
            item.get("wind_speed"), units.get("wind_speed_unit")
        ),
        "windGustMps": _wind_mps(
            item.get("wind_gust_speed"), units.get("wind_speed_unit")
        ),
        "windBearing": (
            item.get("wind_bearing")
            if isinstance(item.get("wind_bearing"), (int, float, str))
            and not isinstance(item.get("wind_bearing"), bool)
            else None
        ),
        "pressureHpa": _pressure_hpa(item.get("pressure"), units.get("pressure_unit")),
        "cloudCoveragePercent": _bounded_percent(item.get("cloud_coverage")),
        "uvIndex": _number(item.get("uv_index")),
    }


def _forecast(
    values: object, units: Mapping[str, object], limit: int
) -> list[dict[str, object]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    result: list[dict[str, object]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        item = _forecast_item(value, units)
        if item is not None:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def _climate_load(
    *,
    temperature_c: float | None,
    target_c: float | None,
    wind_mps: float | None,
    humidity: float | None,
    uv_index: float | None,
    precipitation_probability: float | None,
) -> dict[str, object]:
    if temperature_c is None or target_c is None:
        return {"level": "unknown", "score": None, "factors": []}
    thermal = min(60.0, abs(temperature_c - target_c) * 4.0)
    wind = min(25.0, max(0.0, wind_mps or 0.0) * 4.0)
    humidity_load = 0.0
    if humidity is not None and (humidity < 30.0 or humidity > 70.0):
        humidity_load = min(
            8.0, min(abs(humidity - 30.0), abs(humidity - 70.0)) * 0.4
        )
    sun = min(5.0, max(0.0, (uv_index or 0.0) - 3.0))
    precipitation = min(5.0, max(0.0, precipitation_probability or 0.0) / 20.0)
    score = round(
        min(100.0, thermal + wind + humidity_load + sun + precipitation), 1
    )
    factors: list[str] = []
    if thermal >= 8:
        factors.append("temperature")
    if wind >= 6:
        factors.append("wind")
    if humidity_load > 0:
        factors.append("humidity")
    if sun > 0:
        factors.append("sun")
    if precipitation >= 2:
        factors.append("precipitation")
    level = "negligible" if score < 15 else "weak" if score < 40 else "strong"
    return {"level": level, "score": score, "factors": factors}


def build_weather_read_model(
    *,
    condition: str | None,
    attributes: Mapping[str, object] | None,
    daily: object = None,
    hourly: object = None,
    target_temperature_c: float | None = None,
    updated_at: str | None = None,
    outdoor_sensor_value: object = None,
    outdoor_sensor_unit: object = None,
    outdoor_sensor_entity_id: str | None = None,
    outdoor_sensor_updated_at: int | None = None,
    outdoor_sensor_available: bool = False,
) -> dict[str, object]:
    """Return one stable weather payload without provider-specific attributes."""

    attrs: Mapping[str, object] = attributes or {}
    temperature = _temperature_c(attrs.get("temperature"), attrs.get("temperature_unit"))
    wind = _wind_mps(attrs.get("wind_speed"), attrs.get("wind_speed_unit"))
    humidity = _bounded_percent(attrs.get("humidity"))
    daily_values = _forecast(daily, attrs, 10)
    hourly_values = _forecast(hourly, attrs, 24)
    first_forecast = daily_values[0] if daily_values else {}
    sensor_available = outdoor_sensor_available is True
    return {
        "available": bool(condition) or temperature is not None,
        "condition": condition,
        "temperatureC": temperature,
        "feelsLikeC": _temperature_c(
            attrs.get("apparent_temperature"), attrs.get("temperature_unit")
        ),
        "humidityPercent": humidity,
        "pressureHpa": _pressure_hpa(attrs.get("pressure"), attrs.get("pressure_unit")),
        "visibilityKm": _visibility_km(attrs.get("visibility"), attrs.get("visibility_unit")),
        "windSpeedMps": wind,
        "windGustMps": _wind_mps(
            attrs.get("wind_gust_speed"), attrs.get("wind_speed_unit")
        ),
        "windBearing": (
            attrs.get("wind_bearing")
            if isinstance(attrs.get("wind_bearing"), (int, float, str))
            and not isinstance(attrs.get("wind_bearing"), bool)
            else None
        ),
        "cloudCoveragePercent": _bounded_percent(attrs.get("cloud_coverage")),
        "uvIndex": _number(attrs.get("uv_index")),
        "updatedAt": updated_at,
        # A missing or silent physical sensor stays null/false, never a
        # synthetic zero or an empty string (contracts rule).
        "outdoorSensorTemperatureC": (
            _temperature_c(outdoor_sensor_value, outdoor_sensor_unit)
            if sensor_available
            else None
        ),
        "outdoorSensorEntityId": (
            outdoor_sensor_entity_id
            if sensor_available
            and isinstance(outdoor_sensor_entity_id, str)
            and outdoor_sensor_entity_id
            else None
        ),
        "outdoorSensorUpdatedAt": (
            outdoor_sensor_updated_at
            if sensor_available
            and type(outdoor_sensor_updated_at) is int
            and outdoor_sensor_updated_at >= 0
            else None
        ),
        "outdoorSensorAvailable": sensor_available,
        "climateLoad": _climate_load(
            temperature_c=temperature,
            target_c=target_temperature_c,
            wind_mps=wind,
            humidity=humidity,
            uv_index=_number(attrs.get("uv_index")),
            precipitation_probability=(
                first_forecast.get("precipitationProbabilityPercent")
                if isinstance(first_forecast, Mapping)
                else None
            ),
        ),
        "dailyForecast": daily_values,
        "hourlyForecast": hourly_values,
    }


def unavailable_weather_read_model(
    *,
    outdoor_sensor_value: object = None,
    outdoor_sensor_unit: object = None,
    outdoor_sensor_entity_id: str | None = None,
    outdoor_sensor_updated_at: int | None = None,
    outdoor_sensor_available: bool = False,
) -> dict[str, object]:
    """Return the complete empty model instead of misleading defaults.

    A configured physical outdoor sensor still reports through the same four
    fields even when the weather provider entity itself is missing.
    """

    return build_weather_read_model(
        condition=None,
        attributes=None,
        outdoor_sensor_value=outdoor_sensor_value,
        outdoor_sensor_unit=outdoor_sensor_unit,
        outdoor_sensor_entity_id=outdoor_sensor_entity_id,
        outdoor_sensor_updated_at=outdoor_sensor_updated_at,
        outdoor_sensor_available=outdoor_sensor_available,
    )
