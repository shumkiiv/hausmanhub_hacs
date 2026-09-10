"""Home Assistant boundary for the HausmanHub integration.

It always creates the nine diagnostic count sensors. An explicitly armed
legacy canary may additionally control one ``input_boolean`` helper. The
separate climate facade persists logical bindings and can use only two fixed
Climate API paths in shadow or one-room canary mode. HausmanHub registers no service
and never calls a Home Assistant climate entity directly except through the single
strict climate-call executor used by trial, managed ticks, and settings application.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from .application.configuration import (
    ConfigurationViolation,
    RELIABLE_SCOPE_EXTERNAL_KEYRING_INITIALIZED_FIELD,
    RELIABLE_SCOPE_INTEGRITY_INITIALIZED_FIELD,
    RELIABLE_SCOPE_INTEGRITY_KEY_FIELD,
    effective_configuration,
)

_LOGGER = logging.getLogger(__name__)

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
    from .application.device_power_dependencies import (
        DevicePowerDependencyService,
        async_migrate_obsolete_small_corridor_power_source,
    )
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
    await async_migrate_obsolete_small_corridor_power_source(
        device_power_dependency_service
    )
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
    from .operation_journal_api import DATA_OPERATION_JOURNAL, DATA_OPERATION_JOURNAL_ARCHIVE
    from .operation_journal_storage import HomeAssistantOperationJournalArchiveStore, HomeAssistantOperationJournalStore

    operation_journal = OperationJournalService(
        HomeAssistantOperationJournalStore(hass, entry.entry_id)
    )
    await operation_journal.async_load()
    domain_data[DATA_OPERATION_JOURNAL] = operation_journal
    from .application.operation_journal_admin import OperationJournalArchiveService
    operation_journal_archive = OperationJournalArchiveService(
        operation_journal,
        HomeAssistantOperationJournalArchiveStore(hass, entry.entry_id),
        keyring=scope_integrity_key,
    )
    await operation_journal_archive.async_load()
    domain_data[DATA_OPERATION_JOURNAL_ARCHIVE] = operation_journal_archive
    from .application.device_action_idempotency import DangerousActionIdempotency
    from .device_action_idempotency_storage import (
        HomeAssistantDeviceActionIdempotencyStore,
    )

    device_action_idempotency = DangerousActionIdempotency(
        HomeAssistantDeviceActionIdempotencyStore(hass, entry.entry_id)
    )
    await device_action_idempotency.async_load()
    domain_data["device_action_idempotency"] = device_action_idempotency
    from .application.safe_device_command_lifecycle import (
        SafeDeviceCommandLifecycle,
    )
    from .safe_device_command_storage import HomeAssistantSafeDeviceCommandStore

    safe_device_command_lifecycle = SafeDeviceCommandLifecycle(
        HomeAssistantSafeDeviceCommandStore(hass, entry.entry_id)
    )
    await safe_device_command_lifecycle.async_load()
    domain_data["safe_device_command_lifecycle"] = safe_device_command_lifecycle

    def close_safe_device_commands() -> None:
        """Schedule fallback cleanup for setup aborts on Home Assistant's loop."""

        if safe_device_command_lifecycle.closed:
            return
        hass.async_create_task(
            safe_device_command_lifecycle.async_close()
        )

    entry.async_on_unload(close_safe_device_commands)
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

    from .application.scenario_catalog import (
        ScenarioCatalog,
        async_build_scenario_catalog,
    )
    from .application.electrical_breakers import configured_source_device_ids
    from .application.scenario_executor import ScenarioExecutor
    from .application.scenario_service import ScenarioService
    from .scenario_schedule import async_start_scenario_schedule
    from .scenario_schedule_storage import HomeAssistantScenarioScheduleStore
    from .scenario_storage import HomeAssistantScenarioStore

    scenario_store = HomeAssistantScenarioStore(hass, entry.entry_id)
    scenario_catalog = await async_build_scenario_catalog(hass)
    from .application.manual_light_off_protection import (
        ManualLightOffProtectionCoordinator,
    )
    from .application.system_light_profiles import (
        SYSTEM_LIGHT_PROFILES,
        audit_system_light_protection_coverage,
        scenario_targets_for_system_light_profiles,
    )
    from .manual_light_off_protection_storage import (
        HomeAssistantManualLightOffProtectionStore,
    )

    manual_light_off_protection = ManualLightOffProtectionCoordinator(
        HomeAssistantManualLightOffProtectionStore(hass, entry.entry_id)
    )
    await manual_light_off_protection.async_load()

    def _refresh_manual_light_off_protection_coverage(
        catalog: ScenarioCatalog,
    ) -> None:
        coverage = audit_system_light_protection_coverage(
            SYSTEM_LIGHT_PROFILES,
            scenario_targets_for_system_light_profiles(catalog),
        )
        manual_light_off_protection.set_catalog_coverage_healthy(coverage.healthy)
        domain_data["manual_light_off_protection_coverage"] = coverage

    _refresh_manual_light_off_protection_coverage(scenario_catalog)

    async def _load_scenario_catalog() -> ScenarioCatalog:
        catalog = await async_build_scenario_catalog(hass)
        _refresh_manual_light_off_protection_coverage(catalog)
        return catalog

    domain_data["manual_light_off_protection"] = manual_light_off_protection
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
        catalog_loader=_load_scenario_catalog,
        intercom_entity_resolver=lambda: next(
            iter(tablet_preferences_service.tablet_pinned_entity_ids), None
        ),
        schedule_store=HomeAssistantScenarioScheduleStore(hass, entry.entry_id),
        operation_journal=operation_journal,
        node_red_backend=scenario_node_red_backend,
        intercom_release_publisher=_publish_intercom_release,
        scenario_change_publisher=_publish_scenario_change,
        intercom_release_obligation=intercom_release_obligation,
        manual_light_off_protection=manual_light_off_protection,
        electrical_breaker_device_ids_resolver=(
            lambda: configured_source_device_ids(
                energy_meters_service.source_bindings
            )
        ),
    )
    device_power_dependency_service.set_electrical_breaker_resolver(
        scenario_service.is_electrical_breaker_entity
    )
    await scenario_service.async_load()
    from .application.scenario_control_policy import ScenarioControlPolicyService
    from .scenario_control_storage import HomeAssistantScenarioControlPolicyStore

    def _safe_storage_exhaust_binding(target_id: str, device: object) -> bool:
        entity_id = getattr(device, "entity_id", None)
        water_actuators = water_safety.configuration.get("actuators", [])
        protected_water_entities = {
            actuator.get("entityId")
            for actuator in water_actuators
            if isinstance(actuator, dict)
        }
        return bool(
            isinstance(entity_id, str)
            and entity_id not in protected_water_entities
            and not scenario_service.is_electrical_breaker_entity_for_target(target_id)
            and not scenario_service.is_contextually_dangerous_action(target_id, "turn_on")
            and not scenario_service.is_contextually_dangerous_action(target_id, "turn_off")
        )

    scenario_control_policy = ScenarioControlPolicyService(
        HomeAssistantScenarioControlPolicyStore(hass, entry.entry_id),
        capability_resolver=lambda target_id: (
            scenario_service.current_catalog().device(target_id)
        ),
        binding_safety_validator=_safe_storage_exhaust_binding,
    )
    await scenario_control_policy.async_load()
    manual_light_off_protection.set_release_owned_block_seconds_provider(
        lambda: max(
            600, scenario_control_policy.current.policy.manual_off_block_seconds
        )
    )
    domain_data["scenario_control_policy_service"] = scenario_control_policy
    from .application.curtain_scale_confirmation import CurtainScaleConfirmation
    from .curtain_scale_confirmation_storage import (
        HomeAssistantCurtainScaleConfirmationStore,
        resolve_office_curtain_identity,
    )

    curtain_scale_confirmation = CurtainScaleConfirmation(
        HomeAssistantCurtainScaleConfirmationStore(hass, entry.entry_id),
        entry_id=entry.entry_id,
        identity_resolver=lambda target_id: resolve_office_curtain_identity(
            hass,
            lambda requested: scenario_service.current_catalog().device(requested),
            target_id,
        ),
    )
    await curtain_scale_confirmation.async_load()
    domain_data["curtain_scale_confirmation"] = curtain_scale_confirmation
    from .application.managed_switch_migration import (
        HomeAssistantManagedSwitchMigrationStore,
        ManagedSwitchActivation,
    )
    from .application.tambur_room_migration import (
        HomeAssistantTamburRoomMigrationStore,
        TAMBUR_INPUT_TARGET_IDS,
        TAMBUR_PRESENCE_TARGET_IDS,
        TamburRoomMigration,
        TamburRoomStartupCoordinator,
        build_home_assistant_tambur_native_migration,
    )

    migration_lock = asyncio.Lock()
    global_migration_store = HomeAssistantManagedSwitchMigrationStore(
        hass, entry.entry_id
    )
    domain_data["managed_switch_migration"] = {
        "state": "deferred",
        "reason": "tambur_room_only",
    }
    tambur_room_migration = TamburRoomMigration(
        scenario_service,
        HomeAssistantTamburRoomMigrationStore(hass, entry.entry_id),
        global_receipt_store=global_migration_store,
        native_automation_migration=build_home_assistant_tambur_native_migration(
            hass, entry.entry_id
        ),
        migration_lock=migration_lock,
    )

    sensor_states: dict[str, object] = {}
    for sensor_target_id in (
        *TAMBUR_PRESENCE_TARGET_IDS,
    ):
        sensor = scenario_service.current_catalog().device(sensor_target_id)
        entity_id = getattr(sensor, "entity_id", None)
        if isinstance(entity_id, str):
            sensor_states[entity_id] = hass.states.get(entity_id)
    await manual_light_off_protection.async_restore_release_owned_sensor_evidence(
        sensor_states
    )
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
    from .application.scenario_command_context import (
        ScenarioCommandContextRegistry,
    )
    from .application.curtain_command_policy import (
        CurtainCommandPolicy,
        CurtainPositionEvidencePolicy,
    )
    from .application.curtain_protection import CurtainProtectionCoordinator
    from .curtain_protection_storage import HomeAssistantCurtainProtectionStore

    def next_curtain_sunrise_ms() -> int | None:
        state = hass.states.get("sun.sun")
        attributes = getattr(state, "attributes", {})
        value = (
            attributes.get("next_rising")
            if isinstance(attributes, Mapping)
            else None
        )
        if not isinstance(value, str):
            return None
        from homeassistant.util import dt as dt_util

        parsed = dt_util.parse_datetime(value)
        return int(parsed.timestamp() * 1000) if parsed is not None else None

    curtain_protection = CurtainProtectionCoordinator(
        HomeAssistantCurtainProtectionStore(hass, entry.entry_id),
        catalog_resolver=lambda target_id: (
            scenario_service.current_catalog().device(target_id)
        ),
        next_sunrise_ms=next_curtain_sunrise_ms,
    )
    await curtain_protection.async_load()
    domain_data["curtain_protection"] = curtain_protection

    scenario_command_contexts = ScenarioCommandContextRegistry()
    scenario_executor = ScenarioExecutor(
        hass,
        scenario_service.current_catalog(),
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
        electrical_breaker_resolver=scenario_service.is_electrical_breaker_entity,
        command_contexts=scenario_command_contexts,
        manual_light_off_protection=manual_light_off_protection,
        curtain_command_policy=CurtainCommandPolicy(
            lambda: scenario_control_policy.current,
            scale_authorization_provider=(
                curtain_scale_confirmation.authorization_snapshot
            ),
            position_evidence_policy=CurtainPositionEvidencePolicy(),
        ),
        curtain_protection=curtain_protection,
    )
    entry.async_on_unload(
        light_safety_obligations.start(
            scenario_executor.async_reconcile_light_obligation
        )
    )
    scenario_service.set_executor(scenario_executor)
    from .application.scenario_control_coordinator import ScenarioControlCoordinator
    from .scenario_control_state_storage import HomeAssistantScenarioControlStateStore

    scenario_control_coordinator = ScenarioControlCoordinator(
        hass,
        scenario_service,
        scenario_control_policy,
        HomeAssistantScenarioControlStateStore(hass, entry.entry_id),
        light_priority,
        catalog_resolver=lambda target_id: (
            scenario_service.current_catalog().device(target_id)
        ),
    )
    await scenario_control_coordinator.async_load()
    scenario_executor.set_scenario_generation_validator(
        scenario_control_coordinator.async_validate_generation
    )
    async def managed_control_context(
        scenario_id: str, run_id: str, trigger: Mapping[str, object]
    ) -> dict[str, object]:
        context = await scenario_control_coordinator.async_control_context(
            scenario_id, run_id, trigger
        )
        if scenario_id == "system-curtains-privacy-controller":
            context["state"] = await curtain_protection.async_control_state(
                run_id, trigger
            )
        return context

    scenario_node_red_backend.set_control_context_provider(managed_control_context)
    domain_data["scenario_control_coordinator"] = scenario_control_coordinator
    entry.async_on_unload(scenario_control_coordinator.cancel)
    entry.async_on_unload(
        intercom_release_obligation.start(
            scenario_service.async_reconcile_intercom_release
        )
    )
    entry.async_on_unload(scenario_service.cancel_running_scenarios)
    from .application.activation_latch import ActivationLatch
    from .application.scenario_decision_bridge import (
        ScenarioDecisionBridge,
        TamburHaObservationCoordinator,
    )
    from .application.tambur_decision_runtime import TamburDecisionRuntime
    from .scenario_decision_bridge_storage import (
        HomeAssistantScenarioDecisionBridgeStore,
    )
    from .application.smart_switch_runtime import (
        HomeAssistantSmartSwitchDedupStore,
        SmartSwitchTriggerAdapter,
    )
    activation_latch = ActivationLatch()
    smart_switch_adapter = SmartSwitchTriggerAdapter(
        hass,
        scenario_service,
        state_store=HomeAssistantSmartSwitchDedupStore(hass, entry.entry_id),
        readiness_check=lambda: bool(
            manual_light_off_protection.ready_for_release_owned_switches
            and all(
                scenario_service.current_catalog().device(target_id) is not None
                for target_id in TAMBUR_INPUT_TARGET_IDS
            )
        ),
        activation_latch=activation_latch,
        included_bindings=frozenset(
            {
                "tambur-light-group",
                "tambur-mirror-left",
                "tambur-master-off",
            }
        ),
    )
    scenario_service.set_smart_switch_receipt_consumer(smart_switch_adapter)

    tambur_runtime_holder: dict[str, object] = {}

    def _cleanup_tambur_runtime() -> None:
        activation_latch.close()
        errors: list[Exception] = []
        runtime = tambur_runtime_holder.pop("runtime", None)
        cancel_runtime = getattr(runtime, "cancel", None)
        if callable(cancel_runtime):
            try:
                cancel_runtime()
            except Exception as error:  # noqa: BLE001
                errors.append(error)
        try:
            smart_switch_adapter.async_unload()
        except Exception as error:  # noqa: BLE001
            errors.append(error)
        if errors:
            raise RuntimeError("Tambur runtime cleanup failed") from errors[0]

    async def _async_activate_tambur_runtime(scope: object) -> ManagedSwitchActivation:
        """Prepare only the decision bridge and inputs for the migrated room."""

        try:
            if (
                getattr(scope, "scenario_ids", None)
                != ("system-tambur-adaptive-controller",)
                or getattr(scope, "presence_target_ids", None)
                != TAMBUR_PRESENCE_TARGET_IDS
                or getattr(getattr(hass, "config", None), "time_zone", None)
                != "Asia/Omsk"
            ):
                raise RuntimeError("Tambur activation scope is invalid")
            bindings = {
                "chandelier": "entity_71859313239a14e4",
                "points": "entity_cd0098e5ff95da46",
                "mirror": "entity_fbdf27871edb89bf",
                "power": "entity_b47991988cc6b9f3",
                "presenceSensors": list(TAMBUR_PRESENCE_TARGET_IDS),
            }
            settings = {
                "morningStart": "09:00",
                "morningEnd": "10:00",
                "eveningLatestStart": "21:00",
                "mainOff": "23:00",
                "mirrorOff": "01:00",
                "minPercent": 5,
                "maxPercent": 80,
                "dayKelvin": 3000,
                "eveningKelvin": 2200,
                "absenceDaySeconds": 600,
                "absenceNightSeconds": 180,
                "fadeSeconds": 20,
                "manualOffMinSeconds": 600,
                "manualOffAbsenceSeconds": 30,
                "manualOnHoldSeconds": 3600,
            }

            def entity_id_for(target_id: str) -> str | None:
                device = scenario_service.current_catalog().device(target_id)
                entity_id = getattr(device, "entity_id", None)
                return entity_id if isinstance(entity_id, str) else None

            async def ownership_for(target_id: str) -> dict[str, object]:
                entity_id = entity_id_for(target_id)
                if entity_id is None:
                    return {
                        "owner": "uncertain",
                        "generation": 0,
                        "protectionActive": True,
                    }
                manual = bool(
                    light_priority.manual_claim_entity_ids(
                        frozenset({entity_id})
                    )
                )
                owned = light_priority.is_owned(entity_id, hass)
                return {
                    "owner": "manual" if manual else "automatic" if owned else "none",
                    "generation": 0,
                    "protectionActive": manual,
                }

            now_ms = lambda: int(dt_util.utcnow().timestamp() * 1000)
            observations = TamburHaObservationCoordinator(
                hass,
                bindings=bindings,
                entity_id_provider=entity_id_for,
                settings=settings,
                settings_revision=1,
                authority_provider=ownership_for,
                freshness_deadline_provider=(
                    lambda _target, _entity, reported: reported + 120_000
                ),
                now_ms=now_ms,
                timezone_name="Asia/Omsk",
            )
            observation_epoch = {"value": 0}

            async def decision_authority(target_id: str) -> dict[str, object]:
                return await observations.async_authority_snapshot(
                    target_id, observation_epoch["value"]
                )

            bridge = ScenarioDecisionBridge(
                HomeAssistantScenarioDecisionBridgeStore(hass, entry.entry_id),
                snapshot_provider=observations.async_snapshot_source,
                authority_provider=decision_authority,
                now_ms=now_ms,
                executor=scenario_executor,
            )

            async def register_tambur_manual_intent(
                request_id: str,
                target_id: str,
                action_id: str,
                value: object | None,
            ) -> None:
                if target_id in {
                    bindings["chandelier"],
                    bindings["points"],
                    bindings["mirror"],
                }:
                    await bridge.async_register_manual_intent(
                        request_id, target_id, action_id, value
                    )

            scenario_service.set_manual_action_pre_admission(
                register_tambur_manual_intent
            )

            async def register_tambur_manual_intents(
                request_id: str,
                actions: tuple[Mapping[str, object], ...],
            ) -> None:
                allowed_targets = {
                    bindings["chandelier"],
                    bindings["points"],
                    bindings["mirror"],
                }
                if (
                    not actions
                    or any(item.get("targetId") not in allowed_targets for item in actions)
                ):
                    raise RuntimeError("Tambur manual fence scope is invalid")
                await bridge.async_register_manual_intents(request_id, actions)

            scenario_service.set_manual_action_batch_pre_admission(
                register_tambur_manual_intents
            )
            presence_entities = {
                entity_id: target_id
                for target_id in TAMBUR_PRESENCE_TARGET_IDS
                if (entity_id := entity_id_for(target_id)) is not None
            }
            if len(presence_entities) != len(TAMBUR_PRESENCE_TARGET_IDS):
                raise RuntimeError("Tambur presence binding is incomplete")
            runtime = TamburDecisionRuntime(
                hass,
                scenario_node_red_backend,
                bridge,
                observations,
                presence_entities=presence_entities,
                now_ms=now_ms,
                recovered_callback=lambda epoch: observation_epoch.update(
                    value=epoch
                ),
            )
            await runtime.async_start()
            tambur_runtime_holder["runtime"] = runtime
            tambur_runtime_holder["bridge"] = bridge
            await smart_switch_adapter.async_start()
        except asyncio.CancelledError:
            try:
                _cleanup_tambur_runtime()
            except Exception:  # noqa: BLE001
                _LOGGER.error("Tambur runtime cleanup failed")
            raise
        except Exception as activation_error:  # noqa: BLE001
            cleanup_failed = False
            try:
                _cleanup_tambur_runtime()
            except Exception:  # noqa: BLE001
                cleanup_failed = True
                _LOGGER.error("Tambur runtime cleanup failed")
            domain_data["smart_switch_runtime"] = {
                "state": "unavailable",
                "reason": (
                    "tambur_runtime_cleanup_failed"
                    if cleanup_failed
                    else "tambur_runtime_attach_failed"
                ),
            }
            raise RuntimeError("Tambur runtime activation failed") from activation_error

        def _commit_tambur_runtime() -> None:
            activation_latch.open()
            runtime.activate()
            domain_data["smart_switch_runtime"] = {
                "state": "ready",
                "adapter": smart_switch_adapter,
            }
            domain_data["tambur_decision_runtime"] = runtime

        return ManagedSwitchActivation(
            _cleanup_tambur_runtime,
            _commit_tambur_runtime,
            activation_latch.close,
        )

    def _publish_tambur_status(status: dict[str, str]) -> None:
        domain_data["tambur_room_migration"] = dict(status)
        if status.get("state") == "waiting":
            domain_data["smart_switch_runtime"] = {
                "state": "waiting",
                "reason": "tambur_room_pending",
            }
        elif status.get("state") == "blocked":
            domain_data["smart_switch_runtime"] = {
                "state": "unavailable",
                "reason": status.get("stage", "binding"),
            }

    tambur_room_startup = TamburRoomStartupCoordinator(
        scenario_service,
        tambur_room_migration,
        _async_activate_tambur_runtime,
        status_publisher=_publish_tambur_status,
    )
    entry.async_on_unload(tambur_room_startup.cancel)
    await tambur_room_startup.async_start()
    entry.async_on_unload(scenario_service.start_catalog_warmup())
    from .manual_light_off_protection_events import (
        async_start_manual_light_off_protection_events,
    )

    await async_start_manual_light_off_protection_events(
        hass,
        entry,
        manual_light_off_protection,
        scenario_command_contexts,
        scenario_catalog,
        light_priority,
        light_safety_obligations,
        light_priority.authority_lock(),
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
    from .manual_light_off_protection_api import (
        register_manual_light_off_protection_api,
    )

    register_manual_light_off_protection_api(hass)
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
    from .manual_light_off_protection_api import clear_manual_light_off_protection_api

    clear_event_stream(hass, entry.entry_id)
    clear_voice_greeting(hass, entry.entry_id)
    clear_water_safety_api(hass)
    clear_operation_journal(hass)
    clear_tablet_power_api(hass)
    clear_manual_light_off_protection_api(hass)
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
    from .manual_light_off_protection_api import clear_manual_light_off_protection_api

    loaded_entries = tuple(hass.config_entries.async_loaded_entries(domain))
    for loaded_entry in loaded_entries:
        clear_local_summary_access(hass, loaded_entry)
        clear_event_stream(hass, loaded_entry.entry_id)
        clear_voice_greeting(hass, loaded_entry.entry_id)
        clear_water_safety_api(hass)
        clear_operation_journal(hass)
        clear_tablet_power_api(hass)
        clear_manual_light_off_protection_api(hass)
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
    from .manual_light_off_protection_api import clear_manual_light_off_protection_api

    safe_device_command_lifecycle = hass.data.get("hausman_hub", {}).get(
        "safe_device_command_lifecycle"
    )
    unloaded = await hass.config_entries.async_unload_platforms(
        entry,
        (Platform.SENSOR, Platform.SWITCH),
    )
    if unloaded:
        climate_tablet = hass.data.get("hausman_hub", {}).get("climate_tablet")
        if climate_tablet is not None:
            close_climate = getattr(climate_tablet, "async_close", None)
            if callable(close_climate):
                await close_climate()
        if safe_device_command_lifecycle is not None:
            close = getattr(safe_device_command_lifecycle, "async_close", None)
            if callable(close):
                await close()
        _clear_hausmanhub_state_values(hass, entry)
        clear_local_summary_access(hass, entry)
        clear_event_stream(hass, entry.entry_id)
        clear_voice_greeting(hass, entry.entry_id)
        clear_water_safety_api(hass)
        clear_operation_journal(hass)
        clear_tablet_power_api(hass)
        clear_manual_light_off_protection_api(hass)
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
