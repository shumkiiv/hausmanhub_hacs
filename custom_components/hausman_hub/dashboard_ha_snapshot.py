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

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .application.scenario_service import ScenarioService


def _non_empty_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


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


async def async_dashboard_snapshot(
    hass: HomeAssistant,
    scenario_service: ScenarioService | None = None,
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
    return build_dashboard_snapshot(
        areas=area_values,
        devices=device_values,
        entities=entity_values,
        scenarios=await _dashboard_scenarios(scenario_service),
        generated_at_ms=int(local_now.timestamp() * 1000),
        local_iso=local_now.isoformat(),
        home_name=_non_empty_string(getattr(hass.config, "location_name", None))
        or "Дом",
    )
