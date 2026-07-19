"""Stable public climate codes and their plain Russian display names."""

from __future__ import annotations

from collections.abc import Mapping


PUBLIC_CLIMATE_DISPLAY_NAMES: Mapping[str, Mapping[str, str]] = {
    "room_modes": {
        "automatic": "Автоматически",
        "manual": "Вручную",
        "unknown": "Режим неизвестен",
    },
    "device_kinds": {
        "air_conditioner": "Кондиционер",
        "radiator_thermostat": "Термоголовка радиатора",
        "humidifier": "Увлажнитель",
        "floor_heating": "Тёплый пол",
        "temperature_sensor": "Датчик температуры",
        "humidity_sensor": "Датчик влажности",
    },
    "control_scopes": {
        "observed": "Только наблюдение",
        "canary": "Пробное управление",
        "managed": "Управление HASC",
    },
    "device_capabilities": {
        "power": "Включение и выключение",
        "target_temperature": "Установка температуры",
        "target_humidity": "Установка влажности",
        "hvac_mode": "Режим обогрева и охлаждения",
        "fan_mode": "Скорость вентилятора",
        "auto_manual": "Автоматический и ручной режим",
        "target_strategy": "Характер работы",
        "cooldown": "Защита от частых переключений",
        "physical_feedback": "Подтверждение работы устройства",
    },
    "device_states": {
        "working": "Работает",
        "idle": "Ожидание",
        "off": "Выключено",
        "unavailable": "Недоступно",
        "unknown": "Состояние неизвестно",
    },
    "data_statuses": {
        "current": "Данные актуальны",
        "stale": "Данные устарели",
        "unavailable": "Данных нет",
    },
    "blocked_reasons": {
        "bridge_disabled": "Управление климатом выключено",
        "shadow_only": "Включена только проверка без команд",
        "room_not_selected": "Комната не выбрана для управления",
        "state_stale": "Данные о климате устарели",
        "registry_mismatch": "Настройка устройств не совпадает",
        "authority_not_ready": "Климатический модуль не готов к управлению",
        "device_unavailable": "Устройство недоступно",
        "actions_unsupported": "Устройство не поддерживает нужные действия",
        "evidence_not_ready": "Проверка безопасности ещё не завершена",
        "operation_pending": "Предыдущее действие ещё проверяется",
    },
    "contour_reasons": {
        "room_state_unavailable": "Нет данных о комнате",
        "state_stale": "Данные о климате устарели",
        "device_unavailable": "Устройство недоступно",
        "engine_not_automatic": "В климатическом модуле выключена автоматика",
        "authority_not_ready": "Климатический модуль не готов к управлению",
        "target_temperature_differs": "Заданная температура ещё не применена",
        "target_humidity_differs": "Заданная влажность ещё не применена",
        "target_strategy_unavailable": "Характер работы не поддерживается",
        "target_strategy_differs": "Характер работы ещё не применён",
    },
    "contour_kinds": {"climate": "Климат"},
    "contour_modes": {
        "disabled": "Выключен в HASC",
        "observe": "Наблюдение",
        "automatic": "Автоматически",
    },
    "contour_statuses": {
        "disabled": "Выключен",
        "ready": "Работает",
        "attention": "Требует внимания",
        "unavailable": "Недоступен",
        "stale": "Данные устарели",
    },
    "room_statuses": {
        "ready": "Доступна",
        "unavailable": "Недоступна",
    },
    "strategies": {
        "soft": "Мягко и тихо",
        "normal": "Обычно",
        "aggressive": "Быстро",
        "unknown": "Неизвестно",
    },
    "profiles": {
        "day": "День",
        "night": "Ночь",
    },
}

_WORKING_DEVICE_STATES = frozenset(
    {
        "active",
        "cool",
        "cooling",
        "dry",
        "fan_only",
        "heat",
        "heating",
        "on",
        "open",
    }
)
_IDLE_DEVICE_STATES = frozenset({"idle", "standby"})
_OFF_DEVICE_STATES = frozenset({"closed", "disabled", "off"})


def public_climate_display_names(
    *,
    include_room_data_statuses: bool = True,
) -> dict[str, dict[str, str]]:
    """Return a detached fixed code-to-name catalog for public responses."""

    result = {
        category: dict(names)
        for category, names in PUBLIC_CLIMATE_DISPLAY_NAMES.items()
    }
    if not include_room_data_statuses:
        result.pop("data_statuses")
    return result


def public_room_mode(value: object) -> str:
    """Normalize a private engine room mode to one stable HASC code."""

    if value in {"auto", "forced_auto_only"}:
        return "automatic"
    if value == "manual":
        return "manual"
    return "unknown"


def public_room_data_status(*, present: bool, fresh: bool) -> str:
    """Describe whether one room has current, stale, or no factual data."""

    if not present:
        return "unavailable"
    if not fresh:
        return "stale"
    return "current"


def public_device_state(value: object, *, available: bool) -> str:
    """Normalize arbitrary engine device state without echoing it to clients."""

    if not available:
        return "unavailable"
    normalized = value.strip().lower() if isinstance(value, str) else ""
    if normalized in _WORKING_DEVICE_STATES:
        return "working"
    if normalized in _IDLE_DEVICE_STATES:
        return "idle"
    if normalized in _OFF_DEVICE_STATES:
        return "off"
    return "unknown"


def public_strategy(value: object) -> str:
    """Return only a known HASC strategy code."""

    if value in {"soft", "normal", "aggressive"}:
        return str(value)
    return "unknown"
