"""Pure projection for the local HausmanHub tablet dashboard."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
import hashlib
import math
from typing import Any

from ..domain.hub_settings import HausmanHubSettings
from ..domain.device_power_dependencies import (
    PowerDependencyStatus,
    effective_device_state,
)
from ..domain.contours import (
    CLIMATE_TARGET_HUMIDITY_DEFAULT,
    CLIMATE_TARGET_TEMPERATURE_DEFAULT,
    CLIMATE_TARGET_TEMPERATURE_MAXIMUM,
    CLIMATE_TARGET_TEMPERATURE_MINIMUM,
)
from .dashboard_comfort import build_dashboard_comfort


DASHBOARD_CONTRACT_NAME = "universal-home"
DASHBOARD_CONTRACT_VERSION = 1

_PHYSICAL_DOMAINS = frozenset(
    {
        "alarm_control_panel",
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
            "alarm_control_panel",
            "lock",
            "vacuum",
            "media_player",
            "camera",
            "binary_sensor",
            "sensor",
        )
    )
}
_PRIMARY_DEVICE_CLASS_ORDER = {
    device_class: index
    for index, device_class in enumerate(
        (
            "moisture",
            "smoke",
            "gas",
            "carbon_monoxide",
            "safety",
            "problem",
            "occupancy",
            "presence",
            "motion",
            "opening",
            "door",
            "window",
            "temperature",
            "humidity",
            "carbon_dioxide",
            "volatile_organic_compounds",
            "pm25",
            "power",
            "current",
            "energy",
            "voltage",
            "distance",
            "battery",
        )
    )
}
_ALARM_DEVICE_CLASSES = frozenset(
    {"moisture", "smoke", "gas", "carbon_monoxide", "safety", "problem"}
)
_ACTIVE_STATES = frozenset(
    {
        "on",
        "open",
        "opening",
        "playing",
        "cleaning",
        "heat",
        "cool",
        "dry",
        "fan_only",
        "armed_home",
        "armed_away",
        "armed_night",
        "armed_vacation",
        "armed_custom_bypass",
    }
)
_UNAVAILABLE_STATES = frozenset({"unknown", "unavailable"})
_VIRTUAL_DEVICE_INTEGRATIONS = frozenset(
    {
        "climate_group",
        "smartir",
        "template",
        "yandex_smart_home",
    }
)
_MEDIA_IDENTITY_NOISE = frozenset(
    {"android", "device", "display", "media", "smart", "television", "uhd"}
)
_ALLOWLISTED_ATTRIBUTES = (
    "app_name",
    "brightness",
    "current_position",
    "current_temperature",
    "fan_mode",
    "humidity",
    "hvac_action",
    "media_title",
    "percentage",
    "source",
    "temperature",
)
_ENERGY_USAGE_DEVICE_CLASSES = frozenset({"power", "current", "energy"})
_ENERGY_CONTROL_DOMAINS = frozenset({"switch"})
_MINIMUM_MAINS_VOLTAGE = 80.0

_DETAIL_LABEL_TRANSLATIONS = {
    "audio volume": "Громкость звука",
    "battery charging state": "Состояние зарядки",
    "black toner_s/n_:crum-201111a3c97": "Чёрный тонер",
    "center": "Средняя клавиша",
    "clean 清扫模式": "Режим уборки",
    "countdown": "Таймер отключения",
    "custom the-tank-filed": "Бак установлен",
    "custom water-shortage-fault": "Недостаточно воды",
    "humidifier fan level": "Скорость увлажнения",
    "identify": "Идентификация",
    "illumination": "Уровень освещённости",
    "illuminance interval": "Интервал измерения освещённости",
    "ir emitter": "ИК-передатчик",
    "left": "Левая клавиша",
    "mode switch": "Режим выключателя",
    "motor fault": "Неисправность мотора",
    "occupancy sensitivity": "Чувствительность присутствия",
    "over current breaker": "Защита от превышения тока",
    "over current threshold": "Порог отключения по току",
    "over voltage breaker": "Защита от превышения напряжения",
    "over voltage threshold": "Верхний порог напряжения",
    "power breaker": "Защита от превышения мощности",
    "power threshold": "Порог отключения по мощности",
    "restart": "Перезапуск",
    "reverse direction": "Обратное направление",
    "right": "Правая клавиша",
    "temperature breaker": "Защита от перегрева",
    "temperature threshold": "Порог отключения по температуре",
    "time format": "Формат времени",
    "under voltage breaker": "Защита от низкого напряжения",
    "under voltage threshold": "Нижний порог напряжения",
}

_STATE_TRANSLATIONS = {
    "anti_flicker_mode": "защита от мерцания",
    "back": "обратное",
    "bright": "ярко",
    "charging": "заряжается",
    "level1": "уровень 1",
    "level2": "уровень 2",
    "level3": "уровень 3",
    "medium": "средняя",
    "stopped": "остановлен",
    "强力": "мощный",
}


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
    entry_type: str | None = None
    integrations: tuple[str, ...] = ()
    disabled: bool = False
    image_url: str | None = None


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


@dataclass(frozen=True, slots=True)
class DashboardEvent:
    """One redacted, human-readable activity item for dashboard clients."""

    event_id: str
    timestamp_ms: int
    title: str
    message: str | None = None
    kind: str | None = None
    level: str = "info"


def _opaque_id(prefix: str, source_id: str) -> str:
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def stable_public_id(prefix: str, source_id: str) -> str:
    """Expose the canonical private-id-free identifier to admin adapters."""

    return _opaque_id(prefix, source_id)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, str):
        try:
            numeric = float(value.replace(",", "."))
            return numeric if math.isfinite(numeric) else None
        except ValueError:
            return None
    return None


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 1) if present else None


def _device_class(entity: DashboardEntity) -> str | None:
    value = entity.attributes.get("device_class")
    return value if isinstance(value, str) and value else None


def _primary_sort_key(entity: DashboardEntity) -> tuple[int, int, str]:
    """Prefer a device's purpose over diagnostics such as battery charge."""

    return (
        _PRIMARY_DOMAIN_ORDER.get(entity.domain, 999),
        _PRIMARY_DEVICE_CLASS_ORDER.get(_device_class(entity) or "", 998),
        entity.entity_id,
    )


def _presentation_members(
    entities: Iterable[DashboardEntity],
) -> tuple[DashboardEntity, ...]:
    """Keep one useful battery capability while preserving real device controls."""

    collected = tuple(entities)
    battery = [entity for entity in collected if _device_class(entity) == "battery"]
    chosen_battery = min(
        battery,
        key=lambda entity: (
            entity.attributes.get("unit_of_measurement") != "%",
            entity.domain != "sensor",
            _number(entity.state) is None,
            entity.entity_id,
        ),
        default=None,
    )
    return tuple(
        sorted(
            (
                entity
                for entity in collected
                if _device_class(entity) != "battery" or entity is chosen_battery
            ),
            key=_primary_sort_key,
        )
    )


def _state_label(entity: DashboardEntity) -> str:
    if entity.domain == "binary_sensor":
        semantic = {
            "moisture": ("сухо", "обнаружена вода"),
            "smoke": ("дыма нет", "обнаружен дым"),
            "gas": ("газа нет", "обнаружен газ"),
            "carbon_monoxide": ("угарного газа нет", "обнаружен угарный газ"),
            "safety": ("в норме", "тревога"),
            "problem": ("в норме", "обнаружена проблема"),
            "motion": ("движения нет", "обнаружено движение"),
            "occupancy": ("присутствия нет", "обнаружено присутствие"),
            "presence": ("присутствия нет", "обнаружено присутствие"),
            "opening": ("закрыто", "открыто"),
            "door": ("закрыто", "открыто"),
            "window": ("закрыто", "открыто"),
        }.get(_device_class(entity) or "")
        if semantic is not None and entity.state in {"off", "on"}:
            return semantic[entity.state == "on"]
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
        "locked": "закрыт",
        "unlocked": "открыт",
        "locking": "закрывается",
        "unlocking": "открывается",
        "jammed": "заклинило",
        "disarmed": "охрана выключена",
        "armed_home": "охрана включена: дома",
        "armed_away": "охрана включена: вне дома",
        "armed_night": "охрана включена: ночь",
        "armed_vacation": "охрана включена: отпуск",
        "armed_custom_bypass": "охрана включена: особый режим",
        "arming": "охрана включается",
        "pending": "задержка перед включением",
        "triggered": "тревога",
        "unavailable": "нет связи",
        "unknown": "состояние неизвестно",
    }
    return labels.get(
        entity.state,
        _STATE_TRANSLATIONS.get(entity.state.casefold(), entity.state),
    )


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
        "power": "Мощность",
        "current": "Ток",
        "voltage": "Напряжение",
        "energy": "Энергия",
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
        "alarm_control_panel": "Охрана",
    }
    translated_label = _DETAIL_LABEL_TRANSLATIONS.get(entity.name.casefold())
    if translated_label is not None:
        return translated_label
    domain_label = domains.get(entity.domain)
    if domain_label is not None:
        return domain_label
    return entity.name


def _detail_value(entity: DashboardEntity) -> str:
    unit = entity.attributes.get("unit_of_measurement")
    suffix = f" {unit}" if isinstance(unit, str) and unit else ""
    return f"{entity.state}{suffix}" if suffix else _state_label(entity)


def _range_control(entity: DashboardEntity) -> dict[str, object] | None:
    """Return an advertised numeric control only with complete HA bounds."""

    if entity.domain != "number":
        return None
    minimum = _number(entity.attributes.get("min"))
    maximum = _number(entity.attributes.get("max"))
    step = _number(entity.attributes.get("step"))
    if (
        minimum is None
        or maximum is None
        or step is None
        or minimum >= maximum
        or step <= 0
        or step > maximum - minimum
    ):
        return None
    unit = entity.attributes.get("unit_of_measurement")
    return {
        "kind": "range",
        "minimum": minimum,
        "maximum": maximum,
        "step": step,
        "unit": unit if isinstance(unit, str) and len(unit) <= 32 else None,
        "targetId": _opaque_id("entity", entity.entity_id),
        "actionId": "set_value",
    }


def _category(domain: str, entity: DashboardEntity) -> str:
    if domain in {"climate", "fan", "humidifier"}:
        return "climate"
    if domain in {"camera", "lock", "alarm_control_panel"}:
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
    if device_class in _PRIMARY_DEVICE_CLASS_ORDER:
        return device_class
    if domain in {"binary_sensor", "sensor"}:
        return "other"
    return domain


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


def _apply_power_dependencies(
    entities: tuple[DashboardEntity, ...], dependencies: Mapping[str, str]
) -> tuple[tuple[DashboardEntity, ...], dict[str, PowerDependencyStatus]]:
    """Replace stale child states with their effective powered states."""

    reported_by_id = {entity.entity_id: entity for entity in entities}

    def read_state(entity_id: str) -> str | None:
        entity = reported_by_id.get(entity_id)
        return entity.state if entity is not None else None

    effective_entities: list[DashboardEntity] = []
    statuses: dict[str, PowerDependencyStatus] = {}
    for entity in entities:
        effective_state, status = effective_device_state(
            entity.entity_id, dependencies, read_state
        )
        effective_entities.append(
            replace(entity, state=effective_state)
            if effective_state != entity.state
            else entity
        )
        if status is not None:
            statuses[entity.entity_id] = status
    return tuple(effective_entities), statuses


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


def _measurement(
    entities: Iterable[DashboardEntity], device_class: str
) -> float | None:
    """Return one canonical electrical measurement for a physical device."""

    scales = {
        "power": {"W": 1.0, "kW": 1000.0},
        "current": {"A": 1.0, "mA": 0.001},
        "voltage": {"V": 1.0, "mV": 0.001},
        "energy": {"kWh": 1.0, "Wh": 0.001},
    }
    for entity in entities:
        if _device_class(entity) != device_class:
            continue
        value = _number(entity.state)
        unit = entity.attributes.get("unit_of_measurement")
        if value is None or not isinstance(unit, str):
            continue
        scale = scales.get(device_class, {}).get(unit)
        if scale is not None:
            return round(value * scale, 3)
    return None


def _energy_measurements(entities: Iterable[DashboardEntity]) -> dict[str, float | None]:
    collected = tuple(entities)
    return {
        "currentPowerW": _measurement(collected, "power"),
        "currentA": _measurement(collected, "current"),
        "voltageV": _measurement(collected, "voltage"),
        "totalKwh": _measurement(collected, "energy"),
    }


def _is_energy_source(
    entities: Iterable[DashboardEntity],
    measurements: Mapping[str, float | None],
) -> bool:
    """Reject battery-voltage diagnostics while retaining real energy meters."""

    device_classes = {_device_class(entity) for entity in entities}
    if device_classes & _ENERGY_USAGE_DEVICE_CLASSES:
        return True
    voltage = measurements.get("voltageV")
    return voltage is not None and voltage >= _MINIMUM_MAINS_VOLTAGE


def _energy_source_available(
    entities: Iterable[DashboardEntity],
    measurements: Mapping[str, float | None],
) -> bool:
    """Require a live meter and, when present, a live physical control entity."""

    collected = tuple(entities)
    measurement_entities = tuple(
        entity
        for entity in collected
        if _device_class(entity) in _ENERGY_USAGE_DEVICE_CLASSES
        or (
            _device_class(entity) == "voltage"
            and (measurements.get("voltageV") or 0) >= _MINIMUM_MAINS_VOLTAGE
        )
    )
    if not measurement_entities or not any(
        entity.state not in _UNAVAILABLE_STATES for entity in measurement_entities
    ):
        return False
    control_entities = tuple(
        entity for entity in collected if entity.domain in _ENERGY_CONTROL_DOMAINS
    )
    return not control_entities or any(
        entity.state not in _UNAVAILABLE_STATES for entity in control_entities
    )


def _energy_source_powered(entities: Iterable[DashboardEntity]) -> bool | None:
    """Return the observed physical switch state without confusing it with reachability."""

    control_entities = tuple(
        entity
        for entity in entities
        if entity.domain in _ENERGY_CONTROL_DOMAINS
        and entity.state not in _UNAVAILABLE_STATES
    )
    if not control_entities:
        return None
    return any(entity.state == "on" for entity in control_entities)


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


def _normalized_fingerprint_part(value: str | None) -> str:
    return "".join(character for character in (value or "").casefold() if character.isalnum())


def _is_virtual_device(
    device: DashboardDevice, members: Iterable[DashboardEntity]
) -> bool:
    domains = {member.domain for member in members}
    integrations = set(device.integrations)
    if device.entry_type == "service":
        return True
    if integrations & _VIRTUAL_DEVICE_INTEGRATIONS:
        return True
    return "yandex_station" in integrations and "media_player" not in domains


def _device_details(
    device_name: str, entities: Iterable[DashboardEntity]
) -> list[dict[str, object]]:
    """Build readable, non-duplicated capability rows for one device card."""

    members = _presentation_members(entities)
    base_labels = [_detail_label(member) for member in members]
    duplicate_labels = {
        label for label in base_labels if base_labels.count(label) > 1
    }
    details: list[dict[str, object]] = []
    for member, base_label in zip(members, base_labels, strict=True):
        label = (
            _DETAIL_LABEL_TRANSLATIONS.get(member.name.casefold(), member.name)
            if base_label in duplicate_labels
            else base_label
        )
        if label.casefold().startswith(f"{device_name} ".casefold()):
            label = label[len(device_name) :].strip()
        label = _DETAIL_LABEL_TRANSLATIONS.get(label.casefold(), label)
        detail = {
            "label": label or base_label,
            "value": _detail_value(member),
            "entityId": member.entity_id,
            "domain": member.domain,
            "state": member.state,
        }
        control = _range_control(member)
        if control is not None:
            detail["control"] = control
        details.append(detail)
    return details


def _media_identity_tokens(device: DashboardDevice) -> frozenset[str]:
    """Return conservative brand/model words for cross-integration matching."""

    source = f"{device.manufacturer or ''} {device.model or ''}".casefold()
    normalized = "".join(character if character.isalnum() else " " for character in source)
    return frozenset(
        token
        for token in normalized.split()
        if len(token) >= 3
        and not token.isdecimal()
        and token not in _MEDIA_IDENTITY_NOISE
    )


def _media_duplicate_groups(
    devices: Mapping[str, DashboardDevice],
    registry_members: Mapping[str, list[DashboardEntity]],
) -> tuple[tuple[str, ...], ...]:
    """Match one media appliance exposed once by each distinct integration."""

    candidates: dict[tuple[str, str], list[str]] = defaultdict(list)
    for device_id, device in devices.items():
        members = registry_members[device_id]
        domains = {member.domain for member in members}
        area_id = device.area_id or next(
            (member.area_id for member in members if member.area_id), None
        )
        normalized_name = _normalized_fingerprint_part(device.name)
        if (
            _is_virtual_device(device, members)
            or "media_player" not in domains
            or not area_id
            or not normalized_name
            or not device.integrations
            or not _media_identity_tokens(device)
        ):
            continue
        candidates[(area_id, normalized_name)].append(device_id)

    groups: list[tuple[str, ...]] = []
    for candidate_ids in candidates.values():
        if len(candidate_ids) < 2:
            continue
        integration_sets = [set(devices[item].integrations) for item in candidate_ids]
        if any(
            integration_sets[left] & integration_sets[right]
            for left in range(len(integration_sets))
            for right in range(left + 1, len(integration_sets))
        ):
            continue
        identity_sets = [_media_identity_tokens(devices[item]) for item in candidate_ids]
        if not set.intersection(*(set(tokens) for tokens in identity_sets)):
            continue
        groups.append(tuple(sorted(candidate_ids)))
    return tuple(sorted(groups))


def _device_status(
    device: DashboardDevice | None, members: Iterable[DashboardEntity]
) -> str:
    collected = tuple(members)
    if device is not None and device.disabled:
        return "disabled"
    if not collected:
        return "empty"
    if all(member.state in _UNAVAILABLE_STATES for member in collected):
        return "unavailable"
    return "available"


def _inventory_reason(
    *,
    kind: str,
    status: str,
    area_id: str | None,
    possible_duplicate: bool,
) -> str | None:
    if possible_duplicate:
        return "Похожий виртуальный контур уже представлен одной основной карточкой."
    if status == "disabled":
        return "Устройство отключено в реестре Home Assistant."
    if status == "empty":
        return "В реестре нет доступных сущностей этого устройства."
    if status == "unavailable":
        return "Все сущности устройства сейчас недоступны."
    if area_id is None:
        return "Устройство не привязано к комнате Home Assistant."
    if kind == "virtual":
        return "Виртуальный контур управления, а не отдельное физическое устройство."
    return None


def build_dashboard_snapshot(
    *,
    areas: Iterable[DashboardArea],
    devices: Iterable[DashboardDevice],
    entities: Iterable[DashboardEntity],
    scenarios: Iterable[DashboardScenario] = (),
    events: Iterable[DashboardEvent] = (),
    generated_at_ms: int,
    local_iso: str,
    home_name: str = "Дом",
    state_revision: int | None = None,
    energy_settings: HausmanHubSettings | None = None,
    climate_targets: Mapping[str, tuple[float, int]] | None = None,
    pinned_entity_ids: Iterable[str] | None = None,
    power_dependencies: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Project one immutable, read-only universal dashboard snapshot."""

    event_payloads = [
        {
            "id": event.event_id,
            "ts": event.timestamp_ms,
            "title": event.title,
            "message": event.message,
            "kind": event.kind,
            "level": event.level,
        }
        for event in sorted(events, key=lambda item: item.timestamp_ms, reverse=True)
    ][:100]
    pinned_ids = frozenset(
        value.strip()
        for value in (pinned_entity_ids or ())
        if isinstance(value, str) and value.strip()
    )

    area_by_id = {area.area_id: area for area in areas}
    device_by_id = {device.device_id: device for device in devices}
    reported_entities = tuple(entities)
    dependency_mapping = dict(power_dependencies or {})
    reported_by_id = {entity.entity_id: entity for entity in reported_entities}
    all_entities, dependency_statuses = _apply_power_dependencies(
        reported_entities, dependency_mapping
    )
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

    registry_members: dict[str, list[DashboardEntity]] = {
        device_id: sorted(
            grouped.get(f"device:{device_id}", []),
            key=_primary_sort_key,
        )
        for device_id in device_by_id
    }
    virtual_groups: dict[tuple[object, ...], list[str]] = defaultdict(list)
    for device_id, device in device_by_id.items():
        members = registry_members[device_id]
        if not _is_virtual_device(device, members):
            continue
        domains = tuple(sorted({member.domain for member in members}))
        fingerprint = (
            device.area_id or next((member.area_id for member in members if member.area_id), None),
            _normalized_fingerprint_part(device.name),
            _normalized_fingerprint_part(device.manufacturer),
            _normalized_fingerprint_part(device.model),
            tuple(sorted(device.integrations)),
            domains,
        )
        virtual_groups[fingerprint].append(device_id)

    canonical_source: dict[str, str] = {device_id: device_id for device_id in device_by_id}
    merged_media_sources: dict[str, tuple[str, ...]] = {}
    duplicate_groups = 0
    media_groups = _media_duplicate_groups(device_by_id, registry_members)
    for groups, merge_members in (
        (tuple(tuple(group) for group in virtual_groups.values()), False),
        (media_groups, True),
    ):
        for group in groups:
            if len(group) < 2:
                continue
            duplicate_groups += 1
            canonical = min(
                group,
                key=lambda device_id: (
                    device_by_id[device_id].disabled,
                    _device_status(device_by_id[device_id], registry_members[device_id])
                    != "available",
                    device_by_id[device_id].image_url is None,
                    -len(registry_members[device_id]),
                    device_id,
                ),
            )
            canonical_source.update({device_id: canonical for device_id in group})
            if merge_members:
                merged_media_sources[canonical] = group

    source_to_public: dict[str, str] = {
        device_id: _opaque_id("device", f"device:{canonical_source[device_id]}")
        for device_id in device_by_id
    }
    inventory_payloads: list[dict[str, object]] = []
    for device_id, device in device_by_id.items():
        members = registry_members[device_id]
        primary = members[0] if members else None
        area_id = (
            primary.area_id if primary is not None and primary.area_id else device.area_id
        )
        area = area_by_id.get(area_id) if area_id is not None else None
        kind = "virtual" if _is_virtual_device(device, members) else "physical"
        status = _device_status(device, members)
        canonical_id = canonical_source[device_id]
        is_canonical = canonical_id == device_id
        inventory_payloads.append(
            {
                "id": _opaque_id("inventory", f"device:{device_id}"),
                "canonicalId": source_to_public[device_id],
                "name": device.name,
                "roomId": area.area_id if area is not None else None,
                "roomName": area.name if area is not None else None,
                "kind": kind,
                "status": status,
                "canonical": is_canonical,
                "possibleDuplicate": not is_canonical,
                "duplicateOf": None
                if is_canonical
                else source_to_public[canonical_id],
                "entityCount": len(members),
                "domains": sorted({member.domain for member in members}),
                "model": device.model,
                "manufacturer": device.manufacturer,
                "imageUrl": device.image_url,
                "integrations": list(device.integrations),
                "disabled": device.disabled,
                "reason": _inventory_reason(
                    kind=kind,
                    status=status,
                    area_id=area_id,
                    possible_duplicate=not is_canonical,
                ),
            }
        )
    for group_key, members in grouped.items():
        if group_sources[group_key] is not None:
            continue
        primary = sorted(
            members,
            key=_primary_sort_key,
        )[0]
        area = area_by_id.get(primary.area_id) if primary.area_id is not None else None
        status = _device_status(None, members)
        public_id = _opaque_id("device", group_key)
        inventory_payloads.append(
            {
                "id": _opaque_id("inventory", group_key),
                "canonicalId": public_id,
                "name": primary.name,
                "roomId": area.area_id if area is not None else None,
                "roomName": area.name if area is not None else None,
                "kind": "entity_only",
                "status": status,
                "canonical": True,
                "possibleDuplicate": False,
                "duplicateOf": None,
                "entityCount": len(members),
                "domains": sorted({member.domain for member in members}),
                "model": None,
                "manufacturer": None,
                "imageUrl": None,
                "integrations": [],
                "disabled": False,
                "reason": _inventory_reason(
                    kind="entity_only",
                    status=status,
                    area_id=primary.area_id,
                    possible_duplicate=False,
                ),
            }
        )

    device_payloads: list[dict[str, object]] = []
    energy_sources: list[dict[str, object]] = []
    for group_key, members in grouped.items():
        members.sort(key=_primary_sort_key)
        presentation_members = _presentation_members(members)
        primary = presentation_members[0]
        source_device_id = group_sources[group_key]
        if (
            source_device_id is not None
            and canonical_source[source_device_id] != source_device_id
        ):
            continue
        registry_device = (
            device_by_id.get(source_device_id) if source_device_id is not None else None
        )
        if registry_device is not None and _is_virtual_device(registry_device, members):
            # Virtual duplicate projections stay hidden unless the user pinned
            # one of their entities (for example the intercom) in the tablet
            # profile: an explicitly chosen device must remain reachable.
            if not any(member.entity_id in pinned_ids for member in members):
                continue
        if source_device_id in merged_media_sources:
            members = list(
                {
                    member.entity_id: member
                    for merged_source in merged_media_sources[source_device_id]
                    for member in registry_members[merged_source]
                }.values()
            )
            members.sort(
                key=lambda item: (
                    item.domain != "media_player",
                    *_primary_sort_key(item),
                )
            )
            primary = members[0]
        image_url = registry_device.image_url if registry_device is not None else None
        if source_device_id in merged_media_sources and image_url is None:
            image_url = next(
                (
                    device_by_id[merged_source].image_url
                    for merged_source in merged_media_sources[source_device_id]
                    if device_by_id[merged_source].image_url is not None
                ),
                None,
            )
        public_id = _opaque_id("device", group_key)
        area_id = primary.area_id or (
            registry_device.area_id if registry_device is not None else None
        )
        area = area_by_id.get(area_id) if area_id is not None else None
        # Reachability and activity describe the controllable device itself, not
        # its auxiliary telemetry. A humidifier may expose an unavailable
        # ``humidifier`` entity while its temperature and humidity sensors keep
        # returning values. Treating every member equally made that physical
        # device appear online even though no command could reach it.
        operational_members = [
            member for member in members if member.domain in _PHYSICAL_DOMAINS
        ] or members
        unavailable = all(
            member.state in _UNAVAILABLE_STATES for member in operational_members
        )
        active = not unavailable and any(
            member.state in _ACTIVE_STATES for member in operational_members
        )
        name = registry_device.name if registry_device is not None else primary.name
        details = _device_details(name, members)
        electrical = _energy_measurements(members)
        has_energy = _is_energy_source(members, electrical)
        energy_available = _energy_source_available(members, electrical)
        energy_powered = _energy_source_powered(members)
        dependency_status = dependency_statuses.get(primary.entity_id)
        reported_primary = reported_by_id.get(primary.entity_id)
        dependency_payload = None
        if dependency_status is not None:
            source_entity = reported_by_id.get(dependency_status.source_entity_id)
            if source_entity is not None and source_entity.device_id in source_to_public:
                source_public_id = source_to_public[source_entity.device_id]
            else:
                source_public_id = _opaque_id(
                    "device", f"entity:{dependency_status.source_entity_id}"
                )
            dependency_payload = {
                "sourceDeviceId": source_public_id,
                "state": dependency_status.state,
                "reason": dependency_status.reason,
                "blocksCommands": dependency_status.blocks_commands,
            }
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
                "reportedState": (
                    reported_primary.state
                    if reported_primary is not None
                    and reported_primary.state != primary.state
                    else None
                ),
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
                "imageUrl": image_url,
                "attributes": _safe_attributes(primary),
                "actions": [],
                "details": details,
                "energy": electrical if has_energy else None,
                "powerDependency": dependency_payload,
            }
        )
        if has_energy:
            energy_sources.append(
                {
                    "id": public_id,
                    "deviceId": public_id,
                    "name": name,
                    "roomId": area.area_id if area is not None else None,
                    "roomName": area.name if area is not None else None,
                    "available": energy_available,
                    "powered": energy_powered,
                    **electrical,
                }
            )

    room_payloads: list[dict[str, object]] = []
    visible_device_ids = {str(device["id"]) for device in device_payloads}
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
        device_target = (
            _number(climate.attributes.get("temperature"))
            if climate is not None
            else None
        )
        configured_target = (
            climate_targets.get(area.area_id)
            if climate_targets is not None
            else None
        )
        if configured_target is not None:
            target = float(configured_target[0])
            target_humidity = int(configured_target[1])
        elif climate_targets is not None and climate is not None:
            target = CLIMATE_TARGET_TEMPERATURE_DEFAULT
            target_humidity = CLIMATE_TARGET_HUMIDITY_DEFAULT
        else:
            target = (
                device_target
                if device_target is not None
                and CLIMATE_TARGET_TEMPERATURE_MINIMUM
                <= device_target
                <= CLIMATE_TARGET_TEMPERATURE_MAXIMUM
                else (
                    CLIMATE_TARGET_TEMPERATURE_DEFAULT
                    if climate is not None and device_target is not None
                    else None
                )
            )
            target_humidity = None
        temperature = _sensor_value(room_entities, "temperature")
        if temperature is None and climate is not None:
            temperature = _number(climate.attributes.get("current_temperature"))
        humidity = _sensor_value(room_entities, "humidity")
        room_device_ids = sorted(
            {
                source_to_public[entity.device_id]
                for entity in room_entities
                if entity.device_id in source_to_public
                and source_to_public[entity.device_id] in visible_device_ids
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
                "targetHumidity": target_humidity,
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
            entity.domain == "light" and entity.state == "on" for entity in all_entities
        ),
        "activeClimate": sum(
            entity.domain == "climate" and entity.state in _ACTIVE_STATES
            for entity in all_entities
        ),
        "activeAlarms": sum(bool(alarm["active"]) for alarm in alarms),
    }
    comfort = build_dashboard_comfort(room_payloads, co2=summary["co2"])
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
    inventory_payloads.sort(
        key=lambda item: (
            str(item.get("roomName") or "Я"),
            str(item.get("name") or ""),
            bool(item.get("possibleDuplicate")),
            str(item.get("id") or ""),
        )
    )
    physical_device_count = sum(
        bool(item["canonical"])
        and item["kind"] == "physical"
        and int(item["entityCount"]) > 0
        for item in inventory_payloads
    )
    logical_entity_count = sum(
        item["kind"] == "entity_only" for item in inventory_payloads
    )
    inventory_summary = {
        "registeredCount": len(inventory_payloads),
        "canonicalDeviceCount": sum(
            bool(item["canonical"]) and int(item["entityCount"]) > 0
            for item in inventory_payloads
        ),
        "physicalDeviceCount": physical_device_count,
        "logicalEntityCount": logical_entity_count,
        "virtualCount": sum(item["kind"] == "virtual" for item in inventory_payloads),
        "unassignedCount": sum(item["roomId"] is None for item in inventory_payloads),
        "unavailableCount": sum(
            item["status"] == "unavailable" for item in inventory_payloads
        ),
        "emptyCount": sum(item["status"] == "empty" for item in inventory_payloads),
        "duplicateGroupCount": duplicate_groups,
        "attentionCount": sum(
            item["possibleDuplicate"]
            or item["roomId"] is None
            or item["status"] != "available"
            for item in inventory_payloads
        ),
    }
    saved_energy = energy_settings or HausmanHubSettings()
    selected_ids = set(saved_energy.energy_selected_device_ids)
    selected_sources = [
        source
        for source in energy_sources
        if saved_energy.energy_use_all_devices or source["id"] in selected_ids
    ]
    live_selected_sources = [source for source in selected_sources if source["available"]]
    power_values = [
        float(source["currentPowerW"])
        for source in live_selected_sources
        if source["currentPowerW"] is not None
    ]
    current_values = [
        float(source["currentA"])
        for source in live_selected_sources
        if source["currentA"] is not None
    ]
    voltage_values = [
        float(source["voltageV"])
        for source in live_selected_sources
        if source["voltageV"] is not None
    ]
    energy_values = [
        float(source["totalKwh"])
        for source in selected_sources
        if source["totalKwh"] is not None
    ]
    energy_payload = {
        "available": any(
            source["available"]
            and any(source[key] is not None for key in ("currentPowerW", "currentA", "voltageV", "totalKwh"))
            for source in selected_sources
        ),
        "currentPowerW": round(sum(power_values), 1) if power_values else None,
        "currentA": round(sum(current_values), 3) if current_values else None,
        "voltageV": round(sum(voltage_values) / len(voltage_values), 1)
        if voltage_values
        else None,
        "totalKwh": round(sum(energy_values), 3) if energy_values else None,
        "sources": energy_sources,
        "selectedSourceIds": [source["id"] for source in selected_sources],
        "settings": {
            "displayUnits": saved_energy.energy_display_units,
            "showVoltage": saved_energy.energy_show_voltage,
            "aggregation": saved_energy.energy_aggregation,
            "useAllDevices": saved_energy.energy_use_all_devices,
        },
    }
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
        "comfort": comfort,
        "rooms": sorted(room_payloads, key=lambda item: str(item["name"])),
        "devices": sorted(device_payloads, key=lambda item: str(item["name"])),
        "energy": energy_payload,
        "inventory": {
            "summary": inventory_summary,
            "devices": inventory_payloads,
        },
        "scenarios": sorted(scenario_payloads, key=lambda item: str(item["title"])),
        "alarms": alarms,
        "events": event_payloads,
        "capabilities": {
            "actions": False,
            "scenarios": bool(scenario_payloads),
            "scenarioEditing": True,
            "alarms": True,
            "events": bool(event_payloads),
            "smartClimate": any(room["hasClimateControl"] for room in room_payloads),
            "physicalDevices": True,
            "dashboardSnapshot": True,
            "energy": any(
                any(source[key] is not None for key in ("currentPowerW", "currentA", "voltageV", "totalKwh"))
                for source in energy_sources
            ),
            "energyHistory": True,
        },
    }
