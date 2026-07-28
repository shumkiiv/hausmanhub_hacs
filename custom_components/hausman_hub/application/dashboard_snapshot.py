"""Pure projection for the local HausmanHub tablet dashboard."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
from typing import Any


DASHBOARD_CONTRACT_NAME = "universal-home"
DASHBOARD_CONTRACT_VERSION = 1

_PHYSICAL_DOMAINS = frozenset(
    {
        "camera",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "light",
        "lock",
        "media_player",
        "switch",
        "vacuum",
    }
)
_PRIMARY_DOMAIN_ORDER = {
    domain: index
    for index, domain in enumerate(
        (
            "climate",
            "humidifier",
            "light",
            "switch",
            "fan",
            "cover",
            "lock",
            "vacuum",
            "media_player",
            "camera",
            "binary_sensor",
            "sensor",
        )
    )
}
_ALARM_DEVICE_CLASSES = frozenset(
    {"moisture", "smoke", "gas", "carbon_monoxide", "safety", "problem"}
)
_ACTIVE_STATES = frozenset(
    {"on", "open", "opening", "playing", "cleaning", "heat", "cool", "dry", "fan_only"}
)
_UNAVAILABLE_STATES = frozenset({"unknown", "unavailable"})
_ALLOWLISTED_ATTRIBUTES = (
    "brightness",
    "current_position",
    "current_temperature",
    "fan_mode",
    "humidity",
    "hvac_action",
    "percentage",
    "temperature",
)


@dataclass(frozen=True, slots=True)
class DashboardArea:
    """One HA area reduced to fields safe for the tablet."""

    area_id: str
    name: str
    icon: str | None = None


@dataclass(frozen=True, slots=True)
class DashboardDevice:
    """One physical HA device registry entry."""

    device_id: str
    name: str
    area_id: str | None = None
    model: str | None = None
    manufacturer: str | None = None


@dataclass(frozen=True, slots=True)
class DashboardEntity:
    """One current entity state with an allowlisted metadata boundary."""

    entity_id: str
    domain: str
    state: str
    name: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    device_id: str | None = None
    area_id: str | None = None


@dataclass(frozen=True, slots=True)
class DashboardScenario:
    """Saved scenario presentation used by the dashboard."""

    scenario_id: str
    title: str
    group: str | None = None
    description: str | None = None
    icon: str | None = None
    requires_confirmation: bool = False
    favorite: bool = False
    danger: bool = False


def _opaque_id(prefix: str, source_id: str) -> str:
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 1) if present else None


def _device_class(entity: DashboardEntity) -> str | None:
    value = entity.attributes.get("device_class")
    return value if isinstance(value, str) and value else None


def _state_label(entity: DashboardEntity) -> str:
    labels = {
        "on": "включено",
        "off": "выключено",
        "open": "открыто",
        "closed": "закрыто",
        "playing": "воспроизведение",
        "paused": "пауза",
        "heat": "обогрев",
        "cool": "охлаждение",
        "dry": "осушение",
        "fan_only": "вентиляция",
        "idle": "ожидание",
        "unavailable": "нет связи",
        "unknown": "состояние неизвестно",
    }
    return labels.get(entity.state, entity.state)


def _detail_label(entity: DashboardEntity) -> str:
    device_class = _device_class(entity)
    labels = {
        "temperature": "Температура",
        "humidity": "Влажность",
        "battery": "Заряд",
        "carbon_dioxide": "CO₂",
        "moisture": "Протечка",
        "smoke": "Дым",
        "gas": "Газ",
    }
    if device_class in labels:
        return labels[device_class]
    domains = {
        "climate": "Климат",
        "light": "Освещение",
        "switch": "Выключатель",
        "fan": "Вентиляция",
        "cover": "Шторы",
        "media_player": "Медиа",
        "humidifier": "Увлажнение",
        "lock": "Замок",
        "vacuum": "Уборка",
        "camera": "Камера",
    }
    return domains.get(entity.domain, entity.name)


def _detail_value(entity: DashboardEntity) -> str:
    unit = entity.attributes.get("unit_of_measurement")
    suffix = f" {unit}" if isinstance(unit, str) and unit else ""
    return f"{entity.state}{suffix}" if suffix else _state_label(entity)


def _category(domain: str, entity: DashboardEntity) -> str:
    if domain in {"climate", "fan", "humidifier"}:
        return "climate"
    if domain in {"camera", "lock"}:
        return "security"
    if domain == "media_player":
        return "media"
    if domain == "light":
        return "lighting"
    if domain == "cover":
        return "cover"
    if domain == "vacuum":
        return "appliance"
    if domain == "switch":
        return "switch"
    device_class = _device_class(entity)
    return device_class or domain


def _primary_value(entity: DashboardEntity) -> str | None:
    if entity.domain == "climate":
        value = _number(entity.attributes.get("temperature"))
        return f"{value:g} °C" if value is not None else None
    if entity.domain == "humidifier":
        value = _number(entity.attributes.get("humidity"))
        return f"{value:g}%" if value is not None else None
    if entity.domain == "cover":
        value = _number(entity.attributes.get("current_position"))
        return f"{value:g}%" if value is not None else None
    if entity.domain == "fan":
        value = _number(entity.attributes.get("percentage"))
        return f"{value:g}%" if value is not None else None
    return None


def _safe_attributes(entity: DashboardEntity) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in _ALLOWLISTED_ATTRIBUTES:
        value = entity.attributes.get(key)
        if isinstance(value, (str, int, float, bool)) and not (
            isinstance(value, str) and len(value) > 120
        ):
            result[key] = value
    return result


def _sensor_value(
    entities: Iterable[DashboardEntity], device_class: str
) -> float | None:
    return next(
        (
            value
            for entity in entities
            if _device_class(entity) == device_class
            for value in [_number(entity.state)]
            if value is not None
        ),
        None,
    )


def _room_climate(entities: Iterable[DashboardEntity]) -> DashboardEntity | None:
    return next((entity for entity in entities if entity.domain == "climate"), None)


def _weather_summary(entities: Iterable[DashboardEntity]) -> dict[str, object]:
    weather = next((entity for entity in entities if entity.domain == "weather"), None)
    if weather is None:
        return {}
    attrs = weather.attributes
    return {
        "outdoorTemp": _number(attrs.get("temperature")),
        "weatherCondition": weather.state,
        "weatherHumidity": _number(attrs.get("humidity")),
        "weatherFeelsLike": _number(attrs.get("apparent_temperature")),
        "weatherWindSpeed": _number(attrs.get("wind_speed")),
        "weatherWindSpeedUnit": attrs.get("wind_speed_unit")
        if isinstance(attrs.get("wind_speed_unit"), str)
        else None,
        "weatherIsDay": attrs.get("is_daytime")
        if isinstance(attrs.get("is_daytime"), bool)
        else None,
    }


def build_dashboard_snapshot(
    *,
    areas: Iterable[DashboardArea],
    devices: Iterable[DashboardDevice],
    entities: Iterable[DashboardEntity],
    scenarios: Iterable[DashboardScenario] = (),
    generated_at_ms: int,
    local_iso: str,
    home_name: str = "Дом",
    state_revision: int | None = None,
) -> dict[str, object]:
    """Project one immutable, read-only universal dashboard snapshot."""

    area_by_id = {area.area_id: area for area in areas}
    device_by_id = {device.device_id: device for device in devices}
    all_entities = tuple(entities)
    grouped: dict[str, list[DashboardEntity]] = defaultdict(list)
    group_sources: dict[str, str | None] = {}

    for entity in all_entities:
        if entity.device_id and entity.device_id in device_by_id:
            group_key = f"device:{entity.device_id}"
            group_sources[group_key] = entity.device_id
            grouped[group_key].append(entity)
        elif entity.domain in _PHYSICAL_DOMAINS:
            group_key = f"entity:{entity.entity_id}"
            group_sources[group_key] = None
            grouped[group_key].append(entity)

    device_payloads: list[dict[str, object]] = []
    source_to_public: dict[str, str] = {}
    for group_key, members in grouped.items():
        members.sort(
            key=lambda item: (
                _PRIMARY_DOMAIN_ORDER.get(item.domain, 999), item.entity_id
            )
        )
        primary = members[0]
        source_device_id = group_sources[group_key]
        registry_device = (
            device_by_id.get(source_device_id) if source_device_id is not None else None
        )
        public_id = _opaque_id("device", group_key)
        if source_device_id is not None:
            source_to_public[source_device_id] = public_id
        area_id = primary.area_id or (
            registry_device.area_id if registry_device is not None else None
        )
        area = area_by_id.get(area_id) if area_id is not None else None
        unavailable = all(member.state in _UNAVAILABLE_STATES for member in members)
        active = any(member.state in _ACTIVE_STATES for member in members)
        details = [
            {
                "label": _detail_label(member),
                "value": _detail_value(member),
                "entityId": member.entity_id,
                "domain": member.domain,
                "state": member.state,
            }
            for member in members
        ]
        name = registry_device.name if registry_device is not None else primary.name
        device_payloads.append(
            {
                "id": public_id,
                "entityId": primary.entity_id,
                "physicalId": public_id,
                "name": name,
                "roomId": area.area_id if area is not None else None,
                "roomName": area.name if area is not None else None,
                "domain": primary.domain,
                "category": _category(primary.domain, primary),
                "icon": primary.attributes.get("icon")
                if isinstance(primary.attributes.get("icon"), str)
                else None,
                "state": primary.state,
                "stateLabel": _state_label(primary),
                "active": active,
                "unavailable": unavailable,
                "tone": "bad" if unavailable else ("good" if active else "neutral"),
                "primaryValue": _primary_value(primary),
                "source": "home-assistant",
                "model": registry_device.model if registry_device is not None else None,
                "manufacturer": registry_device.manufacturer
                if registry_device is not None
                else None,
                "attributes": _safe_attributes(primary),
                "actions": [],
                "details": details,
            }
        )

    room_payloads: list[dict[str, object]] = []
    for area in area_by_id.values():
        room_entities = tuple(
            entity
            for entity in all_entities
            if entity.area_id == area.area_id
            or (
                entity.device_id in device_by_id
                and device_by_id[entity.device_id].area_id == area.area_id
            )
        )
        climate = _room_climate(room_entities)
        climate_state = climate.state if climate is not None else None
        target = (
            _number(climate.attributes.get("temperature"))
            if climate is not None
            else None
        )
        temperature = _sensor_value(room_entities, "temperature")
        if temperature is None and climate is not None:
            temperature = _number(climate.attributes.get("current_temperature"))
        humidity = _sensor_value(room_entities, "humidity")
        room_device_ids = sorted(
            {
                source_to_public[entity.device_id]
                for entity in room_entities
                if entity.device_id in source_to_public
            }
            | {
                _opaque_id("device", f"entity:{entity.entity_id}")
                for entity in room_entities
                if entity.device_id is None and entity.domain in _PHYSICAL_DOMAINS
            }
        )
        room_payloads.append(
            {
                "id": area.area_id,
                "name": area.name,
                "icon": area.icon,
                "temp": temperature,
                "humidity": humidity,
                "targetTemp": target,
                "minTargetTemp": target,
                "manualTarget": False,
                "manualControl": False,
                "targetStrategy": "normal" if climate is not None else None,
                "climateState": climate_state,
                "climateRunning": climate_state in _ACTIVE_STATES,
                "hasClimateControl": climate is not None,
                "deviceIds": room_device_ids,
                "status": _state_label(climate) if climate is not None else None,
            }
        )

    alarms: list[dict[str, object]] = []
    for entity in all_entities:
        device_class = _device_class(entity)
        if entity.domain != "binary_sensor" or device_class not in _ALARM_DEVICE_CLASSES:
            continue
        area_id = entity.area_id
        if area_id is None and entity.device_id in device_by_id:
            area_id = device_by_id[entity.device_id].area_id
        active = entity.state == "on"
        alarms.append(
            {
                "id": _opaque_id("alarm", entity.entity_id),
                "title": entity.name,
                "message": _detail_label(entity),
                "level": "bad" if active else "info",
                "active": active,
                "roomId": area_id,
                "entityId": entity.entity_id,
                "ts": None,
            }
        )

    room_temperatures = [_number(room.get("temp")) for room in room_payloads]
    room_targets = [_number(room.get("targetTemp")) for room in room_payloads]
    weather = _weather_summary(all_entities)
    summary: dict[str, object] = {
        "homeName": home_name,
        "mode": "home",
        "targetTemp": _mean(room_targets),
        "avgTemp": _mean(room_temperatures),
        **weather,
        "co2": _mean(
            _number(entity.state)
            for entity in all_entities
            if _device_class(entity) == "carbon_dioxide"
        ),
        "activeLights": sum(
            entity.domain == "light" and entity.state == "on"
            for entity in all_entities
        ),
        "activeClimate": sum(
            entity.domain == "climate" and entity.state in _ACTIVE_STATES
            for entity in all_entities
        ),
        "activeAlarms": sum(bool(alarm["active"]) for alarm in alarms),
    }
    scenario_payloads = [
        {
            "id": scenario.scenario_id,
            "title": scenario.title,
            "group": scenario.group,
            "description": scenario.description,
            "icon": scenario.icon,
            "requiresConfirmation": scenario.requires_confirmation,
            "favorite": scenario.favorite,
            "danger": scenario.danger,
        }
        for scenario in scenarios
    ]
    return {
        "contract": {
            "name": DASHBOARD_CONTRACT_NAME,
            "version": DASHBOARD_CONTRACT_VERSION,
            "profile": "hausmanhub-dashboard",
            "source": "home-assistant",
        },
        "generatedAt": generated_at_ms,
        "stateRevision": state_revision,
        "localIso": local_iso,
        "summary": summary,
        "rooms": sorted(room_payloads, key=lambda item: str(item["name"])),
        "devices": sorted(device_payloads, key=lambda item: str(item["name"])),
        "scenarios": sorted(scenario_payloads, key=lambda item: str(item["title"])),
        "alarms": alarms,
        "events": [],
        "capabilities": {
            "actions": False,
            "scenarios": bool(scenario_payloads),
            "scenarioEditing": True,
            "alarms": True,
            "events": False,
            "smartClimate": any(room["hasClimateControl"] for room in room_payloads),
            "physicalDevices": True,
            "dashboardSnapshot": True,
        },
    }
