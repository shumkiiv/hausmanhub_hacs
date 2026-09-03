"""Home Assistant boundary for the HausmanHub integration.

It always creates the nine diagnostic count sensors. An explicitly armed
legacy canary may additionally control one ``input_boolean`` helper. The
separate climate facade persists logical bindings and can use only two fixed
Climate API paths in shadow or one-room canary mode. HausmanHub registers no service
and never calls a Home Assistant climate entity directly except through the single
strict climate-call executor used by trial, managed ticks, and settings application.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from .application.configuration import (
    ConfigurationViolation,
    RELIABLE_SCOPE_EXTERNAL_KEYRING_INITIALIZED_FIELD,
    RELIABLE_SCOPE_INTEGRITY_INITIALIZED_FIELD,
    RELIABLE_SCOPE_INTEGRITY_KEY_FIELD,
    effective_configuration,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load observation, local climate facade, and optional narrow canaries."""

    # Retired local signing material must leave ConfigEntry storage before any
    # duplicate, keyring, configuration, or runtime failure can prevent cleanup.
    cleaned_data = {
        key: value for key, value in entry.data.items()
        if key not in {
            RELIABLE_SCOPE_INTEGRITY_KEY_FIELD,
            RELIABLE_SCOPE_INTEGRITY_INITIALIZED_FIELD,
        }
    }
    cleaned_options = {
        key: value for key, value in entry.options.items()
        if key not in {
            RELIABLE_SCOPE_INTEGRITY_KEY_FIELD,
            RELIABLE_SCOPE_INTEGRITY_INITIALIZED_FIELD,
        }
    }
    if cleaned_data != entry.data or cleaned_options != entry.options:
        update_entry = getattr(hass.config_entries, "async_update_entry", None)
        if callable(update_entry):
            update_entry(entry, data=cleaned_data, options=cleaned_options)

    configured_entry_ids = tuple(
        configured_entry.entry_id
        for configured_entry in hass.config_entries.async_entries(entry.domain)
    )
    if configured_entry_ids != (entry.entry_id,):
        await _close_running_duplicate_hausmanhub_entries(hass, entry.domain)
        _clear_restored_hausmanhub_records(hass, configured_entry_ids + (entry.entry_id,))
        return False

    from .climate_ledger_keyring import (
        ClimateLedgerKeyringError,
        load_external_climate_ledger_keyring,
    )
    try:
        scope_integrity_key = load_external_climate_ledger_keyring(
            config_dir=getattr(getattr(hass, "config", None), "config_dir", None)
        )
    except ClimateLedgerKeyringError:
        # The read-only integration remains available, but no climate writer
        # may create an unauthenticated durable record.
        scope_integrity_key = None
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
    from .climate_ha_executor import HomeAssistantClimateCallExecutor
    from .ha_area_assignment import HomeAssistantAreaAssignmentService
    from .climate_ha_state_view import HomeAssistantClimateStateView
    from .climate_protection_storage import HomeAssistantClimateProtectionStore
    from .climate_manual_storage import HomeAssistantClimateManualStore
    from .climate_command_guard_storage import HomeAssistantClimateCommandGuardStore
    from .application.climate_deviation_guard import ClimateDeviationGuardService
    from .climate_deviation_guard_storage import (
        HomeAssistantClimateDeviationGuardStore,
    )
    from .climate_storage import HomeAssistantClimateRegistryStore
    from .contour_storage import HomeAssistantContourStore
    from .application.ir_code_service import IRCodeService
    from .application.ir_code_sources import HomeAssistantIRCodeCatalog
    from .ir_code_gateway import HomeAssistantIRCodeTransmitter
    from .ir_code_storage import HomeAssistantIRCodeStore
    from .application.settings_service import HausmanHubSettingsService
    from .application.tablet_preferences import TabletPreferencesService
    from .settings_storage import HomeAssistantSettingsStore
    from .tablet_preferences_storage import HomeAssistantTabletPreferencesStore
    from .application.device_power_dependencies import DevicePowerDependencyService
    from .device_power_dependency_storage import (
        HomeAssistantDevicePowerDependencyStore,
    )
    from .application.energy_meter import EnergyMeterService
    from .application.energy_meters import EnergyMetersService
    from .energy_meter_storage import HomeAssistantEnergyMeterStore
    from .energy_meters_storage import HomeAssistantEnergyMetersStore
    from .application.device_discovery import DeviceDiscoveryService
    from .device_discovery_storage import HomeAssistantDeviceDiscoveryStore
    from .local_summary import register_local_summary_access
    from .tablet_power_api import tablet_power_service

    tablet_power = tablet_power_service(hass)

    await hass.config_entries.async_forward_entry_setups(
        entry,
        (Platform.SENSOR, Platform.SWITCH),
    )
    contour_store = HomeAssistantContourStore(hass, entry.entry_id)
    ir_code_store = HomeAssistantIRCodeStore(hass, entry.entry_id)
    ir_code_service = IRCodeService(
        ir_code_store,
        HomeAssistantIRCodeCatalog(hass),
        HomeAssistantIRCodeTransmitter(hass),
    )
    await ir_code_service.async_load()
    settings_service = HausmanHubSettingsService(
        entry.entry_id,
        HomeAssistantSettingsStore(hass, entry.entry_id),
    )
    await settings_service.async_load()
    tablet_preferences_service = TabletPreferencesService(
        HomeAssistantTabletPreferencesStore(hass, entry.entry_id)
    )
    await tablet_preferences_service.async_load(settings_service.current)
    device_power_dependency_service = DevicePowerDependencyService(
        HomeAssistantDevicePowerDependencyStore(hass, entry.entry_id),
        entity_pair_validator=lambda dependent, source: (
            hass.states.get(dependent) is not None and hass.states.get(source) is not None
        ),
    )
    await device_power_dependency_service.async_load()
    energy_meter_service = EnergyMeterService(
        HomeAssistantEnergyMeterStore(hass, entry.entry_id),
        local_today=lambda: dt_util.now().date(),
    )
    await energy_meter_service.async_load()
    energy_meters_service = EnergyMetersService(
        HomeAssistantEnergyMetersStore(hass, entry.entry_id),
        energy_meter_service,
        local_today=lambda: dt_util.now().date(),
    )
    await energy_meters_service.async_load()
    from .application.energy_anomaly import EnergyAnomalyTracker

    energy_anomaly_tracker = EnergyAnomalyTracker()
    device_discovery_service = DeviceDiscoveryService(
        HomeAssistantDeviceDiscoveryStore(hass, entry.entry_id)
    )
    await device_discovery_service.async_load()
    domain_data = hass.data.setdefault(entry.domain, {})
    domain_data["settings_service"] = settings_service
    domain_data["tablet_preferences_service"] = tablet_preferences_service
    domain_data["device_power_dependency_service"] = (
        device_power_dependency_service
    )
    domain_data["energy_meter_service"] = energy_meter_service
    domain_data["energy_meters_service"] = energy_meters_service
    domain_data["energy_anomaly_tracker"] = energy_anomaly_tracker
    domain_data["device_discovery_service"] = device_discovery_service
    from .application.vendor_resilience import VendorCircuitBreaker

    vendor_resilience = VendorCircuitBreaker()
    domain_data["vendor_resilience"] = vendor_resilience
    from .application.operation_journal import OperationJournalService
    from .operation_journal_api import DATA_OPERATION_JOURNAL
    from .operation_journal_storage import HomeAssistantOperationJournalStore

    operation_journal = OperationJournalService(
        HomeAssistantOperationJournalStore(hass, entry.entry_id)
    )
    await operation_journal.async_load()
    domain_data[DATA_OPERATION_JOURNAL] = operation_journal
    from .application.device_action_idempotency import DangerousActionIdempotency
    from .device_action_idempotency_storage import (
        HomeAssistantDeviceActionIdempotencyStore,
    )

    device_action_idempotency = DangerousActionIdempotency(
        HomeAssistantDeviceActionIdempotencyStore(hass, entry.entry_id)
    )
    await device_action_idempotency.async_load()
    domain_data["device_action_idempotency"] = device_action_idempotency
    from homeassistant.helpers.event import async_track_time_interval

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            lambda _now: tablet_power.expire(),
            timedelta(minutes=5),
        )
    )
    climate_deviation_guard = ClimateDeviationGuardService(
        HomeAssistantClimateDeviationGuardStore(hass, entry.entry_id),
        operation_journal=operation_journal,
    )
    domain_data["climate_deviation_guard"] = climate_deviation_guard
    if configuration.local_summary_enabled:
        register_local_summary_access(hass, entry)
    from .tablet_power_api import register_tablet_power_api

    register_tablet_power_api(hass)
    from .application.water_safety import WaterSafetyService
    from .water_safety_api import DATA_WATER_SAFETY, register_water_safety_api
    from .water_safety_gateway import HomeAssistantWaterSafetyGateway
    from .water_safety_storage import HomeAssistantWaterSafetyStore

    water_safety = WaterSafetyService(
        hass,
        HomeAssistantWaterSafetyStore(hass, entry.entry_id),
        command_gateway=HomeAssistantWaterSafetyGateway(hass),
        operation_journal=operation_journal,
    )
    await water_safety.async_load()
    domain_data[DATA_WATER_SAFETY] = water_safety
    entry.async_on_unload(water_safety.start())
    register_water_safety_api(hass)
    from .climate_operation_storage import HomeAssistantClimateOperationStore
    climate_operation_store = HomeAssistantClimateOperationStore(
        hass,
        entry.entry_id,
        reliable_scope_integrity_key=scope_integrity_key,
        require_authenticated=True,
    )
    if scope_integrity_key is not None:
        # Old local operation and Tablet history has no externally verifiable
        # provenance. Create a new anchored ledger before runtime can inspect
        # or replay any of it.
        await climate_operation_store.async_initialize_external_ledger()
    climate_runtime = ClimateRuntime(
        entry_id=entry.entry_id,
        configuration=configuration,
        registry_store=HomeAssistantClimateRegistryStore(hass, entry.entry_id),
        contour_store=contour_store,
        protection_store=HomeAssistantClimateProtectionStore(hass, entry.entry_id),
        manual_store=HomeAssistantClimateManualStore(hass, entry.entry_id),
        command_guard_store=HomeAssistantClimateCommandGuardStore(
            hass, entry.entry_id
        ),
        deviation_guard=climate_deviation_guard,
        strict_ha_call_executor=HomeAssistantClimateCallExecutor(hass),
        ha_state_view=HomeAssistantClimateStateView(hass),
        ha_area_assignment=HomeAssistantAreaAssignmentService(hass),
        ir_code_service=ir_code_service,
        local_now=dt_util.now,
        direct_control_store=climate_operation_store,
    )
    ir_code_service.set_binding_validator(climate_runtime)
    await climate_runtime.async_start()
    if climate_runtime.direct_control_validation_failed:
        # Do not sign, publish, or mark an entry whose unsigned direct
        # history failed the authoritative startup validation.
        return False
    if scope_integrity_key is not None:
        updated_data = {
            **entry.data,
            RELIABLE_SCOPE_EXTERNAL_KEYRING_INITIALIZED_FIELD: True,
        }
        if updated_data != entry.data:
            update_entry = getattr(hass.config_entries, "async_update_entry", None)
            if callable(update_entry):
                update_entry(entry, data=updated_data)
    from .application.climate_tablet import (
        ClimateTabletService,
        ClimateTabletUnavailable,
    )
    climate_tablet = ClimateTabletService(
        climate_runtime,
        climate_operation_store,
        local_now=dt_util.now,
    )
    try:
        await climate_tablet.async_load()
    except ClimateTabletUnavailable:
        climate_tablet = None
    from .ai_assistant_setup import async_start_ai_assistant
    from .climate_schedule import async_start_climate_schedule
    from .climate_synchronization import async_start_climate_synchronization
    from .climate_shadow import async_start_climate_shadow
    from .climate_trial import async_start_climate_trial

    ai_assistant = await async_start_ai_assistant(hass, entry, climate_runtime)
    await async_start_climate_schedule(hass, entry, climate_runtime)
    await async_start_climate_synchronization(hass, entry, climate_runtime)
    climate_shadow = await async_start_climate_shadow(hass, entry, climate_runtime)
    await async_start_climate_trial(hass, entry, climate_runtime)

    from .application.scenario_catalog import async_build_scenario_catalog
    from .application.scenario_executor import ScenarioExecutor
    from .application.scenario_service import ScenarioService
    from .scenario_schedule import async_start_scenario_schedule
    from .scenario_schedule_storage import HomeAssistantScenarioScheduleStore
    from .scenario_storage import HomeAssistantScenarioStore

    scenario_store = HomeAssistantScenarioStore(hass, entry.entry_id)
    scenario_catalog = await async_build_scenario_catalog(hass)
    from .application.scenario_node_red import NodeRedScenarioBackend

    scenario_node_red_backend = NodeRedScenarioBackend(hass)
    from .application.intercom_release_obligation import (
        IntercomReleaseObligation,
    )
    from .intercom_release_obligation_storage import (
        HomeAssistantIntercomReleaseObligationStore,
    )

    intercom_release_obligation = IntercomReleaseObligation(
        HomeAssistantIntercomReleaseObligationStore(hass, entry.entry_id)
    )
    await intercom_release_obligation.async_load()

    def _publish_intercom_release(receipt: dict[str, object]) -> None:
        from .realtime_api import publish_command_receipt

        publish_command_receipt(hass, receipt, operation="intercom_release")

    def _publish_scenario_change(
        change: str,
        scenario_id: str,
        revision: int,
        changed_fields: tuple[str, ...] = (),
    ) -> None:
        from .realtime_api import publish_scenario_change

        publish_scenario_change(
            hass, change, scenario_id, revision, changed_fields
        )

    scenario_service = ScenarioService(
        hass,
        scenario_store,
        scenario_catalog,
        catalog_loader=lambda: async_build_scenario_catalog(hass),
        intercom_entity_resolver=lambda: next(
            iter(tablet_preferences_service.tablet_pinned_entity_ids), None
        ),
        schedule_store=HomeAssistantScenarioScheduleStore(hass, entry.entry_id),
        operation_journal=operation_journal,
        node_red_backend=scenario_node_red_backend,
        intercom_release_publisher=_publish_intercom_release,
        scenario_change_publisher=_publish_scenario_change,
        intercom_release_obligation=intercom_release_obligation,
    )
    await scenario_service.async_load()
    from .application.scenario_light_priority import LightAutomationPriority
    from .light_automation_priority_storage import (
        HomeAssistantLightAutomationPriorityStore,
    )

    light_priority = LightAutomationPriority(
        HomeAssistantLightAutomationPriorityStore(hass, entry.entry_id)
    )
    await light_priority.async_load()
    domain_data["light_automation_priority"] = light_priority
    from .application.light_safety_obligations import LightSafetyObligations
    from .light_safety_obligation_storage import (
        HomeAssistantLightSafetyObligationStore,
    )
    from .light_safety_repairs import HomeAssistantLightSafetyIssueReporter

    light_safety_obligations = LightSafetyObligations(
        HomeAssistantLightSafetyObligationStore(hass, entry.entry_id),
        issue_reporter=HomeAssistantLightSafetyIssueReporter(hass),
    )
    await light_safety_obligations.async_load()
    from .application.manual_light_off_protection import (
        ManualLightOffProtectionCoordinator,
    )
    from .manual_light_off_protection_storage import (
        HomeAssistantManualLightOffProtectionStore,
    )

    manual_light_off_protection = ManualLightOffProtectionCoordinator(
        HomeAssistantManualLightOffProtectionStore(hass, entry.entry_id)
    )
    await manual_light_off_protection.async_load()
    domain_data["manual_light_off_protection"] = manual_light_off_protection
    from .application.scenario_command_context import (
        ScenarioCommandContextRegistry,
    )

    scenario_command_contexts = ScenarioCommandContextRegistry()
    scenario_executor = ScenarioExecutor(
        hass,
        scenario_catalog,
        scenario_service.async_run_scenario,
        power_dependency_resolver=lambda: device_power_dependency_service.mapping,
        command_guard=water_safety.command_guard,
        vendor_resilience=vendor_resilience,
        node_red_backend=scenario_node_red_backend,
        light_priority=light_priority,
        light_safety_obligations=light_safety_obligations,
        contextual_dangerous_resolver=(
            scenario_service.is_contextually_dangerous_action
        ),
        command_contexts=scenario_command_contexts,
    )
    entry.async_on_unload(
        light_safety_obligations.start(
            scenario_executor.async_reconcile_light_obligation
        )
    )
    scenario_service.set_executor(scenario_executor)
    entry.async_on_unload(
        intercom_release_obligation.start(
            scenario_service.async_reconcile_intercom_release
        )
    )
    entry.async_on_unload(scenario_service.cancel_running_scenarios)
    entry.async_on_unload(scenario_service.start_catalog_warmup())
    await async_start_scenario_schedule(hass, entry, scenario_service)
    from .scenario_events import async_start_scenario_events

    await async_start_scenario_events(
        hass,
        entry,
        scenario_service,
        scenario_command_contexts,
    )
    from .manual_light_off_protection_events import (
        async_start_manual_light_off_protection_events,
    )

    await async_start_manual_light_off_protection_events(
        hass,
        entry,
        manual_light_off_protection,
        scenario_command_contexts,
    )

    from .error_taxonomy import async_preload_error_policies

    await async_preload_error_policies(hass)
    register_climate_api(
        hass,
        climate_runtime,
        ai_assistant,
        scenario_service,
        ir_code_service,
        climate_shadow,
        climate_tablet,
    )
    from .operation_journal_api import register_operation_journal_api

    register_operation_journal_api(hass)
    from .realtime_api import register_event_stream

    register_event_stream(hass, entry.entry_id)
    from .voice_api import async_start_voice_greeting

    await async_start_voice_greeting(hass, entry)
    from .panel import async_register_hausmanhub_panel

    await async_register_hausmanhub_panel(hass)
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
    from .realtime_api import clear_event_stream
    from .voice_api import clear_voice_greeting
    from .operation_journal_api import clear_operation_journal
    from .water_safety_api import clear_water_safety_api
    from .tablet_power_api import clear_tablet_power_api

    clear_event_stream(hass, entry.entry_id)
    clear_voice_greeting(hass, entry.entry_id)
    clear_water_safety_api(hass)
    clear_operation_journal(hass)
    clear_tablet_power_api(hass)
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
    from .realtime_api import clear_event_stream
    from .voice_api import clear_voice_greeting
    from .operation_journal_api import clear_operation_journal
    from .water_safety_api import clear_water_safety_api
    from .tablet_power_api import clear_tablet_power_api

    loaded_entries = tuple(hass.config_entries.async_loaded_entries(domain))
    for loaded_entry in loaded_entries:
        clear_local_summary_access(hass, loaded_entry)
        clear_event_stream(hass, loaded_entry.entry_id)
        clear_voice_greeting(hass, loaded_entry.entry_id)
        clear_water_safety_api(hass)
        clear_operation_journal(hass)
        clear_tablet_power_api(hass)
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
    from .realtime_api import clear_event_stream
    from .voice_api import clear_voice_greeting
    from .operation_journal_api import clear_operation_journal
    from .water_safety_api import clear_water_safety_api
    from .tablet_power_api import clear_tablet_power_api

    unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        (Platform.SENSOR, Platform.SWITCH),
    )
    if unloaded:
        _clear_hausmanhub_state_values(hass, entry)
        clear_local_summary_access(hass, entry)
        clear_event_stream(hass, entry.entry_id)
        clear_voice_greeting(hass, entry.entry_id)
        clear_water_safety_api(hass)
        clear_operation_journal(hass)
        clear_tablet_power_api(hass)
        clear_climate_api(hass, entry.entry_id)
        from .panel import unregister_hausmanhub_panel

        unregister_hausmanhub_panel(hass)
    return unloaded


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate legacy shadow/canary entries to the retired two-mode world once."""

    if entry.version >= 2:
        return True
    from .application.contours import with_climate_contour_mode
    from .domain.climate_bridge import LEGACY_BRIDGE_MODES
    from .domain.contours import ContourMode

    saved_mode = entry.options.get("climate_bridge_mode") or entry.data.get(
        "climate_bridge_mode"
    )
    updates: dict[str, object] = dict(entry.options)
    if saved_mode in LEGACY_BRIDGE_MODES:
        updates["climate_bridge_mode"] = "disabled"
    for stale_field in ("climate_bridge_target", "climate_canary_room_id"):
        updates.pop(stale_field, None)
    if updates != dict(entry.options):
        hass.config_entries.async_update_entry(
            entry,
            data=entry.data,
            options=updates,
            version=2,
        )
    else:
        hass.config_entries.async_update_entry(entry, version=2)
    if saved_mode in LEGACY_BRIDGE_MODES:
        contour_store = HomeAssistantContourStore(hass, entry.entry_id)
        contours = await contour_store.async_load()
        if (
            contours.contour("climate") is not None
            and contours.contour("climate").mode is ContourMode.AUTOMATIC
        ):
            await contour_store.async_save(
                with_climate_contour_mode(contours, ContourMode.OBSERVE)
            )
    return True
