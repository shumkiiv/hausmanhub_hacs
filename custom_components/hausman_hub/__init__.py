"""Home Assistant boundary for the HausmanHub integration.

It always creates the nine diagnostic count sensors. An explicitly armed
legacy canary may additionally control one ``input_boolean`` helper. The
separate climate facade persists logical bindings and can use only two fixed
Climate API paths in shadow or one-room canary mode. HausmanHub registers no service
and never calls a Home Assistant climate entity directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.configuration import ConfigurationViolation, effective_configuration

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load observation, local climate facade, and optional narrow canaries."""

    configured_entry_ids = tuple(
        configured_entry.entry_id
        for configured_entry in hass.config_entries.async_entries(entry.domain)
    )
    if configured_entry_ids != (entry.entry_id,):
        await _close_running_duplicate_hausmanhub_entries(hass, entry.domain)
        _clear_restored_hausmanhub_records(hass, configured_entry_ids + (entry.entry_id,))
        return False

    try:
        configuration = effective_configuration(entry.data, entry.options)
    except ConfigurationViolation:
        _clear_restored_hausmanhub_records(hass, (entry.entry_id,))
        return False

    # Imports stay at the outer boundary so framework-independent tests can run
    # without Home Assistant itself.
    from homeassistant.const import Platform
    from homeassistant.util import dt as dt_util

    from .application.climate_runtime import ClimateRuntime
    from .climate_api import register_climate_api
    from .climate_evidence_storage import HomeAssistantClimateEvidenceStore
    from .climate_ha_executor import HomeAssistantClimateCallExecutor
    from .climate_protection_storage import HomeAssistantClimateProtectionStore
    from .climate_storage import HomeAssistantClimateRegistryStore
    from .contour_storage import HomeAssistantContourStore
    from .local_summary import register_local_summary_access

    await hass.config_entries.async_forward_entry_setups(
        entry,
        (Platform.SENSOR, Platform.SWITCH),
    )
    if configuration.local_summary_enabled:
        register_local_summary_access(hass, entry)
    climate_client = None
    if configuration.climate_bridge_target is not None:
        from .climate_bridge import ClimateApiClient

        climate_client = ClimateApiClient(hass, configuration.climate_bridge_target)
    climate_runtime = ClimateRuntime(
        entry_id=entry.entry_id,
        configuration=configuration,
        registry_store=HomeAssistantClimateRegistryStore(hass, entry.entry_id),
        bridge_client=climate_client,
        evidence_store=HomeAssistantClimateEvidenceStore(hass, entry.entry_id),
        contour_store=HomeAssistantContourStore(hass, entry.entry_id),
        protection_store=HomeAssistantClimateProtectionStore(hass, entry.entry_id),
        trial_executor=HomeAssistantClimateCallExecutor(hass),
        local_now=dt_util.now,
    )
    await climate_runtime.async_start()
    from .climate_schedule import async_start_climate_schedule
    from .climate_trial import async_start_climate_trial

    await async_start_climate_schedule(hass, entry, climate_runtime)
    await async_start_climate_trial(hass, entry, climate_runtime)
    register_climate_api(hass, climate_runtime)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply a saved HausmanHub setting by reloading only this HausmanHub entry.

    Turning off the optional local page closes any already active page before
    Home Assistant reloads the nine-count display. An old address therefore
    cannot read a summary during the short reload interval.
    """

    from .climate_api import clear_climate_api
    from .local_summary import clear_local_summary_access

    clear_climate_api(hass, entry.entry_id)

    try:
        configuration = effective_configuration(entry.data, entry.options)
    except ConfigurationViolation:
        clear_local_summary_access(hass, entry)
    else:
        if not configuration.local_summary_enabled:
            clear_local_summary_access(hass, entry)

    await hass.config_entries.async_reload(entry.entry_id)


async def _close_running_duplicate_hausmanhub_entries(
    hass: HomeAssistant,
    domain: str,
) -> None:
    """Close only active HausmanHub displays when more than one record is saved.

    A damaged saved pair can also appear while one HausmanHub entry is already
    running. Close its local summary before awaiting the ordinary integration
    unload, then let Home Assistant stop only those loaded HausmanHub displays. The
    saved entries remain untouched for the owner to repair manually.
    """

    from .climate_api import clear_climate_api
    from .local_summary import clear_local_summary_access

    loaded_entries = tuple(hass.config_entries.async_loaded_entries(domain))
    for loaded_entry in loaded_entries:
        clear_local_summary_access(hass, loaded_entry)
        clear_climate_api(hass, loaded_entry.entry_id)
    for loaded_entry in loaded_entries:
        await hass.config_entries.async_unload(loaded_entry.entry_id)


def _clear_restored_hausmanhub_records(
    hass: HomeAssistant,
    entry_ids: tuple[str, ...],
) -> None:
    """Remove stale HausmanHub count records when saved settings must stay closed.

    Home Assistant can restore previous entity states before an integration gets
    a chance to reject invalid settings or multiple saved HausmanHub entries.
    Clearing only records owned by the captured HausmanHub entries makes rejection
    fail closed without changing saved settings, devices, services, other
    entities, or anything outside HausmanHub. A later safe reload with exactly one
    valid entry creates the same fixed nine count sensors again.
    """

    from homeassistant.core import callback
    from homeassistant.helpers import entity_registry
    from homeassistant.helpers.start import async_at_started

    @callback
    def clear_hausmanhub_records_after_start(_: HomeAssistant) -> None:
        entities = entity_registry.async_get(hass)
        for entry_id in dict.fromkeys(entry_ids):
            entries = entity_registry.async_entries_for_config_entry(
                entities,
                entry_id,
            )
            for registered_entity in entries:
                hass.states.async_remove(registered_entity.entity_id)
                entities.async_remove(registered_entity.entity_id)

    # The entity registry writes unavailable placeholders during startup, so
    # wait for that normal framework step before cleanup. A running system has
    # already passed startup and must clear the stale HausmanHub records immediately.
    if getattr(hass, "is_running", False):
        clear_hausmanhub_records_after_start(hass)
    else:
        async_at_started(hass, clear_hausmanhub_records_after_start)


def _clear_hausmanhub_state_values(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear only current state values owned by one HausmanHub setup."""

    from homeassistant.helpers import entity_registry

    entities = entity_registry.async_get(hass)
    for registered_entity in entity_registry.async_entries_for_config_entry(
        entities,
        entry.entry_id,
    ):
        hass.states.async_remove(registered_entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload HausmanHub entities, clear their values, and close its local summary."""

    from homeassistant.const import Platform

    from .climate_api import clear_climate_api
    from .local_summary import clear_local_summary_access

    unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        (Platform.SENSOR, Platform.SWITCH),
    )
    if unloaded:
        _clear_hausmanhub_state_values(hass, entry)
        clear_local_summary_access(hass, entry)
        clear_climate_api(hass, entry.entry_id)
    return unloaded
