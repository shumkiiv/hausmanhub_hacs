"""Home Assistant boundary for the read-only HausMan Hub skeleton.

This module deliberately creates no entities, services, device connections, or
execution routes. It only exposes one authenticated local GET view with the
approved nine aggregate counts after validating the stored safety posture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.configuration import ConfigurationViolation, effective_configuration

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load a safe configuration without acquiring runtime authority."""

    try:
        effective_configuration(entry.data, entry.options)
    except ConfigurationViolation:
        return False

    # This outer adapter needs Home Assistant's HTTP API. Keeping this import
    # here lets the framework-independent safety tests run without HA itself.
    from .local_summary import register_local_summary_access

    register_local_summary_access(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the safe entry and make its local summary unavailable."""

    # See the setup import: the application layer remains Home Assistant-free.
    from .local_summary import clear_local_summary_access

    clear_local_summary_access(hass, entry)
    return True
