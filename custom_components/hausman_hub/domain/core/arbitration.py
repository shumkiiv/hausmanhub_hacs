"""Pure arbitration of module needs into one resource target (K-05, K-06).

The function never sends anything and never reads the clock: all inputs
are explicit values so the same inputs give the same output. Manual-priority
activity is UTC-based (K-07), so the caller passes it as a proven boolean
instead of letting this module guess a clock conversion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    REASON_AUTOMATION_DISABLED,
    REASON_CONFIG_CHANGED,
    REASON_MANUAL_PRIORITY,
    REASON_NO_DEMAND,
    REASON_OWNERSHIP_UNPROVEN,
    REASON_STABILITY_HOLD,
    REASON_TTL_EXPIRED,
    REASON_UNCERTAIN_RIGHTS,
    ArbitrationMode,
    Authority,
    ControlRecord,
    Proposal,
    ProposalKind,
    RejectedNeed,
    ResourceSpec,
    StabilityProfile,
    Target,
)


@dataclass(frozen=True)
class CandidateHold:
    """Tracked candidate target of the resource (K-06)."""

    target: Target | None
    since_mono_ms: int | None


@dataclass(frozen=True)
class HoldWait:
    """The candidate has not been stable for ``candidate_hold_ms`` yet."""

    next_candidate: CandidateHold
    settled_at_mono_ms: int


@dataclass(frozen=True)
class ArbitrationOutcome:
    """Result of one calculation for one resource (K-05, K-11)."""

    resource_id: str
    mode: str
    accepted: tuple[Proposal, ...] = ()
    rejected: tuple[RejectedNeed, ...] = ()
    target: Target | None = None
    result_reason: str | None = None
    hold_wait: HoldWait | None = None
    chosen: Proposal | None = None
    evidence_refs: tuple[str, ...] = field(default=())


def _select_by_priority(
    proposals: tuple[Proposal, ...],
    previous_selected: Target | None,
) -> Proposal:
    """Tie-break of K-05: highest ``policy_priority``; on a tie keep the
    previously selected target if it is still present and allowed; otherwise
    the smallest ``(module_id, proposal_id)`` in ASCII order. Arrival order
    of packages never decides."""

    top_priority = max(proposal.policy_priority for proposal in proposals)
    equals = [
        proposal
        for proposal in proposals
        if proposal.policy_priority == top_priority
    ]
    if previous_selected is not None:
        for proposal in equals:
            if proposal.target == previous_selected:
                return proposal
    return min(equals, key=lambda item: (item.module_id, item.proposal_id))


def _rejection_reason(
    proposal: Proposal,
    record: ControlRecord,
    manual_hold_active: bool,
    now_mono_ms: int,
    current_config_revision: int,
) -> str | None:
    """Steps 1-3 of the arbitration order (K-05)."""

    if proposal.resource_id != record.resource_id:
        return "foreign_resource"
    if proposal.config_revision != current_config_revision:
        return REASON_CONFIG_CHANGED
    if proposal.expired_at(now_mono_ms):
        return REASON_TTL_EXPIRED
    if manual_hold_active or record.authority is Authority.MANUAL:
        return REASON_MANUAL_PRIORITY
    if record.authority is Authority.DISABLED:
        return REASON_AUTOMATION_DISABLED
    if record.authority is Authority.UNCERTAIN:
        return REASON_UNCERTAIN_RIGHTS
    return None


def _settle_hold(
    target: Target,
    candidate: CandidateHold,
    stability: StabilityProfile,
    now_mono_ms: int,
) -> HoldWait | None:
    """K-06 candidate hold; ``None`` means the candidate is settled."""

    if stability.candidate_hold_ms <= 0:
        return None
    if candidate.target == target and candidate.since_mono_ms is not None:
        settled_at = candidate.since_mono_ms + stability.candidate_hold_ms
        if now_mono_ms >= settled_at:
            return None
        return HoldWait(
            next_candidate=candidate, settled_at_mono_ms=settled_at
        )
    return HoldWait(
        next_candidate=CandidateHold(target=target, since_mono_ms=now_mono_ms),
        settled_at_mono_ms=now_mono_ms + stability.candidate_hold_ms,
    )


def arbitrate(
    spec: ResourceSpec,
    record: ControlRecord,
    needs: tuple[Proposal, ...],
    manual_hold_active: bool,
    auto_proof_exists: bool,
    previous_selected: Target | None,
    candidate: CandidateHold,
    stability: StabilityProfile,
    now_mono_ms: int,
    current_config_revision: int,
) -> ArbitrationOutcome:
    """Resolve the demands of all modules into one target or a refusal."""

    accepted: list[Proposal] = []
    rejected: list[RejectedNeed] = []
    for proposal in needs:
        reason = _rejection_reason(
            proposal,
            record,
            manual_hold_active,
            now_mono_ms,
            current_config_revision,
        )
        if reason is None:
            accepted.append(proposal)
        else:
            rejected.append(
                RejectedNeed(
                    module_id=proposal.module_id,
                    proposal_id=proposal.proposal_id,
                    reason_code=reason,
                )
            )

    def outcome(accepted: tuple[Proposal, ...], **kwargs: object) -> ArbitrationOutcome:
        return ArbitrationOutcome(
            resource_id=spec.resource_id,
            mode=spec.arbitration.value,
            accepted=accepted,
            rejected=tuple(rejected),
            **kwargs,  # type: ignore[arg-type]
        )

    on_demands = tuple(
        item for item in accepted if item.kind is ProposalKind.REQUIRE_ON
    )
    target_demands = tuple(
        item for item in accepted if item.kind is ProposalKind.TARGET
    )

    if spec.arbitration is ArbitrationMode.ANY_ON:
        # any_on is driven by require_on needs only (K-05); whole-target
        # proposals are not an any_on demand and must not stay in accepted.
        for item in target_demands:
            rejected.append(
                RejectedNeed(
                    module_id=item.module_id,
                    proposal_id=item.proposal_id,
                    reason_code="kind_not_acceptable",
                )
            )
        accepted = on_demands
        if on_demands:
            return _decide(
                outcome,
                accepted,
                on_demands,
                previous_selected,
                candidate,
                stability,
                now_mono_ms,
            )
        return _resolve_idle(
            outcome,
            accepted,
            spec,
            record,
            auto_proof_exists,
            candidate,
            stability,
            now_mono_ms,
        )

    if target_demands:
        return _decide(
            outcome,
            tuple(accepted),
            target_demands,
            previous_selected,
            candidate,
            stability,
            now_mono_ms,
        )
    return _resolve_idle(
        outcome,
        tuple(accepted),
        spec,
        record,
        auto_proof_exists,
        candidate,
        stability,
        now_mono_ms,
    )


def _decide(
    outcome_factory,
    accepted: tuple[Proposal, ...],
    pool: tuple[Proposal, ...],
    previous_selected: Target | None,
    candidate: CandidateHold,
    stability: StabilityProfile,
    now_mono_ms: int,
) -> ArbitrationOutcome:
    chosen = _select_by_priority(pool, previous_selected)
    hold = _settle_hold(chosen.target, candidate, stability, now_mono_ms)
    if hold is not None:
        return outcome_factory(
            accepted,
            target=None,
            result_reason=REASON_STABILITY_HOLD,
            hold_wait=hold,
            chosen=chosen,
        )
    return outcome_factory(
        accepted,
        target=chosen.target,
        result_reason="demand",
        chosen=chosen,
    )


def _resolve_idle(
    outcome_factory,
    accepted: tuple[Proposal, ...],
    spec: ResourceSpec,
    record: ControlRecord,
    auto_proof_exists: bool,
    candidate: CandidateHold,
    stability: StabilityProfile,
    now_mono_ms: int,
) -> ArbitrationOutcome:
    """No demand left: an explicit idle target may act only with proved
    ownership; otherwise the result is ``no_demand``, never an automatic
    OFF (K-05)."""

    if spec.idle_target is None:
        return outcome_factory(accepted, target=None, result_reason=REASON_NO_DEMAND)
    if not auto_proof_exists or record.auto_proof is None:
        return outcome_factory(
            accepted, target=None, result_reason=REASON_OWNERSHIP_UNPROVEN
        )
    hold = _settle_hold(spec.idle_target, candidate, stability, now_mono_ms)
    if hold is not None:
        return outcome_factory(
            accepted,
            target=None,
            result_reason=REASON_STABILITY_HOLD,
            hold_wait=hold,
        )
    return outcome_factory(
        accepted, target=spec.idle_target, result_reason="idle_target"
    )
