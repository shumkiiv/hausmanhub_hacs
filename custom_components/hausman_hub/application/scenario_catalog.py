"""Build the live device/action catalog for HausmanHub scenarios."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from .scenarios import ScenarioCatalog, ScenarioDeviceAction, ScenarioDeviceEntry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


SCENARIO_CATALOG_DOMAINS = frozenset(
    {
        "button",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "light",
        "lock",
        "media_player",
        "switch",
        "vacuum",
        "valve",
        "water_heater",
    }
)


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


def _registry_entry(registry: object, key: str, collection_name: str) -> object | None:
    collection = getattr(registry, collection_name, None)
    if collection is None:
        return None
    return collection.get(key)


async def async_build_scenario_catalog(hass: HomeAssistant) -> ScenarioCatalog:
    """Build a live catalog of controllable devices for the scenario editor.

    Each controllable entity becomes its own target. This keeps multi-channel
    switches and multi-entity devices predictable: every entity has a stable
    targetId and its own entity_id for command resolution.
    """

    device_registry = hass.data.get("device_registry")
    entity_registry = hass.data.get("entity_registry")
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
        if not actions:
            continue

        target_id = _stable_target_id_from_entity(entity_id)
        name = _friendly_name(state)

        registry_entry = None
        if entity_registry is not None:
            registry_entry = _registry_entry(
                entity_registry, entity_id, "entities"
            )
        device_id: str | None = None
        if registry_entry is not None:
            device_id = getattr(registry_entry, "device_id", None)
        if device_id is not None and device_registry is not None:
            device_entry = _registry_entry(device_registry, device_id, "devices")
            if device_entry is not None:
                device_name = getattr(device_entry, "name_by_user", None) or getattr(
                    device_entry, "name", None
                )
                if isinstance(device_name, str) and device_name:
                    if name and name != device_name:
                        name = f"{device_name} · {name}"
                    else:
                        name = device_name

        devices[target_id] = ScenarioDeviceEntry(
            target_id=target_id,
            name=name,
            entity_id=entity_id,
            actions=actions,
        )

    return ScenarioCatalog(devices=devices, scenarios={})
