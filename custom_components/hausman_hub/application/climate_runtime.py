"""Orchestrate registry, state import, tablet projection, and typed actions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import time
from typing import Protocol

from ..domain.climate import (
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..domain.climate_comparison import ClimateComparisonSnapshot
from ..domain.climate_demand import ClimateDemandSnapshot
from ..domain.climate_equipment import ClimateEquipmentSnapshot
from ..domain.climate_ha_calls import ClimateHaCallPlanSnapshot, ClimateHaServiceCall
from ..domain.climate_isolation import ClimateIsolationSnapshot
from ..domain.climate_ownership import ClimateOwnershipReceipt
from ..domain.climate_observation import (
    ClimateDataStatus,
    ClimateObservationSnapshot,
    ClimateObservationViolation,
)
from ..domain.climate_policy import ClimatePolicySnapshot
from ..domain.climate_protection import (
    ClimateProtectionMemory,
    empty_climate_protection_memory,
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
from .climate_equipment import build_climate_equipment_snapshot
from .climate_ha_adapters import build_climate_ha_call_plan
from .climate_ha_observations import (
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
from .climate_registry import (
    ClimateRegistryViolation,
    reconcile_climate_registry,
    registry_from_payload,
    registry_to_payload,
)
from .climate_setup import (
    build_climate_contour_draft_setup,
    climate_draft_save_receipt,
    climate_setup_options,
    create_climate_contour_draft,
    current_climate_contour_setup,
    update_climate_profiles,
    update_climate_schedule,
    validate_climate_contour_draft,
)
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
    with_climate_temporary_temperature,
    without_climate_temporary_temperature,
)
from .contour_apply import (
    CONTOUR_APPLY_CONTRACT_VERSION,
    CONTOUR_APPLY_PREVIEW_CONTRACT_NAME,
    ClimateControlAction,
    ClimateControlContext,
    ContourApplyReceipt,
    ContourApplyStatus,
    ContourApplyViolation,
    _ContourApplyLedger,
    build_contour_apply_plan,
    contour_fingerprint,
    local_desired_state_changes,
    parse_contour_apply_request,
)
from .contour_override import (
    TemporaryTemperatureAction,
    TemporaryTemperatureViolation,
    parse_temporary_temperature_request,
)


class ClimateRuntimeUnavailable(RuntimeError):
    """The climate surface cannot provide a safe complete result."""


class ClimateSnapshotUnavailable(ClimateRuntimeUnavailable):
    """The public snapshot is safely absent because climate is not observable."""


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
        strict_ha_call_executor: ClimateStrictHaCallExecutor | None = None,
        ha_state_view: ClimateHaStateView | None = None,
        operation_id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
        local_now: Callable[[], datetime] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.configuration = configuration
        self._registry_store = registry_store
        self._contour_store = contour_store
        self._protection_store = protection_store
        self._strict_ha_call_executor = strict_ha_call_executor
        self._ha_state_view = ha_state_view
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._local_now = local_now or (lambda: datetime.now().astimezone())
        self._registry = ClimateRegistry()
        self._contours = ContourRegistry()
        self._protection_memory = empty_climate_protection_memory(updated_at=0)
        self._protection_restart_after: int | None = None
        self._weather_heating_lockout: bool | None = None
        self._lock = asyncio.Lock()
        self._contour_applications = _ContourApplyLedger(
            operation_id_factory=operation_id_factory,
            now_ms=self._now_ms,
        )
        self.last_error: str | None = None

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

    async def async_start(self) -> None:
        """Load local registry and best-effort initial read-only state."""

        async with self._lock:
            try:
                self._registry = await self._registry_store.async_load()
                self._contours = (
                    await self._contour_store.async_load()
                    if self._contour_store is not None
                    else ContourRegistry()
                )
                validate_contour_bindings(self._contours, self._registry)
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
                self.last_error = None
            except Exception as error:
                # Base HausmanHub remains available; climate endpoints fail closed and
                # an administrator can replace a damaged local registry.
                self.last_error = type(error).__name__

    async def async_public_snapshot(self) -> dict[str, object]:
        """Refresh and return the private-id-free tablet contract."""

        async with self._lock:
            if self.configuration.climate_bridge_mode is ClimateControlMode.MANAGED:
                observation = await self._async_native_climate_observation_unlocked()
                if observation.data_status is ClimateDataStatus.UNAVAILABLE:
                    raise ClimateSnapshotUnavailable("climate state is unavailable")
                return native_android_climate_snapshot(
                    self._registry,
                    observation,
                    contours=self._contours,
                    bridge_mode=self.configuration.climate_bridge_mode,
                    pending_room_ids=(),
                    local_now=self._local_now(),
                )
            raise ClimateSnapshotUnavailable("climate bridge is disabled")

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
            return create_climate_contour_draft(self._registry, snapshot, payload)

    async def async_climate_setup_options(self) -> dict[str, object]:
        """Return current safe choices for the local climate setup form."""

        async with self._lock:
            snapshot = await self._async_native_setup_snapshot_unlocked()
            return climate_setup_options(self._registry, snapshot)

    async def async_current_contour_setup(self) -> dict[str, object]:
        """Return saved editor values without persistence or commands."""

        async with self._lock:
            snapshot = await self._async_native_setup_snapshot_unlocked()
            return current_climate_contour_setup(
                self._registry,
                self._contours,
                snapshot,
            )

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

    async def async_apply_contour(self, payload: object) -> ContourApplyReceipt:
        """Idempotently apply three supported settings after explicit consent."""

        request_id, contour_id = parse_contour_apply_request(payload)
        async with self._lock:
            self._require_native_contour_apply_mode()
            return await self._async_apply_native_contour_unlocked(
                request_id,
                contour_id,
                context=ClimateControlContext(
                    action=ClimateControlAction.APPLY_SAVED_SETTINGS,
                ),
                desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
            )

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
    ) -> ContourApplyReceipt:
        """Apply one room temperature until the next saved schedule boundary."""

        request = parse_temporary_temperature_request(payload)
        if not isinstance(now, datetime):
            raise TemporaryTemperatureViolation(
                "temporary temperature needs local datetime"
            )
        async with self._lock:
            self._require_native_contour_apply_mode()
            contour = self._climate_contour()
            selected = contour.schedule.profile_at(hour=now.hour, minute=now.minute)
            if (
                contour.mode is not ContourMode.AUTOMATIC
                or not contour.schedule.enabled
                or contour.schedule.last_applied_profile is not selected
                or any(room.active_profile is not selected for room in contour.rooms)
            ):
                raise ContourApplyViolation(
                    "climate schedule is not ready for a temporary temperature"
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
                    ) is not None:
                        return await self._async_apply_native_contour_unlocked(
                            request.request_id,
                            CLIMATE_CONTOUR_ID,
                            context=context,
                            room_ids=room_scope,
                            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
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
                )
                is not None
            ):
                return await self._async_apply_native_contour_unlocked(
                    request.request_id,
                    CLIMATE_CONTOUR_ID,
                    context=context,
                    room_ids=room_scope,
                    desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
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
            return await self._async_apply_native_contour_unlocked(
                request.request_id,
                CLIMATE_CONTOUR_ID,
                context=context,
                room_ids=room_scope,
                desired_state_changes=desired_state_changes,
            )

    async def _async_apply_native_contour_unlocked(
        self,
        request_id: str,
        contour_id: str,
        *,
        context: ClimateControlContext,
        room_ids: tuple[str, ...] | None = None,
        desired_state_changes: ClimateDesiredStateChanges,
    ) -> ContourApplyReceipt:
        self._require_native_contour_apply_mode()
        contour = self._climate_contour()
        if contour.contour_id != contour_id:
            raise ContourApplyViolation("climate contour is not configured")
        fingerprint = contour_fingerprint(contour, room_ids=room_ids)
        prior = self._contour_applications.existing(
            request_id,
            fingerprint,
            context,
        )
        if prior is not None:
            return await self._async_reobserve_native_contour_application_unlocked(
                request_id,
                prior,
                contour,
                room_ids=room_ids,
            )

        observation = await self._async_native_climate_observation_unlocked()
        plan = build_contour_apply_plan(
            contour,
            self._registry,
            self.configuration.climate_bridge_mode,
            observation,
            room_ids=room_ids,
            desired_state_changes=desired_state_changes,
        )
        record = self._contour_applications.begin(request_id, plan, context)
        if not plan.native_plan.preflight_permitted or not plan.strict_calls:
            return record.receipt

        if self._strict_ha_call_executor is None:
            return self._contour_applications.update(
                request_id,
                status=ContourApplyStatus.UNAVAILABLE,
                accepted_count=0,
                confirmed_room_count=0,
                reasons=("command_result_unavailable",),
            ).receipt

        try:
            accepted_count = await self._strict_ha_call_executor.async_execute(
                plan.strict_calls
            )
        except Exception as error:
            self.last_error = type(error).__name__
            completed = _bounded_completed_count(
                getattr(error, "completed", 0),
                len(plan.strict_calls),
            )
            return self._contour_applications.update(
                request_id,
                status=(
                    ContourApplyStatus.PARTIAL
                    if completed
                    else ContourApplyStatus.UNAVAILABLE
                ),
                accepted_count=completed,
                confirmed_room_count=0,
                reasons=("command_result_unavailable",),
            ).receipt

        accepted_count = _bounded_completed_count(
            accepted_count,
            len(plan.strict_calls),
        )
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
        return await self._async_verify_native_contour_application_unlocked(
            request_id,
            plan,
            accepted_count,
        )

    async def _async_reobserve_native_contour_application_unlocked(
        self,
        request_id: str,
        prior,
        contour: ContourDefinition,
        *,
        room_ids: tuple[str, ...] | None,
    ) -> ContourApplyReceipt:
        try:
            observation = await self._async_native_climate_observation_unlocked()
        except ClimateRuntimeUnavailable:
            return prior.receipt
        if observation.data_status is ClimateDataStatus.UNAVAILABLE:
            return prior.receipt
        verified = build_contour_apply_plan(
            contour,
            self._registry,
            self.configuration.climate_bridge_mode,
            observation,
            room_ids=room_ids,
            desired_state_changes=prior.plan.desired_state_changes,
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
        for attempt in range(11):
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
                self._climate_contour(),
                self._registry,
                self.configuration.climate_bridge_mode,
                observation,
                room_ids=plan.target_room_ids,
                desired_state_changes=plan.desired_state_changes,
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
            if attempt < 10:
                await asyncio.sleep(0.2)
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
            observation = await self._async_native_climate_observation_unlocked()
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
            return build_climate_ha_call_plan(self._registry, isolation)

    async def async_run_climate_trial(
        self,
    ) -> ClimateTrialReceipt | None:
        """Run one gated internal-control check for the single trial room."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            trial_room_id = self._trial_room_id(contour)
            if contour is None or trial_room_id is None:
                return None
            observation = await self._async_native_climate_observation_unlocked()
            isolation = build_isolated_climate_policy_snapshot(contour, observation)
            comparison = build_climate_comparison_snapshot(isolation, observation)
            call_plan = build_climate_ha_call_plan(self._registry, isolation)
            decision = plan_climate_trial(
                trial_room_id,
                bridge_mode=self.configuration.climate_bridge_mode,
                contour_mode=contour.mode,
                isolation=isolation,
                comparison=comparison,
                call_plan=call_plan,
                registry=self._registry,
            )
            return await self._async_apply_trial_decision(decision)

    async def async_run_climate_managed(
        self,
    ) -> tuple[ClimateTrialReceipt, ...]:
        """Run one gated control check for every HausmanHub-managed room."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            if contour is None:
                return ()
            managed_room_ids = self._managed_room_ids(contour)
            if not managed_room_ids:
                return ()
            observation = await self._async_native_climate_observation_unlocked()
            isolation = build_isolated_climate_policy_snapshot(contour, observation)
            comparison = build_climate_comparison_snapshot(isolation, observation)
            call_plan = build_climate_ha_call_plan(self._registry, isolation)
            receipts: list[ClimateTrialReceipt] = []
            for room_id in managed_room_ids:
                decision = plan_climate_trial(
                    room_id,
                    bridge_mode=self.configuration.climate_bridge_mode,
                    contour_mode=contour.mode,
                    isolation=isolation,
                    comparison=comparison,
                    call_plan=call_plan,
                    registry=self._registry,
                    required_scope=ClimateControlScope.MANAGED,
                    allowed_bridge_modes=frozenset(
                        {ClimateControlMode.MANAGED}
                    ),
                )
                receipts.append(await self._async_apply_trial_decision(decision))
            return tuple(receipts)

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
        trial_rooms = {
            room.room_id
            for room in contour.rooms
            if any(
                device.control_scope is ClimateControlScope.CANARY
                and device.kind not in _PASSIVE_KINDS
                for device in self._registry.devices
                if device.device_id in set(room.device_ids)
            )
        }
        if len(trial_rooms) != 1:
            return None
        return next(iter(trial_rooms))

    def _managed_room_ids(self, contour) -> tuple[str, ...]:
        trial_room_id = self._trial_room_id(contour)
        result: list[str] = []
        for room in contour.rooms:
            if room.room_id == trial_room_id:
                continue
            actuators = tuple(
                device
                for device in self._registry.devices
                if device.device_id in set(room.device_ids)
                and device.kind not in _PASSIVE_KINDS
            )
            if not actuators:
                continue
            if all(
                device.control_scope is ClimateControlScope.MANAGED
                and device.endpoint(ClimateEndpointRole.CONTROL) is not None
                for device in actuators
            ):
                result.append(room.room_id)
        return tuple(result)

    async def _async_apply_trial_decision(
        self,
        decision,
    ) -> ClimateTrialReceipt:
        if not decision.permitted:
            return climate_trial_skip_receipt(decision)
        if self._strict_ha_call_executor is None:
            return climate_trial_failure_receipt(
                decision,
                reason=ClimateTrialReason.EXECUTOR_UNAVAILABLE,
                executed_count=0,
            )
        try:
            executed = await self._strict_ha_call_executor.async_execute(
                decision.calls
            )
        except Exception as error:
            self.last_error = type(error).__name__
            return climate_trial_failure_receipt(
                decision,
                reason=ClimateTrialReason.SERVICE_ERROR,
                executed_count=getattr(error, "completed", 0),
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
    ) -> ClimateObservationSnapshot:
        """Read one observation without saving evidence or creating commands."""

        observed_at = self._safe_now()
        observation = self._native_ha_observation(observed_at)
        if observation is None:
            # Without a native state view the internal pipeline must not
            # observe at all: the external bridge is never a fallback.
            observation = unavailable_climate_observation_snapshot(
                self._registry,
                observed_at=observed_at,
            )
        try:
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

    def _native_ha_observation(
        self,
        observed_at: int,
    ) -> ClimateObservationSnapshot | None:
        """Build the native observation, or None when observation is absent.

        A present state view makes the native Home Assistant observation the
        only input of the internal pipeline; facts are never mixed with the
        external module. A build failure fails closed to an unavailable
        observation instead of falling back to the bridge.
        """

        if (
            self._ha_state_view is None
            or self.configuration.climate_bridge_mode is ClimateControlMode.DISABLED
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
            )
            self._weather_heating_lockout = observation.home.weather_heating_lockout
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
            self.last_error = None
            return registry_to_payload(registry)

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

    async def async_climate_mode_status(self) -> dict[str, object]:
        """Report the saved climate control mode and contour configuration."""

        async with self._lock:
            contour = self._contours.contour(CLIMATE_CONTOUR_ID)
            return {
                "mode": self.configuration.climate_bridge_mode.value,
                "contour_configured": contour is not None and bool(contour.rooms),
            }

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
        allowed_domains: frozenset[str],
    ) -> list[dict[str, object]]:
        """List bounded local candidates for one signal binding selection."""

        view = self._ha_state_view
        catalog = getattr(view, "signal_entity_catalog", None)
        if view is None or catalog is None:
            raise ClimateRuntimeUnavailable(
                "the local signal entity catalog is unavailable"
            )
        entries = catalog(allowed_domains).entries
        return [
            {
                "entity_id": entry.entity_id,
                "name": entry.friendly_name or entry.entity_id,
                "available": entry.available,
            }
            for entry in entries
        ]

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

def _bounded_completed_count(value: object, maximum: int) -> int:
    if type(value) is int and 0 <= value <= maximum:
        return value
    return 0


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
