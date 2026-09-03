"""Orchestrate registry, state import, tablet projection, and typed actions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import logging
import math
import re
import time
from typing import Protocol

from ..domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..climate_revision import MAX_JS_SAFE_INTEGER, is_control_revision
from ..domain.climate_command_guard import ClimateCommandGuardMemory
from ..domain.ai_assistant_json import AiJsonObject
from ..domain.climate_comparison import ClimateComparisonSnapshot
from ..domain.climate_demand import ClimateDemandSnapshot
from ..domain.climate_equipment import ClimateEquipmentSnapshot
from ..domain.climate_ha_calls import (
    ClimateHaCallPlanSnapshot,
    ClimateHaService,
    ClimateHaServiceCall,
)
from ..domain.climate_isolation import ClimateIsolationSnapshot
from ..domain.climate_ownership import ClimateOwnershipReceipt
from ..domain.climate_observation import (
    ClimateDataStatus,
    ClimateDeviceAvailability,
    ClimateObservationSnapshot,
    ClimateObservationViolation,
)
from ..domain.climate_policy import ClimatePolicySnapshot
from ..domain.climate_protection import (
    ClimateProtectionMemory,
    empty_climate_protection_memory,
)
from ..domain.climate_manual import (
    ClimateManualMemory,
    ClimateManualViolation,
    empty_climate_manual_memory,
)
from ..domain.climate_resolution import ClimateResolutionSnapshot
from ..domain.climate_stability import ClimateStabilitySnapshot
from ..domain.climate_bridge import ClimateControlMode
from ..domain.climate_trial import ClimateTrialReceipt, ClimateTrialReason
from ..domain.configuration import SafeConfiguration
from ..domain.contours import ContourDefinition, ContourMode, ContourRegistry
from ..domain.native_climate import NativeClimatePolicy, preview_native_climate
from ..domain.climate_targets import ClimateTargetSnapshot
from .climate_application import ClimateDesiredStateChanges
from .climate_tablet import (
    CLIMATE_ACTION_CONTRACT_NAME,
    CLIMATE_TABLET_CONTRACT_VERSION,
    ClimateTabletViolation,
    parse_climate_tablet_action,
)
from .climate_command_guard import (
    GuardedClimatePlan,
    GuardedDeviceCalls,
    climate_call_is_satisfied,
    clear_aligned_climate_commands,
    empty_climate_command_guard,
    full_climate_synchronization_plan,
    guard_diverged_climate_calls,
    reconcile_climate_command_guard,
    reserve_guarded_commands,
    reserve_scheduled_synchronization,
)
from .climate_deviation_guard import ClimateDeviationGuardService
from .climate_area_assignment import (
    ClimateAreaAssignmentPort,
    climate_area_assignment_targets,
)
from .climate_device_bindings import (
    apply_climate_device_bindings,
    climate_device_binding_options,
    preview_climate_device_bindings,
    reconcile_native_climate_registry,
)
from .climate_equipment import build_climate_equipment_snapshot
from .ai_assistant_evidence import ai_evidence_from_observation
from .climate_ha_adapters import build_climate_ha_call_plan
from .ir_code_service import IRCodeService
from .climate_ha_observations import (
    MAX_NATIVE_STATE_AGE_MS,
    OUTDOOR_SOURCE_DIVERGENCE_ALERT_C,
    ClimateHaObservationViolation,
    ClimateHaStateView,
    build_native_ha_climate_observation,
)
from .climate_discovery import ClimateImportSnapshot
from .climate_isolation import build_isolated_climate_policy_snapshot
from .climate_native_projections import (
    native_readiness_reasons,
    native_admin_climate_import_snapshot,
    native_android_climate_snapshot,
    native_climate_readiness,
    native_climate_reconciliation,
    native_contour_apply_preview,
    native_contour_snapshot,
)
from .climate_migration import (
    ClimateMigrationReceipt,
    rollback_migrated_setup,
)
from .legacy_settings_apply import build_legacy_settings_apply
from .settings_service import HausmanHubSettingsService
from .climate_native_setup import build_native_climate_setup_snapshot
from .climate_comparison import build_climate_comparison_snapshot
from .climate_demands import build_climate_demand_snapshot
from .climate_observations import (
    unavailable_climate_observation_snapshot,
)
from .climate_policy import build_climate_policy_snapshot
from .climate_protection import (
    reconcile_climate_protection_memory,
    update_climate_protection,
)
from .climate_manual import (
    apply_manual_rooms,
    contour_without_manual_devices,
    effective_manual_room_ids,
    reconcile_climate_manual_memory,
    record_direct_wifi_commands,
    update_climate_manual_observation,
    with_climate_device_mode,
    with_climate_room_mode,
)
from .climate_resolutions import build_climate_resolution_snapshot
from .climate_stability import build_climate_stability_snapshot
from .climate_trial_control import (
    climate_trial_applied_receipt,
    climate_trial_failure_receipt,
    climate_trial_skip_receipt,
    plan_climate_trial,
)
from .climate_ownership import (
    climate_ownership_failure_receipt,
    climate_ownership_promoted_receipt,
    climate_ownership_skip_receipt,
    plan_room_promotion,
)
from .climate_rollout import climate_rollout_status
from .climate_cutover import climate_cutover_status
from .climate_registry import (
    ClimateRegistryViolation,
    reconcile_climate_registry,
    registry_from_payload,
    registry_to_payload,
)
from .climate_setup import (
    build_climate_contour_draft_setup,
    climate_draft_save_receipt,
    climate_ir_code_bindings,
    climate_setup_revision,
    climate_setup_options,
    create_climate_contour_draft,
    current_climate_contour_setup,
    update_climate_profiles,
    update_climate_schedule,
    validate_climate_contour_draft,
)
from .climate_signal_settings import interseason_settings_wire
from .climate_targets import build_climate_target_snapshot
from .contours import (
    CLIMATE_CONTOUR_ID,
    ContourRegistryViolation,
    contour_registry_from_payload,
    contour_registry_to_payload,
    contour_snapshot,
    validate_contour_bindings,
    with_active_climate_profile,
    with_applied_climate_schedule_profile,
    with_home_climate_targets,
    with_room_climate_humidity,
    with_room_climate_minimum_temperature,
    with_room_climate_target_strategy,
    with_climate_temporary_temperature,
    without_climate_temporary_temperature,
)
from .contour_apply import (
    CONTOUR_APPLY_CONTRACT_VERSION,
    CONTOUR_APPLY_PREVIEW_CONTRACT_NAME,
    ClimateControlAction,
    ClimateControlContext,
    ContourApplyPlan,
    ContourApplyReceipt,
    ContourApplyStatus,
    ContourApplyViolation,
    _ContourApplyLedger,
    build_contour_apply_plan,
    contour_fingerprint,
    local_desired_state_changes,
    parse_contour_apply_request,
)
from .climate_application_models import ClimateTargetAxis

_CLIMATE_READBACK_ATTEMPTS = 33
_CLIMATE_READBACK_INTERVAL_SECONDS = 0.25
_CLIMATE_DEVIATION_OFF_READBACK_ATTEMPTS = 20

_LOGGER = logging.getLogger(__name__)
from .contour_override import (
    TemporaryTemperatureAction,
    TemporaryTemperatureViolation,
    parse_temporary_temperature_request,
)
from .home_climate_targets import (
    HomeClimateTargetsViolation,
    parse_home_climate_targets_request,
)


class ClimateRuntimeUnavailable(RuntimeError):
    """The climate surface cannot provide a safe complete result."""


class ClimateSnapshotUnavailable(ClimateRuntimeUnavailable):
    """The public snapshot is safely absent because climate is not observable."""


def _recovery_pre_dispatch_unavailable(message: str) -> ClimateRuntimeUnavailable:
    """Mark a recovery rejection that occurred before the HA boundary."""

    error = ClimateRuntimeUnavailable(message)
    error.recovery_pre_dispatch = True
    return error


class ClimateRegistryStorage(Protocol):
    """Minimal versioned registry persistence boundary."""

    async def async_load(self) -> ClimateRegistry:
        """Load a complete validated registry."""

    async def async_save(self, registry: ClimateRegistry) -> None:
        """Atomically save one complete validated registry."""


class ContourStorage(Protocol):
    """Minimal versioned persistence boundary for HausmanHub contour definitions."""

    async def async_load(self) -> ContourRegistry:
        """Load a complete validated contour registry."""

    async def async_save(self, registry: ContourRegistry) -> None:
        """Atomically save one complete contour registry."""


class ClimateProtectionStorage(Protocol):
    """Minimal persistence boundary for restart-safe transition facts."""

    async def async_load(self) -> ClimateProtectionMemory | None:
        """Load validated protection memory when it exists."""

    async def async_save(self, memory: ClimateProtectionMemory) -> None:
        """Atomically save one complete protection memory."""


class ClimateManualStorage(Protocol):
    """Minimal persistence boundary for direct Wi-Fi manual ownership."""

    async def async_load(self) -> ClimateManualMemory | None:
        """Load validated manual-control memory when it exists."""

    async def async_save(self, memory: ClimateManualMemory) -> None:
        """Atomically save one complete manual-control memory."""


class ClimateCommandGuardStorage(Protocol):
    """Minimal persistence boundary for repeated-command suppression."""

    async def async_load(self) -> ClimateCommandGuardMemory | None:
        """Load validated command guard memory when it exists."""

    async def async_save(self, memory: ClimateCommandGuardMemory) -> None:
        """Atomically save one complete command guard memory."""


class ClimateStrictHaCallExecutor(Protocol):
    """Minimal execution boundary for one permitted strict HA batch."""

    async def async_execute(self, calls: tuple[ClimateHaServiceCall, ...]) -> int:
        """Execute strict calls in order; return the completed count."""


_PASSIVE_KINDS = frozenset(
    {
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        ClimateDeviceKind.HUMIDITY_SENSOR,
    }
)


@dataclass(frozen=True, slots=True)
class _ClimateRoomModeReceipt:
    """Minimal confirmed result consumed by the existing tablet operation layer."""

    status: ContourApplyStatus = ContourApplyStatus.CONFIRMED
    confirmed_room_count: int = 1
    accepted_count: int = 1


@dataclass(frozen=True, slots=True)
class _ClimateSynchronizationResult:
    """Minimal typed result consumed by the tablet operation layer."""

    status: ContourApplyStatus
    confirmed_room_count: int
    accepted_count: int


class ClimateRuntime:
    """One loaded HausmanHub entry's climate facade and rollout state."""

    def __init__(
        self,
        *,
        entry_id: str,
        configuration: SafeConfiguration,
        registry_store: ClimateRegistryStorage,
        contour_store: ContourStorage | None = None,
        protection_store: ClimateProtectionStorage | None = None,
        manual_store: ClimateManualStorage | None = None,
        command_guard_store: ClimateCommandGuardStorage | None = None,
        deviation_guard: ClimateDeviationGuardService | None = None,
        strict_ha_call_executor: ClimateStrictHaCallExecutor | None = None,
        ha_state_view: ClimateHaStateView | None = None,
        ha_area_assignment: ClimateAreaAssignmentPort | None = None,
        ir_code_service: IRCodeService | None = None,
        operation_id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
        local_now: Callable[[], datetime] | None = None,
        direct_control_store: object | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.configuration = configuration
        self._registry_store = registry_store
        self._contour_store = contour_store
        self._protection_store = protection_store
        self._manual_store = manual_store
        self._command_guard_store = command_guard_store
        self._deviation_guard = deviation_guard
        self._strict_ha_call_executor = strict_ha_call_executor
        self._ha_state_view = ha_state_view
        self._ha_area_assignment = ha_area_assignment
        self._ir_code_service = ir_code_service
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._local_now = local_now or (lambda: datetime.now().astimezone())
        self._direct_control_store = direct_control_store
        self._registry = ClimateRegistry()
        self._contours = ContourRegistry()
        self._protection_memory = empty_climate_protection_memory(updated_at=0)
        self._manual_memory = empty_climate_manual_memory(updated_at=0)
        self._command_guard_memory = empty_climate_command_guard(updated_at=0)
        self._protection_restart_after: int | None = None
        self._weather_heating_lockout: bool | None = None
        self._central_heating_on: bool | None = None
        self._outdoor_temperature_source: str = "none"
        self._outdoor_source_divergence_c: float | None = None
        self._outdoor_divergence_alerted = False
        self._lock = asyncio.Lock()
        self._contour_applications = _ContourApplyLedger(
            operation_id_factory=operation_id_factory,
            now_ms=self._now_ms,
        )
        # The negotiated control profile uses this revision as an optimistic
        # concurrency token. Legacy callers do not see or depend on it.
        self._control_revision = 0
        self._last_contour_apply_was_duplicate = False
        self._recovery_private_metadata: dict[tuple[str, str], dict[str, object]] = {}
        self.last_error: str | None = None
        self._direct_control_validation_failed = False

    @property
    def room_count(self) -> int:
        """Return only a non-sensitive registry count for diagnostics."""

        return len(self._registry.rooms)

    @property
    def device_count(self) -> int:
        """Return only a non-sensitive registry count for diagnostics."""

        return len(self._registry.devices)

    @property
    def status(self) -> str:
        """Return a coarse redacted runtime status."""

        if self.last_error is not None:
            return "unavailable"
        if self.configuration.climate_bridge_mode is ClimateControlMode.DISABLED:
            return "disabled"
        if self._registry is None:
            return "not_refreshed"
        return "fresh"

    @property
    def direct_control_validation_failed(self) -> bool:
        """Whether startup rejected untrusted durable direct-control history."""

        return self._direct_control_validation_failed

    @property
    def outdoor_temperature_source(self) -> str:
        """Return which outdoor input the last observation used, id-free."""

        return self._outdoor_temperature_source

    @property
    def outdoor_source_divergence_c(self) -> float | None:
        """Return the last physical-vs-service outdoor gap for diagnostics."""

        return self._outdoor_source_divergence_c

    def _track_outdoor_source(self, observation: ClimateObservationSnapshot) -> None:
        """Log source switches and cross-check gaps without blocking commands."""

        home = observation.home
        source = home.outdoor_temperature_source.value
        if source != self._outdoor_temperature_source:
            _LOGGER.info(
                "climate outdoor temperature source: %s (service cross-check: %s)",
                source,
                (
                    f"{home.outdoor_provider_temperature:.1f} C"
                    if home.outdoor_provider_temperature is not None
                    else "unavailable"
                ),
            )
            self._outdoor_temperature_source = source
        divergence = home.outdoor_source_divergence_c
        self._outdoor_source_divergence_c = divergence
        if divergence is None:
            self._outdoor_divergence_alerted = False
            return
        if divergence > OUTDOOR_SOURCE_DIVERGENCE_ALERT_C:
            if not self._outdoor_divergence_alerted:
                _LOGGER.warning(
                    "climate outdoor sources diverge by %.1f C: physical sensor "
                    "%.1f C is primary, weather service reports %.1f C; "
                    "commands stay unblocked",
                    divergence,
                    home.outdoor_temperature,
                    home.outdoor_provider_temperature,
                )
                self._outdoor_divergence_alerted = True
        elif self._outdoor_divergence_alerted:
            _LOGGER.info(
                "climate outdoor sources agree again within %.1f C",
                divergence,
            )
            self._outdoor_divergence_alerted = False

    async def async_start(self) -> None:
        """Load local registry and best-effort initial read-only state."""

        async with self._lock:
            validating_direct_control = False
            self._direct_control_validation_failed = False
            try:
                registry = await self._registry_store.async_load()
                contours = (
                    await self._contour_store.async_load()
                    if self._contour_store is not None
                    else ContourRegistry()
                )
                registry_changed = False
                if self._ha_state_view is not None:
                    try:
                        registry, registry_changed = reconcile_native_climate_registry(
                            registry,
                            self._native_entity_catalog_unlocked(),
                        )
                    except Exception:
                        # Endpoint recovery is a best-effort compatibility repair.
                        # A broken or not-yet-ready HA catalog must not make an
                        # otherwise valid persisted registry unavailable.
                        registry_changed = False
                validate_contour_bindings(contours, registry)
                if registry_changed:
                    await self._registry_store.async_save(registry)
                self._registry = registry
                self._contours = contours
                if self._direct_control_store is not None:
                    loader = getattr(self._direct_control_store, "async_load_direct_control", None)
                    if not callable(loader):
                        raise ClimateRuntimeUnavailable("direct control store is invalid")
                    authenticated = getattr(
                        self._direct_control_store,
                        "async_direct_control_is_authenticated", None,
                    )
                    trusted_history = await authenticated() if callable(authenticated) else False
                    records = await loader()
                    has_untrusted_records = (
                        not trusted_history
                        and records is not None
                        and records != []
                    )
                    validating_direct_control = has_untrusted_records
                    restore_arguments: dict[str, object] = {}
                    if has_untrusted_records:
                        restore_arguments = {
                            "authoritative_contour": self._climate_contour(),
                            "authoritative_registry": self._registry,
                        }
                    self._contour_applications.restore(
                        records, **restore_arguments,
                    )
                    validating_direct_control = False
                    self._control_revision = self._contour_applications.control_revision
                    revision_loader = getattr(self._direct_control_store, "async_current_control_revision", None)
                    if callable(revision_loader):
                        shared_revision = await revision_loader()
                        if (
                            not is_control_revision(shared_revision)
                            or shared_revision < self._control_revision
                        ):
                            raise ClimateRuntimeUnavailable("shared climate control revision is invalid")
                        self._control_revision = shared_revision
                now = self._safe_now()
                loaded_protection = (
                    await self._protection_store.async_load()
                    if self._protection_store is not None
                    else None
                )
                protection = loaded_protection or empty_climate_protection_memory(
                    updated_at=now
                )
                protection, protection_changed = (
                    reconcile_climate_protection_memory(
                        protection,
                        self._registry,
                        now_ms=now,
                    )
                )
                self._protection_memory = protection
                self._protection_restart_after = (
                    now
                    if loaded_protection is not None and protection.devices
                    else None
                )
                if loaded_protection is None or protection_changed:
                    await self._async_save_protection(self._protection_memory)
                loaded_manual = (
                    await self._manual_store.async_load()
                    if self._manual_store is not None
                    else None
                )
                manual = loaded_manual or empty_climate_manual_memory(
                    updated_at=now
                )
                manual, manual_changed = reconcile_climate_manual_memory(
                    manual,
                    self._registry,
                    now_ms=now,
                )
                self._manual_memory = manual
                if loaded_manual is None or manual_changed:
                    await self._async_save_manual(self._manual_memory)
                loaded_command_guard = (
                    await self._command_guard_store.async_load()
                    if self._command_guard_store is not None
                    else None
                )
                command_guard = loaded_command_guard or empty_climate_command_guard(
                    updated_at=now
                )
                command_guard, command_guard_changed = (
                    reconcile_climate_command_guard(
                        command_guard,
                        self._registry,
                        now_ms=now,
                    )
                )
                self._command_guard_memory = command_guard
                if loaded_command_guard is None or command_guard_changed:
                    await self._async_save_command_guard(command_guard)
                if self._deviation_guard is not None:
                    await self._deviation_guard.async_load(
                        tuple(
                            device.device_id
                            for device in self._registry.devices
                            if device.kind is ClimateDeviceKind.AIR_CONDITIONER
                        )
                    )
                self.last_error = None
            except Exception as error:
                # Base HausmanHub remains available; climate endpoints fail closed and
                # an administrator can replace a damaged local registry.
                self.last_error = type(error).__name__
                self._direct_control_validation_failed = validating_direct_control

    async def async_dashboard_climate_targets(
        self,
    ) -> dict[str, tuple[float, int]]:
        """Return effective HausmanHub comfort goals for the shared dashboard."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return {}
            return {
                room.room_id: (
                    room.active_settings.target_temperature,
                    room.active_settings.target_humidity,
                )
                for room in contour.rooms
            }

    async def async_dashboard_outdoor_temperature_entity_ids(self) -> tuple[str, ...]:
        """Return the configured outdoor sources in failover order for the dashboard."""

        async with self._lock:
            return self._registry.home.prioritized_outdoor_temperature_entity_ids

    async def async_dashboard_climate_ownership(self) -> dict[str, dict[str, str]]:
        """Return private lookup keys used to annotate shared device cards."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return {"rooms": {}, "entities": {}}
            assigned_device_ids = {
                device_id
                for room in contour.rooms
                for device_id in room.device_ids
            }
            manual_room_ids = set(
                effective_manual_room_ids(self._manual_memory, self._registry)
            )
            manual_device_ids = set(self._manual_memory.manual_device_ids)
            rooms = {
                room.room_id: (
                    "manual" if room.room_id in manual_room_ids else "automatic"
                )
                for room in contour.rooms
            }
            entities: dict[str, str] = {}
            for device in self._registry.devices:
                if (
                    device.device_id not in assigned_device_ids
                    or device.kind is not ClimateDeviceKind.AIR_CONDITIONER
                ):
                    continue
                endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
                if endpoint is None:
                    continue
                entities[endpoint.entity_id] = (
                    "manual"
                    if device.room_id in manual_room_ids
                    or device.device_id in manual_device_ids
                    else "automatic"
                )
            return {"rooms": rooms, "entities": entities}

    async def async_public_snapshot(self) -> dict[str, object]:
        """Refresh and return the private-id-free tablet contract."""

        async with self._lock:
            await self._async_sync_control_revision_unlocked()
            if (
                self.configuration.climate_bridge_mode is ClimateControlMode.MANAGED
                or self.configuration.mode == "shadow"
            ):
                observation = await self._async_native_climate_observation_unlocked(
                    allow_disabled=(
                        self.configuration.climate_bridge_mode
                        is ClimateControlMode.DISABLED
                    )
                )
                if observation.data_status is ClimateDataStatus.UNAVAILABLE:
                    raise ClimateSnapshotUnavailable("climate state is unavailable")
                manual_reasons = {
                    item.device_id: item.reason
                    for item in self._manual_memory.attributions
                }
                self._recovery_private_metadata = {
                    (device.room_id, device.device_id): {
                        "manual_reason": manual_reasons.get(device.device_id),
                        "source_observed_at": (
                            observed.observed_at
                            if observed is not None
                            and observed.availability is ClimateDeviceAvailability.AVAILABLE
                            else None
                        ),
                        "reported_target_temperature": (
                            observed.current_target_temperature
                            if observed is not None else None
                        ),
                        "reported_target_humidity": (
                            observed.current_target_humidity
                            if observed is not None else None
                        ),
                    }
                    for device in self._registry.devices
                    for observed in (observation.device(device.device_id),)
                }
                snapshot = native_android_climate_snapshot(
                    self._registry,
                    observation,
                    contours=self._contours,
                    bridge_mode=self.configuration.climate_bridge_mode,
                    pending_room_ids=(),
                    manual_device_ids=self._manual_memory.manual_device_ids,
                    manual_changed_at={
                        device_id: self._manual_memory.updated_at
                        for device_id in self._manual_memory.manual_device_ids
                    },
                    manual_reasons=manual_reasons,
                    local_now=self._local_now(),
                )
                return self._with_deviation_guard_status(snapshot)
            raise ClimateSnapshotUnavailable("climate bridge is disabled")

    async def async_recovery_private_metadata(
        self,
    ) -> dict[tuple[str, str], dict[str, object]]:
        """Return redacted per-device proof data for the local recovery service.

        This is deliberately not an HTTP surface and is never included in the
        versioned v12 home document.
        """

        async with self._lock:
            return {
                key: dict(value)
                for key, value in self._recovery_private_metadata.items()
            }

    async def async_admin_import_snapshot(self) -> dict[str, object]:
        """Refresh and return private discovery data for a local admin."""

        async with self._lock:
            if self.configuration.climate_bridge_mode is ClimateControlMode.MANAGED:
                observation = await self._async_native_climate_observation_unlocked()
                if observation.data_status is ClimateDataStatus.UNAVAILABLE:
                    raise ClimateRuntimeUnavailable("climate state is unavailable")
                return native_admin_climate_import_snapshot(
                    self._registry,
                    observation,
                )
            raise ClimateRuntimeUnavailable("climate bridge is disabled")

    async def async_create_contour_draft(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Create an unsaved draft after one read-only discovery refresh."""

        async with self._lock:
            snapshot = await self._async_native_setup_snapshot_unlocked()
            return create_climate_contour_draft(
                self._registry,
                snapshot,
                payload,
                contours=self._contours,
            )

    async def async_climate_setup_options(self) -> dict[str, object]:
        """Return current safe choices for the local climate setup form."""

        async with self._lock:
            snapshot = await self._async_native_setup_snapshot_unlocked()
            if self._ha_state_view is None:
                raise ClimateRuntimeUnavailable("climate state is unavailable")
            return climate_setup_options(
                self._registry,
                snapshot,
                self._ha_state_view.ir_remote_catalog(),
            )

    async def async_climate_device_binding_options(self) -> dict[str, object]:
        """Return explicit native HA entity choices for saved devices."""

        async with self._lock:
            catalog = self._native_entity_catalog_unlocked()
            await self._async_reconcile_native_registry_unlocked(catalog)
            return climate_device_binding_options(
                self._registry,
                catalog,
            )

    async def async_preview_climate_device_bindings(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Check native HA bindings without persistence or commands."""

        async with self._lock:
            return preview_climate_device_bindings(
                self._registry,
                self._native_entity_catalog_unlocked(),
                payload,
            )

    async def async_save_climate_device_bindings(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Persist one unchanged checked binding set without commands."""

        async with self._lock:
            registry, receipt = apply_climate_device_bindings(
                self._registry,
                self._native_entity_catalog_unlocked(),
                payload,
            )
            validate_contour_bindings(self._contours, registry)
            await self._registry_store.async_save(registry)
            self._registry = registry
            self._central_heating_on = None
            self.last_error = None
            return receipt

    async def async_assign_home_assistant_areas(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Validate and atomically persist physical room assignments in HA."""

        async with self._lock:
            if self._ha_area_assignment is None:
                raise ClimateRuntimeUnavailable("HA area assignment is unavailable")
            snapshot = await self._async_native_setup_snapshot_unlocked()
            targets = climate_area_assignment_targets(
                self._registry,
                snapshot,
                payload,
            )
            return await self._ha_area_assignment.async_assign(targets)

    async def async_current_contour_setup(self) -> dict[str, object]:
        """Return saved editor values without persistence or commands."""

        async with self._lock:
            snapshot = await self._async_native_setup_snapshot_unlocked()
            return current_climate_contour_setup(
                self._registry,
                self._contours,
                snapshot,
            )

    async def async_ir_code_bindings(self) -> dict[str, object]:
        """Return current raw-remote bindings for the local IR admin API."""

        async with self._lock:
            snapshot = await self._async_native_setup_snapshot_unlocked()
            return climate_ir_code_bindings(
                self._registry,
                self._contours,
                snapshot,
            )

    async def async_validate_ir_code_binding(
        self, device_id: str, remote_entity_id: str
    ) -> str | None:
        """Return a public violation when an IR request misses its saved raw remote."""

        async with self._lock:
            device = self._registry.device(device_id)
            if device is None:
                return "IR code device is not part of the saved climate contour."
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None or not any(
                device_id in assignment.device_ids for assignment in contour.rooms
            ):
                return "IR code device is not part of the saved climate contour."
            endpoint = next(
                (
                    item
                    for item in device.endpoints
                    if item.role.value == "control"
                ),
                None,
            )
            if (
                endpoint is None
                or not endpoint.entity_id.startswith("remote.")
                or endpoint.entity_id != remote_entity_id
            ):
                return "IR code remote does not match the saved device control endpoint."
            return None

    async def async_validate_contour_draft(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Validate one draft without persistence, commands, or shadow evidence."""

        async with self._lock:
            snapshot = await self._async_native_setup_snapshot_unlocked()
            return validate_climate_contour_draft(
                self._registry,
                snapshot,
                payload,
                contours=self._contours,
            )

    async def async_save_contour_draft(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Validate and atomically save one unchanged climate contour draft."""

        async with self._lock:
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            snapshot = await self._async_native_setup_snapshot_unlocked()
            registry, contours, validation = build_climate_contour_draft_setup(
                self._registry,
                snapshot,
                payload,
                contours=self._contours,
            )
            await self._async_persist_contour_setup_unlocked(registry, contours)
            return climate_draft_save_receipt(payload, validation)

    async def async_update_climate_profiles(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Atomically save day/night profiles without sending commands."""

        async with self._lock:
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            updated, receipt = update_climate_profiles(
                self._registry,
                self._contours,
                payload,
                saved_at=self._safe_now(),
                automatic_application_enabled=(
                    self.configuration.climate_bridge_mode
                    is ClimateControlMode.MANAGED
                ),
            )
            await self._contour_store.async_save(updated)
            self._contours = updated
            self.last_error = None
            return receipt

    async def async_update_climate_schedule(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Atomically save the day/night schedule without sending commands."""

        async with self._lock:
            # Disarming must remain available even in canary, shadow, or disabled
            # bridge modes. The strict use case below rejects every enabling
            # request unless this runtime is explicitly managed.
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            updated, receipt = update_climate_schedule(
                self._registry,
                self._contours,
                payload,
                saved_at=self._safe_now(),
                automatic_application_enabled=(
                    self.configuration.climate_bridge_mode
                    is ClimateControlMode.MANAGED
                ),
            )
            await self._contour_store.async_save(updated)
            self._contours = updated
            self.last_error = None
            return receipt

    async def async_registry_import_snapshot(self) -> ClimateImportSnapshot:
        """Refresh one typed read-only snapshot for the local options wizard."""

        async with self._lock:
            return await self._async_native_setup_snapshot_unlocked()

    async def async_registry_payload(self) -> dict[str, object]:
        """Return the exact private registry shape to a local admin."""

        async with self._lock:
            return registry_to_payload(self._registry)

    async def async_contour_registry_payload(self) -> dict[str, object]:
        """Return the exact public-id-only contour configuration."""

        async with self._lock:
            return contour_registry_to_payload(self._contours)

    async def async_reset_configuration(self) -> dict[str, object]:
        """Reset every climate setting without sending commands to devices."""

        async with self._lock:
            previous_registry = self._registry
            previous_contours = self._contours
            previous_protection = self._protection_memory
            previous_manual = self._manual_memory
            registry = ClimateRegistry()
            contours = ContourRegistry()
            await self._async_persist_contour_setup_unlocked(registry, contours)
            protection = empty_climate_protection_memory(updated_at=self._safe_now())
            manual = empty_climate_manual_memory(updated_at=self._safe_now())
            try:
                await self._async_save_protection(protection)
                await self._async_save_manual(manual)
            except Exception:
                await self._async_persist_contour_setup_unlocked(
                    previous_registry,
                    previous_contours,
                )
                self._protection_memory = previous_protection
                self._manual_memory = previous_manual
                raise
            self._protection_memory = protection
            self._manual_memory = manual
            self._protection_restart_after = None
            self._weather_heating_lockout = None
            self._central_heating_on = None
            return {
                "status": "reset",
                "room_count": 0,
                "device_count": 0,
                "contour_count": 0,
            }

    async def async_contours_snapshot(self) -> dict[str, object]:
        """Return public contour status using the existing climate engine."""

        async with self._lock:
            if self.configuration.climate_bridge_mode is ClimateControlMode.MANAGED:
                try:
                    observation = (
                        await self._async_native_climate_observation_unlocked()
                    )
                except ClimateRuntimeUnavailable:
                    observation = None
                if (
                    observation is not None
                    and observation.data_status is ClimateDataStatus.UNAVAILABLE
                ):
                    observation = None
                return native_contour_snapshot(
                    self._contours,
                    self._registry,
                    observation,
                    settings_apply_enabled=True,
                    local_now=self._local_now(),
                )
            return native_contour_snapshot(
                self._contours,
                self._registry,
                None,
                settings_apply_enabled=False,
                local_now=self._local_now(),
            )

    async def async_ai_evidence_snapshot(self) -> AiJsonObject:
        async with self._lock:
            observation = await self._async_native_climate_observation_unlocked()
            return ai_evidence_from_observation(observation, self._contours)

    async def async_contour_apply_preview(self) -> dict[str, object]:
        """Preview supported saved-contour changes without posting commands."""

        async with self._lock:
            if self.configuration.climate_bridge_mode is not ClimateControlMode.MANAGED:
                raise ClimateRuntimeUnavailable(
                    "contour settings require the normal existing-engine connection"
                )
            contour = self._climate_contour()
            observation = await self._async_native_climate_observation_unlocked()
            return native_contour_apply_preview(
                contour,
                self._registry,
                self.configuration.climate_bridge_mode,
                observation,
                fingerprint=contour_fingerprint(contour),
            )

    async def async_control_operation(self, operation_id: object) -> dict[str, object] | None:
        """Refresh one direct receipt by read-back, never by redispatch."""

        async with self._lock:
            record = self._contour_applications.by_operation(operation_id)
            if record is None:
                return None
            # A restored or live receipt has a finite observation window. It
            # is never safe to keep polling a frozen intent forever.
            if (
                record.receipt.status in {ContourApplyStatus.PENDING, ContourApplyStatus.PARTIAL}
                and self._safe_now() > record.receipt.created_at + 8_000
            ):
                self._contour_applications.update(
                    record.receipt.request_id,
                    status=ContourApplyStatus.UNAVAILABLE,
                    accepted_count=record.receipt.accepted_count,
                    confirmed_room_count=record.receipt.confirmed_room_count,
                    reasons=("verification_unavailable",),
                )
                await self._async_persist_direct_control_unlocked()
                record = self._contour_applications.by_operation(operation_id) or record
                return record.receipt.as_payload()
            # A poll is allowed to observe the frozen plan and advance a
            # pending receipt to confirmed.  Restored records intentionally
            # have no executable plan, so they are replayed frozen instead of
            # guessing a command after restart.
            if (
                record.receipt.status in {ContourApplyStatus.PENDING, ContourApplyStatus.PARTIAL}
                and isinstance(record.plan, ContourApplyPlan)
            ):
                contour = self._climate_contour()
                refreshed = await self._async_reobserve_native_contour_application_unlocked(
                    record.receipt.request_id,
                    record,
                    contour,
                    room_ids=record.plan.target_room_ids,
                )
                if refreshed is not record.receipt:
                    await self._async_persist_direct_control_unlocked()
                    record = self._contour_applications.by_operation(operation_id) or record
            elif record.receipt.status in {ContourApplyStatus.PENDING, ContourApplyStatus.PARTIAL}:
                # A restored operation deliberately has no executable plan.
                # It must never rebuild a plan from the current contour: that
                # could confirm a different desired intent after restart.
                # Keep its durable pending/partial receipt until the fixed
                # confirmation window turns it terminal above.  No service
                # call is made on this path.
                pass
            return record.receipt.as_payload()

    async def async_execute_reserved_tablet_action(
        self,
        *,
        action: str,
        room_id: object,
        parameters: dict[str, object],
        request_id: str,
        correlation_id: str,
        expected_control_revision: object,
        resulting_control_revision: object,
        local_now: object,
        tablet_request_fingerprint: object,
        tablet_action: object,
        tablet_parameters: object,
    ) -> object:
        """Execute physical work for a tablet intent already reserved in store.

        This is intentionally not an HTTP payload.  Its only caller is the
        tablet ledger after the coordinator accepted ``expected -> expected+1``.
        Public direct APIs validate and reserve inside their own methods.
        """
        # This private handoff receives values detached from the HTTP payload.
        # Rebuild and parse the public envelope before touching a contour, the
        # command guard or Home Assistant, so every action keeps exactly the
        # same grammar as the public Tablet boundary.
        try:
            canonical = parse_climate_tablet_action({
                "contract": {
                    "name": CLIMATE_ACTION_CONTRACT_NAME,
                    "version": CLIMATE_TABLET_CONTRACT_VERSION,
                },
                "request_id": request_id,
                "correlation_id": correlation_id,
                "expected_state_revision": 0,
                "expected_control_revision": expected_control_revision,
                "reliability_profile": "climate_reliability_v1",
                "action": action,
                "room_id": room_id,
                "parameters": parameters,
            })
        except ClimateTabletViolation as error:
            raise ClimateRuntimeUnavailable(
                "reserved tablet climate action is invalid"
            ) from error
        if (
            canonical.request_id != request_id
            or canonical.correlation_id != correlation_id
            or canonical.action != action
            or canonical.room_id != room_id
            or canonical.parameters != parameters
        ):
            raise ClimateRuntimeUnavailable("reserved tablet climate action is invalid")
        async with self._lock:
            current = await self._async_sync_control_revision_unlocked()
            if (
                not is_control_revision(expected_control_revision)
                or not is_control_revision(resulting_control_revision)
                or resulting_control_revision != expected_control_revision + 1
                or current != resulting_control_revision
            ):
                raise ClimateRuntimeUnavailable("reserved climate control revision is stale")
        if (
            not isinstance(tablet_request_fingerprint, str)
            or re.fullmatch(r"[a-f0-9]{64}", tablet_request_fingerprint) is None
            or tablet_action != canonical.action
            or not isinstance(tablet_parameters, dict)
            or tablet_parameters != canonical.parameters
        ):
            raise ClimateRuntimeUnavailable("reserved tablet climate identity is invalid")
        tablet_identity = {
            "request_fingerprint": tablet_request_fingerprint,
            "action": canonical.action,
            "parameters": dict(tablet_parameters),
        }
        if canonical.action == "set_home_targets":
            return await self.async_home_climate_targets({
                "correlation_id": canonical.correlation_id, "request_id": canonical.request_id,
                "contour_id": "climate", "target_temperature": canonical.parameters.get("target_temperature"),
                "target_humidity": canonical.parameters.get("target_humidity"), "confirm": True,
            }, reliability_request=canonical,
            pre_reserved_resulting_control_revision=resulting_control_revision,
            external_reliability_identity=tablet_identity)
        if canonical.action == "synchronize_home":
            return await self.async_synchronize_climate()
        if canonical.action == "set_room_mode":
            return await self.async_set_room_mode(canonical.room_id, canonical.parameters.get("mode"))
        if canonical.action == "set_device_mode":
            return await self.async_set_device_mode(canonical.room_id, canonical.parameters.get("device_id"), canonical.parameters.get("mode"))
        if canonical.action == "set_room_humidity_target":
            return await self.async_room_humidity_target(request_id=canonical.request_id, room_id=canonical.room_id, target_humidity=canonical.parameters.get("target_humidity"))
        if canonical.action == "set_room_min_target":
            return await self.async_room_minimum_temperature(request_id=canonical.request_id, room_id=canonical.room_id, minimum_temperature=canonical.parameters.get("minimum_temperature"))
        if canonical.action == "set_room_target_strategy":
            return await self.async_room_target_strategy(request_id=canonical.request_id, room_id=canonical.room_id, target_strategy=canonical.parameters.get("target_strategy"))
        if canonical.action == "turn_room_off":
            return await self.async_turn_room_off(request_id=canonical.request_id, room_id=canonical.room_id)
        if canonical.action == "set_room_target":
            temporary_action = "set"
            target_temperature = canonical.parameters["target_temperature"]
        elif canonical.action == "clear_room_override":
            temporary_action = "clear"
            target_temperature = None
        else:
            raise ClimateRuntimeUnavailable("reserved tablet climate action is unsupported")
        return await self.async_temporary_temperature({
            "correlation_id": canonical.correlation_id, "request_id": canonical.request_id,
            "contour_id": "climate", "room_id": canonical.room_id,
            "action": temporary_action, "target_temperature": target_temperature,
            "confirm": True, "reliability_profile": "climate_reliability_v1",
            "expected_control_revision": expected_control_revision,
        }, local_now,
        pre_reserved_resulting_control_revision=resulting_control_revision,
        external_reliability_identity=tablet_identity,
        )

    async def async_apply_contour(self, payload: object) -> ContourApplyReceipt:
        """Idempotently apply three supported settings after explicit consent."""

        request = parse_contour_apply_request(payload)
        async with self._lock:
            self._require_native_contour_apply_mode()
            await self._async_sync_control_revision_unlocked()
            if (
                request.reliability_profile is not None
                and request.expected_control_revision != self._control_revision
                and self._contour_applications.by_request(request.request_id) is None
            ):
                raise ContourApplyViolation("climate control revision is stale")
            context = ClimateControlContext(
                action=(
                    ClimateControlAction.APPLY_SCHEDULE_PROFILE
                    if request.schedule_profile is not None
                    else ClimateControlAction.APPLY_SAVED_SETTINGS
                ),
                profile=request.schedule_profile,
            )
            # The shared revision is the first durable physical boundary for
            # direct control.  Do it before a ledger entry can be dispatched.
            # Existing request ids replay through the frozen ledger and never
            # reserve a second revision.
            resulting_revision: int | None = None
            if (request.reliability_profile is not None
                    and self._contour_applications.by_request(request.request_id) is None):
                resulting_revision = await self._async_reserve_control_revision_unlocked(
                    request.expected_control_revision
                )
                self._control_revision = resulting_revision
            receipt = await self._async_apply_native_contour_unlocked(
                request.request_id,
                request.contour_id,
                correlation_id=request.correlation_id,
                context=context,
                room_ids=request.room_ids,
                desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
                reliability_request=request,
                resulting_control_revision=resulting_revision,
            )
            await self._async_persist_direct_control_unlocked()
            return receipt

    async def async_run_climate_schedule(
        self,
        now: datetime,
    ) -> ContourApplyReceipt | None:
        """Switch and apply a profile once when an armed local-time boundary passes."""

        if not isinstance(now, datetime):
            raise ClimateRuntimeUnavailable("climate schedule needs local datetime")
        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if (
                contour is None
                or not contour.schedule.enabled
                or contour.mode is not ContourMode.AUTOMATIC
                or self.configuration.climate_bridge_mode
                is not ClimateControlMode.MANAGED
            ):
                return None
            selected = contour.schedule.profile_at(hour=now.hour, minute=now.minute)
            if (
                contour.schedule.last_applied_profile is selected
                and all(room.active_profile is selected for room in contour.rooms)
            ):
                return None
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            updated = with_active_climate_profile(
                self._contours,
                selected.value,
                clear_temporary=(
                    contour.schedule.last_applied_profile is not None
                    and contour.schedule.last_applied_profile is not selected
                ),
            )
            updated = with_applied_climate_schedule_profile(updated, selected)
            desired_state_changes = local_desired_state_changes(
                contour,
                self._require_climate_contour(updated),
            )
            await self._contour_store.async_save(updated)
            self._contours = updated
            fingerprint = contour_fingerprint(self._climate_contour())
            request_id = (
                f"schedule-{now:%Y%m%d}-{selected.value}-{fingerprint[:12]}"
            )
            return await self._async_apply_native_contour_unlocked(
                request_id,
                CLIMATE_CONTOUR_ID,
                context=ClimateControlContext(
                    action=ClimateControlAction.APPLY_SCHEDULE_PROFILE,
                    profile=selected,
                ),
                desired_state_changes=desired_state_changes,
            )

    async def async_temporary_temperature(
        self,
        payload: object,
        now: datetime,
        *,
        pre_reserved_resulting_control_revision: int | None = None,
        external_reliability_identity: Mapping[str, object] | None = None,
    ) -> ContourApplyReceipt:
        """Apply one room temperature until the next saved schedule boundary."""

        request = parse_temporary_temperature_request(payload)
        if not isinstance(now, datetime):
            raise TemporaryTemperatureViolation(
                "temporary temperature needs local datetime"
            )
        async with self._lock:
            self._require_native_contour_apply_mode()
            await self._async_sync_control_revision_unlocked()
            contour = self._climate_contour()
            selected = contour.schedule.profile_at(hour=now.hour, minute=now.minute)
            if contour.mode is not ContourMode.AUTOMATIC or (
                contour.schedule.enabled
                and (
                    contour.schedule.last_applied_profile is not selected
                    or any(
                        room.active_profile is not selected for room in contour.rooms
                    )
                )
            ):
                raise ContourApplyViolation(
                    "climate target control is not ready"
                )
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            room_scope = (request.room_id,)
            if request.action is TemporaryTemperatureAction.CLEAR:
                current_room = next(
                    (
                        room
                        for room in contour.rooms
                        if room.room_id == request.room_id
                    ),
                    None,
                )
                if current_room is not None:
                    context = ClimateControlContext(
                        action=ClimateControlAction.RETURN_TO_SCHEDULE,
                        room_id=request.room_id,
                        target_temperature=current_room.target_temperature,
                    )
                    fingerprint = contour_fingerprint(
                        contour,
                        room_ids=room_scope,
                    )
                    if self._contour_applications.existing(
                        request.request_id,
                        fingerprint,
                        context,
                        request.correlation_id,
                    ) is not None:
                        return await self._async_apply_native_contour_unlocked(
                            request.request_id,
                            CLIMATE_CONTOUR_ID,
                            correlation_id=request.correlation_id,
                            context=context,
                            room_ids=room_scope,
                            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
                            reliability_request=request,
                            external_reliability_identity=external_reliability_identity,
                        )
            try:
                if request.action is TemporaryTemperatureAction.SET:
                    updated = with_climate_temporary_temperature(
                        self._contours,
                        room_id=request.room_id,
                        target_temperature=request.target_temperature,
                    )
                else:
                    updated = without_climate_temporary_temperature(
                        self._contours,
                        room_id=request.room_id,
                    )
            except ContourRegistryViolation as error:
                if error.code == "temperature_out_of_bounds":
                    raise TemporaryTemperatureViolation(
                        "temporary temperature is outside room bounds",
                        code=error.code,
                        room_id=request.room_id,
                    ) from error
                raise ContourApplyViolation(str(error)) from error
            updated_contour = updated.contour(CLIMATE_CONTOUR_ID)
            if updated_contour is None:
                raise TemporaryTemperatureViolation(
                    "climate contour is not configured"
                )
            updated_room = next(
                room
                for room in updated_contour.rooms
                if room.room_id == request.room_id
            )
            context = ClimateControlContext(
                action=(
                    ClimateControlAction.SET_TEMPORARY_TEMPERATURE
                    if request.action is TemporaryTemperatureAction.SET
                    else ClimateControlAction.RETURN_TO_SCHEDULE
                ),
                room_id=request.room_id,
                target_temperature=updated_room.target_temperature,
            )
            fingerprint = contour_fingerprint(
                updated_contour,
                room_ids=room_scope,
            )
            if (
                self._contour_applications.existing(
                    request.request_id,
                    fingerprint,
                    context,
                    request.correlation_id,
                )
                is not None
            ):
                return await self._async_apply_native_contour_unlocked(
                    request.request_id,
                    CLIMATE_CONTOUR_ID,
                    correlation_id=request.correlation_id,
                    context=context,
                    room_ids=room_scope,
                    desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
                    reliability_request=request,
                    external_reliability_identity=external_reliability_identity,
                )
            if (
                request.reliability_profile is not None
                and (
                    (
                        pre_reserved_resulting_control_revision is None
                        and request.expected_control_revision != self._control_revision
                    )
                    or (
                        pre_reserved_resulting_control_revision is not None
                        and (
                            pre_reserved_resulting_control_revision
                            != request.expected_control_revision + 1
                            or self._control_revision
                            != pre_reserved_resulting_control_revision
                        )
                    )
                )
            ):
                raise TemporaryTemperatureViolation("climate control revision is stale")
            # Reserve the shared revision before a mutable contour or a
            # dispatchable direct-control record can be saved.  The duplicate
            # branch above has already returned without changing the token.
            resulting_revision: int | None = None
            if request.reliability_profile is not None:
                if pre_reserved_resulting_control_revision is None:
                    resulting_revision = await self._async_reserve_control_revision_unlocked(
                        request.expected_control_revision
                    )
                    self._control_revision = resulting_revision
                elif (
                    pre_reserved_resulting_control_revision
                    == request.expected_control_revision + 1
                    and self._control_revision
                    == pre_reserved_resulting_control_revision
                ):
                    resulting_revision = pre_reserved_resulting_control_revision
                else:
                    raise TemporaryTemperatureViolation(
                        "reserved climate control revision is stale"
                    )
            # Reserve the desired temporary state in durable storage before the
            # first POST. A lost response therefore cannot trigger an automatic
            # retry; only another explicit user request may try again.
            desired_state_changes = local_desired_state_changes(
                contour,
                updated_contour,
                target_room_ids=room_scope,
            )
            await self._contour_store.async_save(updated)
            self._contours = updated
            receipt = await self._async_apply_native_contour_unlocked(
                request.request_id,
                CLIMATE_CONTOUR_ID,
                correlation_id=request.correlation_id,
                context=context,
                room_ids=room_scope,
                desired_state_changes=desired_state_changes,
                reliability_request=request,
                resulting_control_revision=resulting_revision,
                external_reliability_identity=external_reliability_identity,
            )
            await self._async_persist_direct_control_unlocked()
            return receipt

    async def async_set_room_mode(
        self,
        room_id: object,
        mode: object,
    ) -> _ClimateRoomModeReceipt:
        """Persist manual ownership without issuing a physical command."""

        if not isinstance(room_id, str) or mode not in {"automatic", "manual"}:
            raise ClimateManualViolation("climate room mode request is invalid")
        async with self._lock:
            contour = self._climate_contour()
            if not any(room.room_id == room_id for room in contour.rooms):
                raise ClimateManualViolation("climate room is not in the contour")
            updated = with_climate_room_mode(
                self._manual_memory,
                self._registry,
                room_id=room_id,
                manual=mode == "manual",
                updated_at=self._safe_now(),
            )
            if updated != self._manual_memory:
                await self._async_save_manual(updated)
                self._manual_memory = updated
            self.last_error = None
            return _ClimateRoomModeReceipt()

    async def async_set_device_mode(
        self,
        room_id: object,
        device_id: object,
        mode: object,
    ) -> _ClimateRoomModeReceipt:
        """Persist one device exclusion without issuing a physical command."""

        if (
            not isinstance(room_id, str)
            or not isinstance(device_id, str)
            or mode not in {"automatic", "manual"}
        ):
            raise ClimateManualViolation("climate device mode request is invalid")
        async with self._lock:
            contour = self._climate_contour()
            assignment = next(
                (room for room in contour.rooms if room.room_id == room_id),
                None,
            )
            if assignment is None or device_id not in assignment.device_ids:
                raise ClimateManualViolation("climate device is not in the contour")
            updated = with_climate_device_mode(
                self._manual_memory,
                self._registry,
                room_id=room_id,
                device_id=device_id,
                manual=mode == "manual",
                updated_at=self._safe_now(),
            )
            if updated != self._manual_memory:
                await self._async_save_manual(updated)
                self._manual_memory = updated
            self.last_error = None
            return _ClimateRoomModeReceipt()

    async def async_set_device_mode_for_entity(
        self,
        entity_id: object,
        mode: object,
    ) -> dict[str, object] | None:
        """Persist an AC ownership choice resolved from its control entity."""

        if not isinstance(entity_id, str) or mode not in {"automatic", "manual"}:
            raise ClimateManualViolation("climate device entity mode request is invalid")
        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            assigned_device_ids = {
                device_id
                for room in contour.rooms
                for device_id in room.device_ids
            }
            device = next(
                (
                    candidate
                    for candidate in self._registry.devices
                    if candidate.device_id in assigned_device_ids
                    and candidate.kind is ClimateDeviceKind.AIR_CONDITIONER
                    and (
                        endpoint := candidate.endpoint(ClimateEndpointRole.CONTROL)
                    ) is not None
                    and endpoint.entity_id == entity_id
                ),
                None,
            )
            if device is None:
                return None
            manual_room_ids = set(
                effective_manual_room_ids(self._manual_memory, self._registry)
            )
            was_explicitly_manual = device.device_id in set(
                self._manual_memory.manual_device_ids
            )
            previous_mode = (
                "manual"
                if device.room_id in manual_room_ids or was_explicitly_manual
                else "automatic"
            )
            should_be_manual = mode == "manual"
            if device.room_id in manual_room_ids and should_be_manual:
                updated = self._manual_memory
            else:
                updated = with_climate_device_mode(
                    self._manual_memory,
                    self._registry,
                    room_id=device.room_id,
                    device_id=device.device_id,
                    manual=should_be_manual,
                    updated_at=self._safe_now(),
                )
            changed = updated != self._manual_memory
            if changed:
                await self._async_save_manual(updated)
                self._manual_memory = updated
            self.last_error = None
            effective_mode = (
                "manual"
                if device.room_id in manual_room_ids
                or device.device_id in set(self._manual_memory.manual_device_ids)
                else "automatic"
            )
            return {
                "device_id": device.device_id,
                "room_id": device.room_id,
                "entity_id": entity_id,
                "previous_mode": previous_mode,
                "mode": effective_mode,
                "changed": changed,
            }

    async def async_home_climate_targets(
        self, payload: object, *, reliability_request: object | None = None,
        pre_reserved_resulting_control_revision: int | None = None,
        external_reliability_identity: Mapping[str, object] | None = None,
    ) -> ContourApplyReceipt:
        """Save and apply one common temperature and/or humidity target."""

        request = parse_home_climate_targets_request(payload)
        async with self._lock:
            self._require_native_contour_apply_mode()
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            if pre_reserved_resulting_control_revision is not None:
                current = await self._async_sync_control_revision_unlocked()
                expected = getattr(reliability_request, "expected_control_revision", None)
                if (
                    not is_control_revision(expected)
                    or pre_reserved_resulting_control_revision != expected + 1
                    or current != pre_reserved_resulting_control_revision
                ):
                    error = ClimateRuntimeUnavailable(
                        "reserved climate control revision is stale"
                    )
                    # The tablet coordinator has already written its durable
                    # started checkpoint, but this recheck happens before the
                    # contour save or native plan can reach the executor.
                    # Preserve that distinction for the coordinator's receipt.
                    error.reserved_tablet_pre_dispatch_conflict = True  # type: ignore[attr-defined]
                    raise error
            contour = self._climate_contour()
            try:
                updated = with_home_climate_targets(
                    self._contours,
                    target_temperature=request.target_temperature,
                    target_humidity=request.target_humidity,
                )
            except ContourRegistryViolation as error:
                raise HomeClimateTargetsViolation(str(error)) from error
            updated_contour = self._require_climate_contour(updated)
            # Preflight and execution must see the identical ownership-filtered
            # scope.  A manually owned device is never allowed to enter a
            # frozen plan only to disappear after the contour has been saved.
            execution_contour = contour_without_manual_devices(
                updated_contour, self._manual_memory
            )
            desired_state_changes = local_desired_state_changes(
                contour,
                updated_contour,
            )
            axes = frozenset(
                axis for axis, value in (
                    (ClimateTargetAxis.TEMPERATURE, request.target_temperature),
                    (ClimateTargetAxis.HUMIDITY, request.target_humidity),
                ) if value is not None
            )
            desired_state_changes = replace(
                desired_state_changes, requested_axes=axes,
            )
            # Build the exact selected-axis plan before mutating the contour.
            # A missing humidifier, stale observation or executor must not
            # leave a durable target which cannot cross the physical boundary.
            try:
                observation = await self._async_native_climate_observation_unlocked()
                preflight = build_contour_apply_plan(
                    execution_contour, self._registry,
                    self.configuration.climate_bridge_mode, observation,
                    desired_state_changes=desired_state_changes,
                )
                if not preflight.native_plan.preflight_permitted:
                    raise ClimateRuntimeUnavailable("home climate target scope is unavailable")
                if preflight.strict_calls and self._strict_ha_call_executor is None:
                    raise ClimateRuntimeUnavailable("climate executor is unavailable")
            except Exception as error:
                # No executor boundary has been crossed while the immutable
                # plan is assembled.  The coordinator must close this as
                # unavailable, never as a physical partial outcome.
                error.home_target_pre_dispatch = True  # type: ignore[attr-defined]
                raise
            # Observation is asynchronous.  Another coordinator can reserve
            # the shared revision while this runtime lock waits for it, so
            # re-read the durable authority immediately before the native
            # checkpoint is created.  The following call enters its synchronous
            # reservation path before its first await.
            if pre_reserved_resulting_control_revision is not None:
                current = await self._async_sync_control_revision_unlocked()
                expected = getattr(reliability_request, "expected_control_revision", None)
                if (
                    not is_control_revision(expected)
                    or pre_reserved_resulting_control_revision != expected + 1
                    or current != pre_reserved_resulting_control_revision
                ):
                    error = ClimateRuntimeUnavailable(
                        "reserved climate control revision is stale"
                    )
                    error.reserved_tablet_pre_dispatch_conflict = True  # type: ignore[attr-defined]
                    raise error
            return await self._async_apply_native_contour_unlocked(
                request.request_id,
                CLIMATE_CONTOUR_ID,
                correlation_id=request.correlation_id,
                context=ClimateControlContext(
                    action=ClimateControlAction.APPLY_SAVED_SETTINGS,
                ),
                desired_state_changes=desired_state_changes,
                reliability_request=reliability_request,
                resulting_control_revision=pre_reserved_resulting_control_revision,
                external_reliability_identity=external_reliability_identity,
                preflight_plan=preflight,
                preflight_observation=observation,
                contour_to_save=updated,
            )

    async def async_preflight_home_climate_targets(
        self, payload: object,
    ) -> dict[str, object]:
        """Prove the exact native home-target scope without mutating state."""

        request = parse_home_climate_targets_request(payload)
        async with self._lock:
            self._require_native_contour_apply_mode()
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            contour = self._climate_contour()
            updated = with_home_climate_targets(
                self._contours,
                target_temperature=request.target_temperature,
                target_humidity=request.target_humidity,
            )
            updated_contour = self._require_climate_contour(updated)
            execution_contour = contour_without_manual_devices(
                updated_contour, self._manual_memory
            )
            axes = frozenset(
                axis for axis, value in (
                    (ClimateTargetAxis.TEMPERATURE, request.target_temperature),
                    (ClimateTargetAxis.HUMIDITY, request.target_humidity),
                ) if value is not None
            )
            changes = replace(
                local_desired_state_changes(contour, updated_contour),
                requested_axes=axes,
            )
            observation = await self._async_native_climate_observation_unlocked()
            plan = build_contour_apply_plan(
                execution_contour, self._registry,
                self.configuration.climate_bridge_mode, observation,
                desired_state_changes=changes,
            )
            if not plan.native_plan.preflight_permitted:
                raise ClimateRuntimeUnavailable("home climate target scope is unavailable")
            if plan.strict_calls and self._strict_ha_call_executor is None:
                raise ClimateRuntimeUnavailable("climate executor is unavailable")
            return {"resolved_scope": _native_plan_resolved_scope(plan)}

    async def async_room_humidity_target(
        self, *, request_id: str, room_id: str, target_humidity: int
    ) -> ContourApplyReceipt:
        """Persist and apply one room's desired humidity through the contour."""

        async with self._lock:
            self._require_native_contour_apply_mode()
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            contour = self._climate_contour()
            try:
                updated = with_room_climate_humidity(
                    self._contours, room_id=room_id, target_humidity=target_humidity
                )
            except ContourRegistryViolation as error:
                raise HomeClimateTargetsViolation(str(error)) from error
            updated_contour = self._require_climate_contour(updated)
            desired_state_changes = local_desired_state_changes(contour, updated_contour)
            await self._contour_store.async_save(updated)
            self._contours = updated
            return await self._async_apply_native_contour_unlocked(
                request_id,
                CLIMATE_CONTOUR_ID,
                context=ClimateControlContext(
                    action=ClimateControlAction.APPLY_SAVED_SETTINGS,
                ),
                room_ids=(room_id,),
                desired_state_changes=desired_state_changes,
            )

    async def async_room_minimum_temperature(
        self, *, request_id: str, room_id: str, minimum_temperature: float
    ) -> ContourApplyReceipt:
        """Save one room's safe lower bound and apply its revised contour."""
        async with self._lock:
            self._require_native_contour_apply_mode()
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            contour = self._climate_contour()
            try:
                updated = with_room_climate_minimum_temperature(
                    self._contours, room_id=room_id,
                    minimum_temperature=minimum_temperature,
                )
            except ContourRegistryViolation as error:
                raise HomeClimateTargetsViolation(str(error)) from error
            updated_contour = self._require_climate_contour(updated)
            changes = local_desired_state_changes(contour, updated_contour)
            await self._contour_store.async_save(updated)
            self._contours = updated
            return await self._async_apply_native_contour_unlocked(
                request_id, CLIMATE_CONTOUR_ID,
                context=ClimateControlContext(action=ClimateControlAction.APPLY_SAVED_SETTINGS),
                room_ids=(room_id,), desired_state_changes=changes,
            )

    async def async_room_target_strategy(
        self, *, request_id: str, room_id: str, target_strategy: str
    ) -> ContourApplyReceipt:
        """Save one room's typed strategy and apply the authoritative contour."""
        async with self._lock:
            self._require_native_contour_apply_mode()
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            contour = self._climate_contour()
            try:
                updated = with_room_climate_target_strategy(
                    self._contours, room_id=room_id, target_strategy=target_strategy
                )
            except ContourRegistryViolation as error:
                raise HomeClimateTargetsViolation(str(error)) from error
            updated_contour = self._require_climate_contour(updated)
            changes = local_desired_state_changes(contour, updated_contour)
            await self._contour_store.async_save(updated)
            self._contours = updated
            return await self._async_apply_native_contour_unlocked(
                request_id, CLIMATE_CONTOUR_ID,
                context=ClimateControlContext(action=ClimateControlAction.APPLY_SAVED_SETTINGS),
                room_ids=(room_id,), desired_state_changes=changes,
            )

    async def async_turn_room_off(self, *, request_id: str, room_id: str) -> ContourApplyReceipt:
        """Turn off only managed climate/humidifier leaves in one room.

        This path never manufactures a generic service call: every endpoint is
        taken from the private registry and only the two documented HA domains
        can cross the executor boundary.
        """
        async with self._lock:
            self._require_native_contour_apply_mode()
            contour = self._climate_contour()
            assignment = next((room for room in contour.rooms if room.room_id == room_id), None)
            if assignment is None:
                raise ContourApplyViolation("climate room is not configured")
            actuators = tuple(
                self._registry.device(device_id)
                for device_id in assignment.device_ids
                if (device := self._registry.device(device_id)) is not None
                and device.kind not in _PASSIVE_KINDS
            )
            calls: list[ClimateHaServiceCall] = []
            if not actuators or not all(
                self._is_unique_managed_core_control_device(device)
                for device in actuators
            ):
                actuators = ()
            for device in actuators:
                endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
                if endpoint is None:  # guarded above, retained for type narrowing
                    continue
                if endpoint.entity_id.startswith("climate."):
                    calls.append(ClimateHaServiceCall(ClimateHaService.CLIMATE_TURN_OFF, endpoint.entity_id, owner_device_id=device.device_id))
                elif endpoint.entity_id.startswith("humidifier."):
                    calls.append(ClimateHaServiceCall(ClimateHaService.HUMIDIFIER_TURN_OFF, endpoint.entity_id, owner_device_id=device.device_id))
                elif device.kind is ClimateDeviceKind.FLOOR_HEATING and endpoint.entity_id.startswith("switch."):
                    calls.append(ClimateHaServiceCall(ClimateHaService.SWITCH_TURN_OFF, endpoint.entity_id, owner_device_id=device.device_id))
            now = self._safe_now()
            def receipt(status: ContourApplyStatus, accepted: int, reasons: tuple[str, ...],
                        device_outcomes: Mapping[str, Mapping[str, object]] | None = None) -> ContourApplyReceipt:
                return ContourApplyReceipt(
                    operation_id=request_id, request_id=request_id, correlation_id=None,
                    contour_id=CLIMATE_CONTOUR_ID,
                    context=ClimateControlContext(action=ClimateControlAction.APPLY_SAVED_SETTINGS),
                    status=status, room_count=1, command_count=len(calls), accepted_count=accepted,
                    confirmed_room_count=0, temperature_changes=0, strategy_changes=0,
                    automatic_mode_changes=0, reasons=reasons, created_at=now, updated_at=now,
                    device_outcomes=device_outcomes,
                )
            if (
                not calls
                or not self._calls_match_strict_registry(tuple(calls))
                or self._strict_ha_call_executor is None
            ):
                return receipt(ContourApplyStatus.UNAVAILABLE, 0, ("command_result_unavailable",))
            try:
                isolated = getattr(self._strict_ha_call_executor, "async_execute_isolated", None)
                if callable(isolated):
                    outcomes = await isolated(tuple(calls))
                    if (
                        type(outcomes) is not tuple
                        or len(outcomes) != len(calls)
                        or any(type(value) is not bool for value in outcomes)
                    ):
                        return receipt(ContourApplyStatus.UNAVAILABLE, 0, ("command_result_unavailable",))
                    accepted = sum(1 for value in outcomes if value is True)
                    leaves = {
                        call.owner_device_id: {
                            "owner_device_id": call.owner_device_id,
                            "command_count": 1,
                            "accepted_count": 1 if outcome is True else 0,
                            "execution_state": "accepted_unverified" if outcome is True else "dispatched_not_accepted",
                            "retry_policy": "forbidden_after_dispatch",
                            "observed_actual": None,
                            "observed_at": None,
                        }
                        for call, outcome in zip(calls, outcomes)
                        if isinstance(call.owner_device_id, str)
                    }
                    await self._async_capture_hausman_contexts()
                    status = (
                        ContourApplyStatus.PENDING if accepted == len(calls)
                        else ContourApplyStatus.PARTIAL if accepted
                        else ContourApplyStatus.UNAVAILABLE
                    )
                    return receipt(status, accepted, () if accepted else ("command_result_unavailable",), leaves)
                else:
                    accepted = await self._strict_ha_call_executor.async_execute(tuple(calls))
                    if not self._valid_executor_count(accepted, len(calls)):
                        return receipt(ContourApplyStatus.UNAVAILABLE, 0, ("command_result_unavailable",))
            except Exception as error:
                self.last_error = type(error).__name__
                accepted = _bounded_completed_count(getattr(error, "completed", 0), len(calls))
                status = ContourApplyStatus.PARTIAL if accepted else ContourApplyStatus.UNAVAILABLE
                return receipt(status, accepted, ("command_result_unavailable",))
            leaves = {
                call.owner_device_id: {
                    "owner_device_id": call.owner_device_id,
                    "command_count": 1,
                    "accepted_count": 1 if index < accepted else 0,
                    "execution_state": "accepted_unverified" if index < accepted else "dispatched_not_accepted",
                    "retry_policy": "forbidden_after_dispatch",
                    "observed_actual": None,
                    "observed_at": None,
                }
                for index, call in enumerate(calls)
                if isinstance(call.owner_device_id, str)
            }
            return receipt(ContourApplyStatus.PENDING, _bounded_completed_count(accepted, len(calls)), (), leaves)

    async def async_recover_device(
        self, *, request_id: str, room_id: str, device_id: str,
        desired: Mapping[str, object], expected_control_revision: int | None = None,
    ) -> _ClimateRoomModeReceipt:
        """Reconcile one selected device to its durable recovery snapshot.

        Device selection remains exact: this method resolves its private
        endpoint before invoking the strict executor and never broadens a
        recovery request into a room-wide apply.
        """
        if not isinstance(desired, Mapping):
            raise _recovery_pre_dispatch_unavailable("recovery desired state is invalid")
        async with self._lock:
            # The preflight receipt is deliberately only advisory.  The
            # physical boundary repeats every ownership and managed-mode gate
            # while holding the runtime lock, immediately before it clears a
            # manual exclusion or touches HA.
            if self.configuration.climate_bridge_mode is not ClimateControlMode.MANAGED:
                raise _recovery_pre_dispatch_unavailable("climate recovery is not managed")
            # Tablet recovery reserves the same durable revision before its
            # first leaf. Re-read it under the runtime lock immediately before
            # touching HA, so a direct command that already advanced it turns
            # this leaf into a safe pre-dispatch failure instead of a stale
            # second physical command.
            if expected_control_revision is not None:
                current_revision = await self._async_sync_control_revision_unlocked()
                if current_revision != expected_control_revision:
                    raise _recovery_pre_dispatch_unavailable(
                        "climate recovery control revision is stale"
                    )
            device = next((item for item in self._registry.devices if item.device_id == device_id and item.room_id == room_id), None)
            contour = self._climate_contour()
            assignment = next((item for item in contour.rooms if item.room_id == room_id), None)
            if (
                device is None
                or assignment is None
                or device_id not in assignment.device_ids
                or device.control_scope is not ClimateControlScope.MANAGED
            ):
                raise _recovery_pre_dispatch_unavailable(
                    "climate device is not in the contour"
                )
            try:
                observation = await self._async_native_climate_observation_unlocked()
                observed = observation.device(device_id)
                reconciliation = native_climate_reconciliation(self._registry, observation)
            except Exception as error:
                if getattr(error, "recovery_pre_dispatch", False):
                    raise
                raise _recovery_pre_dispatch_unavailable(
                    "climate recovery evidence is unavailable"
                ) from error
            attribution = next(
                (item for item in self._manual_memory.attributions if item.device_id == device_id),
                None,
            )
            # Recovery is explicit and may return either durable manual
            # participation reason to the contour.  Preserve the reason in
            # the receipt/preflight; do not reinterpret external shutdown as
            # a user exclusion.
            if attribution is None or attribution.reason not in {"user_excluded", "external_off"}:
                raise _recovery_pre_dispatch_unavailable(
                    "climate recovery ownership is not manual participation"
                )
            if device_id not in self._manual_memory.manual_device_ids:
                raise _recovery_pre_dispatch_unavailable(
                    "climate recovery ownership is unavailable"
                )
            if (
                observation.data_status is not ClimateDataStatus.FRESH
                or not reconciliation.matches
                or type(observation.observed_at) is not int
                or observed is None
                or observed.availability is not ClimateDeviceAvailability.AVAILABLE
                or device.control_owner is not ClimateControlOwner.CLIMATE_CORE
            ):
                raise _recovery_pre_dispatch_unavailable(
                    "climate recovery device is unavailable"
                )
            endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
            if endpoint is None or self._strict_ha_call_executor is None:
                raise _recovery_pre_dispatch_unavailable(
                    "climate recovery control endpoint is unavailable"
                )
            calls: list[ClimateHaServiceCall] = []
            target = desired.get("target_temperature")
            humidity = desired.get("target_humidity")
            if endpoint.entity_id.startswith("climate.") and type(target) in {int, float}:
                calls.append(ClimateHaServiceCall(ClimateHaService.CLIMATE_SET_TEMPERATURE, endpoint.entity_id, temperature=float(target), owner_device_id=device.device_id))
            elif endpoint.entity_id.startswith("humidifier.") and type(humidity) is int:
                calls.append(ClimateHaServiceCall(ClimateHaService.HUMIDIFIER_SET_HUMIDITY, endpoint.entity_id, humidity=humidity, owner_device_id=device.device_id))
            if not calls:
                raise _recovery_pre_dispatch_unavailable(
                    "climate recovery desired state is unsupported"
                )
            if not self._calls_match_strict_registry(tuple(calls)):
                raise _recovery_pre_dispatch_unavailable("climate recovery control endpoint is invalid")
            completed = await self._strict_ha_call_executor.async_execute(tuple(calls))
            if not self._valid_executor_count(completed, len(calls)) or completed != len(calls):
                raise ClimateRuntimeUnavailable("climate recovery command result is invalid")
            try:
                await self._async_capture_hausman_contexts()
                # Ownership changes only after the physical boundary accepted
                # the command. If persistence or read-back fails afterwards,
                # the caller must still retain the accepted 1/1 outcome.
                updated = with_climate_device_mode(
                    self._manual_memory, self._registry, room_id=room_id,
                    device_id=device_id, manual=False, updated_at=self._safe_now(),
                )
                if updated != self._manual_memory:
                    await self._async_save_manual(updated)
                    self._manual_memory = updated
                # Force a native observation/reconciliation while retaining
                # the lock. This makes a completed recovery visible to the
                # immediate tablet read-back without accepting a stale
                # coordinator snapshot.
                await self._async_native_climate_observation_unlocked()
            except Exception as error:
                accepted = ClimateRuntimeUnavailable(
                    "climate recovery post-dispatch state is unavailable"
                )
                accepted.recovery_accepted_after_dispatch = True
                raise accepted from error
            return _ClimateRoomModeReceipt()

    async def async_recover_offline_device(
        self, *, room_id: str, device_id: str, expected_control_revision: int
    ) -> _ClimateRoomModeReceipt:
        """Return an offline manual leaf to ownership only under its reservation.

        This has no HA service call. It still changes durable control rights,
        so it must obey the exact same shared revision and contour boundary as
        a physical recovery leaf.
        """
        async with self._lock:
            current_revision = await self._async_sync_control_revision_unlocked()
            if current_revision != expected_control_revision:
                error = ClimateRuntimeUnavailable("climate recovery control revision is stale")
                error.recovery_pre_dispatch = True
                raise error
            if self.configuration.climate_bridge_mode is not ClimateControlMode.MANAGED:
                raise ClimateRuntimeUnavailable("climate recovery is not managed")
            device = next((item for item in self._registry.devices if item.device_id == device_id and item.room_id == room_id), None)
            contour = self._climate_contour()
            assignment = next((item for item in contour.rooms if item.room_id == room_id), None)
            observation = await self._async_native_climate_observation_unlocked()
            observed = observation.device(device_id)
            attribution = next((item for item in self._manual_memory.attributions if item.device_id == device_id), None)
            if (
                device is None or assignment is None or device_id not in assignment.device_ids
                or device.control_scope is not ClimateControlScope.MANAGED
                or observed is None or observed.availability is ClimateDeviceAvailability.AVAILABLE
                or device_id not in self._manual_memory.manual_device_ids
                or attribution is None or attribution.reason not in {"user_excluded", "external_off"}
            ):
                error = ClimateRuntimeUnavailable("climate recovery offline ownership is unavailable")
                error.recovery_pre_dispatch = True
                raise error
            updated = with_climate_device_mode(
                self._manual_memory, self._registry, room_id=room_id,
                device_id=device_id, manual=False, updated_at=self._safe_now(),
            )
            if updated != self._manual_memory:
                await self._async_save_manual(updated)
                self._manual_memory = updated
            return _ClimateRoomModeReceipt()

    async def _async_apply_native_contour_unlocked(
        self,
        request_id: str,
        contour_id: str,
        *,
        correlation_id: str | None = None,
        context: ClimateControlContext,
        room_ids: tuple[str, ...] | None = None,
        desired_state_changes: ClimateDesiredStateChanges,
        reliability_request: object | None = None,
        resulting_control_revision: int | None = None,
        external_reliability_identity: Mapping[str, object] | None = None,
        preflight_plan: ContourApplyPlan | None = None,
        preflight_observation: ClimateObservationSnapshot | None = None,
        contour_to_save: ContourRegistry | None = None,
    ) -> ContourApplyReceipt:
        self._require_native_contour_apply_mode()
        contour = contour_without_manual_devices(
            (
                self._require_climate_contour(contour_to_save)
                if contour_to_save is not None
                else self._climate_contour()
            ),
            self._manual_memory,
        )
        if contour.contour_id != contour_id:
            raise ContourApplyViolation("climate contour is not configured")
        fingerprint = contour_fingerprint(contour, room_ids=room_ids)
        prior = self._contour_applications.existing(
            request_id,
            fingerprint,
            context,
            correlation_id,
        )
        if prior is not None:
            # The legacy ledger key is the contour plan fingerprint.  Enhanced
            # calls additionally bind every public control token to the
            # frozen request fingerprint, so a reused id cannot replay a
            # receipt after changing profile, revision, correlation or scope.
            if getattr(reliability_request, "reliability_profile", None) == "climate_reliability_v1":
                enhanced = prior.enhanced
                prior_scope = enhanced.get("resolved_scope") if isinstance(enhanced, Mapping) else None
                expected = getattr(reliability_request, "expected_control_revision", None)
                if not isinstance(prior_scope, Mapping) or not isinstance(expected, int):
                    raise ContourApplyViolation("climate control request id conflicts")
                if external_reliability_identity is not None:
                    candidate = external_reliability_identity.get("request_fingerprint")
                else:
                    candidate = _direct_reliability_request_fingerprint(
                        request_id=request_id, correlation_id=correlation_id,
                        context=context, scope=prior_scope,
                        expected_control_revision=expected,
                    )
                if enhanced.get("request_fingerprint") != candidate:
                    raise ContourApplyViolation("climate control request id conflicts")
            elif prior.enhanced is not None:
                # A negotiated control token cannot silently degrade to the
                # legacy idempotency surface.  The latter has no revision or
                # fingerprint binding and would otherwise turn a conflicting
                # request into an unsafe apparent duplicate.
                raise ContourApplyViolation("climate control request id conflicts")
            self._last_contour_apply_was_duplicate = True
            if not isinstance(prior.plan, ContourApplyPlan):
                return prior.receipt
            return await self._async_reobserve_native_contour_application_unlocked(
                request_id,
                prior,
                contour,
                room_ids=room_ids,
            )

        self._last_contour_apply_was_duplicate = False

        if preflight_plan is None:
            observation = await self._async_native_climate_observation_unlocked()
            plan = build_contour_apply_plan(
                contour,
                self._registry,
                self.configuration.climate_bridge_mode,
                observation,
                room_ids=room_ids,
                desired_state_changes=desired_state_changes,
                explicit_temperature_alignment=(
                    context.action
                    in {
                        ClimateControlAction.SET_TEMPORARY_TEMPERATURE,
                        ClimateControlAction.RETURN_TO_SCHEDULE,
                    }
                ),
            )
        else:
            if not isinstance(preflight_observation, ClimateObservationSnapshot):
                raise ContourApplyViolation("climate preflight observation is unavailable")
            plan = preflight_plan
            observation = preflight_observation
        record = self._contour_applications.begin(
            request_id,
            plan,
            context,
            correlation_id,
            enhanced=(
                _contour_reliability_metadata(
                    contour, plan, context, reliability_request, observation,
                    expected_control_revision=getattr(
                        reliability_request, "expected_control_revision", self._control_revision
                    ),
                    resulting_control_revision=resulting_control_revision,
                    external_reliability_identity=external_reliability_identity,
                )
                if getattr(reliability_request, "reliability_profile", None)
                == "climate_reliability_v1" else None
            ),
        )
        try:
            await self._async_persist_direct_control_unlocked()
        except Exception as error:
            # Saving failed before a physical boundary.  Retaining this entry
            # would create a phantom dispatchable operation on a later retry.
            self._contour_applications.discard_unpersisted(request_id)
            error.home_target_pre_dispatch = True  # type: ignore[attr-defined]
            raise
        # Validate the final frozen calls and their exact owners before the
        # user-visible contour is saved.  A malformed handoff must remain a
        # durable pre-dispatch failure, never leave a saved contour behind.
        enhanced = record.enhanced if isinstance(record.enhanced, dict) else None
        ledger = enhanced.get("leaf_ledger") if enhanced is not None else None
        scoped_ids = set(ledger) if isinstance(ledger, dict) else set()
        call_owners = [self._device_ids_for_climate_call(call) for call in plan.strict_calls]
        if (plan.strict_calls and not self._calls_match_strict_registry(
            plan.strict_calls, room_ids=plan.target_room_ids
        )) or (
            plan.explicit_target_alignment
            and any(
                len(owners) != 1 or not self._is_explicit_target_call(call, plan)
                for call, owners in zip(plan.strict_calls, call_owners, strict=True)
            )
        ) or (
            enhanced is not None
            and any(
                len(owners) != 1 or owners[0] not in scoped_ids
                for owners in call_owners
            )
        ):
            if contour_to_save is None:
                return self._contour_applications.update(
                    request_id, status=ContourApplyStatus.UNAVAILABLE,
                    accepted_count=0, confirmed_room_count=0,
                    reasons=("engine_rejected",),
                ).receipt
            error = ClimateRuntimeUnavailable("frozen climate call ownership is invalid")
            await self._async_block_native_before_dispatch_unlocked(request_id)
            error.home_target_pre_dispatch = True  # type: ignore[attr-defined]
            raise error
        if contour_to_save is not None:
            try:
                if self._contour_store is None:
                    raise ClimateRuntimeUnavailable("contour storage is unavailable")
                await self._contour_store.async_save(contour_to_save)
            except Exception as error:
                # The native checkpoint existed, but no physical operation is
                # dispatchable when the user-visible contour was not saved.
                try:
                    await self._async_block_native_before_dispatch_unlocked(request_id)
                except Exception as persist_error:
                    # Keep the in-memory checkpoint sticky.  It is safer to
                    # make this runtime unavailable than let a retry cross a
                    # boundary after terminal persistence failed.
                    error.home_target_terminal_persist_failed = True  # type: ignore[attr-defined]
                    self.last_error = type(persist_error).__name__
                error.home_target_pre_dispatch = True  # type: ignore[attr-defined]
                raise
            self._contours = contour_to_save
        if not plan.native_plan.preflight_permitted or not plan.strict_calls:
            if not plan.native_plan.preflight_permitted:
                _LOGGER.warning(
                    "climate contour apply %s rejected by gates: %s",
                    request_id,
                    _contour_apply_diagnostics(plan),
                )
            return record.receipt

        if self._strict_ha_call_executor is None:
            return self._contour_applications.update(
                request_id,
                status=ContourApplyStatus.UNAVAILABLE,
                accepted_count=0,
                confirmed_room_count=0,
                reasons=("command_result_unavailable",),
            ).receipt

        # The frozen receipt has one physical-device ledger.  Refuse to send a
        # plan whose calls cannot be tied to exactly one leaf in that scope.
        # In particular, an HA entity shared by two registry rows is never
        # allowed to make both rows look successful.
        enhanced = record.enhanced if isinstance(record.enhanced, dict) else None
        ledger = enhanced.get("leaf_ledger") if enhanced is not None else None
        scoped_ids = set(ledger) if isinstance(ledger, dict) else set()
        call_owners = [self._device_ids_for_climate_call(call) for call in plan.strict_calls]
        if (plan.strict_calls and not self._calls_match_strict_registry(
            plan.strict_calls, room_ids=plan.target_room_ids
        )) or (
            plan.explicit_target_alignment
            and any(
                len(owners) != 1 or not self._is_explicit_target_call(call, plan)
                for call, owners in zip(plan.strict_calls, call_owners, strict=True)
            )
        ) or (
            enhanced is not None
            and any(
                len(owners) != 1 or owners[0] not in scoped_ids
                for owners in call_owners
            )
        ):
            return await self._async_block_native_before_dispatch_unlocked(request_id)

        # A device may need several HA calls.  They are a strict sequence for
        # that one owner: after its first error no later sub-call can run.
        # Different owners remain independent, so one failed AC must not hide
        # the result of a humidifier in the same room.
        if isinstance(ledger, dict):
            grouped: dict[str, list] = {}
            for call, owners in zip(plan.strict_calls, call_owners, strict=True):
                grouped.setdefault(owners[0], []).append(call)
            accepted_count = 0
            accepted_calls: list[ClimateHaServiceCall] = []
            for device_id, calls in grouped.items():
                ledger[device_id] = "started"
                await self._async_persist_direct_control_unlocked()
                try:
                    completed = await self._strict_ha_call_executor.async_execute(tuple(calls))
                except Exception as error:
                    self.last_error = type(error).__name__
                    completed = _bounded_completed_count(getattr(error, "completed", 0), len(calls))
                    accepted_count += completed
                    accepted_calls.extend(calls[:completed])
                    # The started checkpoint is proof that at least the first
                    # call crossed the physical boundary.  The whole leaf is
                    # non-retryable even when its first call failed.
                    ledger[device_id] = "dispatched_not_accepted"
                else:
                    completed = _bounded_completed_count(completed, len(calls))
                    accepted_count += completed
                    accepted_calls.extend(calls[:completed])
                    ledger[device_id] = (
                        "accepted_unverified"
                        if completed == len(calls) else "dispatched_not_accepted"
                    )
                await self._async_persist_direct_control_unlocked()
        else:
            try:
                accepted_count = await self._strict_ha_call_executor.async_execute(
                    plan.strict_calls
                )
            except Exception as error:
                self.last_error = type(error).__name__
                completed = _bounded_completed_count(
                    getattr(error, "completed", 0), len(plan.strict_calls)
                )
                await self._async_record_direct_wifi_commands(plan.strict_calls, executed_count=completed)
                await self._async_record_deviation_off_commands(plan.strict_calls, executed_count=completed)
                return self._contour_applications.update(
                    request_id,
                    status=ContourApplyStatus.PARTIAL if completed else ContourApplyStatus.UNAVAILABLE,
                    accepted_count=completed, confirmed_room_count=0,
                    reasons=("command_result_unavailable",),
                ).receipt

        accepted_count = _bounded_completed_count(
            accepted_count,
            len(plan.strict_calls),
        )
        # Grouped executor results are not a prefix of the original plan:
        # owner A may fail while owner B succeeds. Attribute only concrete
        # accepted calls, never the first N aggregate calls.
        attribution_calls = tuple(accepted_calls) if isinstance(ledger, dict) else plan.strict_calls
        attribution_count = len(attribution_calls) if isinstance(ledger, dict) else accepted_count
        await self._async_record_direct_wifi_commands(attribution_calls, executed_count=attribution_count)
        await self._async_record_deviation_off_commands(attribution_calls, executed_count=attribution_count)
        if accepted_count != len(plan.strict_calls):
            return self._contour_applications.update(
                request_id,
                status=(
                    ContourApplyStatus.PARTIAL
                    if accepted_count
                    else ContourApplyStatus.UNAVAILABLE
                ),
                accepted_count=accepted_count,
                confirmed_room_count=0,
                reasons=("command_result_unavailable",),
            ).receipt
        verified = await self._async_verify_native_contour_application_unlocked(
            request_id,
            plan,
            accepted_count,
        )
        if verified.status is ContourApplyStatus.CONFIRMED:
            record = self._contour_applications.by_request(request_id)
            enhanced = record.enhanced if record is not None and isinstance(record.enhanced, dict) else None
            ledger = enhanced.get("leaf_ledger") if enhanced is not None else None
            if isinstance(ledger, dict):
                for device_id in ledger:
                    # A zero-call owner was proved fresh before dispatch and
                    # remains `already_in_sync`.  Do not turn it into an
                    # invented 1/1 physical call just because another owner
                    # in this same home operation was applied.
                    if ledger[device_id] in {
                        "started", "accepted_unverified", "dispatched_not_accepted",
                    }:
                        ledger[device_id] = "applied"
                # Re-render the receipt after the durable per-leaf checkpoint.
                # Returning the pre-checkpoint object would describe a fully
                # confirmed operation as four unverified leaves.
                verified = self._contour_applications.update(
                    request_id,
                    status=ContourApplyStatus.CONFIRMED,
                    accepted_count=accepted_count,
                    confirmed_room_count=verified.confirmed_room_count,
                    reasons=(),
                ).receipt
                await self._async_persist_direct_control_unlocked()
        return verified

    async def _async_persist_direct_control_unlocked(self) -> None:
        """Checkpoint the reservation before or after every physical phase."""

        if self._direct_control_store is None:
            return
        saver = getattr(self._direct_control_store, "async_save_direct_control", None)
        if not callable(saver):
            raise ClimateRuntimeUnavailable("direct control store is invalid")
        await saver(self._contour_applications.serialized())

    async def _async_block_native_before_dispatch_unlocked(
        self, request_id: str,
    ) -> ContourApplyReceipt:
        """Durably close an already-reserved operation before any HA call."""
        record = self._contour_applications.by_request(request_id)
        if record is None:
            raise ClimateRuntimeUnavailable("climate operation checkpoint is unavailable")
        enhanced = record.enhanced if isinstance(record.enhanced, dict) else None
        if enhanced is not None:
            ledger = enhanced.get("leaf_ledger")
            if isinstance(ledger, dict):
                for device_id in ledger:
                    ledger[device_id] = "blocked_before_dispatch"
            enhanced["already_in_sync_evidence"] = {}
        receipt = self._contour_applications.update(
            request_id,
            status=ContourApplyStatus.UNAVAILABLE,
            accepted_count=0,
            confirmed_room_count=0,
            reasons=("command_result_unavailable",),
        ).receipt
        await self._async_persist_direct_control_unlocked()
        return receipt

    def _device_ids_for_climate_call(
        self, call, *, required_scope: ClimateControlScope = ClimateControlScope.MANAGED
    ) -> tuple[str, ...]:
        """Return the single frozen plan leaf for a strict HA call.

        Never recover ownership by matching an HA entity id.  That would let a
        shared or stale endpoint turn one accepted service call into success
        for unrelated leaves.
        """
        device_id = getattr(call, "owner_device_id", None)
        if not isinstance(device_id, str):
            return ()
        device = self._registry.device(device_id)
        endpoint = None if device is None else device.endpoint(ClimateEndpointRole.CONTROL)
        if (
            device is None
            or device.control_scope is not required_scope
            or device.control_owner is not ClimateControlOwner.CLIMATE_CORE
            or endpoint is None
            or getattr(call, "entity_id", None) != endpoint.entity_id
            or sum(
                candidate.endpoint(ClimateEndpointRole.CONTROL) == endpoint
                for candidate in self._registry.devices
            ) != 1
        ):
            return ()
        return (device_id,)

    def _is_explicit_target_call(self, call, plan) -> bool:
        device_id = getattr(call, "owner_device_id", None)
        device = None if not isinstance(device_id, str) else self._registry.device(device_id)
        room_id = None if device is None else device.room_id
        endpoint = None if device is None else device.endpoint(ClimateEndpointRole.CONTROL)
        common = (
            device is not None
            and room_id in plan.target_room_ids
            and endpoint is not None
        )
        temperature_targets = dict(plan.explicit_temperature_targets)
        temperature_expected = (
            None if room_id is None else temperature_targets.get(room_id)
        )
        temperature_actual = getattr(call, "temperature", None)
        if (
            common
            and type(temperature_expected) in {int, float}
            and type(temperature_actual) in {int, float}
            and math.isfinite(temperature_actual)
            and temperature_actual == temperature_expected
            and device.kind in {
                ClimateDeviceKind.AIR_CONDITIONER,
                ClimateDeviceKind.RADIATOR_THERMOSTAT,
                ClimateDeviceKind.FLOOR_HEATING,
            }
            and ClimateCapability.TARGET_TEMPERATURE in device.capabilities
            and endpoint.entity_id.startswith("climate.")
            and getattr(call, "service", None)
            is ClimateHaService.CLIMATE_SET_TEMPERATURE
            and getattr(call, "hvac_mode", None) is None
        ):
            return True
        humidity_targets = dict(plan.explicit_humidity_targets)
        humidity_expected = None if room_id is None else humidity_targets.get(room_id)
        humidity_actual = getattr(call, "humidity", None)
        return bool(
            common
            and type(humidity_expected) is int
            and type(humidity_actual) is int
            and humidity_actual == humidity_expected
            and device.kind is ClimateDeviceKind.HUMIDIFIER
            and ClimateCapability.TARGET_HUMIDITY in device.capabilities
            and endpoint.entity_id.startswith("humidifier.")
            and getattr(call, "service", None)
            is ClimateHaService.HUMIDIFIER_SET_HUMIDITY
            and getattr(call, "temperature", None) is None
            and getattr(call, "hvac_mode", None) is None
        )

    def _is_unique_managed_core_control_device(self, device) -> bool:
        return bool(
            self._is_unique_core_control_device(device)
            and device.control_scope is ClimateControlScope.MANAGED
        )

    def _is_unique_core_control_device(self, device) -> bool:
        endpoint = None if device is None else device.endpoint(ClimateEndpointRole.CONTROL)
        return bool(
            device is not None
            and device.control_owner is ClimateControlOwner.CLIMATE_CORE
            and endpoint is not None
            and sum(
                candidate.endpoint(ClimateEndpointRole.CONTROL) == endpoint
                for candidate in self._registry.devices
            ) == 1
        )

    def _calls_match_strict_registry(
        self, calls: tuple[ClimateHaServiceCall, ...], *, room_ids: tuple[str, ...] | None = None,
        required_scope: ClimateControlScope = ClimateControlScope.MANAGED,
    ) -> bool:
        return bool(calls) and all(
            self._call_matches_strict_registry(call, room_ids=room_ids, required_scope=required_scope) for call in calls
        )

    def _call_matches_strict_registry(
        self, call: ClimateHaServiceCall, *, room_ids: tuple[str, ...] | None = None,
        required_scope: ClimateControlScope = ClimateControlScope.MANAGED,
    ) -> bool:
        owners = self._device_ids_for_climate_call(call, required_scope=required_scope)
        if len(owners) != 1:
            return False
        device = self._registry.device(owners[0])
        endpoint = None if device is None else device.endpoint(ClimateEndpointRole.CONTROL)
        if device is None or endpoint is None:
            return False
        if room_ids is not None and device.room_id not in room_ids:
            return False
        service = call.service
        if device.kind is ClimateDeviceKind.AIR_CONDITIONER:
            if endpoint.entity_id.startswith("remote."):
                return (
                    service is ClimateHaService.REMOTE_SEND_COMMAND
                    and call.device == device.device_id
                    and isinstance(call.command, str)
                    and bool(call.command)
                )
            return endpoint.entity_id.startswith("climate.") and service in {
                ClimateHaService.CLIMATE_SET_TEMPERATURE, ClimateHaService.CLIMATE_SET_HVAC_MODE,
                ClimateHaService.CLIMATE_SET_FAN_MODE, ClimateHaService.CLIMATE_TURN_OFF,
            }
        if device.kind is ClimateDeviceKind.RADIATOR_THERMOSTAT:
            return endpoint.entity_id.startswith("climate.") and service in {
                ClimateHaService.CLIMATE_SET_TEMPERATURE, ClimateHaService.CLIMATE_SET_HVAC_MODE,
                ClimateHaService.CLIMATE_SET_FAN_MODE, ClimateHaService.CLIMATE_TURN_OFF,
            }
        if device.kind is ClimateDeviceKind.HUMIDIFIER:
            return endpoint.entity_id.startswith("humidifier.") and service in {
                ClimateHaService.HUMIDIFIER_TURN_ON, ClimateHaService.HUMIDIFIER_TURN_OFF,
                ClimateHaService.HUMIDIFIER_SET_HUMIDITY,
            }
        if device.kind is ClimateDeviceKind.FLOOR_HEATING:
            return (
                (endpoint.entity_id.startswith("climate.") and service in {
                    ClimateHaService.CLIMATE_SET_TEMPERATURE, ClimateHaService.CLIMATE_SET_HVAC_MODE,
                    ClimateHaService.CLIMATE_TURN_OFF,
                })
                or (endpoint.entity_id.startswith("switch.") and service is ClimateHaService.SWITCH_TURN_OFF)
            )
        return False

    @staticmethod
    def _valid_executor_count(value: object, total: int) -> bool:
        return type(value) is int and 0 <= value <= total

    async def _async_reserve_control_revision_unlocked(self, expected: object) -> int:
        """Allocate a negotiated revision through the shared climate ledger."""
        if (
            not is_control_revision(expected)
            or not is_control_revision(self._control_revision)
            or expected != self._control_revision
            or expected >= MAX_JS_SAFE_INTEGER
        ):
            raise ContourApplyViolation("climate control revision is stale")
        reserve = getattr(self._direct_control_store, "async_reserve_control_revision", None)
        if not callable(reserve):
            return expected + 1
        try:
            value = await reserve(expected)
        except Exception as error:
            raise ContourApplyViolation("climate control revision is stale") from error
        if not is_control_revision(value) or value != expected + 1:
            raise ClimateRuntimeUnavailable("shared climate control revision is invalid")
        return value

    async def _async_sync_control_revision_unlocked(self) -> int:
        """Mirror the shared durable revision before a negotiated request.

        The instance field is a cache for legacy paths only.  It is never the
        authority when the coordinator is configured, because tablet and
        direct endpoints have independent runtime locks.
        """
        current = getattr(self._direct_control_store, "async_current_control_revision", None)
        if not callable(current):
            return self._control_revision
        try:
            value = await current()
        except Exception as error:
            raise ClimateRuntimeUnavailable("shared climate control revision is unavailable") from error
        if not is_control_revision(value):
            raise ClimateRuntimeUnavailable("shared climate control revision is invalid")
        self._control_revision = value
        return value

    async def _async_reobserve_native_contour_application_unlocked(
        self,
        request_id: str,
        prior,
        contour: ContourDefinition,
        *,
        room_ids: tuple[str, ...] | None,
    ) -> ContourApplyReceipt:
        # Restored records deliberately contain only frozen receipt metadata,
        # never a dispatchable plan.  A poll may return that durable result,
        # but must not attempt to dereference or reconstruct mutable targets.
        if not isinstance(prior.plan, ContourApplyPlan):
            return prior.receipt
        if (
            isinstance(prior.enhanced, Mapping)
            and isinstance(prior.enhanced.get("leaf_ledger"), Mapping)
            and any(
                state == "blocked_before_dispatch"
                for state in prior.enhanced["leaf_ledger"].values()
            )
        ):
            # A contour-save or final validation failure is terminal.  A
            # duplicate may retrieve its durable receipt, never reopen it
            # into an observation-driven retry or confirmation.
            return prior.receipt
        if (
            not prior.plan.strict_calls
            and prior.plan.explicit_target_alignment
            and isinstance(prior.enhanced, Mapping)
            and isinstance(prior.enhanced.get("leaf_ledger"), Mapping)
            and any(
                state != "already_in_sync"
                for state in prior.enhanced["leaf_ledger"].values()
            )
        ):
            # This operation had no physical call to retry.  A stale or
            # missing source observation must stay pending under its frozen
            # scope; only a newly created request may establish new proof.
            return prior.receipt
        try:
            observation = await self._async_native_climate_observation_unlocked()
        except ClimateRuntimeUnavailable as error:
            _LOGGER.warning(
                "climate contour apply %s reobserve cannot observe: %s",
                request_id,
                type(error).__name__,
            )
            return prior.receipt
        if observation.data_status is ClimateDataStatus.UNAVAILABLE:
            _LOGGER.warning(
                "climate contour apply %s reobserve observation is unavailable",
                request_id,
            )
            return prior.receipt
        verified = build_contour_apply_plan(
            contour_without_manual_devices(contour, self._manual_memory),
            self._registry,
            self.configuration.climate_bridge_mode,
            observation,
            room_ids=room_ids,
            desired_state_changes=prior.plan.desired_state_changes,
            explicit_temperature_alignment=prior.plan.explicit_temperature_alignment,
            explicit_temperature_targets=dict(prior.plan.explicit_temperature_targets) or None,
            explicit_humidity_targets=dict(prior.plan.explicit_humidity_targets) or None,
        )
        confirmed = len(verified.native_plan.initially_aligned_room_ids)
        if confirmed == len(verified.target_room_ids):
            return self._contour_applications.update(
                request_id,
                status=ContourApplyStatus.CONFIRMED,
                accepted_count=prior.receipt.accepted_count,
                confirmed_room_count=confirmed,
                reasons=(),
            ).receipt
        _LOGGER.warning(
            "climate contour apply %s reobserve is not confirmed: %s",
            request_id,
            _contour_apply_diagnostics(verified),
        )
        return self._contour_applications.update(
            request_id,
            status=prior.receipt.status,
            accepted_count=prior.receipt.accepted_count,
            confirmed_room_count=confirmed,
            reasons=prior.receipt.reasons,
        ).receipt

    async def _async_verify_native_contour_application_unlocked(
        self,
        request_id: str,
        plan,
        accepted_count: int,
    ) -> ContourApplyReceipt:
        confirmed = 0
        for attempt in range(_CLIMATE_READBACK_ATTEMPTS):
            try:
                observation = await self._async_native_climate_observation_unlocked()
            except ClimateRuntimeUnavailable:
                return self._contour_applications.update(
                    request_id,
                    status=ContourApplyStatus.PENDING,
                    accepted_count=accepted_count,
                    confirmed_room_count=confirmed,
                    reasons=("verification_unavailable",),
                ).receipt
            if observation.data_status is ClimateDataStatus.UNAVAILABLE:
                return self._contour_applications.update(
                    request_id,
                    status=ContourApplyStatus.PENDING,
                    accepted_count=accepted_count,
                    confirmed_room_count=confirmed,
                    reasons=("verification_unavailable",),
                ).receipt
            verified = build_contour_apply_plan(
                contour_without_manual_devices(
                    self._climate_contour(), self._manual_memory
                ),
                self._registry,
                self.configuration.climate_bridge_mode,
                observation,
                room_ids=plan.target_room_ids,
                desired_state_changes=plan.desired_state_changes,
                explicit_temperature_alignment=plan.explicit_temperature_alignment,
                explicit_temperature_targets=dict(plan.explicit_temperature_targets) or None,
                explicit_humidity_targets=dict(plan.explicit_humidity_targets) or None,
            )
            confirmed = len(verified.native_plan.initially_aligned_room_ids)
            if confirmed == len(plan.target_room_ids):
                return self._contour_applications.update(
                    request_id,
                    status=ContourApplyStatus.CONFIRMED,
                    accepted_count=accepted_count,
                    confirmed_room_count=confirmed,
                    reasons=(),
                ).receipt
            if attempt + 1 < _CLIMATE_READBACK_ATTEMPTS:
                await asyncio.sleep(_CLIMATE_READBACK_INTERVAL_SECONDS)
        _LOGGER.warning(
            "climate contour apply %s stayed unconfirmed after read-back: %s",
            request_id,
            _contour_apply_diagnostics(verified),
        )
        return self._contour_applications.update(
            request_id,
            status=ContourApplyStatus.PENDING,
            accepted_count=accepted_count,
            confirmed_room_count=confirmed,
            reasons=("state_not_confirmed",),
        ).receipt

    async def async_native_climate_preview(
        self,
        policy: NativeClimatePolicy,
    ) -> dict[str, object]:
        """Calculate HausmanHub's one-room decision without enabling any command."""

        async with self._lock:
            observation = self._native_ha_observation(self._safe_now())
            decision = preview_native_climate(policy, self._registry, observation)
            return decision.as_payload()

    async def async_native_climate_targets(self) -> ClimateTargetSnapshot | None:
        """Resolve current HausmanHub contour targets without creating commands."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            observation = await self._async_native_climate_observation_unlocked()
            return build_climate_target_snapshot(contour, observation)

    async def async_native_climate_demands(self) -> ClimateDemandSnapshot | None:
        """Calculate room needs without choosing or commanding equipment."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            observation = await self._async_native_climate_observation_unlocked()
            targets = build_climate_target_snapshot(contour, observation)
            return build_climate_demand_snapshot(targets, observation)

    async def async_native_climate_resolutions(
        self,
    ) -> ClimateResolutionSnapshot | None:
        """Resolve thermal conflicts without choosing or commanding equipment."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            observation = await self._async_native_climate_observation_unlocked()
            targets = build_climate_target_snapshot(contour, observation)
            demands = build_climate_demand_snapshot(targets, observation)
            return build_climate_resolution_snapshot(demands, observation)

    async def async_native_climate_equipment(
        self,
    ) -> ClimateEquipmentSnapshot | None:
        """Plan thermal equipment without creating intents or commands."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            observation = await self._async_native_climate_observation_unlocked()
            targets = build_climate_target_snapshot(contour, observation)
            demands = build_climate_demand_snapshot(targets, observation)
            resolutions = build_climate_resolution_snapshot(demands, observation)
            return build_climate_equipment_snapshot(
                contour,
                targets,
                resolutions,
                observation,
            )

    async def async_native_climate_stability(
        self,
    ) -> ClimateStabilitySnapshot | None:
        """Protect selected devices from oscillation without creating commands."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            observation = await self._async_native_climate_observation_unlocked()
            targets = build_climate_target_snapshot(contour, observation)
            demands = build_climate_demand_snapshot(targets, observation)
            resolutions = build_climate_resolution_snapshot(demands, observation)
            equipment = build_climate_equipment_snapshot(
                contour,
                targets,
                resolutions,
                observation,
            )
            return build_climate_stability_snapshot(
                contour,
                targets,
                equipment,
                observation,
            )

    async def async_native_climate_policy(
        self,
    ) -> ClimatePolicySnapshot | None:
        """Apply the complete command-free policy ladder to one observation."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            observation = await self._async_native_climate_observation_unlocked()
            targets = build_climate_target_snapshot(contour, observation)
            demands = build_climate_demand_snapshot(targets, observation)
            resolutions = build_climate_resolution_snapshot(demands, observation)
            equipment = build_climate_equipment_snapshot(
                contour,
                targets,
                resolutions,
                observation,
            )
            stability = build_climate_stability_snapshot(
                contour,
                targets,
                equipment,
                observation,
            )
            return build_climate_policy_snapshot(
                contour,
                resolutions,
                equipment,
                stability,
                observation,
            )

    async def async_native_climate_isolation(
        self,
    ) -> ClimateIsolationSnapshot | None:
        """Calculate every room independently without creating commands."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            observation = await self._async_native_climate_observation_unlocked()
            return build_isolated_climate_policy_snapshot(contour, observation)

    async def async_native_climate_comparison(
        self,
    ) -> ClimateComparisonSnapshot | None:
        """Compare native decisions with the observed module without commands."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            observation = await self._async_native_climate_observation_unlocked(
                allow_disabled=True
            )
            isolation = build_isolated_climate_policy_snapshot(contour, observation)
            return build_climate_comparison_snapshot(isolation, observation)

    async def async_native_climate_ha_calls(
        self,
    ) -> ClimateHaCallPlanSnapshot | None:
        """Translate the isolated plan into strict HA call plans."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return None
            observation = await self._async_native_climate_observation_unlocked()
            isolation = build_isolated_climate_policy_snapshot(contour, observation)
            return build_climate_ha_call_plan(
                self._registry,
                isolation,
                ir_code_service=self._ir_code_service,
            )

    async def async_run_climate_trial(
        self,
    ) -> ClimateTrialReceipt | None:
        """Run one gated internal-control check for the single trial room."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            trial_room_id = self._trial_room_id(contour)
            if contour is None or trial_room_id is None:
                return None
            contour = contour_without_manual_devices(contour, self._manual_memory)
            observation = await self._async_native_climate_observation_unlocked()
            isolation = build_isolated_climate_policy_snapshot(contour, observation)
            comparison = build_climate_comparison_snapshot(isolation, observation)
            call_plan = build_climate_ha_call_plan(
                self._registry,
                isolation,
                ir_code_service=self._ir_code_service,
            )
            await self._async_rearm_command_guard(comparison)
            guarded = self._guard_diverged_calls(call_plan, comparison)
            decision = plan_climate_trial(
                trial_room_id,
                bridge_mode=self.configuration.climate_bridge_mode,
                contour_mode=contour.mode,
                isolation=isolation,
                comparison=comparison,
                call_plan=guarded.call_plan,
                registry=self._registry,
            )
            devices = tuple(
                device
                for device in guarded.devices
                if device.room_id == trial_room_id
            )
            return await self._async_apply_trial_decision(
                decision,
                devices,
                required_scope=ClimateControlScope.CANARY,
            )

    async def async_run_climate_managed(
        self,
    ) -> tuple[ClimateTrialReceipt, ...]:
        """Run one gated control check for every HausmanHub-managed room."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return ()
            contour = contour_without_manual_devices(contour, self._manual_memory)
            managed_room_ids = self._managed_room_ids(contour)
            if not managed_room_ids:
                return ()
            observation = await self._async_native_climate_observation_unlocked()
            isolation = build_isolated_climate_policy_snapshot(contour, observation)
            comparison = build_climate_comparison_snapshot(isolation, observation)
            call_plan = build_climate_ha_call_plan(
                self._registry,
                isolation,
                ir_code_service=self._ir_code_service,
            )
            await self._async_rearm_command_guard(comparison)
            deviation_device_ids = await self._async_run_deviation_guard(
                call_plan,
                managed_room_ids=managed_room_ids,
            )
            guarded = self._guard_diverged_calls(call_plan, comparison)
            guarded = _without_guarded_devices(guarded, deviation_device_ids)
            receipts: list[ClimateTrialReceipt] = []
            for room_id in managed_room_ids:
                decision = plan_climate_trial(
                    room_id,
                    bridge_mode=self.configuration.climate_bridge_mode,
                    contour_mode=contour.mode,
                    isolation=isolation,
                    comparison=comparison,
                    call_plan=guarded.call_plan,
                    registry=self._registry,
                    required_scope=ClimateControlScope.MANAGED,
                    allowed_bridge_modes=frozenset(
                        {ClimateControlMode.MANAGED}
                    ),
                )
                devices = tuple(
                    device
                    for device in guarded.devices
                    if device.room_id == room_id
                )
                receipts.append(
                    await self._async_apply_trial_decision(
                        decision,
                        devices,
                        required_scope=ClimateControlScope.MANAGED,
                    )
                )
            return tuple(receipts)

    async def async_synchronize_climate(self) -> _ClimateSynchronizationResult:
        """Explicitly send every saved active setting to automatic devices."""

        async with self._lock:
            return await self._async_synchronize_climate_unlocked()

    async def async_run_scheduled_climate_synchronization(
        self,
        now: datetime,
    ) -> _ClimateSynchronizationResult | None:
        """Run one restart-safe local 10:00 or 22:00 synchronization slot."""

        if not isinstance(now, datetime):
            raise ClimateRuntimeUnavailable(
                "climate synchronization requires local datetime"
            )
        if now.hour not in {10, 22} or now.minute != 0:
            return None
        slot = f"{now:%Y-%m-%dT%H}:00"
        async with self._lock:
            reserved, changed = reserve_scheduled_synchronization(
                self._command_guard_memory,
                slot=slot,
                reserved_at=self._safe_now(),
            )
            if not changed:
                return None
            await self._async_save_command_guard(reserved)
            self._command_guard_memory = reserved
            return await self._async_synchronize_climate_unlocked()

    async def _async_synchronize_climate_unlocked(
        self,
    ) -> _ClimateSynchronizationResult:
        self._require_native_contour_apply_mode()
        contour = contour_without_manual_devices(
            self._climate_contour(), self._manual_memory
        )
        if contour.mode is not ContourMode.AUTOMATIC:
            raise ClimateRuntimeUnavailable(
                "climate synchronization requires automatic contour mode"
            )
        room_ids = self._managed_room_ids(contour)
        if not room_ids:
            return _ClimateSynchronizationResult(
                status=ContourApplyStatus.CONFIRMED,
                confirmed_room_count=0,
                accepted_count=0,
            )
        observation = await self._async_native_climate_observation_unlocked()
        isolation = build_isolated_climate_policy_snapshot(contour, observation)
        call_plan = build_climate_ha_call_plan(
            self._registry,
            isolation,
            ir_code_service=self._ir_code_service,
        )
        synchronization = full_climate_synchronization_plan(
            call_plan,
            room_ids=room_ids,
        )
        calls = tuple(
            call
            for device in synchronization.devices
            for call in device.calls
        )
        if not calls:
            return _ClimateSynchronizationResult(
                status=ContourApplyStatus.UNAVAILABLE,
                confirmed_room_count=0,
                accepted_count=0,
            )
        if not self._calls_match_strict_registry(calls, room_ids=room_ids):
            return _ClimateSynchronizationResult(
                status=ContourApplyStatus.UNAVAILABLE,
                confirmed_room_count=0,
                accepted_count=0,
            )
        if self._strict_ha_call_executor is None:
            return _ClimateSynchronizationResult(
                status=ContourApplyStatus.UNAVAILABLE,
                confirmed_room_count=0,
                accepted_count=0,
            )
        await self._async_reserve_guarded_commands(synchronization.devices)
        try:
            executed = await self._strict_ha_call_executor.async_execute(calls)
        except Exception as error:
            self.last_error = type(error).__name__
            executed = _bounded_completed_count(
                getattr(error, "completed", 0),
                len(calls),
            )
            await self._async_record_direct_wifi_commands(
                calls,
                executed_count=executed,
            )
            await self._async_record_deviation_off_commands(
                calls,
                executed_count=executed,
            )
            return _ClimateSynchronizationResult(
                status=(
                    ContourApplyStatus.PARTIAL
                    if executed
                    else ContourApplyStatus.UNAVAILABLE
                ),
                confirmed_room_count=0,
                accepted_count=executed,
            )
        if not self._valid_executor_count(executed, len(calls)):
            self.last_error = "ClimateSynchronizationInvalidExecutionResult"
            return _ClimateSynchronizationResult(
                status=ContourApplyStatus.UNAVAILABLE,
                confirmed_room_count=0,
                accepted_count=0,
            )
        executed = _bounded_completed_count(executed, len(calls))
        await self._async_record_direct_wifi_commands(
            calls,
            executed_count=executed,
        )
        await self._async_record_deviation_off_commands(
            calls,
            executed_count=executed,
        )
        if executed != len(calls):
            return _ClimateSynchronizationResult(
                status=(
                    ContourApplyStatus.PARTIAL
                    if executed
                    else ContourApplyStatus.UNAVAILABLE
                ),
                confirmed_room_count=0,
                accepted_count=executed,
            )
        confirmed_rooms = self._confirmed_synchronization_rooms(
            synchronization.devices
        )
        return _ClimateSynchronizationResult(
            status=(
                ContourApplyStatus.CONFIRMED
                if confirmed_rooms == len(room_ids)
                else ContourApplyStatus.PARTIAL
            ),
            confirmed_room_count=confirmed_rooms,
            accepted_count=executed,
        )

    async def async_climate_promote_room(
        self,
        room_id: object,
    ) -> ClimateOwnershipReceipt | None:
        """Promote one verified room into HausmanHub management, atomically."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None or not isinstance(room_id, str):
                return None
            observation = await self._async_native_climate_observation_unlocked()
            isolation = build_isolated_climate_policy_snapshot(contour, observation)
            comparison = build_climate_comparison_snapshot(isolation, observation)
            decision = plan_room_promotion(
                room_id,
                bridge_mode=self.configuration.climate_bridge_mode,
                contour=contour,
                isolation=isolation,
                comparison=comparison,
                registry=self._registry,
            )
            observed_at = observation.observed_at
            if decision.registry is None:
                return climate_ownership_skip_receipt(
                    decision,
                    observed_at=observed_at,
                )
            if self._registry_store is None:
                self.last_error = "ClimateRegistryStoreUnavailable"
                return climate_ownership_failure_receipt(
                    decision,
                    observed_at=observed_at,
                )
            try:
                await self._registry_store.async_save(decision.registry)
            except Exception as error:
                self.last_error = type(error).__name__
                return climate_ownership_failure_receipt(
                    decision,
                    observed_at=observed_at,
                )
            self._registry = decision.registry
            return climate_ownership_promoted_receipt(
                decision,
                observed_at=observed_at,
            )

    def _trial_room_id(self, contour) -> str | None:
        """Return the one trial room holding a canary-scoped active device."""

        if contour is None:
            return None
        excluded_devices = set(self._manual_memory.manual_device_ids)
        manual_rooms = set(
            effective_manual_room_ids(self._manual_memory, self._registry)
        )
        trial_rooms: set[str] = set()
        for room in contour.rooms:
            actuators = tuple(
                device for device in self._registry.devices
                if device.device_id in set(room.device_ids)
                and device.kind not in _PASSIVE_KINDS
            )
            if room.room_id not in manual_rooms and actuators and all(
                device.control_scope is ClimateControlScope.CANARY
                and device.device_id not in excluded_devices
                and self._is_unique_core_control_device(device)
                for device in actuators
            ):
                trial_rooms.add(room.room_id)
        if len(trial_rooms) != 1:
            return None
        return next(iter(trial_rooms))

    def _managed_room_ids(self, contour) -> tuple[str, ...]:
        trial_room_id = self._trial_room_id(contour)
        manual_rooms = set(
            effective_manual_room_ids(self._manual_memory, self._registry)
        )
        result: list[str] = []
        for room in contour.rooms:
            if room.room_id == trial_room_id or room.room_id in manual_rooms:
                continue
            actuators = tuple(
                device
                for device in self._registry.devices
                if device.device_id in set(room.device_ids)
                and device.kind not in _PASSIVE_KINDS
            )
            if not actuators:
                continue
            if all(self._is_unique_managed_core_control_device(device) for device in actuators):
                result.append(room.room_id)
        return tuple(result)

    async def _async_apply_trial_decision(
        self,
        decision,
        devices: tuple[GuardedDeviceCalls, ...],
        *,
        required_scope: ClimateControlScope,
    ) -> ClimateTrialReceipt:
        if not decision.permitted:
            return climate_trial_skip_receipt(decision)
        if self._strict_ha_call_executor is None:
            return climate_trial_failure_receipt(
                decision,
                reason=ClimateTrialReason.EXECUTOR_UNAVAILABLE,
                executed_count=0,
            )
        if not self._calls_match_strict_registry(
            decision.calls,
            room_ids=(decision.room_id,),
            required_scope=required_scope,
        ):
            return climate_trial_failure_receipt(
                decision,
                reason=ClimateTrialReason.SERVICE_ERROR,
                executed_count=0,
            )
        try:
            await self._async_reserve_guarded_commands(devices)
        except Exception as error:
            self.last_error = type(error).__name__
            return climate_trial_failure_receipt(
                decision,
                reason=ClimateTrialReason.SERVICE_ERROR,
                executed_count=0,
            )
        try:
            executed = await self._strict_ha_call_executor.async_execute(
                decision.calls
            )
        except Exception as error:
            self.last_error = type(error).__name__
            executed = _bounded_completed_count(
                getattr(error, "completed", 0),
                len(decision.calls),
            )
            await self._async_record_direct_wifi_commands(
                decision.calls,
                executed_count=executed,
            )
            await self._async_record_deviation_off_commands(
                decision.calls,
                executed_count=executed,
            )
            if executed == len(decision.calls):
                return climate_trial_applied_receipt(decision)
            return climate_trial_failure_receipt(
                decision,
                reason=ClimateTrialReason.SERVICE_ERROR,
                executed_count=executed,
            )
        if not self._valid_executor_count(executed, len(decision.calls)):
            self.last_error = "ClimateTrialInvalidExecutionResult"
            return climate_trial_failure_receipt(
                decision,
                reason=ClimateTrialReason.SERVICE_ERROR,
                executed_count=0,
            )
        await self._async_record_direct_wifi_commands(
            decision.calls,
            executed_count=executed,
        )
        await self._async_record_deviation_off_commands(
            decision.calls,
            executed_count=executed,
        )
        if executed != len(decision.calls):
            self.last_error = "ClimateTrialShortExecution"
            return climate_trial_failure_receipt(
                decision,
                reason=ClimateTrialReason.SERVICE_ERROR,
                executed_count=executed,
            )
        return climate_trial_applied_receipt(decision)

    async def _async_native_climate_observation_unlocked(
        self,
        *,
        allow_disabled: bool = False,
    ) -> ClimateObservationSnapshot:
        """Read one observation without saving evidence or creating commands."""

        observed_at = self._safe_now()
        observation = self._native_ha_observation(
            observed_at,
            allow_disabled=allow_disabled,
        )
        if observation is None:
            # Without a native state view the internal pipeline must not
            # observe at all: the external bridge is never a fallback.
            observation = unavailable_climate_observation_snapshot(
                self._registry,
                observed_at=observed_at,
            )
        try:
            reconciled, reconciled_changed = reconcile_climate_manual_memory(
                self._manual_memory,
                self._registry,
                now_ms=observed_at,
            )
            # The native observation path always passes through the generic
            # manual reconciler.  Without an explicit external HA context it
            # only retains the established direct-Wi-Fi attribution rule;
            # unknown state changes never steal ownership.
            updated_manual, observed_changed = update_climate_manual_observation(
                reconciled,
                self._registry,
                observation,
                **self._native_manual_context_inputs(),
            )
            if reconciled_changed or observed_changed:
                await self._async_save_manual(updated_manual)
            self._manual_memory = updated_manual
            observation = apply_manual_rooms(
                observation, self._manual_memory, self._registry
            )
            update = update_climate_protection(
                self._protection_memory,
                self._registry,
                observation,
                restart_rearm_after=self._protection_restart_after,
            )
            if update.changed:
                await self._async_save_protection(update.memory)
            self._protection_memory = update.memory
            if update.rearm_complete:
                self._protection_restart_after = None
            return update.observation
        except Exception as error:
            self.last_error = type(error).__name__
            raise ClimateRuntimeUnavailable(
                "climate protection memory is unavailable"
            ) from error

    def _native_manual_context_inputs(self) -> dict[str, object]:
        """Translate native HA contexts into explicit, fail-closed ownership."""
        view = self._ha_state_view
        if view is None or self.configuration.climate_bridge_mode is ClimateControlMode.DISABLED:
            return {}
        recent = getattr(self._strict_ha_call_executor, "recent_context_ids", None)
        hausman = set(self._manual_memory.hausman_context_ids)
        if callable(recent):
            hausman.update(recent())
        external: list[str] = []
        contexts: dict[str, dict[str, object]] = {}
        for device in self._registry.devices:
            endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
            if endpoint is None:
                continue
            try:
                state = view.entity_state(endpoint.entity_id)
            except Exception:
                continue
            if state is None:
                continue
            context_id = getattr(state, "context_id", None)
            parent_id = getattr(state, "context_parent_id", None)
            user_id = getattr(state, "context_user_id", None)
            ids = {value for value in (context_id, parent_id) if isinstance(value, str)}
            # A user context is external even when it has a parent. Service
            # contexts are external only when neither link is ours.
            if (isinstance(user_id, str) and user_id) or (ids and not ids & hausman):
                external.append(device.device_id)
                contexts[device.device_id] = {
                    "context_id": context_id if isinstance(context_id, str) else None,
                    "parent_id": parent_id if isinstance(parent_id, str) else None,
                    "user_id": user_id if isinstance(user_id, str) else None,
                }
        return {"external_device_ids": tuple(external), "context_by_device": contexts}

    def _native_ha_observation(
        self,
        observed_at: int,
        *,
        allow_disabled: bool = False,
    ) -> ClimateObservationSnapshot | None:
        """Build the native observation, or None when observation is absent.

        A present state view makes the native Home Assistant observation the
        only input of the internal pipeline; facts are never mixed with the
        external module. A build failure fails closed to an unavailable
        observation instead of falling back to the bridge.
        """

        if (
            self._ha_state_view is None
            or (
                self.configuration.climate_bridge_mode
                is ClimateControlMode.DISABLED
                and not allow_disabled
            )
        ):
            return None
        try:
            local = self._local_now()
            observation = build_native_ha_climate_observation(
                self._registry,
                self._contours.contour(CLIMATE_CONTOUR_ID),
                self._ha_state_view,
                observed_at=observed_at,
                protection=self._protection_memory,
                local_time=(local.hour, local.minute),
                previous_weather_lockout=self._weather_heating_lockout,
                previous_central_heating_on=self._central_heating_on,
                local_month_day=(local.month, local.day),
            )
            self._weather_heating_lockout = observation.home.weather_heating_lockout
            self._central_heating_on = observation.home.central_heating_on
            self._track_outdoor_source(observation)
            return observation
        except (ClimateHaObservationViolation, ClimateObservationViolation) as error:
            self.last_error = type(error).__name__
            return unavailable_climate_observation_snapshot(
                self._registry,
                observed_at=observed_at,
            )
        except Exception as error:
            # A broken state view must fail the observation closed exactly
            # like a broken bridge read; it must never reach the pipeline.
            self.last_error = type(error).__name__
            return unavailable_climate_observation_snapshot(
                self._registry,
                observed_at=observed_at,
            )

    async def async_readiness(self) -> dict[str, object]:
        """Return redacted bridge and registry readiness to a local admin."""

        async with self._lock:
            mode = self.configuration.climate_bridge_mode
            if mode is ClimateControlMode.MANAGED:
                try:
                    observation = (
                        await self._async_native_climate_observation_unlocked()
                    )
                except ClimateRuntimeUnavailable:
                    observation = None
                if (
                    observation is not None
                    and observation.data_status is ClimateDataStatus.UNAVAILABLE
                ):
                    observation = None
                return native_climate_readiness(
                    self._registry,
                    observation,
                    bridge_mode=mode,
                )
            return native_climate_readiness(
                self._registry,
                None,
                bridge_mode=ClimateControlMode.DISABLED,
            )

    async def async_preview_registry(self, payload: object) -> dict[str, object]:
        """Validate and reconcile an unsaved registry without mutating storage."""

        async with self._lock:
            registry = registry_from_payload(payload)
            mode = self.configuration.climate_bridge_mode
            if mode is ClimateControlMode.DISABLED:
                return _registry_preview_payload(
                    registry,
                    status="validated_offline",
                    save_allowed=True,
                    fresh=False,
                    reconciliation=None,
                    reasons=("bridge_disabled",),
                )
            observation = self._native_observation_for_registry(registry)
            if observation is None or observation.data_status is ClimateDataStatus.UNAVAILABLE:
                return _registry_preview_payload(
                    registry,
                    status="unavailable",
                    save_allowed=False,
                    fresh=False,
                    reconciliation=None,
                    reasons=("climate_state_unavailable",),
                )
            reconciliation = native_climate_reconciliation(registry, observation)
            reasons = native_readiness_reasons(
                registry,
                observation,
                fresh=observation.data_status is ClimateDataStatus.FRESH,
                matches=reconciliation.matches,
            )
            return _registry_preview_payload(
                registry,
                status="ready" if not reasons else "not_ready",
                save_allowed=True,
                fresh=observation.data_status is ClimateDataStatus.FRESH,
                reconciliation=reconciliation,
                reasons=tuple(dict.fromkeys(reasons)),
            )

    async def async_replace_registry(self, payload: object) -> dict[str, object]:
        """Validate and atomically replace the registry outside active canary."""

        async with self._lock:
            registry = registry_from_payload(payload)
            validate_contour_bindings(self._contours, registry)
            await self._registry_store.async_save(registry)
            self._registry = registry
            self._central_heating_on = None
            self.last_error = None
            return registry_to_payload(registry)

    async def async_update_home_environment(
        self,
        home: dict[str, object],
    ) -> dict[str, object]:
        """Atomically replace only the saved home environment settings."""

        async with self._lock:
            payload = registry_to_payload(self._registry)
            payload["home"] = home
            registry = registry_from_payload(payload)
            validate_contour_bindings(self._contours, registry)
            await self._registry_store.async_save(registry)
            self._registry = registry
            self._central_heating_on = None
            self.last_error = None
            result = registry_to_payload(registry)
            result["setup_revision"] = climate_setup_revision(registry, self._contours)
            return result

    async def async_climate_season_settings_document(self) -> dict[str, object]:
        """Return the public interseason settings document with its revision."""

        async with self._lock:
            payload = registry_to_payload(self._registry)
            home = payload.get("home")
            if not isinstance(home, dict):
                raise ClimateRegistryViolation("climate registry home is invalid")
            return {
                "contract": {
                    "name": "hausman-hub-climate-season-settings",
                    "version": 1,
                },
                "revision": climate_setup_revision(self._registry, self._contours),
                "updatedAt": _season_settings_updated_at(home),
                "settings": interseason_settings_wire(home),
            }

    @staticmethod
    def climate_season_settings_document_from_result(
        result: dict[str, object],
    ) -> dict[str, object]:
        """Render the public interseason document from an update result."""

        home = result.get("home")
        if not isinstance(home, dict):
            raise ClimateRegistryViolation("climate registry home is invalid")
        return {
            "contract": {
                "name": "hausman-hub-climate-season-settings",
                "version": 1,
            },
            "revision": result["setup_revision"],
            "updatedAt": _season_settings_updated_at(home),
            "settings": interseason_settings_wire(home),
        }

    async def async_update_interseason_settings(
        self,
        fields: dict[str, object],
    ) -> dict[str, object]:
        """Atomically merge validated interseason settings into the saved home."""

        async with self._lock:
            payload = registry_to_payload(self._registry)
            home = payload.get("home")
            if not isinstance(home, dict):
                raise ClimateRegistryViolation("climate registry home is invalid")
            home.update(fields)
            home["interseason_updated_at"] = self._safe_now()
            registry = registry_from_payload(payload)
            validate_contour_bindings(self._contours, registry)
            await self._registry_store.async_save(registry)
            self._registry = registry
            self.last_error = None
            result = registry_to_payload(registry)
            result["setup_revision"] = climate_setup_revision(registry, self._contours)
            return result

    async def async_update_room_window(
        self,
        room_id: str,
        window_entity_id: str | None,
    ) -> dict[str, object]:
        """Atomically replace only one saved room window binding."""

        async with self._lock:
            payload = registry_to_payload(self._registry)
            rooms = payload.get("rooms")
            if not isinstance(rooms, list):
                raise ClimateRegistryViolation("climate registry rooms are invalid")
            target = next(
                (
                    room
                    for room in rooms
                    if isinstance(room, dict) and room.get("id") == room_id
                ),
                None,
            )
            if target is None:
                raise ClimateRegistryViolation("climate registry room is unknown")
            target["window_entity_id"] = window_entity_id
            registry = registry_from_payload(payload)
            validate_contour_bindings(self._contours, registry)
            await self._registry_store.async_save(registry)
            self._registry = registry
            self.last_error = None
            return registry_to_payload(registry)

    async def async_update_room_signals(
        self,
        room_id: str,
        window_entity_id: str | None,
        presence_entity_ids: tuple[str, ...],
    ) -> dict[str, object]:
        """Atomically replace one room's window and presence bindings."""

        return await self.async_update_room_signal_batch(
            ((room_id, window_entity_id, presence_entity_ids),)
        )

    async def async_update_room_signal_batch(
        self,
        updates: tuple[tuple[str, str | None, tuple[str, ...]], ...],
    ) -> dict[str, object]:
        """Atomically replace complete signals for a bounded set of rooms."""

        async with self._lock:
            payload = registry_to_payload(self._registry)
            rooms = payload.get("rooms")
            if not isinstance(rooms, list):
                raise ClimateRegistryViolation("climate registry rooms are invalid")
            for room_id, window_entity_id, presence_entity_ids in updates:
                target = next(
                    (
                        room
                        for room in rooms
                        if isinstance(room, dict) and room.get("id") == room_id
                    ),
                    None,
                )
                if target is None:
                    raise ClimateRegistryViolation(
                        "climate registry room is unknown"
                    )
                target["window_entity_id"] = window_entity_id
                if presence_entity_ids:
                    target["presence_entity_ids"] = list(presence_entity_ids)
                else:
                    target.pop("presence_entity_ids", None)
            registry = registry_from_payload(payload)
            validate_contour_bindings(self._contours, registry)
            await self._registry_store.async_save(registry)
            self._registry = registry
            self.last_error = None
            return registry_to_payload(registry)

    async def async_climate_mode_status(self) -> dict[str, object]:
        """Report the saved climate control mode and contour configuration."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            return {
                "mode": self.configuration.climate_bridge_mode.value,
                "contour_configured": contour is not None and bool(contour.rooms),
            }

    async def async_climate_rollout_status(
        self,
        shadow_window: object,
    ) -> dict[str, object]:
        """Evaluate the command-free shadow-to-canary gate."""

        async with self._lock:
            return climate_rollout_status(
                self._registry,
                self._contours,
                bridge_mode=self.configuration.climate_bridge_mode,
                shadow_window=shadow_window,
            )

    async def async_climate_cutover_status(
        self,
        shadow_window: object,
    ) -> dict[str, object]:
        """Evaluate whether the retired Node-RED contour can be switched off."""

        async with self._lock:
            return climate_cutover_status(
                self._registry,
                self._contours,
                bridge_mode=self.configuration.climate_bridge_mode,
                shadow_window=shadow_window,
            )

    def signal_entity_known(self, entity_id: str) -> bool:
        """Answer whether one entity currently has any readable local state."""

        view = self._ha_state_view
        if view is None:
            return False
        try:
            return view.entity_state(entity_id) is not None
        except Exception:
            return False

    async def async_signal_catalog(
        self,
        signal_kind: str,
    ) -> list[dict[str, object]]:
        """List bounded local candidates for one signal binding selection."""

        view = self._ha_state_view
        catalog = getattr(view, "signal_entity_catalog", None)
        if view is None or catalog is None:
            raise ClimateRuntimeUnavailable(
                "the local signal entity catalog is unavailable"
            )
        projected = catalog(signal_kind)
        room_names = {
            room.room_id: room.name
            for room in projected.rooms
        }
        result: list[dict[str, object]] = []
        for entry in projected.entries:
            item: dict[str, object] = {
                "entity_id": entry.entity_id,
                "name": entry.friendly_name or entry.entity_id,
                "available": entry.available,
                "domain": entry.domain,
                "room_id": entry.room_id,
            }
            if entry.device_class is not None:
                item["device_class"] = entry.device_class
            if entry.room_id in room_names:
                item["room_name"] = room_names[entry.room_id]
            for key in (
                "device_group_id",
                "device_name",
                "manufacturer",
                "model",
                "image_url",
            ):
                value = getattr(entry, key)
                if value is not None:
                    item[key] = value
            result.append(item)
        return result

    def signal_entity_suitable(
        self,
        signal_kind: str,
        entity_id: str,
        *,
        room_id: str | None = None,
    ) -> bool:
        """Check one current entity against the same catalog shown by the UI."""

        view = self._ha_state_view
        catalog = getattr(view, "signal_entity_catalog", None)
        if view is None or catalog is None:
            return False
        try:
            entry = catalog(signal_kind).entry(entity_id)
        except Exception:
            return False
        return entry is not None and (
            room_id is None or entry.room_id == room_id
        )

    async def async_replace_contours(self, payload: object) -> dict[str, object]:
        """Replace contour definitions while keeping their bindings exact."""

        async with self._lock:
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            contours = contour_registry_from_payload(payload)
            validate_contour_bindings(contours, self._registry)
            await self._contour_store.async_save(contours)
            self._contours = contours
            self.last_error = None
            return contour_registry_to_payload(contours)

    async def async_apply_legacy_settings(
        self,
        payload: object,
        settings_service: HausmanHubSettingsService,
    ) -> dict[str, object]:
        """Persist one unchanged legacy preview without physical commands."""

        async with self._lock:
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            current_settings = settings_service.current
            plan = build_legacy_settings_apply(
                payload,
                current_settings=current_settings,
                current_contours=self._contours,
            )
            contours_changed = plan.contours != self._contours
            settings_changed = plan.settings != current_settings
            previous_contours = self._contours
            contours_saved = False
            try:
                if contours_changed:
                    await self._contour_store.async_save(plan.contours)
                    contours_saved = True
                if settings_changed:
                    await settings_service.async_replace(plan.settings)
            except Exception as error:
                if contours_saved:
                    try:
                        await self._contour_store.async_save(previous_contours)
                    except Exception as rollback_error:
                        self.last_error = type(rollback_error).__name__
                        raise ClimateRuntimeUnavailable(
                            "legacy settings rollback failed"
                        ) from rollback_error
                self._contours = previous_contours
                self.last_error = type(error).__name__
                raise
            self._contours = plan.contours
            self.last_error = None
            return plan.receipt

    async def async_rollback_climate_migration(
        self,
        receipt: ClimateMigrationReceipt,
    ) -> dict[str, object]:
        """Remove exactly the migrated setup when nothing else changed."""

        async with self._lock:
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            registry, contours = rollback_migrated_setup(
                self._registry,
                self._contours,
                receipt,
            )
            return await self._async_persist_contour_setup_unlocked(
                registry,
                contours,
            )

    async def async_replace_contour_setup(
        self,
        registry_payload: object,
        contour_payload: object,
    ) -> dict[str, object]:
        """Save selected devices and contours as one rollback-protected setup."""

        async with self._lock:
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            registry = registry_from_payload(registry_payload)
            contours = contour_registry_from_payload(contour_payload)
            validate_contour_bindings(contours, registry)
            return await self._async_persist_contour_setup_unlocked(
                registry,
                contours,
            )

    async def _async_persist_contour_setup_unlocked(
        self,
        registry: ClimateRegistry,
        contours: ContourRegistry,
    ) -> dict[str, object]:
        """Persist one already validated setup with complete rollback semantics."""

        if self._contour_store is None:
            raise ClimateRuntimeUnavailable("contour storage is unavailable")
        validate_contour_bindings(contours, registry)
        previous_registry = self._registry
        previous_contours = self._contours
        registry_saved = False
        contours_saved = False
        try:
            await self._registry_store.async_save(registry)
            registry_saved = True
            await self._contour_store.async_save(contours)
            contours_saved = True
        except Exception as error:
            rollback_error: Exception | None = None
            registry_restored = not registry_saved
            if registry_saved:
                try:
                    await self._registry_store.async_save(previous_registry)
                except Exception as failure:
                    rollback_error = failure
                else:
                    registry_restored = True
            contours_restored = not contours_saved
            if contours_saved and registry_restored:
                try:
                    await self._contour_store.async_save(previous_contours)
                except Exception as failure:
                    rollback_error = rollback_error or failure
                else:
                    contours_restored = True

            if registry_restored and contours_restored:
                self._registry = previous_registry
                self._contours = previous_contours
            else:
                # If either backward write fails, keep the already-saved new
                # pair together. A registry rollback can fail before contours
                # are touched; a contour rollback failure is compensated by
                # restoring the new registry.
                if not registry_restored and not contours_saved:
                    try:
                        await self._contour_store.async_save(contours)
                    except Exception as failure:
                        self._registry = registry
                        self._contours = previous_contours
                        self.last_error = type(failure).__name__
                        raise ClimateRuntimeUnavailable(
                            "contour setup storage is inconsistent"
                        ) from failure
                if registry_restored and contours_saved:
                    try:
                        await self._registry_store.async_save(registry)
                    except Exception as failure:
                        self._registry = previous_registry
                        self._contours = contours
                        self.last_error = type(failure).__name__
                        raise ClimateRuntimeUnavailable(
                            "contour setup storage is inconsistent"
                        ) from failure
                self._registry = registry
                self._contours = contours
            self.last_error = type(error).__name__
            if rollback_error is not None:
                raise ClimateRuntimeUnavailable(
                    "contour setup rollback failed"
                ) from rollback_error
            raise
        self._registry = registry
        self._contours = contours
        self.last_error = None
        return {
            "registry": registry_to_payload(registry),
            "contours": contour_registry_to_payload(contours),
        }

    async def _async_save_protection(
        self,
        memory: ClimateProtectionMemory,
    ) -> None:
        if self._protection_store is not None:
            await self._protection_store.async_save(memory)

    async def _async_save_manual(
        self,
        memory: ClimateManualMemory,
    ) -> None:
        if self._manual_store is not None:
            await self._manual_store.async_save(memory)

    async def _async_save_command_guard(
        self,
        memory: ClimateCommandGuardMemory,
    ) -> None:
        if self._command_guard_store is not None:
            await self._command_guard_store.async_save(memory)

    async def _async_rearm_command_guard(
        self,
        comparison: ClimateComparisonSnapshot,
    ) -> None:
        updated, changed = clear_aligned_climate_commands(
            self._command_guard_memory,
            comparison,
            now_ms=self._safe_now(),
        )
        if changed:
            await self._async_save_command_guard(updated)
            self._command_guard_memory = updated

    def _guard_diverged_calls(
        self,
        call_plan: ClimateHaCallPlanSnapshot,
        comparison: ClimateComparisonSnapshot,
    ):
        view = self._ha_state_view
        return guard_diverged_climate_calls(
            call_plan,
            comparison,
            state_lookup=(
                (lambda _entity_id: None)
                if view is None
                else view.entity_state
            ),
            memory=self._command_guard_memory,
        )

    async def _async_reserve_guarded_commands(
        self,
        devices: tuple[GuardedDeviceCalls, ...],
    ) -> None:
        if not devices:
            return
        reserved = reserve_guarded_commands(
            self._command_guard_memory,
            devices,
            attempted_at=self._safe_now(),
        )
        await self._async_save_command_guard(reserved)
        self._command_guard_memory = reserved

    def _confirmed_synchronization_rooms(
        self,
        devices: tuple[GuardedDeviceCalls, ...],
    ) -> int:
        view = self._ha_state_view
        if view is None:
            return 0
        by_room: dict[str, list[GuardedDeviceCalls]] = {}
        for device in devices:
            by_room.setdefault(device.room_id, []).append(device)
        return sum(
            all(
                climate_call_is_satisfied(call, view.entity_state)
                for device in room_devices
                for call in device.calls
            )
            for room_devices in by_room.values()
        )

    async def _async_record_direct_wifi_commands(
        self,
        calls: tuple[ClimateHaServiceCall, ...],
        *,
        executed_count: int,
    ) -> None:
        updated, changed = record_direct_wifi_commands(
            self._manual_memory,
            self._registry,
            calls,
            executed_count=executed_count,
            commanded_at=self._safe_now(),
        )
        recent = getattr(self._strict_ha_call_executor, "recent_context_ids", None)
        context_ids = tuple(recent()) if callable(recent) else ()
        merged = tuple(dict.fromkeys((*self._manual_memory.hausman_context_ids, *context_ids)))[-128:]
        if merged != self._manual_memory.hausman_context_ids:
            updated = replace(updated, hausman_context_ids=merged)
            changed = True
        if changed:
            await self._async_save_manual(updated)
            self._manual_memory = updated

    async def _async_capture_hausman_contexts(self) -> None:
        """Persist a bounded executor provenance window across restart."""
        recent = getattr(self._strict_ha_call_executor, "recent_context_ids", None)
        context_ids = tuple(recent()) if callable(recent) else ()
        merged = tuple(dict.fromkeys((*self._manual_memory.hausman_context_ids, *context_ids)))[-128:]
        if merged == self._manual_memory.hausman_context_ids:
            return
        updated = replace(self._manual_memory, hausman_context_ids=merged)
        await self._async_save_manual(updated)
        self._manual_memory = updated

    async def _async_record_deviation_off_commands(
        self,
        calls: tuple[ClimateHaServiceCall, ...],
        *,
        executed_count: int,
    ) -> None:
        service = self._deviation_guard
        if service is None or executed_count <= 0:
            return
        successful_entities = {
            call.entity_id
            for call in calls[:executed_count]
            if call.service.value == "climate.set_hvac_mode"
            and getattr(call.hvac_mode, "value", None) == "off"
        }
        if not successful_entities:
            return
        device_ids = tuple(
            device.device_id
            for device in self._registry.devices
            if device.kind is ClimateDeviceKind.AIR_CONDITIONER
            if (
                (endpoint := device.endpoint(ClimateEndpointRole.CONTROL)) is not None
                and endpoint.entity_id in successful_entities
            )
        )
        if device_ids:
            await service.async_note_off_commands(
                device_ids,
                commanded_at=self._safe_now(),
            )
            await self._async_confirm_deviation_off_commands(
                successful_entities,
            )

    async def _async_confirm_deviation_off_commands(
        self,
        successful_entities: set[str],
    ) -> None:
        service = self._deviation_guard
        view = self._ha_state_view
        if service is None or view is None:
            return
        confirmed_entities: set[str] = set()
        for attempt in range(_CLIMATE_DEVIATION_OFF_READBACK_ATTEMPTS):
            confirmed_entities.update(
                entity_id
                for entity_id in successful_entities - confirmed_entities
                if getattr(view.entity_state(entity_id), "state", None) == "off"
            )
            if confirmed_entities == successful_entities:
                break
            if attempt + 1 < _CLIMATE_DEVIATION_OFF_READBACK_ATTEMPTS:
                await asyncio.sleep(_CLIMATE_READBACK_INTERVAL_SECONDS)
        if not confirmed_entities:
            return
        confirmed_device_ids = tuple(
            device.device_id
            for device in self._registry.devices
            if (
                (endpoint := device.endpoint(ClimateEndpointRole.CONTROL)) is not None
                and endpoint.entity_id in confirmed_entities
            )
        )
        await service.async_confirm_off_commands(
            confirmed_device_ids,
            confirmed_at=self._safe_now(),
        )

    async def _async_run_deviation_guard(
        self,
        call_plan: ClimateHaCallPlanSnapshot,
        *,
        managed_room_ids: tuple[str, ...],
    ) -> frozenset[str]:
        service = self._deviation_guard
        view = self._ha_state_view
        if service is None or view is None:
            return frozenset()
        now = self._safe_now()
        retries = await service.async_evaluate(
            call_plan,
            managed_room_ids=managed_room_ids,
            state_lookup=view.entity_state,
            now_ms=now,
        )
        for retry in retries:
            accepted = False
            if (
                self._strict_ha_call_executor is not None
                and self._calls_match_strict_registry(
                    (retry.call,),
                    room_ids=managed_room_ids,
                    required_scope=ClimateControlScope.MANAGED,
                )
            ):
                try:
                    result = await self._strict_ha_call_executor.async_execute((retry.call,))
                    accepted = type(result) is int and result == 1
                except Exception as error:
                    self.last_error = type(error).__name__
            await service.async_record_retry(
                retry.device_id,
                attempted_at=now,
                accepted=accepted,
            )
        return service.active_device_ids

    def _with_deviation_guard_status(
        self,
        snapshot: dict[str, object],
    ) -> dict[str, object]:
        service = self._deviation_guard
        rooms = snapshot.get("rooms")
        if service is None or not isinstance(rooms, list):
            return snapshot
        for room in rooms:
            if not isinstance(room, dict) or not isinstance(room.get("devices"), list):
                continue
            for device in room["devices"]:
                if not isinstance(device, dict) or not isinstance(device.get("id"), str):
                    continue
                status = service.device_status(device["id"], device.get("state"))
                if status is not None:
                    device["deviation_guard"] = status
                    next_retry_at = status.get("next_retry_at")
                    generated_at = snapshot.get("observed_at")
                    if type(generated_at) is not int:
                        generated_at = snapshot.get("generated_at")
                    if (
                        type(next_retry_at) is int
                        and type(generated_at) is int
                        and next_retry_at > generated_at
                    ):
                        device["cooldown"] = {
                            "active": True,
                            "remaining_seconds": min(
                                86400,
                                max(1, (next_retry_at - generated_at + 999) // 1000),
                            ),
                            "reason": "rate_limit",
                        }
        return snapshot

    async def async_deviation_guard_device_ids(self) -> tuple[str, ...]:
        """Return stable managed AC IDs accepted by the admin settings API."""

        async with self._lock:
            return tuple(
                device.device_id
                for device in self._registry.devices
                if device.kind is ClimateDeviceKind.AIR_CONDITIONER
                and device.control_scope is ClimateControlScope.MANAGED
            )

    def _native_observation_for_registry(
        self,
        registry: ClimateRegistry,
    ) -> ClimateObservationSnapshot | None:
        """Build the native observation for one unsaved registry draft."""

        if self._ha_state_view is None:
            return None
        observed_at = self._safe_now()
        try:
            local = self._local_now()
            return build_native_ha_climate_observation(
                registry,
                self._contours.contour(CLIMATE_CONTOUR_ID),
                self._ha_state_view,
                observed_at=observed_at,
                protection=self._protection_memory,
                local_time=(local.hour, local.minute),
            )
        except Exception as error:
            self.last_error = type(error).__name__
            return None

    async def _async_native_setup_snapshot_unlocked(self) -> ClimateImportSnapshot:
        """Build the wizard discovery snapshot without any bridge contact."""

        if self._ha_state_view is None:
            raise ClimateRuntimeUnavailable("climate state is unavailable")
        observation = self._native_ha_observation(self._safe_now())
        if observation is None:
            # The disabled control pipeline never observes, but an explicit
            # admin wizard may read Home Assistant for discovery only.
            observed_at = self._safe_now()
            try:
                local = self._local_now()
                observation = build_native_ha_climate_observation(
                    self._registry,
                    self._contours.contour(CLIMATE_CONTOUR_ID),
                    self._ha_state_view,
                    observed_at=observed_at,
                    protection=self._protection_memory,
                    local_time=(local.hour, local.minute),
                )
            except Exception:
                raise ClimateRuntimeUnavailable(
                    "climate state is unavailable"
                ) from None
        catalog = self._ha_state_view.entity_catalog()
        return build_native_climate_setup_snapshot(
            self._registry,
            observation,
            catalog,
        )

    def _native_entity_catalog_unlocked(self):
        """Read the bounded native entity catalog behind the runtime lock."""

        view = self._ha_state_view
        catalog = getattr(view, "binding_entity_catalog", None)
        if not callable(catalog):
            catalog = getattr(view, "entity_catalog", None)
        if view is None or not callable(catalog):
            raise ClimateRuntimeUnavailable(
                "the native Home Assistant entity catalog is unavailable"
            )
        return catalog()

    async def _async_reconcile_native_registry_unlocked(self, catalog) -> None:
        """Persist exact endpoint recovery once HA entities finish loading."""

        registry, changed = reconcile_native_climate_registry(
            self._registry,
            catalog,
        )
        if not changed:
            return
        validate_contour_bindings(self._contours, registry)
        await self._registry_store.async_save(registry)
        self._registry = registry
        self._central_heating_on = None
        self.last_error = None

    def _safe_now(self) -> int:
        value = self._now_ms()
        if type(value) is not int or value < 0:
            raise RuntimeError("climate runtime clock returned an unsafe timestamp")
        return value

    def _require_native_contour_apply_mode(self) -> None:
        if self.configuration.climate_bridge_mode is not ClimateControlMode.MANAGED:
            raise ClimateRuntimeUnavailable(
                "contour settings require managed native climate control"
            )

    def _climate_contour(self) -> ContourDefinition:
        contour = self._contours.contour(CLIMATE_CONTOUR_ID)
        if contour is None:
            raise ContourApplyViolation("climate contour is not configured")
        return contour

    def _require_climate_contour(
        self,
        contours: ContourRegistry,
    ) -> ContourDefinition:
        contour = contours.contour(CLIMATE_CONTOUR_ID)
        if contour is None:
            raise ContourApplyViolation("climate contour is not configured")
        return contour

def _without_guarded_devices(
    guarded: GuardedClimatePlan,
    device_ids: frozenset[str],
) -> GuardedClimatePlan:
    """Let the deviation guard exclusively own its armed device retries."""

    if not device_ids:
        return guarded
    rooms = tuple(
        replace(
            room,
            devices=tuple(
                device for device in room.devices if device.device_id not in device_ids
            ),
        )
        for room in guarded.call_plan.rooms
    )
    return GuardedClimatePlan(
        call_plan=replace(guarded.call_plan, rooms=rooms),
        devices=tuple(
            device for device in guarded.devices if device.device_id not in device_ids
        ),
    )


def _bounded_completed_count(value: object, maximum: int) -> int:
    if type(value) is int and 0 <= value <= maximum:
        return value
    return 0


def _contour_reliability_metadata(
    contour: ContourDefinition,
    plan,
    context: ClimateControlContext,
    request: object,
    observation,
    *,
    expected_control_revision: int,
    resulting_control_revision: int | None = None,
    external_reliability_identity: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Freeze the public scope and desired targets before first dispatch."""

    selected = [
        room for room in contour.rooms
        if room.room_id in set(plan.target_room_ids)
    ]
    # Receipt leaves describe only physical executor ownership.  Sensors and
    # other read-only bindings participate in observation, never in a command
    # outcome.  For a zero-call already-aligned plan retain configured leaves
    # so the public receipt can state that no dispatch was needed.
    called_ids = {
        call.owner_device_id for call in plan.strict_calls
        if isinstance(call.owner_device_id, str)
    }
    explicit_ids: set[str] = set()
    if plan.explicit_target_alignment:
        explicit_ids = {
            gate.device_id for gate in plan.native_plan.device_gates
        }
    # Explicit target alignment must retain already-matching owners beside
    # owners that still need a strict call.  Using only `called_ids` made a
    # mixed operation lose its zero-call leaves from the frozen proof.
    selected_ids = (called_ids | explicit_ids) or {
        device_id for room in selected for device_id in room.device_ids
    }
    devices_by_room = [
        {"room_id": room.room_id,
         "device_ids": [device_id for device_id in room.device_ids if device_id in selected_ids]}
        for room in selected
    ]
    devices_by_room = [row for row in devices_by_room if row["device_ids"]]
    device_ids = [device_id for row in devices_by_room for device_id in row["device_ids"]]
    scope = {
        "room_ids": [room.room_id for room in selected],
        "device_ids": device_ids,
        "devices_by_room": devices_by_room,
    }
    desired: dict[str, dict[str, object]] = {}
    for room in selected:
        settings = room.active_settings
        for device_id in room.device_ids:
            if device_id not in selected_ids:
                continue
            desired[device_id] = {
                "target_temperature": settings.target_temperature,
                "target_humidity": settings.target_humidity,
                "minimum_temperature": room.min_temperature,
                "target_strategy": settings.strategy.value,
                "mode": "automatic", "state": None,
                "override_state": "active" if room.temporary_override is not None else "cleared",
                "synchronization": None,
                "resulting_target_temperature": settings.target_temperature,
            }
    already_in_sync_evidence: dict[str, dict[str, object]] = {}
    if plan.explicit_target_alignment:
        observed_devices = {
            device.device_id: device
            for device in getattr(observation, "devices", ())
            if isinstance(getattr(device, "device_id", None), str)
        }
        observed_rooms = {
            room.room_id: room
            for room in getattr(observation, "rooms", ())
            if isinstance(getattr(room, "room_id", None), str)
        }
        for device_id in device_ids:
            observed = observed_devices.get(device_id)
            expected = desired.get(device_id)
            room_id = next(
                (
                    row["room_id"]
                    for row in devices_by_room
                    if device_id in row["device_ids"]
                ),
                None,
            )
            room_observation = observed_rooms.get(room_id)
            observed_at = getattr(observed, "observed_at", None)
            snapshot_observed_at = getattr(observation, "observed_at", None)
            if (
                expected is None
                or observed is None
                or not bool(getattr(observed, "available", False))
                or type(observed_at) is not int
                or type(snapshot_observed_at) is not int
                or observed_at < 0
                or snapshot_observed_at < 0
                or observed_at > 9_007_199_254_740_991
                or snapshot_observed_at > 9_007_199_254_740_991
                or observed_at > snapshot_observed_at
                or snapshot_observed_at - observed_at > MAX_NATIVE_STATE_AGE_MS
            ):
                continue
            reported_temperature = getattr(
                observed, "current_target_temperature", None
            )
            reported_humidity = getattr(observed, "current_target_humidity", None)
            # Direct legacy receipts retain their established room fallback.
            # A tablet home-target receipt instead proves an individual owner:
            # a thermostat's room humidity and a humidifier's room temperature
            # can never stand in for its unowned axis.
            if external_reliability_identity is None:
                if reported_temperature is None:
                    reported_temperature = getattr(
                        room_observation, "observed_target_temperature", None
                    )
                if reported_humidity is None:
                    reported_humidity = getattr(
                        room_observation, "observed_target_humidity", None
                    )
            actual = {
                **expected,
                "target_temperature": reported_temperature,
                "target_humidity": reported_humidity,
            }
            # Device gates are the frozen authority.  A humidifier does not
            # need a temperature readback and a thermostat does not need a
            # humidity readback merely because both values exist in a room.
            gate = next((gate for gate in plan.native_plan.device_gates if gate.device_id == device_id), None)
            if gate is None or gate.status.value != "aligned":
                continue
            owns_temperature = getattr(observed, "current_target_temperature", None) is not None
            if (owns_temperature and actual["target_temperature"] != expected["target_temperature"]) or (
                not owns_temperature and actual["target_humidity"] != expected["target_humidity"]
            ):
                continue
            if external_reliability_identity is not None:
                tablet_parameters = external_reliability_identity["parameters"]
                tablet_action = external_reliability_identity["action"]
                target_temperature = tablet_parameters.get("target_temperature")
                target_humidity = tablet_parameters.get("target_humidity")
                reported_humidity = (
                    actual["target_humidity"]
                    if "target_humidity" in tablet_parameters else None
                )
                observed_actual = {
                    "desired_target_temperature": target_temperature,
                    "reported_target_temperature": actual["target_temperature"],
                    "desired_target_humidity": target_humidity,
                    "reported_target_humidity": reported_humidity,
                }
                if (
                    tablet_action == "clear_room_override"
                    and expected["override_state"] == "cleared"
                ):
                    # This is a durable desired-state transition, not a
                    # physical call.  Bind the cleared override to the fresh
                    # scheduled actuator read-back without manufacturing a
                    # temperature command.
                    observed_actual.update(
                        desired_minimum_temperature=None,
                        reported_minimum_temperature=expected["minimum_temperature"],
                        desired_target_strategy=None,
                        reported_target_strategy=expected["target_strategy"],
                        desired_mode=None,
                        reported_mode=None,
                        desired_state=None,
                        reported_state=None,
                        desired_override_state="cleared",
                        reported_override_state="cleared",
                        desired_synchronization=None,
                        reported_synchronization=None,
                    )
                already_in_sync_evidence[device_id] = {
                    "desired_target_temperature": target_temperature,
                    "desired_target_humidity": target_humidity,
                    "reported_target_temperature": actual["target_temperature"],
                    "reported_target_humidity": reported_humidity,
                    "observed_actual": observed_actual,
                    "observed_at": observed_at,
                    "fresh": getattr(observation, "data_status", None)
                    is ClimateDataStatus.FRESH,
                }
            else:
                already_in_sync_evidence[device_id] = {
                    "desired_target_temperature": expected["target_temperature"],
                    "desired_target_humidity": expected["target_humidity"],
                    "reported_target_temperature": actual["target_temperature"],
                    "reported_target_humidity": actual["target_humidity"],
                    "observed_actual": actual,
                    "observed_at": observed_at,
                    "fresh": getattr(observation, "data_status", None)
                    is ClimateDataStatus.FRESH,
                }
    if context.action is ClimateControlAction.APPLY_SCHEDULE_PROFILE:
        parameters: dict[str, object] = {"contour_id": "climate", "confirm": True,
                                         "schedule_profile": context.profile.value}
        kind = "contour_apply"
    elif context.action is ClimateControlAction.APPLY_SAVED_SETTINGS:
        parameters = {"contour_id": "climate", "confirm": True}
        kind = "contour_apply"
    else:
        parameters = {"contour_id": "climate", "confirm": True, "room_id": context.room_id,
                      "action": "clear" if context.action is ClimateControlAction.RETURN_TO_SCHEDULE else "set",
                      "target_temperature": None if context.action is ClimateControlAction.RETURN_TO_SCHEDULE else context.target_temperature}
        kind = "temporary_clear" if context.action is ClimateControlAction.RETURN_TO_SCHEDULE else "temporary_set"
    action_code = context.as_payload()["code"]
    if external_reliability_identity is not None:
        fingerprint = external_reliability_identity.get("request_fingerprint")
        external_action = external_reliability_identity.get("action")
        external_parameters = external_reliability_identity.get("parameters")
        if (
            not isinstance(fingerprint, str)
            or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None
            or not isinstance(external_action, str)
            or not isinstance(external_parameters, Mapping)
        ):
            raise ContourApplyViolation("reserved tablet climate identity is invalid")
        action_code = external_action
        parameters = dict(external_parameters)
    else:
        fingerprint = _direct_reliability_request_fingerprint(
            request_id=getattr(request, "request_id"),
            correlation_id=getattr(request, "correlation_id"), context=context,
            scope=scope, expected_control_revision=expected_control_revision,
        )
    desired_fingerprint = hashlib.sha256(json.dumps(desired, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()
    scope_fingerprint = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()
    target = context.target_temperature
    target_humidity: int | None = None
    if external_reliability_identity is not None:
        external_parameters = external_reliability_identity.get("parameters")
        if (
            external_reliability_identity.get("action") == "set_home_targets"
            and isinstance(external_parameters, Mapping)
        ):
            target = external_parameters.get("target_temperature")
            target_humidity = external_parameters.get("target_humidity")
    leaf_ledger = {device_id: "pending_dispatch" for device_id in device_ids}
    aligned_ids = {
        gate.device_id for gate in plan.native_plan.device_gates
        if gate.status.value == "aligned"
    }
    if (
        device_ids
        and aligned_ids == set(device_ids)
        and len(already_in_sync_evidence) == len(device_ids)
        and all(
            evidence.get("fresh") is True
            for evidence in already_in_sync_evidence.values()
        )
    ):
        leaf_ledger = {device_id: "already_in_sync" for device_id in device_ids}
    elif plan.explicit_target_alignment and not plan.strict_calls:
        # The frozen plan has no physical call.  Without device-level proof it
        # is terminally blocked before dispatch, not pending for a command
        # that cannot exist.
        leaf_ledger = {device_id: "blocked_before_dispatch" for device_id in device_ids}
    elif plan.explicit_target_alignment:
        leaf_ledger = {
            device_id: ("already_in_sync" if device_id in already_in_sync_evidence else "pending_dispatch")
            for device_id in device_ids
        }
    return {"kind": kind, "request_fingerprint": fingerprint, "action": action_code, "parameters": parameters,
            "resolved_scope": scope, "desired_snapshot": desired,
            "desired_snapshot_fingerprint": desired_fingerprint, "scope_fingerprint": scope_fingerprint,
            "expected_control_revision": expected_control_revision,
            "resulting_control_revision": (
                resulting_control_revision
                if resulting_control_revision is not None
                else expected_control_revision + 1
            ),
            "desired_target_temperature": target, "desired_target_humidity": target_humidity,
            "already_in_sync_evidence": already_in_sync_evidence,
            "leaf_ledger": leaf_ledger}


def _native_plan_resolved_scope(plan: ContourApplyPlan) -> dict[str, object]:
    """Return the canonical physical-owner scope of a frozen native plan."""

    target_ids = set(plan.target_room_ids)
    rows = [
        {
            "room_id": room.room_id,
            "device_ids": sorted(device.device_id for device in room.devices),
        }
        for room in plan.native_plan.call_plan.rooms
        if room.room_id in target_ids
    ]
    rows = sorted((row for row in rows if row["device_ids"]), key=lambda row: row["room_id"])
    return {
        "room_ids": [row["room_id"] for row in rows],
        "device_ids": [device_id for row in rows for device_id in row["device_ids"]],
        "devices_by_room": rows,
    }


def _direct_reliability_request_fingerprint(
    *, request_id: object, correlation_id: object, context: ClimateControlContext,
    scope: Mapping[str, object], expected_control_revision: int,
) -> str:
    """Canonical direct-control identity, including every public token."""
    if context.action is ClimateControlAction.APPLY_SCHEDULE_PROFILE:
        parameters: dict[str, object] = {"contour_id": "climate", "confirm": True,
                                         "schedule_profile": context.profile.value}
    elif context.action is ClimateControlAction.APPLY_SAVED_SETTINGS:
        parameters = {"contour_id": "climate", "confirm": True}
    else:
        parameters = {"contour_id": "climate", "confirm": True, "room_id": context.room_id,
                      "action": "clear" if context.action is ClimateControlAction.RETURN_TO_SCHEDULE else "set",
                      "target_temperature": None if context.action is ClimateControlAction.RETURN_TO_SCHEDULE else context.target_temperature}
    encoded = json.dumps({"request_id": request_id, "correlation_id": correlation_id,
                          "reliability_profile": "climate_reliability_v1",
                          "expected_control_revision": expected_control_revision,
                          "action": context.action.value, "parameters": parameters,
                          "scope": scope}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _contour_apply_diagnostics(plan) -> dict[str, object]:
    """Project gate and comparison facts for an unconfirmed apply log line."""

    native = plan.native_plan
    return {
        "denials": [reason.value for reason in native.denial_reasons],
        "aligned_rooms": list(native.initially_aligned_room_ids),
        "gates": [
            {
                "room": gate.room_id,
                "status": gate.status.value,
                "reasons": [reason.value for reason in gate.reasons],
            }
            for gate in native.room_gates
        ],
        "comparison": [
            {
                "room": room.room_id,
                "status": room.status.value,
                "reasons": [reason.value for reason in room.reasons],
                "devices": [
                    {
                        "id": device.device_id,
                        "status": device.status.value,
                        "reasons": [reason.value for reason in device.reasons],
                        "planned": device.planned_action.value,
                        "observed": device.observed_activity.value,
                    }
                    for device in room.devices
                ],
            }
            for room in native.comparison.rooms
        ],
    }


def _registry_preview_payload(
    registry: ClimateRegistry,
    *,
    status: str,
    save_allowed: bool,
    fresh: bool,
    reconciliation: object | None,
    reasons: tuple[str, ...],
) -> dict[str, object]:
    return {
        "contract": {
            "name": "hausman-hub-climate-registry-preview",
            "version": 1,
        },
        "status": status,
        "save_allowed": save_allowed,
        "fresh": fresh,
        "registry": {
            "version": registry.version,
            "room_count": len(registry.rooms),
            "device_count": len(registry.devices),
        },
        "reconciliation": _reconciliation_counts(reconciliation),
        "reasons": list(reasons),
    }


def _reconciliation_counts(reconciliation: object | None) -> dict[str, object] | None:
    if reconciliation is None:
        return None
    return {
        "matches": reconciliation.matches,  # type: ignore[attr-defined]
        "matched_device_count": len(reconciliation.matched_device_ids),  # type: ignore[attr-defined]
        "missing_device_count": len(reconciliation.missing_device_ids),  # type: ignore[attr-defined]
        "room_mismatch_device_count": len(  # type: ignore[attr-defined]
            reconciliation.room_mismatch_device_ids  # type: ignore[attr-defined]
        ),
        "unregistered_source_count": len(  # type: ignore[attr-defined]
            reconciliation.unregistered_source_ids  # type: ignore[attr-defined]
        ),
    }


def _season_settings_updated_at(home: dict[str, object]) -> str:
    updated_at = home.get("interseason_updated_at")
    if type(updated_at) is not int or updated_at < 0:
        return datetime.now().astimezone().isoformat(timespec="seconds")
    return (
        datetime.fromtimestamp(updated_at / 1000)
        .astimezone()
        .isoformat(timespec="seconds")
    )
