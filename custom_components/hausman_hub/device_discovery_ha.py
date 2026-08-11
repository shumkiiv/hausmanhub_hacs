"""Home Assistant registry adapter for new-device discovery."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .application.dashboard_snapshot import stable_public_id
from .application.device_discovery import DiscoveredDevice, DiscoveryArea

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_VIRTUAL_INTEGRATIONS = frozenset({"climate_group", "smartir", "template", "yandex_smart_home"})
_ENERGY_CLASSES = frozenset({"power", "current", "energy", "voltage"})
_CLIMATE_CLASSES = frozenset({"temperature", "humidity"})


def device_discovery_snapshot(
    hass: HomeAssistant,
    *,
    energy_selected: frozenset[str] = frozenset(),
    dashboard_visible: frozenset[str] = frozenset(),
) -> tuple[tuple[DiscoveredDevice, ...], tuple[DiscoveryArea, ...]]:
    from homeassistant.helpers import area_registry, device_registry, entity_registry

    areas_registry = area_registry.async_get(hass)
    devices_registry = device_registry.async_get(hass)
    entities_registry = entity_registry.async_get(hass)
    areas = tuple(
        DiscoveryArea(str(area.id), str(area.name))
        for area in _values(areas_registry, "areas")
        if isinstance(getattr(area, "id", None), str)
        and isinstance(getattr(area, "name", None), str)
    )
    area_names = {area.area_id: area.name for area in areas}
    entities_by_device: dict[str, list[object]] = {}
    for entry in _values(entities_registry, "entities"):
        device_id = getattr(entry, "device_id", None)
        if (
            isinstance(device_id, str)
            and device_id
            and getattr(entry, "disabled_by", None) is None
        ):
            entities_by_device.setdefault(device_id, []).append(entry)
    result: list[DiscoveredDevice] = []
    for device in _values(devices_registry, "devices"):
        private_id = getattr(device, "id", None)
        if (
            not isinstance(private_id, str)
            or not private_id
            or getattr(device, "disabled_by", None) is not None
        ):
            continue
        entries = entities_by_device.get(private_id, [])
        if not entries:
            continue
        public_id = stable_public_id("device", f"device:{private_id}")
        domains = tuple(sorted({str(entry.entity_id).split(".", 1)[0] for entry in entries if "." in str(getattr(entry, "entity_id", ""))}))
        states = [
            hass.states.get(str(entry.entity_id))
            for entry in entries
            if isinstance(getattr(entry, "entity_id", None), str)
        ]
        present_states = [state for state in states if state is not None]
        if not present_states:
            status = "empty"
        elif all(str(state.state) in {"unknown", "unavailable"} for state in present_states):
            status = "unavailable"
        else:
            status = "available"
        area_id = _text(getattr(device, "area_id", None)) or next(
            (_text(getattr(entry, "area_id", None)) for entry in entries if _text(getattr(entry, "area_id", None))),
            None,
        )
        classes = {
            _enum_text(getattr(entry, "device_class", None))
            or _enum_text(getattr(state, "attributes", {}).get("device_class"))
            for entry, state in zip(entries, states, strict=False)
            if state is not None
        }
        units = {
            str(getattr(state, "attributes", {}).get("unit_of_measurement", ""))
            for state in present_states
        }
        integrations = {
            str(identifier[0])
            for identifier in (getattr(device, "identifiers", ()) or ())
            if isinstance(identifier, (tuple, list)) and len(identifier) == 2
        }
        result.append(
            DiscoveredDevice(
                private_device_id=private_id,
                device_id=public_id,
                title=(
                    _text(getattr(device, "name_by_user", None))
                    or _text(getattr(device, "name", None))
                    or "Новое устройство"
                ),
                room_id=area_id,
                room_name=area_names.get(area_id),
                kind="virtual" if integrations & _VIRTUAL_INTEGRATIONS else "physical",
                status=status,
                domains=domains,
                manufacturer=_text(getattr(device, "manufacturer", None)),
                model=_text(getattr(device, "model", None)),
                energy_eligible=bool(classes & _ENERGY_CLASSES or units & {"W", "A", "V", "Wh", "kWh"}),
                climate_eligible=bool(classes & _CLIMATE_CLASSES or set(domains) & {"climate", "humidifier", "fan"}),
                energy_selected=public_id in energy_selected,
                dashboard_visible=public_id in dashboard_visible,
            )
        )
    return tuple(result), areas


def assign_device_area(hass: HomeAssistant, device_id: str, area_id: str) -> None:
    """Assign one already discovered physical device to an existing HA area."""

    from homeassistant.helpers import area_registry, device_registry, entity_registry

    areas = area_registry.async_get(hass)
    devices = device_registry.async_get(hass)
    entities = entity_registry.async_get(hass)
    if not any(getattr(area, "id", None) == area_id for area in _values(areas, "areas")):
        raise ValueError("Home Assistant area no longer exists")
    device = next((item for item in _values(devices, "devices") if getattr(item, "id", None) == device_id), None)
    if device is None:
        raise ValueError("Home Assistant device no longer exists")
    update_device = getattr(devices, "async_update_device", None)
    update_entity = getattr(entities, "async_update_entity", None)
    if not callable(update_device) or not callable(update_entity):
        raise ValueError("Home Assistant registry updates are unavailable")
    original_device_area = getattr(device, "area_id", None)
    siblings = [entry for entry in _values(entities, "entities") if getattr(entry, "device_id", None) == device_id]
    original_entity_areas = {str(entry.entity_id): getattr(entry, "area_id", None) for entry in siblings}
    try:
        update_device(device_id, area_id=area_id)
        for entry in siblings:
            update_entity(str(entry.entity_id), area_id=None)
    except Exception as error:
        update_device(device_id, area_id=original_device_area)
        for entity_id, previous_area in original_entity_areas.items():
            update_entity(entity_id, area_id=previous_area)
        raise ValueError("Home Assistant rejected the area assignment") from error


def _values(registry: object, collection: str) -> tuple[object, ...]:
    raw = getattr(registry, collection, None)
    values = getattr(raw, "values", None)
    if callable(values):
        return tuple(values())
    list_method = getattr(registry, f"async_list_{collection}", None)
    return tuple(list_method()) if callable(list_method) else ()


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _enum_text(value: object) -> str | None:
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) and raw else None
