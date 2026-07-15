"""Home Assistant boundary for the read-only HausMan Hub integration.

It creates only nine diagnostic count sensors from the approved aggregate
summary. It has no services, device connections, or execution routes, and its
separate local view remains authenticated and GET-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.configuration import ConfigurationViolation, effective_configuration

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load the safe count display without acquiring runtime authority."""

    try:
        effective_configuration(entry.data, entry.options)
    except ConfigurationViolation:
        _clear_restored_hasc_records_for_invalid_entry(hass, entry)
        return False

    # Imports stay at the outer boundary so framework-independent tests can run
    # without Home Assistant itself.
    from homeassistant.const import Platform

    from .local_summary import register_local_summary_access

    await hass.config_entries.async_forward_entry_setups(entry, (Platform.SENSOR,))
    register_local_summary_access(hass, entry)
    return True


def _clear_restored_hasc_records_for_invalid_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Remove only stale HASC count records when saved settings are unsafe.

    Home Assistant can restore previous entity states before an integration gets
    a chance to reject invalid persisted settings. Clearing only the records
    owned by this HASC entry makes the rejection fail closed without changing
    devices, services, other entities, or anything outside HASC. A later safe
    reload creates the same fixed nine count sensors again.
    """

    from homeassistant.core import callback
    from homeassistant.helpers import entity_registry
    from homeassistant.helpers.start import async_at_started

    @callback
    def clear_hasc_records_after_start(_: HomeAssistant) -> None:
        entities = entity_registry.async_get(hass)
        entries = entity_registry.async_entries_for_config_entry(
            entities,
            entry.entry_id,
        )
        for registered_entity in entries:
            hass.states.async_remove(registered_entity.entity_id)
            entities.async_remove(registered_entity.entity_id)

    # The entity registry writes unavailable placeholders during startup, so
    # wait for that normal framework step before cleanup. A running system has
    # already passed startup and must clear the stale HASC records immediately.
    if getattr(hass, "is_running", False):
        clear_hasc_records_after_start(hass)
    else:
        async_at_started(hass, clear_hasc_records_after_start)


def _clear_hasc_state_values(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear only current state values owned by one HASC setup."""

    from homeassistant.helpers import entity_registry

    entities = entity_registry.async_get(hass)
    for registered_entity in entity_registry.async_entries_for_config_entry(
        entities,
        entry.entry_id,
    ):
        hass.states.async_remove(registered_entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the count display, clear its values, and close its local summary."""

    from homeassistant.const import Platform

    from .local_summary import clear_local_summary_access

    unloaded = await hass.config_entries.async_unload_platforms(entry, (Platform.SENSOR,))
    if unloaded:
        _clear_hasc_state_values(hass, entry)
        clear_local_summary_access(hass, entry)
    return unloaded
