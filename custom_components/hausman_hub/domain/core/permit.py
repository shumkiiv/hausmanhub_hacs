"""Final dispatch permission of the core (K-08, K-09).

The permit is issued at the dispatch boundary only, is bound to one
operation, and is never reusable. Every rejection names a stable reason
code so explanations stay honest.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    REASON_BINDING_CHANGED,
    REASON_CONFIG_CHANGED,
    REASON_CORE_NOT_ACTIVE,
    REASON_FACTS_STALE,
    REASON_GENERATION_CHANGED,
    REASON_MANUAL_PRIORITY,
    REASON_MIN_STATE_TIME,
    REASON_OWNERSHIP_UNPROVEN,
    REASON_REPEAT_BLOCKED_UNKNOWN,
    REASON_SCOPE_CHANGED,
    REASON_TTL_EXPIRED,
    REASON_UNCERTAIN_RIGHTS,
    Authority,
    ControlRecord,
    ModuleMode,
    Operation,
    ScopeSnapshot,
    StabilityProfile,
)


@dataclass(frozen=True)
class DispatchPermit:
    """Single-use permission produced by the last check (K-08, S-03)."""

    permit_id: str
    operation_id: str
    resource_id: str
    issued_at_mono_ms: int


@dataclass(frozen=True)
class PermitDenial:
    """Why the last check refused the physical call (K-08)."""

    operation_id: str
    resource_id: str
    reason_code: str
    detail: str | None = None


def issue_dispatch_permit(
    operation: Operation,
    record: ControlRecord,
    core_mode: ModuleMode,
    stability: StabilityProfile,
    now_mono_ms: int,
    current_config_revision: int,
    current_scope_revisions: ScopeSnapshot,
    required_facts_fresh: bool,
    episode_locked: bool,
    last_state_change_mono_ms: int | None,
    permit_id: str,
) -> DispatchPermit | PermitDenial:
    """Run every pre-dispatch condition once, in order (K-08)."""

    def denial(reason_code: str, detail: str | None = None) -> PermitDenial:
        return PermitDenial(
            operation_id=operation.operation_id,
            resource_id=operation.resource_id,
            reason_code=reason_code,
            detail=detail,
        )

    if core_mode is not ModuleMode.ACTIVE:
        return denial(REASON_CORE_NOT_ACTIVE)
    if operation.is_terminal():
        return denial(REASON_CORE_NOT_ACTIVE, "operation is not dispatchable")
    if operation.generation != record.generation:
        return denial(
            REASON_GENERATION_CHANGED,
            f"op={operation.generation} record={record.generation}",
        )
    if operation.control_revision != record.control_revision:
        return denial(REASON_GENERATION_CHANGED, "control_revision moved")
    if operation.binding_revision != record.binding_revision:
        return denial(REASON_BINDING_CHANGED)
    if operation.config_revision != current_config_revision:
        return denial(REASON_CONFIG_CHANGED)
    if operation.scope_revisions != dict(current_scope_revisions):
        return denial(REASON_SCOPE_CHANGED)
    if operation.expires_at_mono_ms is not None:
        if now_mono_ms >= operation.expires_at_mono_ms:
            return denial(REASON_TTL_EXPIRED)
    if operation.origin_automation and record.authority is Authority.MANUAL:
        return denial(REASON_MANUAL_PRIORITY)
    if record.authority is Authority.UNCERTAIN:
        return denial(REASON_UNCERTAIN_RIGHTS, record.uncertainty_reason)
    if record.uncertainty_reason is not None:
        return denial(REASON_UNCERTAIN_RIGHTS, record.uncertainty_reason)
    if (
        operation.origin_automation
        and not operation.target_requires_on
        and record.auto_proof is None
    ):
        # An automatic OFF needs proven ownership at the dispatch boundary
        # too (SEC-FIRST-CONF-02-R2): a decision made earlier on valid
        # proof cannot survive the proof's revocation.
        return denial(REASON_OWNERSHIP_UNPROVEN)
    if episode_locked and operation.origin_automation:
        return denial(REASON_REPEAT_BLOCKED_UNKNOWN)
    if not required_facts_fresh:
        return denial(REASON_FACTS_STALE)
    # Stability: candidate hold is settled by arbitration before queueing;
    # min state time is re-checked here against the confirmed transition.
    if operation.target_requires_on and stability.min_on_ms > 0:
        if last_state_change_mono_ms is not None:
            if now_mono_ms < last_state_change_mono_ms + stability.min_on_ms:
                return denial(REASON_MIN_STATE_TIME, "min_on")
    if not operation.target_requires_on and stability.min_off_ms > 0:
        if last_state_change_mono_ms is not None:
            if now_mono_ms < last_state_change_mono_ms + stability.min_off_ms:
                return denial(REASON_MIN_STATE_TIME, "min_off")
    return DispatchPermit(
        permit_id=permit_id,
        operation_id=operation.operation_id,
        resource_id=operation.resource_id,
        issued_at_mono_ms=now_mono_ms,
    )
