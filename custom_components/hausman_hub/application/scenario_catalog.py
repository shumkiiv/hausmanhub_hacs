"""Build the live device/action catalog for HausmanHub scenarios."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from .scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
    ScenarioDeviceProperty,
    ScenarioPropertyOption,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


SCENARIO_CATALOG_DOMAINS = frozenset(
    {
        "button",
        "binary_sensor",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "light",
        "lock",
        "media_player",
        "number",
        "select",
        "sensor",
        "sun",
        "switch",
        "vacuum",
        "valve",
        "water_heater",
    }
)

_TYPE_NAMES = {
    "binary_sensor": "Датчики",
    "button": "Кнопки",
    "climate": "Кондиционеры",
    "cover": "Шторы и ворота",
    "fan": "Вентиляция",
    "humidifier": "Увлажнители",
    "light": "Освещение",
    "lock": "Замки",
    "media_player": "Медиа",
    "number": "Настройки",
    "select": "Режимы",
    "sensor": "Датчики",
    "sun": "Солнце",
    "switch": "Выключатели",
    "vacuum": "Пылесосы",
    "valve": "Клапаны",
    "water_heater": "Нагрев воды",
}

_ON_OFF_DEVICE_CLASSES = {
    "battery_charging": ("Заряжается", "Не заряжается"),
    "cold": ("Холодно", "Норма"),
    "connectivity": ("Подключено", "Нет связи"),
    "door": ("Открыто", "Закрыто"),
    "garage_door": ("Открыто", "Закрыто"),
    "gas": ("Обнаружен газ", "Газа нет"),
    "heat": ("Перегрев", "Норма"),
    "light": ("Обнаружен свет", "Свет не обнаружен"),
    "lock": ("Разблокировано", "Заблокировано"),
    "moisture": ("Обнаружена вода", "Сухо"),
    "motion": ("Движение", "Нет движения"),
    "occupancy": ("Движение", "Нет движения"),
    "opening": ("Открыто", "Закрыто"),
    "plug": ("Подключено", "Отключено"),
    "power": ("Питание есть", "Питания нет"),
    "presence": ("Присутствие", "Нет присутствия"),
    "problem": ("Есть проблема", "В норме"),
    "running": ("Работает", "Не работает"),
    "safety": ("Небезопасно", "Безопасно"),
    "smoke": ("Обнаружен дым", "Дыма нет"),
    "sound": ("Обнаружен звук", "Звука нет"),
    "tamper": ("Вскрытие", "В норме"),
    "vibration": ("Вибрация", "Нет вибрации"),
    "window": ("Открыто", "Закрыто"),
}

_STATE_LABELS = {
    "auto": "Автоматически",
    "cleaning": "Уборка",
    "closed": "Закрыто",
    "closing": "Закрывается",
    "cool": "Охлаждение",
    "docked": "На базе",
    "dry": "Осушение",
    "fan_only": "Вентиляция",
    "heat": "Обогрев",
    "heat_cool": "Автоматический климат",
    "idle": "Ожидание",
    "locked": "Заблокировано",
    "off": "Выключено",
    "on": "Включено",
    "open": "Открыто",
    "opening": "Открывается",
    "paused": "Пауза",
    "playing": "Воспроизводится",
    "returning": "Возвращается на базу",
    "unlocked": "Разблокировано",
}

_CAPABILITY_TRANSLATIONS = {
    "do not disturb": "Не беспокоить",
    "illumination": "Освещённость",
    "led indicator": "Индикатор",
    "motion": "Движение",
    "motion timeout": "Тайм-аут движения",
    "occupancy": "Присутствие",
    "operation mode 1": "Режим работы",
    "power-on behavior 1": "Поведение после включения",
    "power type": "Тип питания",
    "temperature": "Температура",
    "humidity": "Влажность",
    "power": "Питание",
    "energy": "Энергия",
    "brightness": "Яркость",
}


def _domain_actions(domain: str) -> tuple[ScenarioDeviceAction, ...]:
    """Return the allowlisted actions for one HA domain."""

    if domain == "light":
        return (
            ScenarioDeviceAction(
                action_id="turn_on",
                title="Включить",
                domain="light",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="turn_off",
                title="Выключить",
                domain="light",
                service="turn_off",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="toggle",
                title="Переключить",
                domain="light",
                service="toggle",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="set_brightness",
                title="Яркость",
                domain="light",
                service="turn_on",
                allowed_fields=frozenset({"value"}),
            ),
            ScenarioDeviceAction(
                action_id="set_adaptive_brightness",
                title="Яркость по времени суток",
                domain="light",
                service="turn_on",
                allowed_fields=frozenset({"value"}),
            ),
            ScenarioDeviceAction(
                action_id="set_brightness_percent",
                title="Яркость, %",
                domain="light",
                service="turn_on",
                allowed_fields=frozenset({"value"}),
            ),
            ScenarioDeviceAction(
                action_id="set_color_temperature",
                title="Температура света",
                domain="light",
                service="turn_on",
                allowed_fields=frozenset({"value"}),
            ),
        )
    if domain == "switch":
        return (
            ScenarioDeviceAction(
                action_id="turn_on",
                title="Включить",
                domain="switch",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="turn_off",
                title="Выключить",
                domain="switch",
                service="turn_off",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="toggle",
                title="Переключить",
                domain="switch",
                service="toggle",
                allowed_fields=frozenset(),
            ),
        )
    if domain == "number":
        return (
            ScenarioDeviceAction(
                action_id="set_value",
                title="Установить значение",
                domain="number",
                service="set_value",
                allowed_fields=frozenset({"value"}),
            ),
        )
    if domain == "fan":
        return (
            ScenarioDeviceAction(
                action_id="turn_on",
                title="Включить",
                domain="fan",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="turn_off",
                title="Выключить",
                domain="fan",
                service="turn_off",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="toggle",
                title="Переключить",
                domain="fan",
                service="toggle",
                allowed_fields=frozenset(),
            ),
        )
    if domain == "cover":
        return (
            ScenarioDeviceAction(
                action_id="open_cover",
                title="Открыть",
                domain="cover",
                service="open_cover",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="close_cover",
                title="Закрыть",
                domain="cover",
                service="close_cover",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="set_position",
                title="Позиция",
                domain="cover",
                service="set_cover_position",
                allowed_fields=frozenset({"value"}),
            ),
        )
    if domain == "media_player":
        return (
            ScenarioDeviceAction(
                action_id="turn_on",
                title="Включить",
                domain="media_player",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="turn_off",
                title="Выключить",
                domain="media_player",
                service="turn_off",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="media_play",
                title="Играть",
                domain="media_player",
                service="media_play",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="media_pause",
                title="Пауза",
                domain="media_player",
                service="media_pause",
                allowed_fields=frozenset(),
            ),
        )
    if domain == "climate":
        return (
            ScenarioDeviceAction(
                action_id="turn_on",
                title="Включить",
                domain="climate",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="turn_off",
                title="Выключить",
                domain="climate",
                service="turn_off",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="set_temperature",
                title="Температура",
                domain="climate",
                service="set_temperature",
                allowed_fields=frozenset({"value"}),
            ),
            ScenarioDeviceAction(
                action_id="set_hvac_mode",
                title="Режим",
                domain="climate",
                service="set_hvac_mode",
                allowed_fields=frozenset({"value"}),
            ),
            ScenarioDeviceAction(
                action_id="set_fan_mode",
                title="Скорость вентилятора",
                domain="climate",
                service="set_fan_mode",
                allowed_fields=frozenset({"value"}),
            ),
        )
    if domain == "humidifier":
        return (
            ScenarioDeviceAction(
                action_id="turn_on",
                title="Включить",
                domain="humidifier",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="turn_off",
                title="Выключить",
                domain="humidifier",
                service="turn_off",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="set_humidity",
                title="Целевая влажность",
                domain="humidifier",
                service="set_humidity",
                allowed_fields=frozenset({"value"}),
            ),
        )
    if domain == "water_heater":
        return (
            ScenarioDeviceAction(
                action_id="turn_on",
                title="Включить",
                domain="water_heater",
                service="turn_on",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="turn_off",
                title="Выключить",
                domain="water_heater",
                service="turn_off",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="set_temperature",
                title="Температура",
                domain="water_heater",
                service="set_temperature",
                allowed_fields=frozenset({"value"}),
            ),
            ScenarioDeviceAction(
                action_id="set_operation_mode",
                title="Режим работы",
                domain="water_heater",
                service="set_operation_mode",
                allowed_fields=frozenset({"value"}),
            ),
        )
    if domain == "lock":
        return (
            ScenarioDeviceAction(
                action_id="lock",
                title="Закрыть",
                domain="lock",
                service="lock",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="unlock",
                title="Открыть",
                domain="lock",
                service="unlock",
                allowed_fields=frozenset(),
            ),
        )
    if domain == "vacuum":
        return (
            ScenarioDeviceAction(
                action_id="start",
                title="Начать уборку",
                domain="vacuum",
                service="start",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="pause",
                title="Пауза",
                domain="vacuum",
                service="pause",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="stop",
                title="Остановить",
                domain="vacuum",
                service="stop",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="return_home",
                title="Вернуться на базу",
                domain="vacuum",
                service="return_to_base",
                allowed_fields=frozenset(),
            ),
        )
    if domain == "button":
        return (
            ScenarioDeviceAction(
                action_id="press",
                title="Нажать",
                domain="button",
                service="press",
                allowed_fields=frozenset(),
            ),
        )
    if domain == "valve":
        return (
            ScenarioDeviceAction(
                action_id="open_valve",
                title="Открыть",
                domain="valve",
                service="open_valve",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="close_valve",
                title="Закрыть",
                domain="valve",
                service="close_valve",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="set_position",
                title="Позиция",
                domain="valve",
                service="set_valve_position",
                allowed_fields=frozenset({"value"}),
            ),
        )
    return ()


def _stable_target_id_from_entity(entity_id: str) -> str:
    digest = hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:16]
    return f"entity_{digest}"


def _friendly_name(state: object) -> str:
    name = getattr(state, "attributes", {}).get("friendly_name")
    return name if isinstance(name, str) else getattr(state, "entity_id", "")


def _stable_physical_id(device_id: str | None, entity_id: str) -> str:
    source = f"device:{device_id}" if device_id else f"entity:{entity_id}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"device_{digest}"


def _relative_capability_name(
    friendly_name: str,
    physical_name: str,
    domain: str,
    device_class: str,
) -> str:
    """Remove repeated device prefixes and localize a concise capability."""

    value = friendly_name.strip()
    prefix = physical_name.strip()
    while prefix and value.casefold().startswith(prefix.casefold()):
        value = value[len(prefix):].strip(" ·:-_/|")
    if device_class in {"motion", "occupancy"}:
        return "Движение"
    if not value:
        return _TYPE_NAMES.get(domain, "Состояние")
    translated = _CAPABILITY_TRANSLATIONS.get(value.casefold())
    return translated or value


def _localized_state_label(value: object) -> str:
    raw = str(value)
    return _STATE_LABELS.get(raw.casefold(), raw.replace("_", " ").capitalize())


def _state_options(
    domain: str,
    device_class: str,
    attributes: Mapping[str, object],
) -> tuple[ScenarioPropertyOption, ...]:
    if domain == "binary_sensor":
        on_label, off_label = _ON_OFF_DEVICE_CLASSES.get(
            device_class, ("Сработал", "Не сработал")
        )
        return (
            ScenarioPropertyOption("on", on_label),
            ScenarioPropertyOption("off", off_label),
        )
    if domain in {"light", "switch", "fan", "humidifier", "water_heater"}:
        return (
            ScenarioPropertyOption("on", "Включено"),
            ScenarioPropertyOption("off", "Выключено"),
        )
    if domain == "sun":
        return (
            ScenarioPropertyOption("above_horizon", "До заката"),
            ScenarioPropertyOption("below_horizon", "После заката"),
        )
    if domain == "lock":
        return (
            ScenarioPropertyOption("locked", "Заблокировано"),
            ScenarioPropertyOption("unlocked", "Разблокировано"),
        )
    if domain in {"cover", "valve"}:
        return tuple(
            ScenarioPropertyOption(value, _localized_state_label(value))
            for value in ("open", "closed", "opening", "closing")
        )
    option_keys = {
        "climate": "hvac_modes",
        "select": "options",
        "sensor": "options",
    }
    raw_options = attributes.get(option_keys.get(domain, ""))
    if isinstance(raw_options, (list, tuple)):
        values = [item for item in raw_options if isinstance(item, (str, int, float))]
        if values:
            return tuple(
                ScenarioPropertyOption(item, _localized_state_label(item))
                for item in values[:64]
            )
    if domain == "media_player":
        values = ("playing", "paused", "idle", "off", "on")
        return tuple(
            ScenarioPropertyOption(value, _localized_state_label(value))
            for value in values
        )
    if domain == "vacuum":
        values = ("cleaning", "docked", "returning", "paused", "idle")
        return tuple(
            ScenarioPropertyOption(value, _localized_state_label(value))
            for value in values
        )
    return ()


def _state_property(
    state: object,
    domain: str,
    device_class: str,
    capability_name: str,
) -> ScenarioDeviceProperty:
    attributes = getattr(state, "attributes", {})
    if not isinstance(attributes, Mapping):
        attributes = {}
    options = _state_options(domain, device_class, attributes)
    raw_state = getattr(state, "state", None)
    unit = attributes.get("unit_of_measurement")
    numeric = domain == "number"
    if domain == "sensor":
        # Числовость сенсора определяется атрибутами, а не живым состоянием:
        # пока Zigbee2MQTT датчик unavailable после рестарта, state_class и
        # unit_of_measurement уже на месте, а строка состояния не парсится.
        state_class = attributes.get("state_class")
        if state_class in {"measurement", "total", "total_increasing"}:
            numeric = True
        elif (
            isinstance(unit, str)
            and bool(unit)
            and device_class not in {"timestamp", "date"}
        ):
            numeric = True
    if isinstance(raw_state, str) and raw_state not in {"unknown", "unavailable", ""}:
        try:
            numeric = math.isfinite(float(raw_state))
        except ValueError:
            pass
    if options:
        value_type = "enum"
        comparisons = ("equals", "not_equals", "changed")
    elif numeric:
        value_type = "number"
        comparisons = ("equals", "not_equals", "above", "below", "changed")
    else:
        value_type = "text"
        comparisons = ("equals", "not_equals", "changed")
    return ScenarioDeviceProperty(
        property_id="state",
        label=capability_name,
        value_type=value_type,
        comparisons=comparisons,
        options=options,
        unit=unit if isinstance(unit, str) and unit else None,
    )


def _registries(hass: HomeAssistant) -> tuple[object | None, object | None, object | None]:
    """Read official HA registries with lightweight test doubles as fallback."""

    try:
        from homeassistant.helpers import area_registry as ar  # noqa: PLC0415
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415
        from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

        return dr.async_get(hass), er.async_get(hass), ar.async_get(hass)
    except (ImportError, AttributeError, KeyError, TypeError):
        return (
            hass.data.get("device_registry"),
            hass.data.get("entity_registry"),
            hass.data.get("area_registry"),
        )


def _number_range(state: object) -> tuple[float, float, float] | None:
    attributes = getattr(state, "attributes", {})
    if not isinstance(attributes, Mapping):
        return None
    values: list[float] = []
    for key in ("min", "max", "step"):
        value = attributes.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        values.append(numeric)
    minimum, maximum, step = values
    if minimum >= maximum or step <= 0 or step > maximum - minimum:
        return None
    return minimum, maximum, step


def _registry_entry(registry: object, key: str, collection_name: str) -> object | None:
    collection = getattr(registry, collection_name, None)
    if collection is None:
        return None
    return collection.get(key)


async def async_build_scenario_catalog(hass: HomeAssistant) -> ScenarioCatalog:
    """Build a live catalog of controllable devices for the scenario editor.

    Each observable entity keeps its own stable target for execution, while
    physical metadata lets clients present one device and its capabilities.
    """

    device_registry, entity_registry, area_registry = _registries(hass)
    devices: dict[str, ScenarioDeviceEntry] = {}

    states_async_all = getattr(hass.states, "async_all", None)
    if states_async_all is None:
        return ScenarioCatalog(devices=devices, scenarios={})

    for state in states_async_all():
        entity_id = getattr(state, "entity_id", "")
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        if domain not in SCENARIO_CATALOG_DOMAINS:
            continue
        actions = _domain_actions(domain)
        number_range = _number_range(state) if domain == "number" else None
        if domain == "number" and number_range is None:
            continue

        target_id = _stable_target_id_from_entity(entity_id)
        entity_name = _friendly_name(state)
        name = entity_name

        registry_entry = None
        if entity_registry is not None:
            registry_entry = _registry_entry(
                entity_registry, entity_id, "entities"
            )
        device_id: str | None = None
        area_id: str | None = None
        if registry_entry is not None:
            device_id = getattr(registry_entry, "device_id", None)
            area_id = getattr(registry_entry, "area_id", None)
        device_name: str | None = None
        if device_id is not None and device_registry is not None:
            device_entry = _registry_entry(device_registry, device_id, "devices")
            if device_entry is not None:
                device_name = getattr(device_entry, "name_by_user", None) or getattr(
                    device_entry, "name", None
                )
                if isinstance(device_name, str) and device_name:
                    if entity_name and entity_name != device_name:
                        name = f"{device_name} · {entity_name}"
                    else:
                        name = device_name
                area_id = area_id or getattr(device_entry, "area_id", None)
        if not isinstance(device_name, str) or not device_name:
            device_name = entity_name
        room_name: str | None = None
        if isinstance(area_id, str) and area_id and area_registry is not None:
            area_entry = _registry_entry(area_registry, area_id, "areas")
            candidate = getattr(area_entry, "name", None) if area_entry else None
            room_name = candidate if isinstance(candidate, str) and candidate else None
        attributes = getattr(state, "attributes", {})
        device_class_value = (
            attributes.get("device_class") if isinstance(attributes, Mapping) else None
        )
        device_class = (
            device_class_value if isinstance(device_class_value, str) else ""
        )
        capability_name = _relative_capability_name(
            entity_name, device_name, domain, device_class
        )
        state_property = _state_property(
            state, domain, device_class, capability_name
        )

        devices[target_id] = ScenarioDeviceEntry(
            target_id=target_id,
            name=name,
            entity_id=entity_id,
            actions=actions,
            physical_id=_stable_physical_id(device_id, entity_id),
            physical_name=device_name,
            room_id=area_id if isinstance(area_id, str) and area_id else None,
            room_name=room_name,
            device_type=device_class or domain,
            device_type_name=_TYPE_NAMES.get(domain, "Устройства"),
            capability_name=capability_name,
            properties=(state_property,),
            range_minimum=number_range[0] if number_range is not None else None,
            range_maximum=number_range[1] if number_range is not None else None,
            range_step=number_range[2] if number_range is not None else None,
        )

    return ScenarioCatalog(devices=devices, scenarios={})
