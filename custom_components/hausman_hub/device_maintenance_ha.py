"""Administrator-only Home Assistant device registry maintenance adapter."""

from __future__ import annotations

import importlib
import inspect
from typing import TYPE_CHECKING, Any

from .application.dashboard_snapshot import stable_public_id
from .device_maintenance_gateway import HomeAssistantDeviceIdentifier

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_IDENTIFY_WORDS = ("identify", "locate", "find", "идентифиц", "найти устройство")


class DeviceMaintenanceViolation(ValueError):
    """Reject an unsafe, stale, or unsupported registry operation."""

    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


def inventory_device_id(device_id: str) -> str:
    """Return the private-id-free identifier already used by inventory cards."""

    return stable_public_id("inventory", f"device:{device_id}")


def _values(registry: object, collection: str) -> tuple[object, ...]:
    raw = getattr(registry, collection, None)
    values = getattr(raw, "values", None)
    if callable(values):
        return tuple(values())
    list_method = getattr(registry, f"async_list_{collection}", None)
    return tuple(list_method()) if callable(list_method) else ()


def _entity_entries(entity_registry: object, device_id: str) -> tuple[object, ...]:
    return tuple(
        entry
        for entry in _values(entity_registry, "entities")
        if getattr(entry, "device_id", None) == device_id
    )


def _entry_name(entry: object) -> str:
    return str(
        getattr(entry, "name", None)
        or getattr(entry, "original_name", None)
        or getattr(entry, "entity_id", "Сущность")
    )


def _identify_entity(entries: tuple[object, ...]) -> object | None:
    for entry in entries:
        entity_id = str(getattr(entry, "entity_id", ""))
        if not entity_id.startswith("button.") or getattr(entry, "disabled_by", None):
            continue
        haystack = " ".join(
            (
                entity_id,
                str(getattr(entry, "name", "") or ""),
                str(getattr(entry, "original_name", "") or ""),
                str(getattr(entry, "translation_key", "") or ""),
            )
        ).lower()
        if any(word in haystack for word in _IDENTIFY_WORDS):
            return entry
    return None


def _scenario_target_ids(scenario: object) -> set[str]:
    definition = getattr(scenario, "definition", None)
    items = (
        *(getattr(definition, "triggers", ()) or ()),
        *(getattr(definition, "conditions", ()) or ()),
        *(getattr(definition, "actions", ()) or ()),
    )
    return {
        value
        for item in items
        if isinstance((value := getattr(item, "target_id", None)), str) and value
    }


async def _hausmanhub_uses(
    hass: HomeAssistant,
    entity_ids: set[str],
    device_id: str,
    context: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    uses: list[dict[str, str]] = []
    if context is None:
        context = await _usage_context(hass)
    entity_targets = context["entity_targets"]
    scenario_titles = context["scenario_titles"]
    target_ids = {
        target_id
        for entity_id in entity_ids
        for target_id in entity_targets.get(entity_id, ())  # type: ignore[union-attr]
    }
    titles = sorted(
        {
            title
            for target_id in target_ids
            for title in scenario_titles.get(target_id, ())  # type: ignore[union-attr]
        },
        key=str.casefold,
    )
    for title in titles:
        uses.append(
            {
                "kind": "scenario",
                "title": title,
                "detail": "Сценарий HausmanHub обращается к этому устройству.",
            }
        )

    climate_text = context["climate_text"]
    if any(entity_id in climate_text for entity_id in entity_ids):  # type: ignore[operator]
        uses.append(
            {
                "kind": "climate",
                "title": "Климатический контур",
                "detail": "Одна или несколько сущностей выбраны в настройке климата.",
            }
        )

    public_device_id = stable_public_id("device", f"device:{device_id}")
    if public_device_id in context["energy_selected"]:  # type: ignore[operator]
        uses.append(
            {
                "kind": "energy",
                "title": "Карточка энергии",
                "detail": "Устройство выбрано источником данных энергопотребления.",
            }
        )
    return uses


async def _usage_context(hass: HomeAssistant) -> dict[str, object]:
    data = getattr(hass, "data", {}).get("hausman_hub", {})
    scenario_service = data.get("scenario_service")
    catalog = getattr(scenario_service, "_catalog", None)
    entity_targets: dict[str, set[str]] = {}
    for target_id, entry in getattr(catalog, "devices", {}).items():
        entity_id = getattr(entry, "entity_id", None)
        if isinstance(entity_id, str):
            entity_targets.setdefault(entity_id, set()).add(target_id)
    scenario_titles: dict[str, set[str]] = {}
    list_scenarios = getattr(scenario_service, "async_list_scenarios", None)
    if callable(list_scenarios):
        for scenario in await list_scenarios():
            for target_id in _scenario_target_ids(scenario):
                scenario_titles.setdefault(target_id, set()).add(
                    str(getattr(scenario, "title", "Сценарий"))
                )

    runtime = data.get("climate_runtime")
    registry_payload = getattr(runtime, "async_registry_payload", None)
    if callable(registry_payload):
        try:
            climate = await registry_payload()
        except Exception:
            climate = None
        climate_text = repr(climate)
    else:
        climate_text = ""

    preferences = data.get("tablet_preferences_service")
    try:
        selected = set(preferences.energy["settings"]["selectedDeviceIds"])
    except (AttributeError, KeyError, TypeError):
        selected = set()
    return {
        "entity_targets": entity_targets,
        "scenario_titles": scenario_titles,
        "climate_text": climate_text,
        "energy_selected": selected,
    }


class HomeAssistantDeviceMaintenanceService:
    """Inspect and mutate native device registry records with explicit safeguards."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _registries(self) -> tuple[object, object, object]:
        try:
            area = importlib.import_module(
                "homeassistant.helpers.area_registry"
            ).async_get(self._hass)
            device = importlib.import_module(
                "homeassistant.helpers.device_registry"
            ).async_get(self._hass)
            entity = importlib.import_module(
                "homeassistant.helpers.entity_registry"
            ).async_get(self._hass)
        except (AttributeError, ModuleNotFoundError) as error:
            raise DeviceMaintenanceViolation(
                "Home Assistant registries are unavailable",
                code="registry_unavailable",
            ) from error
        return area, device, entity

    def _device(self, registry: object, public_id: object) -> object:
        if not isinstance(public_id, str) or not public_id:
            raise DeviceMaintenanceViolation("device id is required")
        device = next(
            (
                item
                for item in _values(registry, "devices")
                if inventory_device_id(str(getattr(item, "id", ""))) == public_id
            ),
            None,
        )
        if device is None:
            raise DeviceMaintenanceViolation(
                "Home Assistant device no longer exists",
                code="not_found",
            )
        return device

    async def async_snapshot(self) -> dict[str, object]:
        area_registry, device_registry, entity_registry = self._registries()
        usage_context = await _usage_context(self._hass)
        areas = sorted(
            (
                {"id": area.id, "name": area.name}
                for area in _values(area_registry, "areas")
                if isinstance(getattr(area, "id", None), str)
                and isinstance(getattr(area, "name", None), str)
            ),
            key=lambda item: item["name"].casefold(),
        )
        devices: dict[str, dict[str, object]] = {}
        for device in _values(device_registry, "devices"):
            raw_id = getattr(device, "id", None)
            if not isinstance(raw_id, str) or not raw_id:
                continue
            entries = _entity_entries(entity_registry, raw_id)
            entity_ids = {
                str(entry.entity_id)
                for entry in entries
                if isinstance(getattr(entry, "entity_id", None), str)
            }
            identify = _identify_entity(entries)
            uses = await _hausmanhub_uses(
                self._hass, entity_ids, raw_id, usage_context
            )
            config_entries = tuple(getattr(device, "config_entries", ()) or ())
            devices[inventory_device_id(raw_id)] = {
                "roomAreaId": getattr(device, "area_id", None),
                "name": str(
                    getattr(device, "name_by_user", None)
                    or getattr(device, "name", None)
                    or "Устройство"
                ),
                "haUrl": f"/config/devices/device/{raw_id}",
                "entityCount": len(entries),
                "entities": [
                    {
                        "id": str(getattr(entry, "entity_id", "")),
                        "name": _entry_name(entry),
                        "disabled": getattr(entry, "disabled_by", None) is not None,
                    }
                    for entry in sorted(entries, key=lambda item: _entry_name(item).casefold())
                ],
                "integrationCount": len(config_entries),
                "uses": uses,
                "used": bool(uses),
                "identifySupported": identify is not None,
                "identifyLabel": _entry_name(identify) if identify is not None else None,
                "deleteBlocked": bool(uses),
                "deleteBlockers": [use["title"] for use in uses],
            }
        return {"areas": areas, "devices": devices}

    async def async_update(self, payload: dict[str, object]) -> dict[str, object]:
        _area_registry, device_registry, entity_registry = self._registries()
        device = self._device(device_registry, payload.get("deviceId"))
        name = payload.get("name")
        area_id = payload.get("areaId")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 128:
            raise DeviceMaintenanceViolation("device name is invalid")
        if area_id is not None and (not isinstance(area_id, str) or len(area_id) > 128):
            raise DeviceMaintenanceViolation("area id is invalid")
        valid_areas = {
            getattr(area, "id", None) for area in _values(_area_registry, "areas")
        }
        if area_id is not None and area_id not in valid_areas:
            raise DeviceMaintenanceViolation("Home Assistant area no longer exists", code="not_found")
        update = getattr(device_registry, "async_update_device", None)
        update_entity = getattr(entity_registry, "async_update_entity", None)
        if not callable(update):
            raise DeviceMaintenanceViolation("device update is unavailable", code="registry_unavailable")
        update(device.id, name_by_user=name.strip(), area_id=area_id)
        if callable(update_entity):
            for entity in _entity_entries(entity_registry, device.id):
                if getattr(entity, "area_id", None) is not None:
                    update_entity(entity.entity_id, area_id=None)
        return {"status": "saved", "deviceId": payload["deviceId"]}

    async def async_identify(self, payload: dict[str, object]) -> dict[str, object]:
        _area_registry, device_registry, entity_registry = self._registries()
        device = self._device(device_registry, payload.get("deviceId"))
        identify = _identify_entity(_entity_entries(entity_registry, device.id))
        if identify is None:
            raise DeviceMaintenanceViolation(
                "physical identification is not supported",
                code="not_supported",
            )
        await HomeAssistantDeviceIdentifier(self._hass).async_identify(
            identify.entity_id
        )
        return {"status": "command_sent", "deviceId": payload["deviceId"]}

    async def async_delete(self, payload: dict[str, object]) -> dict[str, object]:
        _area_registry, device_registry, entity_registry = self._registries()
        device = self._device(device_registry, payload.get("deviceId"))
        if payload.get("confirmed") is not True:
            raise DeviceMaintenanceViolation("explicit confirmation is required", code="confirmation_required")
        entries = _entity_entries(entity_registry, device.id)
        entity_ids = {
            str(entry.entity_id)
            for entry in entries
            if isinstance(getattr(entry, "entity_id", None), str)
        }
        uses = await _hausmanhub_uses(self._hass, entity_ids, device.id)
        if uses:
            raise DeviceMaintenanceViolation(
                "device is still used by HausmanHub",
                code="device_in_use",
            )
        remove = getattr(device_registry, "async_remove_device", None)
        if not callable(remove):
            raise DeviceMaintenanceViolation("device removal is unavailable", code="registry_unavailable")
        result = remove(device.id)
        if inspect.isawaitable(result):
            await result
        return {"status": "deleted", "deviceId": payload["deviceId"]}
