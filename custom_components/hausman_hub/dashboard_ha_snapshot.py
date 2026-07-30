"""Home Assistant read adapter for the universal dashboard snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from .application.dashboard_snapshot import (
    DashboardArea,
    DashboardDevice,
    DashboardEntity,
    DashboardScenario,
    build_dashboard_snapshot,
)
from .application.device_presentation import zigbee2mqtt_image_url
from .domain.hub_settings import HausmanHubSettings


_PUBLIC_DIAGNOSTIC_DEVICE_CLASSES = frozenset(
    {
        "battery",
        "carbon_dioxide",
        "current",
        "energy",
        "gas",
        "humidity",
        "moisture",
        "power",
        "smoke",
        "temperature",
        "volatile_organic_compounds",
        "pm1",
        "pm10",
        "pm25",
        "voltage",
    }
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .application.scenario_service import ScenarioService


def _non_empty_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _enum_string(value: object) -> str | None:
    """Return a compact enum/string value without importing HA enum types."""

    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) and raw else None


def _device_integrations(device: object) -> tuple[str, ...]:
    """Project only integration domains, never registry identifiers."""

    integrations = {
        identifier[0]
        for identifier in (getattr(device, "identifiers", ()) or ())
        if isinstance(identifier, (tuple, list))
        and len(identifier) == 2
        and isinstance(identifier[0], str)
        and identifier[0]
    }
    return tuple(sorted(integrations))


def _friendly_name(state: object, registry_entry: object) -> str:
    attributes = getattr(state, "attributes", {})
    friendly_name = attributes.get("friendly_name")
    if isinstance(friendly_name, str) and friendly_name:
        return friendly_name
    for attribute_name in ("name", "original_name"):
        value = getattr(registry_entry, attribute_name, None)
        if isinstance(value, str) and value:
            return value
    return getattr(state, "entity_id", "")


def _entity_is_public(entry: object, attributes: dict[str, Any]) -> bool:
    """Keep device cards operational and remove HA maintenance controls.

    Configuration entities such as indicator LEDs, power-outage memory and
    alarm toggles are capabilities of their owning device, not separate tablet
    controls. Diagnostic measurements remain visible only when they convey a
    user-facing environmental, battery, safety, or energy value.
    """

    if getattr(entry, "hidden_by", None) is not None:
        return False
    category = _enum_string(getattr(entry, "entity_category", None))
    if category == "config":
        return False
    if category != "diagnostic":
        return True
    device_class = _enum_string(attributes.get("device_class"))
    return device_class in _PUBLIC_DIAGNOSTIC_DEVICE_CLASSES


def _registry_snapshot(hass: HomeAssistant) -> tuple[object, object, object]:
    """Resolve HA registries at the outer boundary only."""

    from homeassistant.helpers import (  # noqa: PLC0415
        area_registry,
        device_registry,
        entity_registry,
    )

    return (
        area_registry.async_get(hass),
        device_registry.async_get(hass),
        entity_registry.async_get(hass),
    )


def _local_now() -> datetime:
    """Read HA-local time without coupling the pure projector to HA."""

    from homeassistant.util import dt as dt_util  # noqa: PLC0415

    return dt_util.now()


async def _dashboard_scenarios(
    scenario_service: ScenarioService | None,
) -> tuple[DashboardScenario, ...]:
    if scenario_service is None:
        return ()
    try:
        scenarios = await scenario_service.async_list_scenarios()
    except Exception:
        return ()
    return tuple(
        DashboardScenario(
            scenario_id=scenario.id,
            title=scenario.title,
            group=_non_empty_string(scenario.group),
            description=_non_empty_string(scenario.description),
            icon=_non_empty_string(scenario.icon),
            requires_confirmation=bool(scenario.requires_confirmation),
            favorite=bool(scenario.favorite),
            danger=bool(scenario.danger),
        )
        for scenario in scenarios
        if bool(getattr(scenario, "enabled", False))
    )


def _attach_catalog_actions(
    payload: dict[str, object],
    scenario_service: ScenarioService | None,
) -> None:
    """Attach only allowlisted catalog actions to their owning physical card."""

    catalog = getattr(scenario_service, "_catalog", None)
    catalog_devices = getattr(catalog, "devices", {})
    by_entity = {
        device.entity_id: device
        for device in catalog_devices.values()
        if isinstance(getattr(device, "entity_id", None), str)
    }
    action_count = 0
    for device_payload in payload.get("devices", []):
        entity_ids = [
            detail.get("entityId")
            for detail in device_payload.get("details", [])
            if isinstance(detail, dict)
        ]
        actions: list[dict[str, object]] = []
        multiple_entities = len(entity_ids) > 1
        for entity_id in entity_ids:
            catalog_device = by_entity.get(entity_id)
            if catalog_device is None:
                continue
            for action in catalog_device.actions:
                actions.append(
                    {
                        "id": f"{catalog_device.target_id}:{action.action_id}",
                        "title": (
                            f"{catalog_device.name} · {action.title}"
                            if multiple_entities
                            else action.title
                        ),
                        "confirmation": action.action_id in {"lock", "unlock"},
                        "payload": {
                            "targetId": catalog_device.target_id,
                            "actionId": action.action_id,
                        },
                    }
                )
        device_payload["actions"] = actions
        action_count += len(actions)
    capabilities = payload.get("capabilities")
    if isinstance(capabilities, dict):
        capabilities["actions"] = action_count > 0


async def async_dashboard_snapshot(
    hass: HomeAssistant,
    scenario_service: ScenarioService | None = None,
    energy_settings: HausmanHubSettings | None = None,
) -> dict[str, object]:
    """Collect current HA registries and project one side-effect-free payload."""

    areas, devices, entities = _registry_snapshot(hass)

    area_values = tuple(
        DashboardArea(
            area_id=area.id,
            name=area.name,
            icon=_non_empty_string(getattr(area, "icon", None)),
        )
        for area in areas.areas.values()
    )
    device_values = tuple(
        DashboardDevice(
            device_id=device.id,
            name=(
                _non_empty_string(getattr(device, "name_by_user", None))
                or _non_empty_string(getattr(device, "name", None))
                or "Устройство"
            ),
            area_id=_non_empty_string(getattr(device, "area_id", None)),
            model=_non_empty_string(getattr(device, "model", None)),
            manufacturer=_non_empty_string(getattr(device, "manufacturer", None)),
            entry_type=_enum_string(getattr(device, "entry_type", None)),
            integrations=_device_integrations(device),
            disabled=getattr(device, "disabled_by", None) is not None,
            image_url=zigbee2mqtt_image_url(
                getattr(device, "model_id", None),
                getattr(device, "identifiers", ()) or (),
            ),
        )
        for device in devices.devices.values()
    )
    device_area_by_id = {device.device_id: device.area_id for device in device_values}
    entity_values: list[DashboardEntity] = []
    for entry in entities.entities.values():
        if getattr(entry, "disabled_by", None) is not None:
            continue
        entity_id = getattr(entry, "entity_id", "")
        if not isinstance(entity_id, str) or "." not in entity_id:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            continue
        attributes = getattr(state, "attributes", {})
        if not isinstance(attributes, dict):
            attributes = dict(attributes) if attributes is not None else {}
        if not _entity_is_public(entry, attributes):
            continue
        device_id = _non_empty_string(getattr(entry, "device_id", None))
        explicit_area_id = _non_empty_string(getattr(entry, "area_id", None))
        entity_values.append(
            DashboardEntity(
                entity_id=entity_id,
                domain=entity_id.split(".", 1)[0],
                state=str(getattr(state, "state", "unknown")),
                name=_friendly_name(state, entry),
                attributes=attributes,
                device_id=device_id,
                area_id=explicit_area_id or device_area_by_id.get(device_id),
            )
        )

    local_now = _local_now()
    payload = build_dashboard_snapshot(
        areas=area_values,
        devices=device_values,
        entities=entity_values,
        scenarios=await _dashboard_scenarios(scenario_service),
        generated_at_ms=int(local_now.timestamp() * 1000),
        local_iso=local_now.isoformat(),
        home_name=_non_empty_string(getattr(hass.config, "location_name", None))
        or "Дом",
        energy_settings=energy_settings,
    )
    _attach_catalog_actions(payload, scenario_service)
    return payload
