"""Orchestrate registry, state import, tablet projection, and typed actions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Protocol

from ..domain.climate import ClimateRegistry
from ..domain.climate_bridge import ClimateBridgeMode
from ..domain.configuration import SafeConfiguration
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
        operation_id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.configuration = configuration
        self._registry_store = registry_store
        self._bridge_client = bridge_client
        self._evidence_store = evidence_store
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._registry = ClimateRegistry()
        self._snapshot: ClimateImportSnapshot | None = None
        self._evidence: ClimateShadowEvidence | None = None
        self._lock = asyncio.Lock()
        self._operations = _ClimateOperationLedger(
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
                bridge_mode=self.configuration.climate_bridge_mode,
                canary_room_id=self.configuration.climate_canary_room_id,
                candidate_ready=candidate_ready,
                pending_room_ids=tuple(
                    room.room_id
                    for room in self._registry.rooms
                    if self._operations.room_has_pending(room.room_id)
                ),
            )

    async def async_admin_import_snapshot(self) -> dict[str, object]:
        """Refresh and return private discovery data for a local admin."""

        async with self._lock:
            snapshot = await self._async_refresh_unlocked()
            return admin_climate_import_snapshot(self._registry, snapshot)

    async def async_registry_import_snapshot(self) -> ClimateImportSnapshot:
        """Refresh one typed read-only snapshot for the local options wizard."""

        async with self._lock:
            return await self._async_refresh_unlocked()

    async def async_registry_payload(self) -> dict[str, object]:
        """Return the exact private registry shape to a local admin."""

        async with self._lock:
            return registry_to_payload(self._registry)

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
            await self._registry_store.async_save(registry)
            self._registry = registry
            evidence = self._require_evidence()
            evidence.ensure_registry(registry, now_ms=self._safe_now())
            await self._async_save_evidence()
            self.last_error = None
            return registry_to_payload(registry)

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
    ) -> ClimateImportSnapshot:
        client = self._require_client()
        try:
            snapshot = await client.async_fetch_state()
        except Exception as error:
            self.last_error = type(error).__name__
            raise ClimateRuntimeUnavailable("climate state is unavailable") from error
        self._snapshot = snapshot
        if self.configuration.climate_bridge_mode is ClimateBridgeMode.SHADOW:
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
