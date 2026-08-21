"""Framework-free builder of the spoken home summary.

Text blocks follow the reference behavior of the legacy voice automations,
but data comes only from the injected server-side snapshot — never from
hard-coded entity ids.
"""

from __future__ import annotations

import re
from typing import Any, Sequence


MAX_ROOMS_IN_SUMMARY = 3

_WEATHER_CONDITIONS_RU = {
    "clear-night": "ясно",
    "sunny": "ясно",
    "partlycloudy": "переменная облачность",
    "cloudy": "облачно",
    "overcast": "пасмурно",
    "rain": "дождь",
    "pouring": "ливень",
    "lightning": "гроза",
    "lightning-rainy": "гроза с дождём",
    "snow": "снег",
    "snowy-rainy": "мокрый снег",
    "fog": "туман",
    "hail": "град",
    "windy": "ветер",
    "windy-variant": "ветер",
    "exceptional": "непогода",
}


def air_quality_label(co2_ppm: int | None) -> str:
    if co2_ppm is None or co2_ppm <= 0:
        return "не определено"
    if co2_ppm < 800:
        return "хорошее"
    if co2_ppm < 1200:
        return "допустимое"
    return "требует проветривания"


def format_degrees(value: float) -> str:
    text = f"{value:.1f}".replace(".", ",")
    if text.endswith(",0"):
        text = text[:-2]
    return text


def _degrees_word(value: float) -> str:
    if value != int(value):
        return "градуса"
    rest = abs(int(value)) % 100
    if 11 <= rest <= 14:
        return "градусов"
    digit = rest % 10
    if digit == 1:
        return "градус"
    if 2 <= digit <= 4:
        return "градуса"
    return "градусов"


def format_outdoor_degrees(value: float) -> str:
    """Human-friendly outdoor temperature: 'минус 14 градусов'."""

    prefix = "минус " if value < 0 else ""
    return f"{prefix}{format_degrees(abs(value))} {_degrees_word(round(value, 1))}"


def build_greeting_speech(
    settings: dict[str, Any],
    *,
    rooms: Sequence[dict[str, Any]],
    co2_ppm: int | None,
    leaks: Sequence[str],
    openings: Sequence[str],
    hazards: Sequence[str],
    outdoor: dict[str, Any] | None = None,
    low_batteries: Sequence[str] = (),
    include_greeting: bool = True,
    include_follow_up: bool = True,
) -> str | None:
    """Compose the exact spoken text from selected blocks.

    Returns ``None`` when none of the requested blocks can produce data —
    callers map that to the ``summary_unavailable`` receipt code instead of
    speaking invented values.
    """

    parts: list[str] = []
    if include_greeting:
        parts.append(str(settings["greetingText"]).rstrip("."))
    style = settings.get("summaryStyle", "human")
    for item in settings["summaryItems"]:
        block = _block(
            item,
            style=style,
            rooms=rooms,
            co2_ppm=co2_ppm,
            leaks=leaks,
            openings=openings,
            hazards=hazards,
            outdoor=outdoor,
            low_batteries=low_batteries,
        )
        if block:
            parts.append(block)
    if not parts or (len(parts) == 1 and include_greeting):
        return None
    if include_follow_up and settings.get("followUpEnabled"):
        parts.append(str(settings["followUpText"]).rstrip("?"))
    return ". ".join(parts) + "."


def _block(
    item: str,
    *,
    style: str,
    rooms: Sequence[dict[str, Any]],
    co2_ppm: int | None,
    leaks: Sequence[str],
    openings: Sequence[str],
    hazards: Sequence[str],
    outdoor: dict[str, Any] | None,
    low_batteries: Sequence[str],
) -> str | None:
    if item == "temperature":
        if style == "human":
            return _temperature_human(rooms)
        values = [
            f"{room['roomName']} {format_degrees(float(room['temperatureC']))} градуса"
            for room in rooms[:MAX_ROOMS_IN_SUMMARY]
            if room.get("temperatureC") is not None and room.get("roomName")
        ]
        return ("В доме " + ", ".join(values)) if values else None
    if item == "humidity":
        if style == "human":
            return _humidity_human(rooms)
        values = [
            f"{room['roomName']} {int(round(float(room['humidityPercent'])))} процентов"
            for room in rooms[:MAX_ROOMS_IN_SUMMARY]
            if room.get("humidityPercent") is not None and room.get("roomName")
        ]
        return ("Влажность: " + ", ".join(values)) if values else None
    if item == "air_quality":
        if co2_ppm is None:
            return None
        if style == "human":
            if co2_ppm < 800:
                return "Воздух свежий"
            if co2_ppm < 1200:
                return "Воздух допустимый"
            return f"Стоит проветрить, углекислый газ {co2_ppm} ppm"
        return (
            f"Углекислый газ {co2_ppm} ppm, качество воздуха "
            f"{air_quality_label(co2_ppm)}"
        )
    if item == "security":
        problems: list[str] = []
        if leaks:
            problems.append("протечка: " + ", ".join(leaks[:3]))
        if hazards:
            problems.append("опасность: " + ", ".join(hazards[:3]))
        if openings:
            problems.append("открыто: " + ", ".join(openings[:3]))
        if problems:
            return "Внимание, " + "; ".join(problems)
        return "Безопасность в порядке, протечек и открытых проёмов нет"
    if item == "outdoor":
        return _outdoor_block(outdoor)
    if item == "low_battery":
        if low_batteries:
            names = ", ".join(low_batteries[:3])
            if style == "human":
                return "Пора заменить батареи: " + names
            return "Низкий заряд батарей: " + names
        return "Батареи устройств в норме"
    return None


def _temperature_human(rooms: Sequence[dict[str, Any]]) -> str | None:
    values = [
        (str(room["roomName"]), float(room["temperatureC"]))
        for room in rooms
        if room.get("temperatureC") is not None and room.get("roomName")
    ]
    if not values:
        return None
    mean = sum(value for _, value in values) / len(values)
    if mean >= 24:
        phrase = "В доме тепло"
    elif mean >= 21:
        phrase = "В доме комфортно"
    elif mean >= 18:
        phrase = "В доме прохладно"
    else:
        phrase = "В доме холодно"
    extremes = [
        f"в {name} {format_degrees(value)} градуса"
        for name, value in values
        if value < 19 or value > 26
    ][:2]
    if extremes:
        phrase += ", " + ", ".join(extremes)
    return phrase


def _humidity_human(rooms: Sequence[dict[str, Any]]) -> str | None:
    values = [
        float(room["humidityPercent"])
        for room in rooms
        if room.get("humidityPercent") is not None
    ]
    if not values:
        return None
    mean = sum(values) / len(values)
    if mean < 40:
        return "Воздух сухой"
    if mean > 60:
        return "В доме влажно"
    return "Влажность в норме"


def _outdoor_block(outdoor: dict[str, Any] | None) -> str | None:
    if not outdoor:
        return None
    temperature = outdoor.get("temperatureC")
    condition = outdoor.get("condition")
    parts: list[str] = []
    if temperature is not None:
        parts.append(format_outdoor_degrees(float(temperature)))
    condition_ru = _WEATHER_CONDITIONS_RU.get(str(condition))
    if condition_ru:
        parts.append(condition_ru)
    return ("На улице " + ", ".join(parts)) if parts else None


def split_speech(text: str, limit: int) -> list[str]:
    """Split long speech into station-sized chunks on sentence boundaries.

    Yandex Station clips overly long text-to-speech payloads; the greeting
    therefore goes out as a sequence of short phrases instead of one long
    string. A single sentence longer than ``limit`` falls back to splitting
    on spaces so no content is ever dropped.
    """

    sentences = [piece for piece in re.split(r"(?<=[.!?])\s+", text) if piece]
    if not sentences:
        return [text]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_sentence(sentence, limit))
            continue
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _split_long_sentence(sentence: str, limit: int) -> list[str]:
    words = sentence.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
