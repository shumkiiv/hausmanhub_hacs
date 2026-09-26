"""Core command service: one bounded path from intent to result (K-08..K-11).

The service owns the per-resource queue, the operation lifecycle, the
manual barrier and the explanation ledger. Home Assistant, storage, the
transport and the clock arrive as injected ports declared here (CA-03).
The FIRST delivery replaces only the external environment in tests; the
permission check, queue, arbitration and lifecycle are production code.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from typing import Callable, Mapping, Protocol, runtime_checkable

from ...domain.core.arbitration import ArbitrationOutcome, CandidateHold, arbitrate
from ...domain.core.models import (
    REASON_CONFIRMATION_TIMEOUT,
    REASON_MANUAL_OVERRIDE,
    REASON_NO_OP,
    REASON_REPEAT_BLOCKED_UNKNOWN,
    REASON_SUPERSEDED,
    REASON_TTL_EXPIRED,
    AutoProof,
    Authority,
    AutomaticScope,
    ControlRecord,
    DecisionExplanation,
    Evidence,
    FactView,
    InputFrame,
    ManualHold,
    ModuleManifest,
    ModuleMode,
    Observation,
    ObservationSnapshot,
    Operation,
    OperationPhase,
    Origin,
    Proposal,
    Quality,
    REASON_CONFIG_CHANGED,
    ResourceSpec,
    StabilityProfile,
    Target,
    ValidationIssue,
    requested_properties,
)
from ...domain.core.permit import (
    DispatchPermit,
    PermitDenial,
    issue_dispatch_permit,
)

MANUAL_PRIORITY_DEFAULT_MS = 3_600_000
MANUAL_OFF_EXTRA_MINIMUM_MS = 600_000
DEFAULT_MANUAL_POLICY_REVISION = 2
STORE_SCHEMA_VERSION = 1
FALLBACK_MAX_AGE_MS = 60_000
FALLBACK_CONFIRMATION_TIMEOUT_MS = 10_000


class CoreServiceError(Exception):
    """Base error of the core command service."""


class UnknownResource(CoreServiceError):
    pass


class UnknownModule(CoreServiceError):
    pass


class RevisionConflict(CoreServiceError):
    pass


class IdempotencyConflict(CoreServiceError):
    pass


class StorageUnavailable(CoreServiceError):
    pass


class CommitConflict(CoreServiceError):
    pass


@runtime_checkable
class ClockPort(Protocol):
    """Injected time source; domain and application never read the OS."""

    def now_mono_ms(self) -> int: ...

    def now_utc_ms(self) -> int: ...

    def boot_id(self) -> str: ...


@runtime_checkable
class ControlStorePort(Protocol):
    """Durable whole-image store port (S-04)."""

    @property
    def commit_revision(self) -> int: ...

    def commit(
        self, expected_commit_revision: int, next_state: Mapping[str, object]
    ) -> None: ...


@runtime_checkable
class TransportPort(Protocol):
    """Boundary of one physical call (S-03)."""

    def call(self, prepared: "PreparedCall") -> "TransportOutcome": ...


@runtime_checkable
class DeviceAdapterPort(Protocol):
    """Adapter port owned by the application layer (S-03)."""

    def capabilities(self, resource_id: str) -> "CapabilityProfile": ...

    def prepare(
        self,
        target: Target,
        entity_id: str,
        profile: "CapabilityProfile",
        operation_id: str,
        permit_id: str,
    ) -> "PreparedCall | ValidationIssue": ...

    def dispatch_once(
        self, prepared: "PreparedCall", transport: TransportPort
    ) -> "TransportOutcome": ...

    def normalize(self, raw: Mapping[str, object]) -> tuple[Observation, ...]: ...


class TransportStatus(Enum):
    ACCEPTED = "accepted"
    ERROR = "error"


@dataclass(frozen=True)
class TransportOutcome:
    """Result of one transport call; reception, not device confirmation."""

    status: TransportStatus
    error_detail: str | None = None
    proven_not_sent: bool = False


@dataclass(frozen=True)
class PreparedCall:
    """Allowlist-built physical call (S-03). No free-form HA payload."""

    domain: str
    service: str
    entity_id: str
    data: Mapping[str, object]
    operation_id: str
    permit_id: str
    requested_properties: tuple[str, ...]


@dataclass(frozen=True)
class PropertyRule:
    """One capability property; every field is mandatory (S-02)."""

    property: str
    unit: str
    readable: bool
    writable: bool
    min_value: Decimal | None
    max_value: Decimal | None
    step: Decimal | None
    allowed_values: tuple[str, ...] | None
    report_mode: str
    state_source: str
    max_age_ms: int
    confirmation: str
    tolerance: Decimal | None
    confirmation_timeout_ms: int
    dispatch_timeout_ms: int
    readback_delay_ms: int | None
    allow_silent_absolute: tuple[str, ...] = ()
    unknown_outcome_transitions: Mapping[str, str] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class CapabilityProfile:
    """Passport of one resource's capabilities (S-02)."""

    profile_id: str
    version: int
    source: str
    properties: Mapping[str, PropertyRule]

    def __post_init__(self) -> None:
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise CoreServiceError("CapabilityProfile.version must be int")


class ManualStatus(Enum):
    ACCEPTED = "accepted"
    NO_OP = "no_op"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass(frozen=True)
class ManualReceipt:
    """Acceptance receipt of one manual intent (S-04; S-06 semantics)."""

    request_id: str
    status: ManualStatus
    reason_code: str | None
    operation_id: str | None
    control_revision: int
    generation: int
    idempotent_replay: bool = False


@dataclass(frozen=True)
class ResourceView:
    """Server projection of one resource (S-07, FIRST subset)."""

    resource_id: str
    authority: Authority
    automatic_scope: AutomaticScope
    manual_priority_until_utc_ms: int | None
    selected_target: Target | None
    facts: tuple[FactView, ...]
    operation_id: str | None
    operation_phase: str | None
    operation_reason: str | None
    control_revision: int
    generation: int


@dataclass(frozen=True)
class ModuleEntry:
    """Registry row: manifest plus the pure module instance (K-04)."""

    manifest: ModuleManifest
    instance: object


class CoreCommandService:
    """Single coordinating path; transitions are serialized here (S-04)."""

    def __init__(
        self,
        *,
        clock: ClockPort,
        store: ControlStorePort,
        adapter: DeviceAdapterPort,
        transport: TransportPort,
        specs: Mapping[str, ResourceSpec],
        bindings: Mapping[str, str],
        modules: Mapping[str, ModuleEntry],
        stability_profiles: Mapping[str, StabilityProfile],
        config_revisions: Mapping[str, int],
        scope_revisions: Mapping[str, int],
        episode_of_proposal: Mapping[str, str] | None = None,
        id_factory: Callable[[], str],
        initial_records: Mapping[str, ControlRecord] | None = None,
        core_mode: ModuleMode = ModuleMode.ACTIVE,
    ) -> None:
        self._clock = clock
        self._store = store
        self._adapter = adapter
        self._transport = transport
        self._specs = dict(specs)
        self._bindings = dict(bindings)
        self._modules = dict(modules)
        self._stability = dict(stability_profiles)
        self._config_revisions = dict(config_revisions)
        self._scope_revisions = dict(scope_revisions)
        self._episode_of_proposal = dict(episode_of_proposal or {})
        self._id_factory = id_factory
        self._core_mode = core_mode

        self._records: dict[str, ControlRecord] = dict(initial_records or {})
        self._needs: dict[str, tuple[Proposal, ...]] = {}
        self._operations: dict[str, Operation] = {}
        self._resource_ops: dict[str, list[str]] = {
            resource_id: [] for resource_id in self._specs
        }
        self._taken: dict[str, str] = {}
        self._selected_target: dict[str, Target | None] = {
            resource_id: None for resource_id in self._specs
        }
        self._candidate: dict[str, CandidateHold] = {
            resource_id: CandidateHold(None, None)
            for resource_id in self._specs
        }
        self._facts: dict[str, dict[str, Observation]] = {
            resource_id: {} for resource_id in self._specs
        }
        self._report_groups: dict[tuple, list[Observation]] = {}
        self._episode_locks: dict[tuple[str, str], str] = {}
        self._receipts: dict[str, ManualReceipt] = {}
        self._receipt_fingerprints: dict[str, tuple] = {}
        self._deny_barrier: set[str] = set()
        self._explanations: dict[str, DecisionExplanation] = {}
        self._last_state_change: dict[str, int | None] = {
            resource_id: None for resource_id in self._specs
        }
        self._consumed_permits: set[str] = set()

    # ------------------------------------------------------------------
    # lookups

    def resolve_resource_id(self, resource_ref: str) -> str:
        if resource_ref in self._specs:
            return resource_ref
        for resource_id, spec in self._specs.items():
            if resource_ref in spec.aliases:
                return resource_id
        raise UnknownResource(resource_ref)

    def spec(self, resource_id: str) -> ResourceSpec:
        try:
            return self._specs[resource_id]
        except KeyError as exc:
            raise UnknownResource(resource_id) from exc

    def record(self, resource_id: str) -> ControlRecord:
        try:
            return self._records[resource_id]
        except KeyError as exc:
            raise UnknownResource(resource_id) from exc

    def explanation(self, resource_id: str) -> DecisionExplanation | None:
        return self._explanations.get(resource_id)

    def operation(self, operation_id: str) -> Operation:
        return self._operations[operation_id]

    def dispatch_count(self) -> int:
        return sum(
            1
            for operation in self._operations.values()
            if operation.dispatch_started_at_mono_ms is not None
        )

    def operations(self, resource_id: str) -> tuple[Operation, ...]:
        """Public read-only trace of the resource operations."""

        return tuple(
            self._operations[operation_id]
            for operation_id in self._resource_ops[resource_id]
        )

    def episode_lock_reason(
        self, resource_id: str, episode_id: str
    ) -> str | None:
        return self._episode_locks.get((resource_id, episode_id))

    def scope_revision(self, group_id: str) -> int:
        return self._scope_revisions.get(group_id, 0)

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{self._id_factory()}"

    def _stability_for(self, resource_id: str) -> StabilityProfile:
        spec = self.spec(resource_id)
        profile_id = spec.stability_profile_id
        if profile_id not in self._stability:
            profile_id = "default"
        return self._stability[profile_id]

    def _manual_hold_active(self, record: ControlRecord) -> bool:
        if record.manual is None:
            return False
        return record.manual.active_at_utc(self._clock.now_utc_ms())

    def _fact_matches(self, resource_id: str, target: Target) -> bool:
        profile = self._adapter.capabilities(resource_id)
        for name, expected in requested_properties(target).items():
            fact = self._facts[resource_id].get(name)
            if fact is None:
                return False
            rule = profile.properties.get(name)
            if rule is not None and rule.tolerance is not None:
                actual = Decimal(str(fact.value))
                limit = Decimal(str(expected))
                if abs(actual - limit) > rule.tolerance:
                    return False
            elif isinstance(expected, bool):
                if bool(fact.value) is not expected:
                    return False
            elif fact.value != expected:
                return False
        return True

    def _facts_fresh(self, resource_id: str, target: Target) -> bool:
        """Freshness of the facts the decision relies on: the current
        switching state. Target-only properties (a brightness we are about
        to set from OFF) need no prior reading."""

        profile = self._adapter.capabilities(resource_id)
        state_property = "on"
        if state_property not in requested_properties(target):
            return True
        rule = profile.properties.get(state_property)
        fact = self._facts[resource_id].get(state_property)
        if fact is None:
            return False
        max_age = rule.max_age_ms if rule else FALLBACK_MAX_AGE_MS
        return self._clock.now_mono_ms() - fact.received_at_mono_ms <= max_age

    def _unresolved_sent_exists(self, resource_id: str) -> bool:
        """A dispatch with an undetermined outcome exists (K-08 no_op)."""

        for operation_id in self._resource_ops[resource_id]:
            operation = self._operations[operation_id]
            if operation.dispatch_started_at_mono_ms is None:
                continue
            if operation.phase in (
                OperationPhase.DISPATCHING,
                OperationPhase.OBSERVING,
                OperationPhase.UNKNOWN,
            ):
                return True
        return False

    def _resource_busy(self, resource_id: str) -> bool:
        """One in-flight operation per resource (K-08); a queued auto target
        waits for the previous operation instead of sending in parallel."""

        for operation_id in self._resource_ops[resource_id]:
            if self._operations[operation_id].phase in (
                OperationPhase.TAKEN,
                OperationPhase.DISPATCHING,
                OperationPhase.OBSERVING,
            ):
                return True
        return resource_id in self._taken

    def _scope_revisions_for(self, resource_id: str) -> dict[str, int]:
        spec = self.spec(resource_id)
        return {
            group: self._scope_revisions[group]
            for group in spec.manual_scopes
            if group in self._scope_revisions
        }

    # ------------------------------------------------------------------
    # store

    def _commit_state(self) -> None:
        image = {
            "schema_version": STORE_SCHEMA_VERSION,
            "records": dict(self._records),
            "episode_locks": dict(self._episode_locks),
        }
        self._store.commit(self._store.commit_revision, image)

    # ------------------------------------------------------------------
    # module events (auto path)

    def process_module_event(self, module_id: str, event_id: str) -> None:
        entry = self._modules.get(module_id)
        if entry is None:
            raise UnknownModule(module_id)
        manifest = entry.manifest
        subscribed = tuple(
            self.resolve_resource_id(name)
            for name in manifest.observation_subscriptions
        )
        frame = InputFrame(
            now_utc_ms=self._clock.now_utc_ms(),
            now_mono_ms=self._clock.now_mono_ms(),
            boot_id=self._clock.boot_id(),
            event_id=event_id,
            config_revision=self._config_revisions.get(
                subscribed[0] if subscribed else "", 0
            ),
            observations=tuple(
                observation
                for resource_id in subscribed
                for observation in self._facts[resource_id].values()
            ),
            resource_controls=tuple(
                self._records[resource_id] for resource_id in subscribed
            ),
            related_scope_revisions=dict(self._scope_revisions),
            inbound_needs=self._needs.get(module_id, ()),
        )
        evaluation = entry.instance.evaluate(frame, None)
        if evaluation.module_id != module_id:
            raise CoreServiceError("evaluation module mismatch")
        if evaluation.constraints:
            # Constraint arbitration is outside the FIRST scope; non-empty
            # sets are rejected instead of being silently ignored (K-05).
            raise CoreServiceError("constraints_are_out_of_first_scope")
        previous = self._needs.get(module_id, ())
        canonical: list[Proposal] = []
        for proposal in evaluation.proposals:
            if proposal.kind not in manifest.allowed_proposal_kinds:
                raise CoreServiceError(
                    f"proposal kind {proposal.kind} not allowed by manifest"
                )
            resolved = self.resolve_resource_id(proposal.resource_id)
            canonical.append(
                proposal
                if resolved == proposal.resource_id
                else replace(proposal, resource_id=resolved)
            )
        per_resource: dict[str, int] = {}
        for proposal in canonical:
            per_resource[proposal.resource_id] = (
                per_resource.get(proposal.resource_id, 0) + 1
            )
        for count in per_resource.values():
            if count > manifest.max_proposals_per_resource:
                raise CoreServiceError("too many proposals for resource")
        self._needs[module_id] = tuple(canonical)
        previous_by_id = {
            proposal.proposal_id: proposal for proposal in previous
        }
        new_by_id = {proposal.proposal_id: proposal for proposal in canonical}
        withdrawn_by_resource: dict[str, list[Proposal]] = {}
        for proposal_id, proposal in previous_by_id.items():
            if proposal_id not in new_by_id:
                withdrawn_by_resource.setdefault(
                    proposal.resource_id, []
                ).append(proposal)
        affected = sorted(
            {proposal.resource_id for proposal in canonical}
            | set(withdrawn_by_resource)
        )
        for resource_id in affected:
            self._arbitrate_resource(
                resource_id,
                tuple(withdrawn_by_resource.get(resource_id, ())),
            )

    def _arbitrate_resource(
        self, resource_id: str, withdrawn: tuple[Proposal, ...]
    ) -> None:
        spec = self.spec(resource_id)
        record = self.record(resource_id)
        needs = tuple(
            proposal
            for proposals in self._needs.values()
            for proposal in proposals
            if proposal.resource_id == resource_id
        )
        outcome = arbitrate(
            spec=spec,
            record=record,
            needs=needs,
            manual_hold_active=self._manual_hold_active(record),
            auto_proof_exists=record.auto_proof is not None,
            previous_selected=self._selected_target[resource_id],
            candidate=self._candidate[resource_id],
            stability=self._stability_for(resource_id),
            now_mono_ms=self._clock.now_mono_ms(),
            current_config_revision=self._config_revisions.get(resource_id, 0),
        )
        if outcome.hold_wait is not None:
            self._candidate[resource_id] = outcome.hold_wait.next_candidate
        else:
            self._candidate[resource_id] = CandidateHold(outcome.target, None)
        decision_id = self._next_id("dec")
        operation: Operation | None = None
        if outcome.target is not None:
            operation = self._enqueue_target(
                resource_id,
                outcome.target,
                accepted=outcome.accepted,
                origin=Origin.AUTOMATION,
                decision_id=decision_id,
            )
            self._selected_target[resource_id] = outcome.target
        self._explanations[resource_id] = DecisionExplanation(
            decision_id=decision_id,
            resource_id=resource_id,
            accepted=outcome.accepted,
            withdrawn=withdrawn,
            rejected=outcome.rejected,
            target_result=outcome.target,
            result_reason=outcome.result_reason,
            current_facts=tuple(
                FactView(
                    property=name,
                    value=fact.value,
                    quality=fact.quality,
                    evidence=fact.evidence_kind,
                    observed_at_mono_ms=fact.received_at_mono_ms,
                )
                for name, fact in sorted(self._facts[resource_id].items())
            ),
            operation_id=(
                operation.operation_id if operation is not None else None
            ),
            next_condition=(
                outcome.result_reason
                if outcome.target is None
                else None
            ),
            next_check_at_mono_ms=(
                outcome.hold_wait.settled_at_mono_ms
                if outcome.hold_wait is not None
                else None
            ),
            evidence_refs=tuple(
                ref
                for proposal in outcome.accepted
                for ref in proposal.evidence_refs
            ),
            rule_versions={
                "config_revision": self._config_revisions.get(resource_id, 0),
                "control_revision": record.control_revision,
                "generation": record.generation,
            },
        )

    def _enqueue_target(
        self,
        resource_id: str,
        target: Target,
        accepted: tuple[Proposal, ...],
        origin: Origin,
        request_id: str | None = None,
        decision_id: str | None = None,
        no_op_decided: bool | None = None,
    ) -> Operation:
        """Queue one target as an operation; a fresh matching fact with no
        unresolved sent operation becomes an explicit no_op (K-08). The
        caller may pass a pre-computed ``no_op_decided`` (manual path) so
        the decision is taken against the world before any cancellation."""

        record = self.record(resource_id)
        spec = self.spec(resource_id)
        chosen = accepted[0] if accepted else None
        resolved_decision_id = decision_id or self._next_id("dec")
        common = {
            "resource_id": resource_id,
            "decision_id": resolved_decision_id,
            "origin": origin,
            "target": target,
            "generation": record.generation,
            "control_revision": record.control_revision,
            "config_revision": self._config_revisions.get(resource_id, 0),
            "binding_revision": spec.binding_revision,
            "scope_revisions": self._scope_revisions_for(resource_id),
            "created_at_mono_ms": self._clock.now_mono_ms(),
            "request_id": request_id,
            "module_id": chosen.module_id if chosen else None,
        }
        if origin is Origin.AUTOMATION:
            # A newer decision replaces still-unsent targets of this
            # resource, including when the new decision is a no_op (K-08).
            for operation_id in list(self._resource_ops[resource_id]):
                pending = self._operations[operation_id]
                if pending.phase in (
                    OperationPhase.QUEUED,
                    OperationPhase.TAKEN,
                ):
                    self._mark_terminal(
                        pending, OperationPhase.SUPERSEDED, REASON_SUPERSEDED
                    )
        if no_op_decided is None:
            no_op_decided = (
                not self._unresolved_sent_exists(resource_id)
                and self._fact_matches(resource_id, target)
                and self._facts_fresh(resource_id, target)
            )
        if no_op_decided:
            operation = Operation(
                operation_id=self._next_id("op"),
                phase=OperationPhase.NO_OP,
                reason_code=REASON_NO_OP,
                **common,
            )
            self._register_operation(resource_id, operation)
            return operation
        operation = Operation(
            operation_id=self._next_id("op"),
            phase=OperationPhase.QUEUED,
            expires_at_mono_ms=(
                chosen.valid_until_mono_ms if chosen else None
            ),
            episode_id=(
                self._episode_of_proposal.get(
                    chosen.proposal_id, chosen.proposal_id
                )
                if chosen
                else None
            ),
            **common,
        )
        self._register_operation(resource_id, operation)
        return operation

    def _register_operation(
        self, resource_id: str, operation: Operation
    ) -> None:
        self._operations[operation.operation_id] = operation
        self._resource_ops[resource_id].append(operation.operation_id)

    def _mark_terminal(
        self,
        operation: Operation,
        phase: OperationPhase,
        reason: str | None,
    ) -> None:
        operation.phase = phase
        operation.reason_code = reason
        if (
            phase is OperationPhase.SUPERSEDED
            and operation.dispatch_started_at_mono_ms is not None
        ):
            operation.phase = OperationPhase.SUPERSEDED_AFTER_DISPATCH
        self._taken.pop(operation.resource_id, None)

    # ------------------------------------------------------------------
    # executor ticks

    def advance_timeouts(self) -> None:
        now = self._clock.now_mono_ms()
        changed = False
        for operation in list(self._operations.values()):
            if operation.phase is not OperationPhase.OBSERVING:
                continue
            deadline = operation.confirmation_deadline_mono_ms
            if deadline is not None and now >= deadline:
                operation.phase = OperationPhase.UNKNOWN
                operation.reason_code = REASON_CONFIRMATION_TIMEOUT
                if operation.origin_automation and operation.episode_id:
                    self._episode_locks[
                        (operation.resource_id, operation.episode_id)
                    ] = REASON_CONFIRMATION_TIMEOUT
                changed = True
        if changed:
            self._commit_state()

    def take_ready(self) -> None:
        for resource_id in sorted(self._specs):
            if resource_id in self._taken or self._resource_busy(resource_id):
                continue
            if resource_id in self._deny_barrier:
                continue
            queued = [
                self._operations[operation_id]
                for operation_id in self._resource_ops[resource_id]
                if self._operations[operation_id].phase
                is OperationPhase.QUEUED
            ]
            if not queued:
                continue
            operation = queued[-1]
            expires = operation.expires_at_mono_ms
            if expires is not None and self._clock.now_mono_ms() >= expires:
                self._mark_terminal(
                    operation, OperationPhase.EXPIRED, REASON_TTL_EXPIRED
                )
                continue
            operation.phase = OperationPhase.TAKEN
            self._taken[resource_id] = operation.operation_id

    def run_taken(self) -> None:
        for operation_id in list(self._taken.values()):
            operation = self._operations[operation_id]
            if operation.phase is not OperationPhase.TAKEN:
                # The job left the dispatch boundary already; never
                # re-permit it (K-08: one application dispatch).
                self._taken.pop(operation.resource_id, None)
                continue
            decision = self._final_permit(operation)
            if isinstance(decision, PermitDenial):
                self._apply_permit_denial(operation, decision)
                continue
            self._dispatch_operation(operation, decision)

    def drain(self) -> None:
        """One permitted executor tick: timeouts, dequeue, dispatch."""

        self.advance_timeouts()
        self.take_ready()
        self.run_taken()

    def _final_permit(
        self, operation: Operation
    ) -> DispatchPermit | PermitDenial:
        record = self.record(operation.resource_id)
        permit = issue_dispatch_permit(
            operation=operation,
            record=record,
            core_mode=self._core_mode,
            stability=self._stability_for(operation.resource_id),
            now_mono_ms=self._clock.now_mono_ms(),
            current_config_revision=self._config_revisions.get(
                operation.resource_id, 0
            ),
            current_scope_revisions=self._scope_revisions_for(
                operation.resource_id
            ),
            required_facts_fresh=self._facts_fresh(
                operation.resource_id, operation.target
            ),
            episode_locked=(operation.resource_id, operation.episode_id or "")
            in self._episode_locks,
            last_state_change_mono_ms=self._last_state_change.get(
                operation.resource_id
            ),
            permit_id=self._next_id("pm"),
        )
        if isinstance(permit, PermitDenial):
            return permit
        if permit.permit_id in self._consumed_permits:
            return PermitDenial(
                operation_id=operation.operation_id,
                resource_id=operation.resource_id,
                reason_code="permit_reuse",
            )
        self._consumed_permits.add(permit.permit_id)
        return permit

    def _apply_permit_denial(
        self, operation: Operation, denial: PermitDenial
    ) -> None:
        if denial.reason_code == REASON_TTL_EXPIRED:
            self._mark_terminal(
                operation, OperationPhase.EXPIRED, denial.reason_code
            )
        elif denial.reason_code == REASON_REPEAT_BLOCKED_UNKNOWN:
            self._mark_terminal(
                operation, OperationPhase.REJECTED, denial.reason_code
            )
        else:
            self._mark_terminal(
                operation, OperationPhase.CANCELLED, denial.reason_code
            )

    def _dispatch_operation(
        self, operation: Operation, permit: DispatchPermit
    ) -> None:
        resource_id = operation.resource_id
        profile = self._adapter.capabilities(resource_id)
        entity_id = self._bindings[resource_id]
        prepared = self._adapter.prepare(
            operation.target,
            entity_id,
            profile,
            operation.operation_id,
            permit.permit_id,
        )
        if isinstance(prepared, ValidationIssue):
            self._mark_terminal(
                operation, OperationPhase.REJECTED, "invalid_profile"
            )
            return
        rule = profile.properties.get("on")
        timeout = (
            rule.confirmation_timeout_ms
            if rule
            else FALLBACK_CONFIRMATION_TIMEOUT_MS
        )
        operation.phase = OperationPhase.DISPATCHING
        operation.dispatch_started_at_mono_ms = self._clock.now_mono_ms()
        operation.confirmation_deadline_mono_ms = (
            operation.dispatch_started_at_mono_ms + timeout
        )
        operation.pre_dispatch_sequence = self._max_sequence(resource_id)
        operation.evidence_refs = operation.evidence_refs + (
            permit.permit_id,
        )
        # Persisting the dispatching phase precedes the physical call (K-08).
        self._commit_state()
        outcome = self._adapter.dispatch_once(prepared, self._transport)
        # The transport call has returned: the resource slot is free again.
        self._taken.pop(resource_id, None)
        if outcome.status is TransportStatus.ACCEPTED:
            operation.phase = OperationPhase.OBSERVING
        elif outcome.proven_not_sent:
            self._mark_terminal(operation, OperationPhase.FAILED, "not_sent")
        else:
            operation.phase = OperationPhase.UNKNOWN
            operation.reason_code = "transport_error_unknown_outcome"
            if operation.origin_automation and operation.episode_id:
                self._episode_locks[(resource_id, operation.episode_id)] = (
                    "transport_error_unknown_outcome"
                )
        self._commit_state()

    def _max_sequence(self, resource_id: str) -> int | None:
        sequences = [
            fact.source_sequence
            for fact in self._facts[resource_id].values()
            if fact.source_sequence is not None
        ]
        return max(sequences) if sequences else None

    # ------------------------------------------------------------------
    # observations

    def ingest_observation(self, observation: Observation) -> str:
        resource_id = self.resolve_resource_id(observation.resource_id)
        self._facts[resource_id][observation.property] = observation
        confirmation_key = (
            resource_id,
            observation.source_sequence,
            observation.received_at_mono_ms,
        )
        self._report_groups.setdefault(confirmation_key, []).append(
            observation
        )
        result = "recorded"
        for operation_id in reversed(self._resource_ops[resource_id]):
            operation = self._operations[operation_id]
            if operation.phase not in (
                OperationPhase.OBSERVING,
                OperationPhase.UNKNOWN,
            ):
                continue
            if operation.dispatch_started_at_mono_ms is None:
                continue
            outcome = self._try_confirm(operation, confirmation_key)
            if outcome is not None:
                result = outcome
                break
        self._commit_state()
        return result

    def _try_confirm(
        self, operation: Operation, confirmation_key: tuple
    ) -> str | None:
        """Five confirmation conditions of S-03, all mandatory."""

        resource_id = operation.resource_id
        profile = self._adapter.capabilities(resource_id)
        rule = profile.properties.get("on")
        confirmation_mode = (
            rule.confirmation if rule else "post_dispatch_report"
        )
        group = self._report_groups.get(confirmation_key, [])
        by_property = {
            observation.property: observation for observation in group
        }
        required = requested_properties(operation.target)
        if not all(name in by_property for name in required):
            return None
        sample = group[0]
        if (
            confirmation_mode == "post_dispatch_report"
            and sample.evidence_kind is not Evidence.DEVICE_REPORT
        ):
            return None
        if sample.binding_revision != operation.binding_revision:
            return None
        if sample.received_at_mono_ms < operation.dispatch_started_at_mono_ms:
            return None
        if sample.source_sequence is None:
            return None
        if (
            operation.pre_dispatch_sequence is not None
            and sample.source_sequence <= operation.pre_dispatch_sequence
        ):
            return None
        for name, expected in required.items():
            observation = by_property[name]
            rule_property = profile.properties.get(name)
            if rule_property is not None and rule_property.tolerance is not None:
                actual = Decimal(str(observation.value))
                limit = Decimal(str(expected))
                if abs(actual - limit) > rule_property.tolerance:
                    return None
            elif isinstance(expected, bool):
                if bool(observation.value) is not expected:
                    return None
            elif observation.value != expected:
                return None
        late = operation.phase is OperationPhase.UNKNOWN
        operation.phase = (
            OperationPhase.CONFIRMED_LATE
            if late
            else OperationPhase.CONFIRMED
        )
        operation.reason_code = "confirmed_by_device_report"
        if not late:
            self._last_state_change[resource_id] = sample.received_at_mono_ms
            record = self.record(resource_id)
            if (
                operation.origin_automation
                and record.manual is None
                and record.generation == operation.generation
            ):
                self._records[resource_id] = replace(
                    record,
                    auto_proof=_build_auto_proof(operation, by_property),
                )
        self._commit_state()
        return "confirmed_late" if late else "confirmed"

    # ------------------------------------------------------------------
    # manual path (S-04 order)

    def submit_manual(
        self,
        resource_ref: str,
        request_id: str,
        action_id: str,
        target: Target,
        source: str,
        expected_control_revision: int,
        policy_revision: int = DEFAULT_MANUAL_POLICY_REVISION,
    ) -> ManualReceipt:
        resource_id = self.resolve_resource_id(resource_ref)
        fingerprint = (resource_id, action_id, target)
        previous = self._receipts.get(request_id)
        if previous is not None:
            if self._receipt_fingerprints.get(request_id) == fingerprint:
                return ManualReceipt(
                    request_id=request_id,
                    status=previous.status,
                    reason_code=previous.reason_code,
                    operation_id=previous.operation_id,
                    control_revision=previous.control_revision,
                    generation=previous.generation,
                    idempotent_replay=True,
                )
            raise IdempotencyConflict(request_id)
        record = self.record(resource_id)
        if record.control_revision != expected_control_revision:
            raise RevisionConflict(
                f"expected {expected_control_revision}, "
                f"actual {record.control_revision}"
            )
        # 2. deny-only barrier closes automatic dispatch immediately.
        self._deny_barrier.add(resource_id)
        # no_op is judged against the world before anything is cancelled:
        # an already-sent operation with an undetermined outcome forbids it
        # (K-08: the older OFF does not legalize a no_op manual OFF).
        no_op_now = (
            not self._unresolved_sent_exists(resource_id)
            and self._fact_matches(resource_id, target)
            and self._facts_fresh(resource_id, target)
        )
        # Pending automatic commands of this resource only (K-07).
        for operation_id in list(self._resource_ops[resource_id]):
            operation = self._operations[operation_id]
            if operation.phase in (OperationPhase.QUEUED, OperationPhase.TAKEN):
                self._mark_terminal(
                    operation,
                    OperationPhase.CANCELLED,
                    REASON_MANUAL_OVERRIDE,
                )
            elif operation.phase in (
                OperationPhase.OBSERVING,
                OperationPhase.DISPATCHING,
            ):
                self._mark_terminal(
                    operation,
                    OperationPhase.SUPERSEDED_AFTER_DISPATCH,
                    REASON_MANUAL_OVERRIDE,
                )
        now_utc = self._clock.now_utc_ms()
        spec = self.spec(resource_id)
        minimum_off_until = (
            now_utc + MANUAL_OFF_EXTRA_MINIMUM_MS
            if not bool(getattr(target, "on", False))
            else None
        )
        next_record = ControlRecord(
            resource_id=resource_id,
            control_revision=record.control_revision + 1,
            generation=record.generation + 1,
            binding_revision=record.binding_revision,
            authority=Authority.MANUAL,
            automatic_scope=AutomaticScope.NONE,
            manual=ManualHold(
                action_id=action_id,
                action="set_target",
                source=source,
                proven_at_utc_ms=now_utc,
                priority_until_utc_ms=now_utc + MANUAL_PRIORITY_DEFAULT_MS,
                minimum_off_until_utc_ms=minimum_off_until,
                policy_revision=policy_revision,
            ),
            auto_proof=None,
            uncertainty_reason=None,
        )
        next_scope = dict(self._scope_revisions)
        for group in spec.manual_scopes:
            next_scope[group] = next_scope.get(group, 0) + 1
        try:
            # 4. one commit of the accepted authority (S-04).
            self._store.commit(
                self._store.commit_revision,
                {
                    "schema_version": STORE_SCHEMA_VERSION,
                    "records": {
                        **self._records,
                        resource_id: next_record,
                    },
                    "episode_locks": dict(self._episode_locks),
                },
            )
        except StorageUnavailable:
            # The internal auto ban stays; the request is not accepted.
            raise
        # 5. accepted: swap in-memory state, publish, release the barrier.
        self._records[resource_id] = next_record
        self._scope_revisions.clear()
        self._scope_revisions.update(next_scope)
        self._deny_barrier.discard(resource_id)
        decision_id = self._next_id("dec")
        if no_op_now:
            # 6. accepted manual intent with a fresh matching fact (K-08):
            # the acceptance itself is recorded, zero dispatches happen.
            no_op = self._enqueue_target(
                resource_id,
                target,
                accepted=(),
                origin=Origin.MANUAL,
                request_id=request_id,
                decision_id=decision_id,
                no_op_decided=True,
            )
            receipt = ManualReceipt(
                request_id=request_id,
                status=ManualStatus.NO_OP,
                reason_code=REASON_NO_OP,
                operation_id=no_op.operation_id,
                control_revision=next_record.control_revision,
                generation=next_record.generation,
            )
        else:
            operation = self._enqueue_target(
                resource_id,
                target,
                accepted=(),
                origin=Origin.MANUAL,
                request_id=request_id,
                decision_id=decision_id,
                no_op_decided=False,
            )
            receipt = ManualReceipt(
                request_id=request_id,
                status=ManualStatus.ACCEPTED,
                reason_code=None,
                operation_id=operation.operation_id,
                control_revision=next_record.control_revision,
                generation=next_record.generation,
            )
            # Manual execution runs immediately: it does not wait behind an
            # observing foreign device, and the transport call has returned.
            decision = self._final_permit(operation)
            if isinstance(decision, PermitDenial):
                self._apply_permit_denial(operation, decision)
            else:
                self._dispatch_operation(operation, decision)
        self._explanations[resource_id] = DecisionExplanation(
            decision_id=decision_id,
            resource_id=resource_id,
            accepted=(),
            withdrawn=(),
            rejected=(),
            target_result=target,
            result_reason=(
                REASON_NO_OP if no_op_now else "manual_intent_accepted"
            ),
            current_facts=tuple(
                FactView(
                    property=name,
                    value=fact.value,
                    quality=fact.quality,
                    evidence=fact.evidence_kind,
                    observed_at_mono_ms=fact.received_at_mono_ms,
                )
                for name, fact in sorted(self._facts[resource_id].items())
            ),
            operation_id=receipt.operation_id,
            next_condition=None,
            next_check_at_mono_ms=None,
            evidence_refs=(),
            rule_versions={
                "config_revision": self._config_revisions.get(resource_id, 0),
                "control_revision": next_record.control_revision,
                "generation": next_record.generation,
            },
        )
        self._receipts[request_id] = receipt
        self._receipt_fingerprints[request_id] = fingerprint
        return receipt

    # ------------------------------------------------------------------
    # projection

    def resource_view(self, resource_ref: str) -> ResourceView:
        resource_id = self.resolve_resource_id(resource_ref)
        record = self.record(resource_id)
        facts = tuple(
            FactView(
                property=name,
                value=fact.value,
                quality=fact.quality,
                evidence=fact.evidence_kind,
                observed_at_mono_ms=fact.received_at_mono_ms,
            )
            for name, fact in sorted(self._facts[resource_id].items())
        )
        operation_id = None
        phase = None
        reason = None
        if self._resource_ops[resource_id]:
            latest = self._operations[self._resource_ops[resource_id][-1]]
            operation_id = latest.operation_id
            phase = latest.phase.value
            reason = latest.reason_code
        return ResourceView(
            resource_id=resource_id,
            authority=record.authority,
            automatic_scope=record.automatic_scope,
            manual_priority_until_utc_ms=(
                record.manual.priority_until_utc_ms if record.manual else None
            ),
            selected_target=self._selected_target[resource_id],
            facts=facts,
            operation_id=operation_id,
            operation_phase=phase,
            operation_reason=reason,
            control_revision=record.control_revision,
            generation=record.generation,
        )


def _build_auto_proof(
    operation: Operation, by_property: Mapping[str, Observation]
) -> AutoProof:
    first = next(iter(by_property.values()))
    return AutoProof(
        operation_id=operation.operation_id,
        observation_id=first.observation_id,
        confirmed_at_utc_ms=first.received_at_utc_ms,
        generation=operation.generation,
        binding_revision=operation.binding_revision,
        confirmed_target=operation.target,
        confirmed_observation=ObservationSnapshot(
            observation_id=first.observation_id,
            values={
                name: str(observation.value)
                for name, observation in sorted(by_property.items())
            },
            evidence=first.evidence_kind,
        ),
        source_evidence=first.evidence_kind,
    )
