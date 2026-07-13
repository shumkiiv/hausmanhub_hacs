"""Home Assistant boundary for the read-only HausMan Hub skeleton.

This module deliberately creates no entities, services, device connections, or
runtime routes. It only refuses a config entry whose stored safety posture is
not the one defined by the framework-independent application layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.configuration import ConfigurationViolation, effective_configuration

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(_hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load a safe configuration without acquiring runtime authority."""

    try:
        effective_configuration(entry.data, entry.options)
    except ConfigurationViolation:
        return False
    return True


async def async_unload_entry(_hass: HomeAssistant, _entry: ConfigEntry) -> bool:
    """Unload the inert read-only skeleton."""

    return True
