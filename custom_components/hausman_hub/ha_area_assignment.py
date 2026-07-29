"""Atomic Home Assistant registry adapter for physical room assignments."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from .application.climate_area_assignment import (
    ClimateAreaAssignmentTarget,
    ClimateAreaAssignmentViolation,
)
from .ha_area_ids import stable_area_room_id

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantAreaAssignmentService:
    """Move physical devices, or entity-only sources, to HA areas atomically."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_assign(
        self,
        targets: tuple[ClimateAreaAssignmentTarget, ...],
    ) -> dict[str, object]:
        """Preflight every target, apply one batch, and roll back on failure."""

        try:
            area_registry = importlib.import_module(
                "homeassistant.helpers.area_registry"
            ).async_get(self._hass)
            device_registry = importlib.import_module(
                "homeassistant.helpers.device_registry"
            ).async_get(self._hass)
            entity_registry = importlib.import_module(
                "homeassistant.helpers.entity_registry"
            ).async_get(self._hass)
        except (AttributeError, ModuleNotFoundError) as error:
            raise ClimateAreaAssignmentViolation(
                "Home Assistant registries are unavailable",
                code="registry_unavailable",
            ) from error

        area_by_room = _area_ids_by_public_room(area_registry)
        entities = _registry_values(entity_registry, "entities")
        entity_by_id = {
            entry.entity_id: entry
            for entry in entities
            if isinstance(getattr(entry, "entity_id", None), str)
        }
        devices = _registry_values(device_registry, "devices")
        device_by_id = {
            entry.id: entry
            for entry in devices
            if isinstance(getattr(entry, "id", None), str)
        }

        device_targets: dict[str, str | None] = {}
        entity_targets: dict[str, str | None] = {}
        for target in targets:
            area_id = (
                area_by_room.get(target.room_id)
                if target.room_id
                else None
            )
            if target.room_id and area_id is None:
                raise ClimateAreaAssignmentViolation(
                    "Home Assistant area no longer exists",
                    code="snapshot_changed",
                )
            for entity_id in target.entity_ids:
                entity = entity_by_id.get(entity_id)
                if entity is None:
                    raise ClimateAreaAssignmentViolation(
                        "Home Assistant entity no longer exists",
                        code="snapshot_changed",
                    )
                device_id = getattr(entity, "device_id", None)
                if isinstance(device_id, str) and device_id:
                    if device_id not in device_by_id:
                        raise ClimateAreaAssignmentViolation(
                            "Home Assistant device no longer exists",
                            code="snapshot_changed",
                        )
                    previous = device_targets.setdefault(device_id, area_id)
                    if previous != area_id:
                        raise ClimateAreaAssignmentViolation(
                            "one physical device has conflicting room assignments"
                        )
                else:
                    previous = entity_targets.setdefault(entity_id, area_id)
                    if previous != area_id:
                        raise ClimateAreaAssignmentViolation(
                            "one entity has conflicting room assignments"
                        )

        sibling_entities = {
            entry.entity_id: entry
            for entry in entities
            if getattr(entry, "device_id", None) in device_targets
            and isinstance(getattr(entry, "entity_id", None), str)
        }
        original_devices = {
            device_id: getattr(device_by_id[device_id], "area_id", None)
            for device_id in device_targets
        }
        affected_entities = {**sibling_entities}
        affected_entities.update(
            {entity_id: entity_by_id[entity_id] for entity_id in entity_targets}
        )
        original_entities = {
            entity_id: getattr(entry, "area_id", None)
            for entity_id, entry in affected_entities.items()
        }

        update_device = getattr(device_registry, "async_update_device", None)
        update_entity = getattr(entity_registry, "async_update_entity", None)
        if not callable(update_device) or not callable(update_entity):
            raise ClimateAreaAssignmentViolation(
                "Home Assistant registry updates are unavailable",
                code="registry_unavailable",
            )
        try:
            for device_id, area_id in device_targets.items():
                update_device(device_id, area_id=area_id)
            for entity_id in sibling_entities:
                update_entity(entity_id, area_id=None)
            for entity_id, area_id in entity_targets.items():
                update_entity(entity_id, area_id=area_id)
        except Exception as error:
            try:
                for device_id, area_id in original_devices.items():
                    update_device(device_id, area_id=area_id)
                for entity_id, area_id in original_entities.items():
                    update_entity(entity_id, area_id=area_id)
            except Exception as rollback_error:
                raise ClimateAreaAssignmentViolation(
                    "Home Assistant registry rollback failed",
                    code="registry_unavailable",
                ) from rollback_error
            raise ClimateAreaAssignmentViolation(
                "Home Assistant rejected the area assignment",
                code="registry_unavailable",
            ) from error

        return {
            "status": "saved",
            "updated_devices": len(device_targets),
            "updated_entities": len(entity_targets),
            "room_ids": sorted({target.room_id for target in targets if target.room_id}),
            "cleared_assignments": sum(not target.room_id for target in targets),
        }


def _area_ids_by_public_room(area_registry: object) -> dict[str, str]:
    list_areas = getattr(area_registry, "async_list_areas", None)
    raw = (
        list_areas()
        if callable(list_areas)
        else _registry_values(area_registry, "areas")
    )
    areas = sorted(
        (area for area in raw if isinstance(getattr(area, "id", None), str)),
        key=lambda area: area.id,
    )
    used: set[str] = set()
    result: dict[str, str] = {}
    for area in areas:
        room_id = stable_area_room_id(area.id, used)
        used.add(room_id)
        result[room_id] = area.id
    return result


def _registry_values(registry: object, collection_name: str) -> tuple[object, ...]:
    collection = getattr(registry, collection_name, None)
    values = getattr(collection, "values", None)
    return tuple(values()) if callable(values) else ()
