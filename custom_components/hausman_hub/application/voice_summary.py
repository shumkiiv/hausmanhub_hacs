"""Framework-free builder of the spoken home summary.

Text blocks follow the reference behavior of the legacy voice automations,
but data comes only from the injected server-side snapshot — never from
hard-coded entity ids.
"""

from __future__ import annotations

from typing import Any, Sequence


MAX_ROOMS_IN_SUMMARY = 3


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


def build_greeting_speech(
    settings: dict[str, Any],
    *,
    rooms: Sequence[dict[str, Any]],
    co2_ppm: int | None,
    leaks: Sequence[str],
    openings: Sequence[str],
    hazards: Sequence[str],
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
    for item in settings["summaryItems"]:
        block = _block(
            item,
            rooms=rooms,
            co2_ppm=co2_ppm,
            leaks=leaks,
            openings=openings,
            hazards=hazards,
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
    rooms: Sequence[dict[str, Any]],
    co2_ppm: int | None,
    leaks: Sequence[str],
    openings: Sequence[str],
    hazards: Sequence[str],
) -> str | None:
    if item == "temperature":
        values = [
            f"{room['roomName']} {format_degrees(float(room['temperatureC']))} градуса"
            for room in rooms[:MAX_ROOMS_IN_SUMMARY]
            if room.get("temperatureC") is not None and room.get("roomName")
        ]
        return ("В доме " + ", ".join(values)) if values else None
    if item == "humidity":
        values = [
            f"{room['roomName']} {int(round(float(room['humidityPercent'])))} процентов"
            for room in rooms[:MAX_ROOMS_IN_SUMMARY]
            if room.get("humidityPercent") is not None and room.get("roomName")
        ]
        return ("Влажность: " + ", ".join(values)) if values else None
    if item == "air_quality":
        if co2_ppm is None:
            return None
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
    return None
