"""Home Assistant Area Registry adapter for canonical room settings."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import TYPE_CHECKING

from .application.tablet_preferences import (
    ROOM_TYPE_ICONS,
    room_type_from_icon,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class RoomSettingsAreaViolation(ValueError):
    """The requested room set no longer matches the HA Area Registry."""


def room_settings_snapshot(
    hass: HomeAssistant,
    stored: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    """Merge durable presentation fields with the current HA room catalog."""

    areas = _area_entries(hass)
    known_ids = {str(area.id) for area in areas}
    configured = [
        deepcopy(dict(item))
        for room_id, item in stored.items()
        if room_id in known_ids
    ]
    configured.sort(key=lambda item: (int(item["order"]), str(item["roomId"])))
    result = configured
    configured_ids = {str(item["roomId"]) for item in configured}
    next_order = max((int(item["order"]) for item in configured), default=-1) + 1
    for area in sorted(areas, key=lambda item: str(getattr(item, "name", ""))):
        area_id = str(area.id)
        if area_id in configured_ids:
            continue
        icon = getattr(area, "icon", None)
        room_type = room_type_from_icon(icon)
        result.append(
            {
                "roomId": area_id,
                "type": room_type,
                "icon": ROOM_TYPE_ICONS[room_type],
                "order": next_order,
                "visible": True,
            }
        )
        next_order += 1
    return result


async def async_apply_room_icons(
    hass: HomeAssistant,
    rooms: list[dict[str, object]],
) -> Callable[[], Awaitable[object]]:
    """Apply canonical icons and return a best-effort rollback callback."""

    registry = _area_registry(hass)
    areas = {str(area.id): area for area in _registry_values(registry)}
    requested_ids = {str(room["roomId"]) for room in rooms}
    if requested_ids != set(areas):
        raise RoomSettingsAreaViolation("room catalog changed")
    originals = {
        room_id: getattr(area, "icon", None) for room_id, area in areas.items()
    }

    async def rollback() -> object:
        for room_id, icon in originals.items():
            registry.async_update(room_id, icon=icon)
        return None

    try:
        for room in rooms:
            room_id = str(room["roomId"])
            icon = str(room["icon"])
            registry.async_update(room_id, icon=icon)
            updated = _area_entry(registry, room_id)
            if updated is None or getattr(updated, "icon", None) != icon:
                raise RoomSettingsAreaViolation("room icon read-back failed")
    except Exception:
        await rollback()
        raise
    return rollback


def _area_registry(hass: HomeAssistant) -> object:
    from homeassistant.helpers import area_registry

    return area_registry.async_get(hass)


def _area_entries(hass: HomeAssistant) -> list[object]:
    return _registry_values(_area_registry(hass))


def _registry_values(registry: object) -> list[object]:
    list_areas = getattr(registry, "async_list_areas", None)
    if callable(list_areas):
        return list(list_areas())
    areas = getattr(registry, "areas", {})
    return list(areas.values()) if isinstance(areas, Mapping) else []


def _area_entry(registry: object, area_id: str) -> object | None:
    getter = getattr(registry, "async_get_area", None)
    if callable(getter):
        return getter(area_id)
    areas = getattr(registry, "areas", {})
    return areas.get(area_id) if isinstance(areas, Mapping) else None
