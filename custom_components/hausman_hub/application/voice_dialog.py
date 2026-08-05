"""Framework-free dialog answers for the Yandex Station private skill.

Branches mirror the reference automation ``hausmanhub_yandex_dialog_conversation``
but read only the injected server-side snapshot. Unknown questions fall back
to the injected conversation callback; when that callback is unavailable the
handler must answer with an explicit static line, never with invented data.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Sequence


ROOM_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("гостиная", ("гостин",)),
    ("кухня", ("кухн",)),
    ("детская", ("детск",)),
    ("спальня", ("спальн",)),
    ("ванная", ("ванн",)),
    ("туалет", ("туалет",)),
    ("прихожая", ("прихож",)),
)

HELP_TEXT = (
    "Я могу дать сводку по дому, назвать температуру и влажность по комнатам, "
    "оценить качество воздуха, проверить протечки, открытые окна и двери, "
    "заряд батарей и общую безопасность. Что рассказать?"
)


def match_room(text: str) -> str | None:
    lowered = text.lower()
    for room, aliases in ROOM_ALIASES:
        if any(alias in lowered for alias in aliases):
            return room
    return None


def _room_named(rooms: Sequence[dict[str, Any]], name: str) -> dict[str, Any] | None:
    lowered = name.lower()
    for room in rooms:
        room_name = str(room.get("roomName") or "").lower()
        if room_name == lowered or room_name.startswith(lowered[:6]):
            return room
    return None


def _temp_text(value: float) -> str:
    text = f"{value:.1f}".replace(".", ",")
    return text[:-2] if text.endswith(",0") else text


def dialog_static_answer(
    text: str,
    *,
    rooms: Sequence[dict[str, Any]],
    co2_ppm: int | None,
    leaks: Sequence[str],
    openings: Sequence[str],
    hazards: Sequence[str],
    low_batteries: Sequence[str],
    greeting_speech: str | None,
) -> str | None:
    """Answer a known question from the snapshot, or ``None`` for fallback."""

    lowered = text.lower().strip()
    if not lowered:
        return None

    if "приветствие при возвращении домой" in lowered:
        return greeting_speech

    if "что ты умеешь" in lowered or "что можно спросить" in lowered or "помощ" in lowered:
        return HELP_TEXT

    if "протеч" in lowered or "вода на полу" in lowered:
        if leaks:
            return "Обнаружена протечка: " + ", ".join(leaks[:5]) + "."
        return "Протечек не обнаружено."

    if ("что" in lowered and "открыт" in lowered) or "двер" in lowered or "окн" in lowered:
        if openings:
            return "Сейчас открыто: " + ", ".join(openings[:5]) + "."
        return "Все окна и двери закрыты."

    if "батаре" in lowered or "заряд" in lowered:
        if low_batteries:
            return "Низкий заряд: " + ", ".join(low_batteries[:5]) + "."
        return "Все батареи в норме."

    if "безопасност" in lowered or "всё ли спокойно" in lowered or "все ли спокойно" in lowered:
        problems: list[str] = []
        if leaks:
            problems.append("протечка: " + ", ".join(leaks[:3]))
        if hazards:
            problems.append("опасность: " + ", ".join(hazards[:3]))
        if openings:
            problems.append("открыто: " + ", ".join(openings[:3]))
        if problems:
            return "Есть замечания: " + "; ".join(problems) + "."
        return "В доме всё спокойно."

    if (
        "co2" in lowered
        or "co₂" in lowered
        or "углекисл" in lowered
        or "качество воздуха" in lowered
    ):
        if co2_ppm is None:
            return "Датчик углекислого газа сейчас недоступен."
        from .voice_summary import air_quality_label

        return (
            f"Уровень CO₂ {co2_ppm} ppm. "
            f"Качество воздуха {air_quality_label(co2_ppm)}."
        )

    room = match_room(lowered)
    asks_climate = "климат" in lowered
    asks_temp = "температур" in lowered or asks_climate
    asks_humidity = "влажност" in lowered or asks_climate

    if room is not None:
        target = _room_named(rooms, room)
        if target is None:
            return "Для этой комнаты пока не найден доступный климатический датчик."
        temp = target.get("temperatureC")
        humidity = target.get("humidityPercent")
        if asks_temp and asks_humidity and temp is not None and humidity is not None:
            return (
                f"В комнате {target['roomName']} {_temp_text(float(temp))} градуса, "
                f"влажность {int(round(float(humidity)))} процентов."
            )
        if asks_temp and temp is not None:
            return f"Температура в комнате {target['roomName']} {_temp_text(float(temp))} градуса."
        if asks_humidity and humidity is not None:
            return f"Влажность в комнате {target['roomName']} {int(round(float(humidity)))} процентов."
        if asks_temp or asks_humidity:
            return "Для этой комнаты пока не найден доступный климатический датчик."

    if asks_temp or asks_humidity:
        field = "temperatureC" if asks_temp else "humidityPercent"
        label = "Температура" if asks_temp else "Влажность"
        values = [
            f"{room['roomName']} {_temp_text(float(room[field]))}"
            if asks_temp
            else f"{room['roomName']} {int(round(float(room[field])))} процентов"
            for room in rooms[:4]
            if room.get(field) is not None and room.get("roomName")
        ]
        if values:
            return f"{label} по дому: " + ", ".join(values) + "."

    if "что происходит дома" in lowered or "состояние дома" in lowered or "сводк" in lowered:
        return greeting_speech

    return None


async def dialog_answer(
    text: str,
    *,
    rooms: Sequence[dict[str, Any]],
    co2_ppm: int | None,
    leaks: Sequence[str],
    openings: Sequence[str],
    hazards: Sequence[str],
    low_batteries: Sequence[str],
    greeting_speech: str | None,
    conversation: Callable[[str], Awaitable[str | None]],
) -> str:
    """Answer from the snapshot, falling back to Home Assistant conversation."""

    static = dialog_static_answer(
        text,
        rooms=rooms,
        co2_ppm=co2_ppm,
        leaks=leaks,
        openings=openings,
        hazards=hazards,
        low_batteries=low_batteries,
        greeting_speech=greeting_speech,
    )
    if static is not None:
        return static
    try:
        answer = await conversation(text)
    except Exception:
        answer = None
    if answer:
        return answer
    return "Не удалось получить ответ. Спросите сводку по дому или климат в комнате."
