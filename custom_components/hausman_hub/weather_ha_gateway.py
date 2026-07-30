"""Explicit read-only Home Assistant gateway for weather response actions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def async_weather_forecast(
    hass: HomeAssistant, entity_id: str, forecast_type: str
) -> object:
    """Read one forecast response; never dispatch a physical device command."""

    services = getattr(hass, "services", None)
    if services is None:
        return []
    has_service = getattr(services, "has_service", None)
    if callable(has_service) and not has_service("weather", "get_forecasts"):
        return []
    async_call = getattr(services, "async_call", None)
    if not callable(async_call):
        return []
    try:
        response = await async_call(
            "weather",
            "get_forecasts",
            {"entity_id": [entity_id], "type": forecast_type},
            blocking=True,
            return_response=True,
        )
    except Exception:
        return []
    if not isinstance(response, Mapping):
        return []
    entity_response = response.get(entity_id)
    if not isinstance(entity_response, Mapping):
        return []
    return entity_response.get("forecast", [])
