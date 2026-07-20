"""Orchestrate registry, state import, tablet projection, and typed actions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import time
from typing import Protocol

from ..domain.climate import ClimateRegistry
from ..domain.climate_bridge import ClimateBridgeMode
from ..domain.configuration import SafeConfiguration
from ..domain.contours import ContourDefinition, ContourMode, ContourRegistry
from ..domain.native_climate import NativeClimatePolicy, preview_native_climate
from .android_climate import admin_climate_import_snapshot, android_climate_snapshot
from .climate_canary_preflight import climate_canary_preflight
from .climate_commands import (
    ClimateCommandPlan,
    ClimateCommandRejected,
    ClimateCommandViolation,
    plan_climate_command,
)
from .climate_evidence import (
    ClimateShadowEvidence,
    SHADOW_EVIDENCE_REQUIRED_ACTIONS,
    candidate_room_from_payload,
    public_intent_context,
)
from .climate_import import ClimateImportSnapshot
from .climate_operations import _ClimateOperationLedger, ClimateOperationReceipt
from .climate_registry import (
    reconcile_climate_registry,
    registry_from_payload,
    registry_to_payload,
)
from .climate_setup import (
    climate_setup_options,
    create_climate_contour_draft,
    validate_climate_contour_draft,
)
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
    ContourApplyReceipt,
    ContourApplyStatus,
    ContourApplyViolation,
    _ContourApplyLedger,
    build_contour_apply_plan,
    confirmed_contour_room_count,
    contour_fingerprint,
    parse_contour_apply_request,
)
from .contour_override import (
    TemporaryTemperatureAction,
    TemporaryTemperatureViolation,
    parse_temporary_temperature_request,
)


class ClimateRuntimeUnavailable(RuntimeError):
    """The climate surface cannot provide a safe complete result."""


class ClimateRegistryStorage(Protocol):
    """Minimal versioned registry persistence boundary."""

    async def async_load(self) -> ClimateRegistry:
        """Load a complete validated registry."""

    async def async_save(self, registry: ClimateRegistry) -> None:
        """Atomically save one complete validated registry."""


class ClimateBridgeClient(Protocol):
    """Minimal fixed-path Climate API boundary."""

    async def async_fetch_state(self) -> ClimateImportSnapshot:
        """Fetch and validate one state snapshot."""

    async def async_execute(self, plan: ClimateCommandPlan) -> object:
        """Post only a plan explicitly marked executable."""


class ClimateEvidenceStorage(Protocol):
    """Minimal bounded shadow-evidence persistence boundary."""

    async def async_load(self) -> ClimateShadowEvidence | None:
        """Load a validated evidence window when one exists."""

    async def async_save(self, evidence: ClimateShadowEvidence) -> None:
        """Atomically save one bounded evidence window."""


class ContourStorage(Protocol):
    """Minimal versioned persistence boundary for HASC contour definitions."""

    async def async_load(self) -> ContourRegistry:
        """Load a complete validated contour registry."""

    async def async_save(self, registry: ContourRegistry) -> None:
        """Atomically save one complete contour registry."""


class ClimateRuntime:
    """One loaded HASC entry's climate facade and rollout state."""

    def __init__(
        self,
        *,
        entry_id: str,
        configuration: SafeConfiguration,
        registry_store: ClimateRegistryStorage,
        bridge_client: ClimateBridgeClient | None,
        evidence_store: ClimateEvidenceStorage | None = None,
        contour_store: ContourStorage | None = None,
        operation_id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
        local_now: Callable[[], datetime] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.configuration = configuration
        self._registry_store = registry_store
        self._bridge_client = bridge_client
        self._evidence_store = evidence_store
        self._contour_store = contour_store
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._local_now = local_now or (lambda: datetime.now().astimezone())
        self._registry = ClimateRegistry()
        self._snapshot: ClimateImportSnapshot | None = None
        self._evidence: ClimateShadowEvidence | None = None
        self._contours = ContourRegistry()
        self._lock = asyncio.Lock()
        self._operations = _ClimateOperationLedger(
            operation_id_factory=operation_id_factory,
            now_ms=self._now_ms,
        )
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
        if self.configuration.climate_bridge_mode is ClimateBridgeMode.DISABLED:
            return "disabled"
        if self._snapshot is None:
            return "not_refreshed"
        return "fresh" if self._snapshot.runtime_fresh else "stale"

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
                loaded_evidence = (
                    await self._evidence_store.async_load()
                    if self._evidence_store is not None
                    else None
                )
                self._evidence = loaded_evidence or ClimateShadowEvidence.for_registry(
                    self._registry,
                    now_ms=now,
                )
                changed = loaded_evidence is None or self._evidence.ensure_registry(
                    self._registry,
                    now_ms=now,
                )
                if changed:
                    await self._async_save_evidence()
                if self.configuration.climate_bridge_mode is not ClimateBridgeMode.DISABLED:
                    await self._async_refresh_unlocked()
                else:
                    self.last_error = None
            except Exception as error:
                # Base HASC remains available; climate endpoints fail closed and
                # an administrator can replace a damaged local registry.
                self.last_error = type(error).__name__

    async def async_public_snapshot(self) -> dict[str, object]:
        """Refresh and return the private-id-free tablet contract."""

        async with self._lock:
            snapshot = await self._async_refresh_unlocked()
            candidate_ready = (
                self.configuration.climate_bridge_mode is ClimateBridgeMode.CANARY
                and self._candidate_is_ready(
                    snapshot,
                    self.configuration.climate_canary_room_id,
                )
            )
            return android_climate_snapshot(
                self._registry,
                snapshot,
                contours=self._contours,
                bridge_mode=self.configuration.climate_bridge_mode,
                canary_room_id=self.configuration.climate_canary_room_id,
                candidate_ready=candidate_ready,
                pending_room_ids=tuple(
                    room.room_id
                    for room in self._registry.rooms
                    if self._operations.room_has_pending(room.room_id)
                ),
                local_now=self._local_now(),
            )

    async def async_admin_import_snapshot(self) -> dict[str, object]:
        """Refresh and return private discovery data for a local admin."""

        async with self._lock:
            snapshot = await self._async_refresh_unlocked()
            return admin_climate_import_snapshot(self._registry, snapshot)

    async def async_create_contour_draft(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Create an unsaved draft after one read-only discovery refresh."""

        async with self._lock:
            snapshot = await self._async_refresh_unlocked(
                persist_evidence=False,
                record_evidence=False,
            )
            return create_climate_contour_draft(self._registry, snapshot, payload)

    async def async_climate_setup_options(self) -> dict[str, object]:
        """Return current safe choices for the local climate setup form."""

        async with self._lock:
            snapshot = await self._async_refresh_unlocked(
                persist_evidence=False,
                record_evidence=False,
            )
            return climate_setup_options(self._registry, snapshot)

    async def async_validate_contour_draft(
        self,
        payload: object,
    ) -> dict[str, object]:
        """Validate one draft without persistence, commands, or shadow evidence."""

        async with self._lock:
            snapshot = await self._async_refresh_unlocked(
                persist_evidence=False,
                record_evidence=False,
            )
            return validate_climate_contour_draft(
                self._registry,
                snapshot,
                payload,
            )

    async def async_registry_import_snapshot(self) -> ClimateImportSnapshot:
        """Refresh one typed read-only snapshot for the local options wizard."""

        async with self._lock:
            return await self._async_refresh_unlocked()

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
            snapshot = self._snapshot
            if self.configuration.climate_bridge_mode is not ClimateBridgeMode.DISABLED:
                try:
                    snapshot = await self._async_refresh_unlocked(
                        persist_evidence=False
                    )
                except ClimateRuntimeUnavailable:
                    snapshot = None
            return contour_snapshot(
                self._contours,
                self._registry,
                snapshot,
                settings_apply_enabled=(
                    self.configuration.climate_bridge_mode
                    is ClimateBridgeMode.MANAGED
                ),
                local_now=self._local_now(),
            )

    async def async_contour_apply_preview(self) -> dict[str, object]:
        """Preview supported saved-contour changes without posting commands."""

        async with self._lock:
            self._require_contour_apply_client()
            contour = self._climate_contour()
            snapshot = await self._async_refresh_unlocked(persist_evidence=False)
            return build_contour_apply_plan(
                contour,
                self._registry,
                snapshot,
            ).preview_payload()

    async def async_apply_contour(self, payload: object) -> ContourApplyReceipt:
        """Idempotently apply three supported settings after explicit consent."""

        request_id, contour_id = parse_contour_apply_request(payload)
        async with self._lock:
            return await self._async_apply_contour_unlocked(request_id, contour_id)

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
                is not ClimateBridgeMode.MANAGED
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
            await self._contour_store.async_save(updated)
            self._contours = updated
            fingerprint = contour_fingerprint(self._climate_contour())
            request_id = (
                f"schedule-{now:%Y%m%d}-{selected.value}-{fingerprint[:12]}"
            )
            return await self._async_apply_contour_unlocked(
                request_id,
                CLIMATE_CONTOUR_ID,
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
            self._require_contour_apply_client()
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
            room_scope = (request.room_id,)
            fingerprint = contour_fingerprint(
                updated_contour,
                room_ids=room_scope,
            )
            if (
                self._contour_applications.existing(
                    request.request_id,
                    fingerprint,
                )
                is not None
            ):
                return await self._async_apply_contour_unlocked(
                    request.request_id,
                    CLIMATE_CONTOUR_ID,
                    room_ids=room_scope,
                )
            snapshot = await self._async_refresh_unlocked(persist_evidence=False)
            build_contour_apply_plan(
                updated_contour,
                self._registry,
                snapshot,
                room_ids=room_scope,
            )
            # Reserve the desired temporary state in durable storage before the
            # first POST. A lost response therefore cannot trigger an automatic
            # retry; only another explicit user request may try again.
            await self._contour_store.async_save(updated)
            self._contours = updated
            return await self._async_apply_contour_unlocked(
                request.request_id,
                CLIMATE_CONTOUR_ID,
                room_ids=room_scope,
            )

    async def _async_apply_contour_unlocked(
        self,
        request_id: str,
        contour_id: str,
        *,
        room_ids: tuple[str, ...] | None = None,
    ) -> ContourApplyReceipt:
        """Apply one already validated request while the runtime lock is held."""

        client = self._require_contour_apply_client()
        contour = self._climate_contour()
        if contour.contour_id != contour_id:
            raise ContourApplyViolation("climate contour is not configured")
        fingerprint = contour_fingerprint(contour, room_ids=room_ids)
        prior = self._contour_applications.existing(request_id, fingerprint)
        if prior is not None:
            if prior.receipt.status is ContourApplyStatus.PENDING:
                try:
                    snapshot = await self._async_refresh_unlocked(
                        persist_evidence=False
                    )
                except ClimateRuntimeUnavailable:
                    return prior.receipt
                confirmed = confirmed_contour_room_count(prior.plan, snapshot)
                prior = self._contour_applications.update(
                    request_id,
                    status=(
                        ContourApplyStatus.CONFIRMED
                        if confirmed == len(prior.plan.rooms)
                        else ContourApplyStatus.PENDING
                    ),
                    accepted_count=prior.receipt.accepted_count,
                    confirmed_room_count=confirmed,
                    reasons=(
                        ()
                        if confirmed == len(prior.plan.rooms)
                        else ("state_not_confirmed",)
                    ),
                )
            return prior.receipt

        snapshot = await self._async_refresh_unlocked(persist_evidence=False)
        plan = build_contour_apply_plan(
            contour,
            self._registry,
            snapshot,
            room_ids=room_ids,
        )
        record = self._contour_applications.begin(request_id, plan)
        if not plan.commands:
            return record.receipt

        accepted_count = 0
        for command in plan.commands:
            try:
                await client.async_execute(command)
            except ClimateCommandRejected:
                return self._contour_applications.update(
                    request_id,
                    status=(
                        ContourApplyStatus.PARTIAL
                        if accepted_count
                        else ContourApplyStatus.REJECTED
                    ),
                    accepted_count=accepted_count,
                    confirmed_room_count=0,
                    reasons=("engine_rejected",),
                ).receipt
            except Exception:
                # The current POST may have reached the engine. Reserve the
                # request forever in this runtime and never resubmit it.
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
            accepted_count += 1

        try:
            observed = await self._async_refresh_unlocked(persist_evidence=False)
        except ClimateRuntimeUnavailable:
            return self._contour_applications.update(
                request_id,
                status=ContourApplyStatus.PENDING,
                accepted_count=accepted_count,
                confirmed_room_count=0,
                reasons=("verification_unavailable",),
            ).receipt
        confirmed = confirmed_contour_room_count(plan, observed)
        return self._contour_applications.update(
            request_id,
            status=(
                ContourApplyStatus.CONFIRMED
                if confirmed == len(plan.rooms)
                else ContourApplyStatus.PENDING
            ),
            accepted_count=accepted_count,
            confirmed_room_count=confirmed,
            reasons=(
                ()
                if confirmed == len(plan.rooms)
                else ("state_not_confirmed",)
            ),
        ).receipt

    async def async_native_climate_preview(
        self,
        policy: NativeClimatePolicy,
    ) -> dict[str, object]:
        """Calculate HASC's one-room decision without enabling any command."""

        async with self._lock:
            snapshot = self._snapshot
            if self.configuration.climate_bridge_mode is not ClimateBridgeMode.DISABLED:
                try:
                    snapshot = await self._async_refresh_unlocked(
                        persist_evidence=False
                    )
                except ClimateRuntimeUnavailable:
                    snapshot = None
            decision = preview_native_climate(policy, self._registry, snapshot)
            return decision.as_payload()

    async def async_readiness(self) -> dict[str, object]:
        """Return redacted bridge and registry readiness to a local admin."""

        async with self._lock:
            mode = self.configuration.climate_bridge_mode
            if mode is ClimateBridgeMode.DISABLED:
                return self._readiness_payload(
                    status="disabled",
                    fresh=False,
                    reconciliation=None,
                    reasons=("bridge_disabled",),
                )
            try:
                snapshot = await self._async_refresh_unlocked()
            except ClimateRuntimeUnavailable:
                return self._readiness_payload(
                    status="unavailable",
                    fresh=False,
                    reconciliation=None,
                    reasons=("climate_state_unavailable",),
                )
            reconciliation = reconcile_climate_registry(self._registry, snapshot)
            reasons = _readiness_reasons(self._registry, snapshot, reconciliation.matches)
            return self._readiness_payload(
                status="ready" if not reasons else "not_ready",
                fresh=snapshot.runtime_fresh,
                reconciliation=reconciliation,
                reasons=reasons,
            )

    async def async_shadow_evidence(self, payload: object) -> dict[str, object]:
        """Return one redacted candidate-room evidence result to a local admin."""

        candidate_room_id = candidate_room_from_payload(payload)
        async with self._lock:
            snapshot = self._snapshot
            if self.configuration.climate_bridge_mode is not ClimateBridgeMode.DISABLED:
                try:
                    snapshot = await self._async_refresh_unlocked(
                        persist_evidence=False
                    )
                except ClimateRuntimeUnavailable:
                    snapshot = None
            evidence = self._require_evidence()
            result = evidence.as_payload(
                registry=self._registry,
                snapshot=snapshot,
                bridge_mode=self.configuration.climate_bridge_mode,
                candidate_room_id=candidate_room_id,
                now_ms=self._safe_now(),
            )
            await self._async_save_evidence()
            return result

    async def async_canary_preflight(self, payload: object) -> dict[str, object]:
        """Combine one room's rollout checks without enabling or posting anything."""

        candidate_room_id = candidate_room_from_payload(payload)
        async with self._lock:
            snapshot = self._snapshot
            if self.configuration.climate_bridge_mode is not ClimateBridgeMode.DISABLED:
                try:
                    snapshot = await self._async_refresh_unlocked(
                        persist_evidence=False
                    )
                except ClimateRuntimeUnavailable:
                    snapshot = None
            checked_at = self._safe_now()
            evidence = self._require_evidence()
            evidence_payload = evidence.as_payload(
                registry=self._registry,
                snapshot=snapshot,
                bridge_mode=self.configuration.climate_bridge_mode,
                candidate_room_id=candidate_room_id,
                now_ms=checked_at,
            )
            await self._async_save_evidence()
            return climate_canary_preflight(
                self._registry,
                snapshot,
                evidence_payload,
                bridge_mode=self.configuration.climate_bridge_mode,
                room_id=candidate_room_id,
                pending_operation=self._operations.room_has_pending(
                    candidate_room_id
                ),
                checked_at=checked_at,
            )

    async def async_preview_registry(self, payload: object) -> dict[str, object]:
        """Validate and reconcile an unsaved registry without mutating storage."""

        async with self._lock:
            registry = registry_from_payload(payload)
            mode = self.configuration.climate_bridge_mode
            if mode is ClimateBridgeMode.DISABLED:
                return _registry_preview_payload(
                    registry,
                    status="validated_offline",
                    save_allowed=True,
                    fresh=False,
                    reconciliation=None,
                    reasons=("bridge_disabled",),
                )
            try:
                snapshot = await self._async_refresh_unlocked()
            except ClimateRuntimeUnavailable:
                return _registry_preview_payload(
                    registry,
                    status="unavailable",
                    save_allowed=mode is ClimateBridgeMode.SHADOW,
                    fresh=False,
                    reconciliation=None,
                    reasons=("climate_state_unavailable",),
                )
            reconciliation = reconcile_climate_registry(registry, snapshot)
            reasons = _readiness_reasons(registry, snapshot, reconciliation.matches)
            if mode is ClimateBridgeMode.CANARY:
                reasons = (*reasons, "canary_registry_locked")
            return _registry_preview_payload(
                registry,
                status="ready" if not reasons else "not_ready",
                save_allowed=mode is not ClimateBridgeMode.CANARY,
                fresh=snapshot.runtime_fresh,
                reconciliation=reconciliation,
                reasons=tuple(dict.fromkeys(reasons)),
            )

    async def async_replace_registry(self, payload: object) -> dict[str, object]:
        """Validate and atomically replace the registry outside active canary."""

        async with self._lock:
            if self.configuration.climate_bridge_mode is ClimateBridgeMode.CANARY:
                raise ClimateRuntimeUnavailable(
                    "climate registry changes require disabled or shadow mode"
                )
            registry = registry_from_payload(payload)
            validate_contour_bindings(self._contours, registry)
            await self._registry_store.async_save(registry)
            self._registry = registry
            evidence = self._require_evidence()
            evidence.ensure_registry(registry, now_ms=self._safe_now())
            await self._async_save_evidence()
            self.last_error = None
            return registry_to_payload(registry)

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

    async def async_replace_contour_setup(
        self,
        registry_payload: object,
        contour_payload: object,
    ) -> dict[str, object]:
        """Save selected devices and contours as one rollback-protected setup."""

        async with self._lock:
            if self.configuration.climate_bridge_mode is ClimateBridgeMode.CANARY:
                raise ClimateRuntimeUnavailable(
                    "contour setup changes require disabled or shadow mode"
                )
            if self._contour_store is None:
                raise ClimateRuntimeUnavailable("contour storage is unavailable")
            registry = registry_from_payload(registry_payload)
            contours = contour_registry_from_payload(contour_payload)
            validate_contour_bindings(contours, registry)
            previous_registry = self._registry
            previous_contours = self._contours
            previous_evidence = self._evidence
            next_evidence = ClimateShadowEvidence.for_registry(
                registry,
                now_ms=self._safe_now(),
            )
            registry_saved = False
            contours_saved = False
            try:
                await self._registry_store.async_save(registry)
                registry_saved = True
                await self._contour_store.async_save(contours)
                contours_saved = True
                if self._evidence_store is not None:
                    await self._evidence_store.async_save(next_evidence)
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
                    self._evidence = previous_evidence
                else:
                    # If either backward write fails, keep the already-saved
                    # new pair together. A registry rollback can fail before
                    # contours are touched; a contour rollback failure is
                    # compensated by restoring the new registry.
                    if not registry_restored and not contours_saved:
                        try:
                            await self._contour_store.async_save(contours)
                        except Exception as failure:
                            self._registry = registry
                            self._contours = previous_contours
                            self._evidence = previous_evidence
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
                            self._evidence = previous_evidence
                            self.last_error = type(failure).__name__
                            raise ClimateRuntimeUnavailable(
                                "contour setup storage is inconsistent"
                            ) from failure
                    self._registry = registry
                    self._contours = contours
                    self._evidence = next_evidence
                self.last_error = type(error).__name__
                if rollback_error is not None:
                    raise ClimateRuntimeUnavailable(
                        "contour setup rollback failed"
                    ) from rollback_error
                raise
            self._registry = registry
            self._contours = contours
            self._evidence = next_evidence
            self.last_error = None
            return {
                "registry": registry_to_payload(registry),
                "contours": contour_registry_to_payload(contours),
            }

    async def async_action(self, payload: object) -> ClimateOperationReceipt:
        """Idempotently validate and optionally post one typed climate action."""

        async with self._lock:
            # Preserve complete rollback semantics: a disabled bridge exposes
            # no action-validation oracle and performs no state I/O.
            self._require_client()
            request_id, intent, canonical = self._operations.parse_action(payload)
            duplicate = self._operations.duplicate(request_id, canonical)
            if duplicate is not None:
                return duplicate
            snapshot = await self._async_refresh_unlocked()
            try:
                plan = plan_climate_command(
                    intent,
                    self._registry,
                    snapshot,
                    bridge_mode=self.configuration.climate_bridge_mode,
                    canary_room_id=self.configuration.climate_canary_room_id,
                )
            except ClimateCommandViolation as violation:
                if self.configuration.climate_bridge_mode is ClimateBridgeMode.SHADOW:
                    room_id, action = public_intent_context(intent, self._registry)
                    try:
                        await self._async_record_shadow_intent(
                            category="rejected",
                            room_id=room_id,
                            action=action,
                        )
                    except Exception as error:
                        # Keep the public validation result stable while making
                        # the evidence persistence fault visible to diagnostics.
                        self.last_error = type(error).__name__
                        raise violation from error
                raise
            if self.configuration.climate_bridge_mode is ClimateBridgeMode.SHADOW:
                await self._async_record_shadow_intent(
                    category="translated",
                    room_id=plan.room_id,
                    action=plan.action,
                )
            if plan.execute:
                if not self._candidate_is_ready(snapshot, plan.room_id):
                    raise ClimateCommandViolation("shadow evidence is not ready")
                if plan.action not in SHADOW_EVIDENCE_REQUIRED_ACTIONS:
                    raise ClimateCommandViolation(
                        "action is outside the evidence-qualified canary scope"
                    )
                self._operations.ensure_submission_available(plan.room_id)
                # Reserve the idempotency key before the POST. If transport
                # becomes ambiguous, a retry returns this pending receipt and
                # cannot submit a second physical command.
                receipt = self._operations.record(
                    request_id=request_id,
                    canonical_intent=canonical,
                    intent=intent,
                    plan=plan,
                )
                client = self._require_client()
                try:
                    await client.async_execute(plan)
                except ClimateCommandRejected:
                    return self._operations.reject(receipt.operation_id)
                return receipt
            return self._operations.record(
                request_id=request_id,
                canonical_intent=canonical,
                intent=intent,
                plan=plan,
            )

    async def async_operation(self, payload: object) -> ClimateOperationReceipt:
        """Return one bounded receipt and refresh only a pending canary result."""

        async with self._lock:
            snapshot = None
            if self._operations.pending(payload):
                try:
                    snapshot = await self._async_refresh_unlocked()
                except ClimateRuntimeUnavailable:
                    # A transient read failure must not rewrite an accepted
                    # command into a fabricated physical result.
                    snapshot = None
            return self._operations.lookup(payload, snapshot)

    async def _async_refresh_unlocked(
        self,
        *,
        persist_evidence: bool = True,
        record_evidence: bool = True,
    ) -> ClimateImportSnapshot:
        client = self._require_client()
        try:
            snapshot = await client.async_fetch_state()
        except Exception as error:
            self.last_error = type(error).__name__
            raise ClimateRuntimeUnavailable("climate state is unavailable") from error
        self._snapshot = snapshot
        if (
            self.configuration.climate_bridge_mode is ClimateBridgeMode.SHADOW
            and record_evidence
        ):
            try:
                evidence = self._require_evidence()
                changed = evidence.record_observation(
                    self._registry,
                    snapshot,
                    now_ms=self._safe_now(),
                )
                if changed and persist_evidence:
                    await self._async_save_evidence()
            except Exception as error:
                self.last_error = type(error).__name__
                raise ClimateRuntimeUnavailable(
                    "climate shadow evidence is unavailable"
                ) from error
        self.last_error = None
        return snapshot

    async def _async_record_shadow_intent(
        self,
        *,
        category: str,
        room_id: str | None,
        action: str | None,
    ) -> None:
        evidence = self._require_evidence()
        evidence.record_intent(
            category=category,
            room_id=room_id,
            action=action,
            now_ms=self._safe_now(),
        )
        await self._async_save_evidence()

    async def _async_save_evidence(self) -> None:
        if self._evidence_store is not None:
            await self._evidence_store.async_save(self._require_evidence())

    def _candidate_is_ready(
        self,
        snapshot: ClimateImportSnapshot,
        room_id: str | None,
    ) -> bool:
        if room_id is None:
            return False
        result = self._require_evidence().as_payload(
            registry=self._registry,
            snapshot=snapshot,
            bridge_mode=self.configuration.climate_bridge_mode,
            candidate_room_id=room_id,
            now_ms=self._safe_now(),
        )
        candidate = result.get("candidate")
        return isinstance(candidate, dict) and candidate.get("ready") is True

    def _require_evidence(self) -> ClimateShadowEvidence:
        if self._evidence is None:
            raise ClimateRuntimeUnavailable("climate shadow evidence is unavailable")
        return self._evidence

    def _safe_now(self) -> int:
        value = self._now_ms()
        if type(value) is not int or value < 0:
            raise RuntimeError("climate runtime clock returned an unsafe timestamp")
        return value

    def _require_client(self) -> ClimateBridgeClient:
        if (
            self.configuration.climate_bridge_mode is ClimateBridgeMode.DISABLED
            or self._bridge_client is None
        ):
            raise ClimateRuntimeUnavailable("climate bridge is disabled")
        return self._bridge_client

    def _require_contour_apply_client(self) -> ClimateBridgeClient:
        """Keep contour application separate from the legacy canary path."""

        if self.configuration.climate_bridge_mode is not ClimateBridgeMode.MANAGED:
            raise ClimateRuntimeUnavailable(
                "contour settings require the normal existing-engine connection"
            )
        return self._require_client()

    def _climate_contour(self) -> ContourDefinition:
        contour = self._contours.contour(CLIMATE_CONTOUR_ID)
        if contour is None:
            raise ContourApplyViolation("climate contour is not configured")
        return contour

    def _readiness_payload(
        self,
        *,
        status: str,
        fresh: bool,
        reconciliation: object | None,
        reasons: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "contract": {
                "name": "hausman-hasc-climate-readiness",
                "version": 1,
            },
            "bridge_mode": self.configuration.climate_bridge_mode.value,
            "status": status,
            "ready": status == "ready",
            "fresh": fresh,
            "registry": {
                "room_count": self.room_count,
                "device_count": self.device_count,
            },
            "reconciliation": _reconciliation_counts(reconciliation),
            "reasons": list(reasons),
        }


def _readiness_reasons(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    matches: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not snapshot.runtime_fresh:
        reasons.append("state_stale")
    if not registry.rooms:
        reasons.append("registry_has_no_rooms")
    if not registry.devices:
        reasons.append("registry_has_no_devices")
    if not matches:
        reasons.append("registry_mismatch")
    return tuple(reasons)


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
            "name": "hausman-hasc-climate-registry-preview",
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
