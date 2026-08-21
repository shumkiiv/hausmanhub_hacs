"""Home Assistant gateway for the voice greeting service.

Thin adapter: station discovery, text-to-speech via the AlexxIT
YandexStation media players, room climate from the server-side HA state,
security rollups, and the conversation fallback. It never sends commands to
anything except the selected station media player and the conversation API.
"""

from __future__ import annotations

from typing import Any

from .application.voice_greeting import DEFAULT_AWAY_ENTITY_ID


_STATION_ID_MARKER = "yandex_station"
YANDEX_INTENT_RESPONSE_EVENT = "yandex_intent_response"
_UNAVAILABLE = {"unknown", "unavailable", None}
_TEMPERATURE = "temperature"
_HUMIDITY = "humidity"
_CO2 = "carbon_dioxide"
_LEAK_CLASSES = frozenset({"moisture"})
_OPENING_CLASSES = frozenset({"door", "window", "garage_door", "opening"})
_HAZARD_CLASSES = frozenset({"smoke", "gas"})
_LOW_BATTERY_PERCENT = 20


class HomeAssistantVoiceGateway:
    def __init__(self, hass: Any, *, away_entity_id: str = DEFAULT_AWAY_ENTITY_ID) -> None:
        self._hass = hass
        self._away_entity_id = away_entity_id

    @property
    def away_entity_id(self) -> str:
        return self._away_entity_id

    def _domain_states(self, domain: str) -> list[Any]:
        prefix = f"{domain}."
        return [
            state
            for state in self._hass.states.async_all()
            if state.entity_id.startswith(prefix)
        ]

    async def async_stations(self) -> list[dict[str, Any]]:
        stations: list[dict[str, Any]] = []
        for state in self._domain_states("media_player"):
            if _STATION_ID_MARKER not in state.entity_id:
                continue
            name = state.attributes.get("friendly_name")
            room_id, room_name = self._room_of(state.entity_id, state.attributes)
            available = state.state not in _UNAVAILABLE
            stations.append(
                {
                    "entityId": state.entity_id,
                    "name": name if isinstance(name, str) and name else state.entity_id,
                    "roomId": room_id,
                    "roomName": room_name,
                    "available": available,
                    "localDialogSupported": available,
                }
            )
        stations.sort(key=lambda item: item["entityId"])
        return stations

    async def async_say_text(self, entity_id: str, text: str) -> None:
        await self._hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": entity_id,
                "media_content_id": text,
                "media_content_type": "text",
            },
            blocking=True,
        )

    async def async_away_state(self) -> str | None:
        state = self._hass.states.get(self._away_entity_id)
        return getattr(state, "state", None)

    async def async_home_climate(self) -> dict[str, Any]:
        rooms: dict[str, dict[str, Any]] = {}
        co2_ppm: int | None = None
        for state in self._domain_states("sensor"):
            device_class = state.attributes.get("device_class")
            value = _float_state(state)
            if device_class == _CO2:
                if co2_ppm is None and value is not None:
                    co2_ppm = int(round(value))
                continue
            if device_class not in {_TEMPERATURE, _HUMIDITY} or value is None:
                continue
            _, room_name = self._room_of(state.entity_id, state.attributes)
            if not room_name:
                continue
            room = rooms.setdefault(
                room_name, {"roomName": room_name, "temperatureC": None, "humidityPercent": None}
            )
            key = "temperatureC" if device_class == _TEMPERATURE else "humidityPercent"
            if room[key] is None:
                room[key] = round(value, 1)
        ordered = [rooms[name] for name in sorted(rooms)]
        return {"rooms": ordered, "co2Ppm": co2_ppm}

    async def async_security_state(self) -> dict[str, list[str]]:
        leaks: list[str] = []
        openings: list[str] = []
        hazards: list[str] = []
        for state in self._domain_states("binary_sensor"):
            if state.state != "on":
                continue
            device_class = state.attributes.get("device_class")
            name = _friendly(state)
            if device_class in _LEAK_CLASSES:
                leaks.append(name)
            elif device_class in _OPENING_CLASSES:
                openings.append(name)
            elif device_class in _HAZARD_CLASSES:
                hazards.append(name)
        low_batteries: list[str] = []
        for state in self._domain_states("sensor"):
            if state.attributes.get("device_class") != "battery":
                continue
            value = _float_state(state)
            if value is not None and value < _LOW_BATTERY_PERCENT:
                low_batteries.append(f"{_friendly(state)} {int(round(value))} процентов")
        return {
            "leaks": sorted(leaks),
            "openings": sorted(openings),
            "hazards": sorted(hazards),
            "lowBatteries": sorted(low_batteries),
        }

    async def async_outdoor_weather(self) -> dict[str, Any] | None:
        weather_states = sorted(
            self._domain_states("weather"), key=lambda state: state.entity_id
        )
        for state in weather_states:
            if state.state in _UNAVAILABLE:
                continue
            temperature = state.attributes.get("temperature")
            try:
                temperature_c = float(temperature) if temperature is not None else None
            except (TypeError, ValueError):
                temperature_c = None
            return {"temperatureC": temperature_c, "condition": state.state}
        return None

    async def async_conversation(self, text: str) -> str | None:
        response = await self._hass.services.async_call(
            "conversation",
            "process",
            {"text": text, "language": "ru"},
            blocking=True,
            return_response=True,
        )
        try:
            return response["response"]["speech"]["plain"]["speech"]
        except (KeyError, TypeError):
            return None

    async def async_publish_dialog_answer(self, text: str) -> None:
        bus = getattr(self._hass, "bus", None)
        if bus is None:
            return
        bus.async_fire(
            YANDEX_INTENT_RESPONSE_EVENT,
            {"text": text, "end_session": False},
        )

    def _room_of(
        self, entity_id: str, attributes: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        area_name = attributes.get("area_name")
        if isinstance(area_name, str) and area_name:
            return area_name, area_name
        try:
            from homeassistant.helpers import (
                area_registry,
                device_registry,
                entity_registry,
            )

            entities = entity_registry.async_get(self._hass)
            entity = entities.async_get(entity_id)
            area_id = getattr(entity, "area_id", None)
            device_id = getattr(entity, "device_id", None)
            if area_id is None and device_id is not None:
                device_entry = device_registry.async_get(self._hass).async_get(device_id)
                area_id = getattr(device_entry, "area_id", None)
            if area_id is None:
                return None, None
            area = area_registry.async_get(self._hass).async_get_area(area_id)
            name = getattr(area, "name", None)
            return (area_id, name) if isinstance(name, str) and name else (area_id, None)
        except (AttributeError, ImportError, KeyError, TypeError):
            return None, None


def _float_state(state: Any) -> float | None:
    if state.state in _UNAVAILABLE:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _friendly(state: Any) -> str:
    name = state.attributes.get("friendly_name")
    return name if isinstance(name, str) and name else state.entity_id
