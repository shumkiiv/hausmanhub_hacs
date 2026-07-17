"""Orchestrate registry, state import, tablet projection, and typed actions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from ..domain.climate import ClimateRegistry
from ..domain.climate_bridge import ClimateBridgeMode
from ..domain.configuration import SafeConfiguration
from .android_climate import admin_climate_import_snapshot, android_climate_snapshot
from .climate_commands import (
    ClimateCommandPlan,
    ClimateCommandRejected,
    plan_climate_command,
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


class ClimateRuntime:
    """One loaded HASC entry's climate facade and rollout state."""

    def __init__(
        self,
        *,
        entry_id: str,
        configuration: SafeConfiguration,
        registry_store: ClimateRegistryStorage,
        bridge_client: ClimateBridgeClient | None,
        operation_id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.configuration = configuration
        self._registry_store = registry_store
        self._bridge_client = bridge_client
        self._registry = ClimateRegistry()
        self._snapshot: ClimateImportSnapshot | None = None
        self._lock = asyncio.Lock()
        self._operations = _ClimateOperationLedger(
            operation_id_factory=operation_id_factory,
            now_ms=now_ms,
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
            enabled = (
                self.configuration.climate_bridge_mode is ClimateBridgeMode.CANARY
                and snapshot.runtime_fresh
            )
            return android_climate_snapshot(
                self._registry,
                snapshot,
                commands_enabled=enabled,
            )

    async def async_admin_import_snapshot(self) -> dict[str, object]:
        """Refresh and return private discovery data for a local admin."""

        async with self._lock:
            snapshot = await self._async_refresh_unlocked()
            return admin_climate_import_snapshot(self._registry, snapshot)

    async def async_registry_payload(self) -> dict[str, object]:
        """Return the exact private registry shape to a local admin."""

        async with self._lock:
            return registry_to_payload(self._registry)

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
            plan = plan_climate_command(
                intent,
                self._registry,
                snapshot,
                bridge_mode=self.configuration.climate_bridge_mode,
                canary_room_id=self.configuration.climate_canary_room_id,
            )
            if plan.execute:
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

    async def _async_refresh_unlocked(self) -> ClimateImportSnapshot:
        client = self._require_client()
        try:
            snapshot = await client.async_fetch_state()
        except Exception as error:
            self.last_error = type(error).__name__
            raise ClimateRuntimeUnavailable("climate state is unavailable") from error
        self._snapshot = snapshot
        self.last_error = None
        return snapshot

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
