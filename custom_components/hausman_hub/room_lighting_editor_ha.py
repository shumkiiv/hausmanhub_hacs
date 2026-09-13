"""Home Assistant adapter for the room lighting editor.

It turns the entity and device registries plus the freshest states into plain
``EditorDevice`` objects. The editor service stays free of Home Assistant
imports, so this is the only place that reads registries or live states.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .application.room_lighting_editor import EditorDevice
from .domain.room_lighting import LightKind, RoomLightingConfig

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def editor_devices(
    hass: HomeAssistant, config: RoomLightingConfig
) -> tuple[EditorDevice, ...]:
    """Return every editor device referenced by one room configuration."""

    entity_registry = getattr(hass, "entity_registry", None)
    device_registry = getattr(hass, "device_registry", None)
    devices: list[EditorDevice] = []
    for target in config.devices.light_targets:
        device = _entity_device(
            hass,
            entity_registry,
            device_registry,
            entity_id=target.entity_id,
            kind="light" if target.kind is LightKind.LIGHT else "switch",
            fallback_label=target.name,
            supports_brightness=target.brightness,
            supports_color_temperature=target.color_temperature,
        )
        if device is not None:
            devices.append(device)
    for sensor in config.devices.sensors:
        device = _entity_device(
            hass,
            entity_registry,
            device_registry,
            entity_id=sensor.entity_id,
            kind="sensor",
            fallback_label=sensor.name,
        )
        if device is not None:
            devices.append(device)
    power = config.devices.power_switch
    if power is not None:
        device = _entity_device(
            hass,
            entity_registry,
            device_registry,
            entity_id=power.entity_id,
            kind="switch",
            fallback_label=power.name,
        )
        if device is not None:
            devices.append(device)
    for wireless in config.devices.wireless_switches:
        entry = _device_by_id(device_registry, wireless.device_id)
        physical = (
            _text(getattr(entry, "name_by_user", None))
            or _text(getattr(entry, "name", None))
            or wireless.name
        )
        devices.append(
            EditorDevice(
                kind="switch",
                entity_id=None,
                physical_device_label=physical,
                channel_label=None,
                friendly_name=wireless.name,
                available=True,
                gestures=tuple(wireless.press_types),
            )
        )
    return tuple(devices)


def _entity_device(
    hass: HomeAssistant,
    entity_registry: Any,
    device_registry: Any,
    *,
    entity_id: str | None,
    kind: str,
    fallback_label: str,
    supports_brightness: bool = False,
    supports_color_temperature: bool = False,
) -> EditorDevice | None:
    if not entity_id:
        return None
    state = _state(hass, entity_id)
    entry = _entity_entry(entity_registry, entity_id)
    device_entry = _device_entry(device_registry, entry)
    user_label = _text(getattr(entry, "name", None)) or fallback_label
    physical = (
        _text(getattr(device_entry, "name_by_user", None))
        or _text(getattr(device_entry, "name", None))
    )
    friendly = (
        _text((getattr(state, "attributes", {}) or {}).get("friendly_name"))
        if state is not None
        else None
    )
    channel = _text(getattr(entry, "original_name", None))
    if channel is not None and channel == physical:
        channel = None
    available = state is not None and _state_name(state) not in {
        "unavailable",
        "unknown",
    }
    return EditorDevice(
        kind=kind,
        entity_id=entity_id,
        user_label=user_label,
        physical_device_label=physical,
        channel_label=channel,
        friendly_name=friendly,
        available=available,
        supports_brightness=supports_brightness,
        supports_color_temperature=supports_color_temperature,
    )


def _state(hass: HomeAssistant, entity_id: str) -> Any:
    states = getattr(hass, "states", None)
    getter = getattr(states, "get", None)
    return getter(entity_id) if callable(getter) else None


def _state_name(state: Any) -> str:
    return str(getattr(state, "state", "unknown")).lower()


def _entity_entry(registry: Any, entity_id: str) -> Any:
    entities = getattr(registry, "entities", None)
    if isinstance(entities, dict):
        if entity_id in entities:
            return entities[entity_id]
        for entry in entities.values():
            if getattr(entry, "entity_id", None) == entity_id:
                return entry
    return None


def _device_entry(registry: Any, entry: Any) -> Any:
    device_id = getattr(entry, "device_id", None)
    return _device_by_id(registry, device_id)


def _device_by_id(registry: Any, device_id: object) -> Any:
    devices = getattr(registry, "devices", None)
    if isinstance(devices, dict) and isinstance(device_id, str):
        return devices.get(device_id)
    return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = ["editor_devices"]
