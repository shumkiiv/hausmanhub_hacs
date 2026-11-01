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
from pathlib import Path
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

    # Observe external light actions before the first setup await. The room
    # catalog and scenario registry are attached later; until then every
    # light/switch event is buffered and automatic dispatch stays closed.
    from .application.manual_light_authority import ManualLightAuthority
    from .adapters.manual_light_single_entity_evidence import (
        proven_single_entity_evidence,
    )
    from .manual_light_authority_storage import HomeAssistantManualLightAuthorityStore
    from .manual_light_authority_events import (
        ManualLightAuthorityEvents,
        room_light_entity_ids,
    )

    manual_light_authority = ManualLightAuthority(
        HomeAssistantManualLightAuthorityStore(hass, entry.entry_id)
    )
    manual_light_authority.set_priority_duration_resolver(lambda entity, action: getattr(
        hass.data.get(entry.domain, {}).get("room_lighting_runtime"),
        "manual_priority_duration_ms", lambda *_: 3600000,
    )(entity, action))
    manual_light_events = ManualLightAuthorityEvents(
        hass,
        manual_light_authority,
        lambda: _managed_light_entities(),
        None,
        lambda: getattr(
            hass.data.get(entry.domain, {}).get("room_lighting_runtime"),
            "_executor", None,
        ),
        priority_duration=lambda entity, action: getattr(
            hass.data.get(entry.domain, {}).get("room_lighting_runtime"),
            "manual_priority_duration_ms", lambda *_: 3600000,
        )(entity, action),
        single_entity_evidence=lambda candidates: (
            proven_single_entity_evidence(hass, candidates)
        ),
    )
    entry.async_on_unload(manual_light_events.start(buffer_startup=True))
    try:
        await manual_light_authority.async_load()
    except Exception:  # noqa: BLE001 - automatic light remains denied
        _LOGGER.exception("manual light authority unavailable; automatic light is denied")

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
    from .application.away_settings import AwaySettingsService
    from .away_settings_storage import HomeAssistantAwaySettingsStore
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
    domain_data = hass.data.setdefault(entry.domain, {})
    admin_mutation_lock = domain_data.setdefault("admin_mutation_lock", asyncio.Lock())
    device_power_dependency_service = DevicePowerDependencyService(
        HomeAssistantDevicePowerDependencyStore(hass, entry.entry_id),
        entity_pair_validator=lambda dependent, source: (
            hass.states.get(dependent) is not None and hass.states.get(source) is not None
        ),
        mutation_lock=admin_mutation_lock,
    )
    await device_power_dependency_service.async_load()
    await async_migrate_obsolete_small_corridor_power_source(
        device_power_dependency_service
    )
    away_settings_service = AwaySettingsService(
        HomeAssistantAwaySettingsStore(hass, entry.entry_id),
        entity_id_validator=lambda entity_id: hass.states.get(entity_id) is not None,
    )
    await away_settings_service.async_load()
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
    domain_data["settings_service"] = settings_service
    domain_data["tablet_preferences_service"] = tablet_preferences_service
    domain_data["device_power_dependency_service"] = (
        device_power_dependency_service
    )
    domain_data["away_settings_service"] = away_settings_service
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
    # Common manual intake of transferred resources (S-06.a): the running
    # integration transfers no resources, so the registry stays empty and
    # every legacy path is unchanged. A stand setup registers its synthetic
    # resource here explicitly.
    from .application.core_manual_input import CoreManualInput

    core_manual_input = CoreManualInput()
    domain_data["core_manual_input"] = core_manual_input
    scenario_service.set_core_manual_input(core_manual_input)
    await scenario_service.async_load()
    from .application.protected_topology import (
        ROLE_AWAY_SENSOR,
        ROLE_CURTAIN_KITCHEN,
        ROLE_CURTAIN_LIVING,
        ROLE_CURTAIN_OFFICE,
        ROLE_CURTAIN_ROOM,
        ProtectedTopologyService,
        role_targets_payload,
    )
    from .protected_topology_api import (
        DATA_PROTECTED_TOPOLOGY,
        register_protected_topology_api,
    )
    from .protected_topology_storage import HomeAssistantProtectedTopologyStore

    protected_topology = ProtectedTopologyService(
        HomeAssistantProtectedTopologyStore(hass, entry.entry_id),
        catalog_resolver=lambda target_id: scenario_service.current_catalog().device(target_id),
        entity_validator=lambda entity_id: hass.states.get(entity_id) is not None,
        mutation_lock=admin_mutation_lock,
    )
    try:
        await protected_topology.async_load()
    except Exception:  # noqa: BLE001 - fail closed; readiness explains the missing roles
        _LOGGER.warning("protected topology is unavailable; command roles remain disabled")
    domain_data[DATA_PROTECTED_TOPOLOGY] = protected_topology
    register_protected_topology_api(hass)
    scenario_service.set_topology_resolver(protected_topology.target_id)
    away_sensor_entity_id = protected_topology.entity_id(ROLE_AWAY_SENSOR)
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
    from .application.curtain_command_policy import curtain_targets_from_resolver

    curtain_targets = curtain_targets_from_resolver(protected_topology.target_id)
    curtain_target_ids = (
        tuple(curtain_targets[role] for role in ("living", "kitchen", "room", "office"))
        if curtain_targets is not None
        else ()
    )
    office_curtain_target_id = (
        curtain_targets["office"] if curtain_targets is not None else None
    )
    curtain_known_entity_ids = {}
    if curtain_targets is not None:
        for role, protected_role in {
            "living": ROLE_CURTAIN_LIVING,
            "kitchen": ROLE_CURTAIN_KITCHEN,
            "room": ROLE_CURTAIN_ROOM,
            "office": ROLE_CURTAIN_OFFICE,
        }.items():
            entity_id = protected_topology.entity_id(protected_role)
            if isinstance(entity_id, str) and entity_id:
                curtain_known_entity_ids[curtain_targets[role]] = entity_id
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
        office_target_id=office_curtain_target_id or "protected_topology_missing",
    )
    await curtain_scale_confirmation.async_load()
    domain_data["curtain_scale_confirmation"] = curtain_scale_confirmation
    from .application.smart_switch_bindings import (
        HomeAssistantSmartSwitchBindingsStore,
        bindings_from_payload,
    )

    smart_switch_bindings_store = HomeAssistantSmartSwitchBindingsStore(
        hass, entry.entry_id
    )
    try:
        smart_switch_bindings = bindings_from_payload(
            await smart_switch_bindings_store.async_load()
        )
    except Exception:  # noqa: BLE001 - native handover must fail closed
        _LOGGER.warning("smart-switch bindings are unavailable; native handover is disabled")
        smart_switch_bindings = None
    domain_data["smart_switch_bindings_store"] = smart_switch_bindings_store
    # No room supplies release-owned sensor evidence at setup any more. Reset
    # every tracked sensor to unknown so stale evidence can never release the
    # manual-off protection after a restart.
    await manual_light_off_protection.async_restore_release_owned_sensor_evidence({})
    from .application.scenario_light_priority import LightAutomationPriority
    from .light_automation_priority_storage import (
        HomeAssistantLightAutomationPriorityStore,
    )

    light_priority = LightAutomationPriority(
        HomeAssistantLightAutomationPriorityStore(hass, entry.entry_id)
    )
    await light_priority.async_load()
    domain_data["light_automation_priority"] = light_priority
    domain_data["manual_light_authority"] = manual_light_authority
    # Old scenario records do not carry a proved deadline. Copy them into
    # the common store before any automatic event subscription can run.
    for entity_id in light_priority.manual_entity_ids():
        if isinstance(entity_id, str) and entity_id.startswith(("light.", "switch.")):
            manual_light_authority.capture_legacy(entity_id, source="scenario")
    try:
        await manual_light_authority.async_drain()
    except Exception:  # noqa: BLE001
        _LOGGER.exception("legacy manual light migration failed; automation is denied")
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
        target_ids=curtain_target_ids,
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
        manual_light_authority=manual_light_authority,
        managed_light_entity_ids=lambda: _managed_light_entities(),
        automatic_light_writer_conflict=lambda entity_id: _room_writer_conflict(entity_id),
        curtain_command_policy=CurtainCommandPolicy(
            lambda: scenario_control_policy.current,
            scale_authorization_provider=(
                curtain_scale_confirmation.authorization_snapshot
            ),
            position_evidence_policy=CurtainPositionEvidencePolicy(
                known_entity_ids=curtain_known_entity_ids,
            ),
            curtain_targets=curtain_targets if curtain_targets is not None else {},
        ),
        curtain_protection=curtain_protection,
    )
    def _managed_light_entities() -> frozenset[str]:
        from .domain.scenarios import ScenarioAction, ScenarioActionType

        catalog = scenario_service.current_catalog()
        entities = {
            device.entity_id
            for device in catalog.devices.values()
            if isinstance(getattr(device, "entity_id", None), str)
            and device.entity_id.startswith("light.")
        }
        # The verified physical graph may also contain an upstream switch.
        # Its manual actions must fence the paired light even when the switch
        # is not classified as a lighting device by the public catalog.
        entities.update(manual_light_authority.related_source_entities)
        for device in catalog.devices.values():
            entity_id = getattr(device, "entity_id", None)
            if not isinstance(entity_id, str) or not entity_id.startswith("switch."):
                continue
            for action in getattr(device, "actions", ()):
                if light_priority.is_lighting_action(
                    ScenarioAction(
                        id="managed_light_probe",
                        type=ScenarioActionType.DEVICE_ACTION,
                        target_id=device.target_id,
                        action_id=action.action_id,
                    ),
                    catalog,
                ):
                    entities.add(entity_id)
                    break
        runtime = domain_data.get("room_lighting_runtime")
        if runtime is not None:
            entities.update(room_light_entity_ids(runtime.configs()))
        return frozenset(entities)

    def _room_writer_conflict(entity_id: str) -> bool:
        runtime = domain_data.get("room_lighting_runtime")
        if runtime is None:
            # A configured room service still loading its writer map cannot
            # prove that a scenario owns the target exclusively.
            return domain_data.get("room_lighting_service") is not None
        for config in runtime.configs():
            for target in config.devices.light_targets:
                if target.entity_id == entity_id and (
                    config.commands_enabled or not target.auto_control
                ):
                    return True
            power = config.devices.power_switch
            if config.commands_enabled and power is not None and power.entity_id == entity_id:
                return True
            power_rooms = getattr(runtime, "_exact_power_by_entity", {})
            if config.commands_enabled and config.room_id in power_rooms.get(entity_id, set()):
                return True
        return False

    manual_light_events.set_automatic_contexts(scenario_command_contexts)
    entry.async_on_unload(
        light_safety_obligations.start(
            scenario_executor.async_reconcile_light_obligation
        )
    )
    scenario_service.set_executor(scenario_executor)
    from .application.scenario_control_coordinator import (
        SHOWER_SCENARIO_ID,
        SMALL_CORRIDOR_SCENARIO_ID,
        ScenarioControlCoordinator,
        TAMBUR_SCENARIO_ID,
    )
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
        topology_resolver=protected_topology.target_id,
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
            curtain_state = await curtain_protection.async_control_state(
                run_id, trigger
            )
            curtain_topology = role_targets_payload(
                protected_topology.target_id,
                {
                    "living": ROLE_CURTAIN_LIVING,
                    "kitchen": ROLE_CURTAIN_KITCHEN,
                    "room": ROLE_CURTAIN_ROOM,
                    "office": ROLE_CURTAIN_OFFICE,
                },
            )
            curtain_state["topology"] = curtain_topology
            if curtain_topology.get("ready") is not True:
                curtain_state["ready"] = False
                curtain_state["transition"] = "protected_topology_missing"
            context["state"] = curtain_state
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

    activation_latch = ActivationLatch()
    try:
        from .application.managed_switch_binding_migration import (
            HomeAssistantManagedSwitchBindingMigrationStore,
            ManagedSwitchBindingMigration,
            managed_switch_binding_manifest_from_protected_topology,
        )
        from .application.managed_switch_migration import (
            HomeAssistantManagedSwitchMigrationStore,
            ManagedSwitchMigration,
            ManagedSwitchMigrationConflict,
            ManagedSwitchStartupCoordinator,
            async_load_managed_switch_migration_entries,
            managed_switch_manifest_from_protected_topology,
        )
        from .application.native_automation_migration import (
            HomeAssistantNativeAutomationAdapter,
            HomeAssistantNativeAutomationMigrationStore,
            NativeAutomationMigration,
            configure_native_automation_topology,
            native_automation_manifest_from_protected_topology,
        )

        configure_native_automation_topology(
            disable_entity_ids={},
            preserve_entity_ids={},
            expected={},
        )
        native_manifest = native_automation_manifest_from_protected_topology(
            protected_topology
        )
        if native_manifest.get("ready") is not True:
            raise ManagedSwitchMigrationConflict(
                str(native_manifest.get("reason") or "protected_native_topology_missing")
            )
        configure_native_automation_topology(
            disable_entity_ids=native_manifest["disableEntityIds"],  # type: ignore[arg-type]
            preserve_entity_ids=native_manifest["preserveEntityIds"],  # type: ignore[arg-type]
            expected=native_manifest["expected"],  # type: ignore[arg-type]
        )
        managed_manifest = managed_switch_manifest_from_protected_topology(
            protected_topology
        )
        binding_manifest = managed_switch_binding_manifest_from_protected_topology(
            protected_topology
        )
        scenario_service.set_managed_switch_binding_manifest(binding_manifest)
        native_automation_migration = NativeAutomationMigration(
            HomeAssistantNativeAutomationAdapter(hass, smart_switch_bindings),
            HomeAssistantNativeAutomationMigrationStore(hass, entry.entry_id),
        )
        managed_switch_migration_store = HomeAssistantManagedSwitchMigrationStore(
            hass, entry.entry_id, manifest=managed_manifest
        )
        # One lock serialises startup migration writes with the admin re-arm
        # service so a stale expectedReceiptRevision cannot race a live write.
        managed_switch_migration_lock = asyncio.Lock()
        managed_switch_migration = ManagedSwitchMigration(
            scenario_service,
            managed_switch_migration_store,
            source_loader=lambda: async_load_managed_switch_migration_entries(
                hass.async_add_executor_job, managed_manifest
            ),
            native_automation_migration=native_automation_migration,
            manifest=managed_manifest,
            mutation_lock=managed_switch_migration_lock,
        )
        managed_switch_binding_migration = ManagedSwitchBindingMigration(
            scenario_service,
            HomeAssistantManagedSwitchBindingMigrationStore(
                hass, entry.entry_id, manifest=binding_manifest
            ),
            manifest=binding_manifest,
        )
        # The re-arm service is created before startup so the supported recovery
        # route stays reachable even when a stale durable receipt blocks the
        # startup migration itself.
        from .application.managed_switch_migration_rearm import (
            ManagedSwitchMigrationRearmService,
        )
        from .managed_switch_migration_api import (
            DATA_MANAGED_SWITCH_MIGRATION_REARM,
        )

        domain_data[DATA_MANAGED_SWITCH_MIGRATION_REARM] = (
            ManagedSwitchMigrationRearmService(
                managed_switch_migration_store,
                lambda: managed_switch_manifest_from_protected_topology(
                    protected_topology
                ),
                mutation_lock=managed_switch_migration_lock,
            )
        )

        def _publish_managed_switch_status(status: dict[str, str]) -> None:
            domain_data["managed_switch_startup_status"] = dict(status)

        async def _activate_managed_switches() -> None:
            await scenario_service.async_open_managed_switch_execution(managed_manifest)

        managed_switch_startup = ManagedSwitchStartupCoordinator(
            scenario_service,
            managed_switch_migration,
            _activate_managed_switches,
            binding_migration=managed_switch_binding_migration,
            status_publisher=_publish_managed_switch_status,
            manifest=managed_manifest,
        )
        domain_data["managed_switch_startup"] = managed_switch_startup
        entry.async_on_unload(managed_switch_startup.cancel)
        await managed_switch_startup.async_start()
    except Exception as error:  # noqa: BLE001 - missing topology keeps runtime fail-closed
        domain_data["managed_switch_startup_status"] = {
            "state": "blocked",
            "reason": str(error) or "protected_topology_missing",
        }
        _LOGGER.warning("managed switch startup is blocked: %s", error)

    from .managed_switch_migration_api import register_managed_switch_migration_api

    register_managed_switch_migration_api(hass)

    # Tambur was transferred to RoomLightingRuntime.  The coordinator remains
    # active for every other controller but must neither subscribe to Tambur
    # nor reserve its targets, otherwise the new runtime has no writer.  The
    # generic event and schedule adapters keep the transferred scenario
    # excluded as well: a manual trigger would run its managed flow and
    # replay the stale recorded action as a second command source.
    externally_managed_scenarios = frozenset(
        {TAMBUR_SCENARIO_ID, SHOWER_SCENARIO_ID, SMALL_CORRIDOR_SCENARIO_ID}
    )
    scenario_control_coordinator.set_externally_managed_scenarios(
        externally_managed_scenarios
    )
    if getattr(hass, "bus", None) is not None:
        await scenario_control_coordinator.async_start(entry, activation_latch)
        await async_start_scenario_schedule(
            hass,
            entry,
            scenario_service,
            activation_latch,
            scenario_control_coordinator.owned_scenario_ids
            | externally_managed_scenarios,
            curtain_protection,
        )
        from .scenario_events import async_start_scenario_events

        await async_start_scenario_events(
            hass,
            entry,
            scenario_service,
            scenario_command_contexts,
            activation_latch,
            scenario_control_coordinator.owned_scenario_ids
            | externally_managed_scenarios,
        )
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
        automatic_attribution=manual_light_events.automatic_attribution,
    )

    from .error_taxonomy import async_preload_error_policies

    await async_preload_error_policies(hass)
    from .application.away_runtime import AwayRuntime

    async def _run_away_actions(
        actions: tuple[object, ...], correlation_id: str
    ) -> object:
        batch = [
            {
                "targetId": item.target_id,
                "actionId": item.action_id,
                **({"value": item.value} if item.value is not None else {}),
            }
            for item in actions
        ]
        return await scenario_service.async_execute_device_action_batch(
            batch,
            correlation_id=correlation_id,
            request_ids=tuple(
                f"{correlation_id}.{index + 1}" for index in range(len(batch))
            ),
        )

    away_runtime = AwayRuntime(
        settings_provider=lambda: away_settings_service.settings,
        state_provider=lambda entity_id: hass.states.get(entity_id),
        action_runner=_run_away_actions,  # type: ignore[arg-type]
        now_ms=lambda: int(dt_util.utcnow().timestamp() * 1000),
        hass=hass,
    )
    domain_data["away_runtime"] = away_runtime
    await away_runtime.async_start()
    entry.async_on_unload(away_runtime.stop)
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
    from .room_lighting_api import register_room_lighting_api

    register_room_lighting_api(hass, entry.entry_id)
    from .room_lighting_editor_api import (
        register_room_lighting_editor_api,
    )

    register_room_lighting_editor_api(hass)
    from .application.room_lighting_runtime import (
        HomeAssistantRoomLightingOwnershipStore,
        RoomLightingRuntime,
    )
    from .application.room_power_migration import (
        RoomPowerMigrationClaimService,
        RoomPowerMigrationManifestError,
        corrupt_manifest,
    )
    from .application.room_lighting_shadow import (
        HomeAssistantRoomLightingShadowStore,
        RoomLightingShadowService,
    )
    from .room_power_migration_storage import (
        HomeAssistantRoomPowerMigrationManifestStore,
    )
    from .room_lighting_api import (
        DATA_ROOM_LIGHTING_RUNTIME,
        DATA_ROOM_LIGHTING_SERVICE,
    )

    room_lighting_service = domain_data.get(DATA_ROOM_LIGHTING_SERVICE)
    if room_lighting_service is not None:
        room_power_migration_store = HomeAssistantRoomPowerMigrationManifestStore(
            hass, entry.entry_id
        )
        try:
            room_power_migration_claim_service = RoomPowerMigrationClaimService(
                room_power_migration_store,
                room_lighting_service,
                device_power_dependency_service,
                mutation_lock=admin_mutation_lock,
            )
            await room_power_migration_claim_service.async_load()
            power_manifest_healthy = True
        except RoomPowerMigrationManifestError:
            _LOGGER.warning(
                "room lighting power migration claims are damaged or unsupported; "
                "blocking exact-power fallback until the protected store is repaired"
            )
            stored_power_manifest = corrupt_manifest()
            power_manifest_healthy = False
        except Exception:  # noqa: BLE001 - an unreadable store must fail closed
            _LOGGER.warning(
                "room lighting power migration claims are unavailable; "
                "blocking exact-power fallback until storage recovers"
            )
            stored_power_manifest = corrupt_manifest()
            power_manifest_healthy = False
        configs = await room_lighting_service.async_list_configs()
        if manual_light_authority.seeded_empty_store:
            # The first authority file may be created while older room
            # profiles already exist. Their pre-subscription history cannot
            # be reconstructed, so only those configured physical lights
            # retain an unknown manual claim until a later proved release.
            try:
                await manual_light_authority.async_capture_initial_room_scope(
                    target.entity_id
                    for config in configs
                    for target in config.devices.light_targets
                    if isinstance(target.entity_id, str)
                    and target.entity_id.startswith(("light.", "switch."))
                )
            except Exception:  # noqa: BLE001 - startup stays fail closed
                manual_light_authority.mark_unhealthy()
                _LOGGER.exception("initial room light authority migration failed")
        if power_manifest_healthy:
            try:
                room_power_migration_manifest = (
                    await room_power_migration_claim_service.async_merge_live_dependencies(
                        configs,
                        device_power_dependency_service.mapping,
                    )
                )
            except Exception:  # noqa: BLE001 - keep the current protected state
                _LOGGER.warning("room lighting power migration claims were not saved")
                room_power_migration_manifest = room_power_migration_claim_service.manifest
            domain_data["room_power_migration_claim_service"] = (
                room_power_migration_claim_service
            )
        else:
            room_power_migration_manifest = stored_power_manifest
        domain_data["room_power_migration_manifest"] = room_power_migration_manifest
        from .adapters.lighting_mqtt_observations import LightingMqttObservations
        from .adapters.lighting_mqtt_refresh import LightingMqttRefresh
        from homeassistant.components import mqtt as lighting_mqtt

        async def publish_lighting_read(topic: str, payload: str) -> None:
            await lighting_mqtt.async_publish(hass, topic, payload, qos=0, retain=False)

        lighting_observations = LightingMqttObservations(hass.states.get)
        await lighting_observations.async_start(hass)
        domain_data["lighting_mqtt_observations"] = lighting_observations
        entry.async_on_unload(lighting_observations.stop)
        room_lighting_shadow = RoomLightingShadowService(
            HomeAssistantRoomLightingShadowStore(hass, entry.entry_id)
        )
        room_lighting_runtime = RoomLightingRuntime(
            hass,
            room_lighting_service,
            room_lighting_shadow,
            state_reader=lighting_observations.read_state,
            observation_refresher=LightingMqttRefresh(lighting_observations, publish_lighting_read),
            ownership_store=HomeAssistantRoomLightingOwnershipStore(
                hass, entry.entry_id
            ),
            reserved_target_ids=scenario_control_coordinator.command_target_ids,
            reserved_entity_ids_provider=scenario_control_coordinator.command_entity_ids,
            power_dependency_resolver=(
                lambda: device_power_dependency_service.mapping
            ),
            electrical_breaker_resolver=(
                scenario_service.is_electrical_breaker_entity
            ),
            power_migration_manifest=room_power_migration_manifest,
            away_entity_id=away_sensor_entity_id,
            light_priority=light_priority,
            manual_light_authority=manual_light_authority,
            single_entity_evidence=lambda candidates: (
                proven_single_entity_evidence(hass, candidates)
            ),
        )
        domain_data[DATA_ROOM_LIGHTING_RUNTIME] = room_lighting_runtime
        await room_lighting_runtime.start(hass, entry.entry_id)
        entry.async_on_unload(room_lighting_runtime.cancel)
        # Optional new-core shadow branch (HH-014.b lighting path): OFF
        # unless the entry explicitly names a room. The branch never
        # dispatches; the legacy writer above stays the only author.
        core_shadow_room_id = entry.data.get("core_shadow_room_id")
        if isinstance(core_shadow_room_id, str) and core_shadow_room_id:
            from .application.room_lighting_core import (
                CoreBranchConfig,
                RoomLightingCoreBranch,
            )

            room_config = await room_lighting_service.async_get_config(
                core_shadow_room_id
            )
            if room_config is not None:
                try:
                    core_branch = RoomLightingCoreBranch(
                        branch_config=CoreBranchConfig(
                            room_id=core_shadow_room_id,
                            config=room_config,
                        ),
                        store_path=Path(hass.config.config_dir)
                        / f"hausman_hub.core_shadow_{core_shadow_room_id}.json",
                        # The runtime stays the only attribution authority:
                        # its manual holds transfer into the shadow frame
                        # (AUD-LP-ISO01 cycle 6) without dispatch rights.
                        manual_holds_provider=(
                            room_lighting_runtime.core_manual_holds
                        ),
                    )
                    core_branch.activate_shadow()
                except Exception:  # noqa: BLE001 - optional branch isolation
                    _LOGGER.exception(
                        "core lighting branch failed to start; continuing "
                        "without it"
                    )
                else:
                    domain_data["core_lighting_branch"] = core_branch
                    room_lighting_runtime.set_core_input_port(core_branch)
                    entry.async_on_unload(
                        room_lighting_runtime._notify_core_stop
                    )
                    entry.async_on_unload(core_branch.deactivate)
                    # A plain callable like every other unload hook: new
                    # dispatches close at once and a drain task awaits the
                    # in-flight shadow input (AUD-LP-ISO01 cycle 6).
                    entry.async_on_unload(
                        room_lighting_runtime.close_and_drain_core_input
                    )
    # No automatic route may open until room-only light events received while
    # the room catalog loaded have been attributed and durably drained.
    await manual_light_events.async_complete_startup()
    from .critical_sensor_notification_api import (
        register_critical_sensor_notification_api,
    )

    register_critical_sensor_notification_api(
        hass, climate_runtime=climate_runtime
    )
    from .realtime_api import register_event_stream

    register_event_stream(hass, entry.entry_id)
    from .voice_api import async_start_voice_greeting

    await async_start_voice_greeting(hass, entry)
    from .panel import async_register_hausmanhub_panel

    await async_register_hausmanhub_panel(hass)
    # Every listener is registered now.  Opening the gate only at this point
    # avoids reacting to partially restored state, then reconciles durable
    # controllers without granting a restart the right to turn lights on.
    activation_latch.open()
    scenario_control_coordinator.activate()

    # Read-only Zigbee2MQTT availability hint, started LAST after the whole
    # lighting contour is up: it manages exactly its own repairs issue,
    # subscribes through the shared MQTT client and degrades to a no-op on
    # any missing dependency. Its failure must never affect the lighting
    # runtime.
    from .adapters.z2m_availability_advisory import (
        start_z2m_availability_advisory,
    )

    z2m_advisory = None
    try:
        z2m_advisory = await start_z2m_availability_advisory(hass)
    except Exception:  # noqa: BLE001 - the hint must never break setup
        z2m_advisory = None
    if z2m_advisory is not None:
        domain_data["z2m_availability_advisory"] = z2m_advisory

        def _close_z2m_advisory(_entry: ConfigEntry = entry) -> None:
            stored = hass.data.get(entry.domain, {}).get(
                "z2m_availability_advisory"
            )
            if stored is z2m_advisory:
                domain_data.pop("z2m_availability_advisory", None)
            close_advisory = getattr(z2m_advisory, "close", None)
            if callable(close_advisory):
                close_advisory()

        entry.async_on_unload(_close_z2m_advisory)

    # Bounded read-only startup recovery: after Home Assistant starts, run a
    # limited number of GET-only observation refreshes for rooms whose only
    # blocker is a restart with unknown device states. A responsive device's
    # real state becomes usable evidence; the pass itself releases nothing
    # and dispatches nothing. Window arithmetic and timers live in the pure
    # application module; this edge only wires Home Assistant ports. The
    # window state lives at process level so an entry reload cannot silently
    # reopen a fresh hour or shorten the interval.
    from homeassistant.core import callback as _startup_recovery_callback
    from homeassistant.helpers.event import async_call_later
    from homeassistant.helpers.start import async_at_started

    import time as _startup_recovery_time

    from .application.startup_recovery_window import (
        StartupRecoveryWindow,
        new_window_state,
    )

    _RECOVERY_STATE_KEY = "hausman_hub_startup_recovery_window"

    _recovery_state = hass.data.setdefault(
        _RECOVERY_STATE_KEY, new_window_state()
    )

    async def _startup_recovery_pass_task() -> None:
        # A short bounded wait for the MQTT client; failure consumes this
        # pass. There is no private MQTT retry loop. The window is
        # re-checked after the wait: a deadline reached while waiting must
        # not turn into a late pass.
        try:
            from homeassistant.components import mqtt as _recovery_mqtt
            async with asyncio.timeout(5):
                ready = await _recovery_mqtt.async_wait_for_mqtt_client(hass)
            if ready is False:
                return
        except Exception:  # noqa: BLE001 - transport problems are not crashes
            return
        if not _recovery_window.is_open():
            return
        try:
            await room_lighting_runtime.async_startup_recovery_pass(
                max_rooms=4,
            )
        except Exception:  # noqa: BLE001 - a failed pass never breaks the home
            _LOGGER.warning("startup lighting recovery pass failed", exc_info=True)

    def _schedule_startup_recovery_timer(delay_s, action):
        # Home Assistant invokes timer callbacks WITH the fire time; the
        # window's port contract is zero-arg. Adapt strictly at this HA
        # boundary: swallow the time argument, call the action exactly once.
        return async_call_later(
            hass,
            delay_s,
            _startup_recovery_callback(lambda *_args: action()),
        )

    _recovery_window = StartupRecoveryWindow(
        _recovery_state,
        now=_startup_recovery_time.monotonic,
        schedule_timer=_schedule_startup_recovery_timer,
        spawn=lambda factory: hass.async_create_task(factory()),
        cancel=lambda handle: handle.cancel(),
        is_done=lambda handle: handle.done(),
        action=_startup_recovery_pass_task,
    )

    def _startup_recovery_at_started(_hass: HomeAssistant) -> None:
        _recovery_window.on_started()

    _recovery_unsub_started = async_at_started(
        hass, _startup_recovery_callback(_startup_recovery_at_started)
    )

    def _close_startup_recovery(_entry: ConfigEntry = entry) -> None:
        _recovery_window.close()
        try:
            _recovery_unsub_started()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            _LOGGER.debug("startup recovery unsubscribe failed")

    entry.async_on_unload(_close_startup_recovery)

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
    from .room_lighting_api import clear_room_lighting_api
    from .room_lighting_editor_api import clear_room_lighting_editor_api
    from .critical_sensor_notification_api import (
        clear_critical_sensor_notification_api,
    )

    clear_event_stream(hass, entry.entry_id)
    clear_voice_greeting(hass, entry.entry_id)
    clear_water_safety_api(hass)
    clear_operation_journal(hass)
    clear_tablet_power_api(hass)
    clear_manual_light_off_protection_api(hass)
    clear_room_lighting_api(hass)
    clear_room_lighting_editor_api(hass)
    clear_critical_sensor_notification_api(hass)
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
    from .room_lighting_api import clear_room_lighting_api
    from .room_lighting_editor_api import clear_room_lighting_editor_api
    from .critical_sensor_notification_api import (
        clear_critical_sensor_notification_api,
    )

    loaded_entries = tuple(hass.config_entries.async_loaded_entries(domain))
    for loaded_entry in loaded_entries:
        clear_local_summary_access(hass, loaded_entry)
        clear_event_stream(hass, loaded_entry.entry_id)
        clear_voice_greeting(hass, loaded_entry.entry_id)
        clear_water_safety_api(hass)
        clear_operation_journal(hass)
        clear_tablet_power_api(hass)
        clear_manual_light_off_protection_api(hass)
        clear_room_lighting_api(hass)
        clear_room_lighting_editor_api(hass)
        clear_critical_sensor_notification_api(hass)
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
    from .room_lighting_api import clear_room_lighting_api
    from .room_lighting_editor_api import clear_room_lighting_editor_api

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
        clear_room_lighting_api(hass)
        clear_room_lighting_editor_api(hass)
        clear_climate_api(hass, entry.entry_id)
        # The availability hint stops listening on unload, but an already
        # created repairs issue intentionally stays visible (stop must not
        # delete the hint; only a bridge message with enabled=true removes it).
        z2m_advisory = hass.data.get(entry.domain, {}).get(
            "z2m_availability_advisory"
        )
        if z2m_advisory is not None:
            close_advisory = getattr(z2m_advisory, "close", None)
            if callable(close_advisory):
                close_advisory()
        hass.data.get(entry.domain, {}).pop(
            "z2m_availability_advisory", None
        )
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
