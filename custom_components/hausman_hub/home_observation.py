"""Home Assistant adapter for the local, aggregate-only home summary.

This adapter reads local registries and current state labels only. It neither
creates nor changes anything, and it passes only a category and an availability
label to the application layer. Names, identifiers, measurements, attributes,
and history stay inside Home Assistant and are never returned.
"""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry, device_registry, entity_registry

from .application.observation import create_home_summary
from .domain.observation import Availability, HomeSummary, RegisteredEntity


def collect_home_summary(hass: HomeAssistant) -> HomeSummary:
    """Return only approved local totals from Home Assistant's registries."""

    areas = area_registry.async_get(hass)
    devices = device_registry.async_get(hass)
    entities = entity_registry.async_get(hass)

    return create_home_summary(
        areas_count=len(areas.areas),
        devices_count=len(devices.devices),
        entities=(
            RegisteredEntity(
                domain=entry.domain,
                availability=_availability_for(hass.states.get(entry.entity_id)),
            )
            for entry in entities.entities.values()
        ),
    )


def _availability_for(state: object | None) -> Availability:
    """Classify one local state immediately without retaining its details."""

    if state is None:
        return "not_reported"
    state_value = getattr(state, "state", None)
    if state_value is None:
        return "unknown"
    if state_value == STATE_UNAVAILABLE:
        return "unavailable"
    if state_value == STATE_UNKNOWN:
        return "unknown"
    return "available"
