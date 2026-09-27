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
from ...domain.core.recovery import (
    ClockTrust,
    REASON_RESTART_UNCERTAIN,
    classify_restored_operation,
)
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
    ObservationAttribution,
    ObservationSnapshot,
    Operation,
    OperationPhase,
    Origin,
    Proposal,
    Quality,
    REASON_AMBIGUOUS_FEEDBACK_REVOKED,
    REASON_CONFIG_CHANGED,
    REASON_MANUAL_EVIDENCE,
    REASON_STORAGE_BEFORE_SEND,
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


class CapacityExceeded(CoreServiceError):
    """Store capacity is exhausted; the request is refused before
    acceptance instead of silently dropping protected records (S-05)."""


RECEIPT_WINDOW_MS = 24 * 60 * 60 * 1000
MAX_RECEIPTS_IN_WINDOW = 2048
MAX_UNRESOLVED_OPERATIONS = 1024


@runtime_checkable
class ClockPort(Protocol):
    """Injected time source; domain and application never read the OS."""

    def now_mono_ms(self) -> int: ...

    def now_utc_ms(self) -> int: ...

    def boot_id(self) -> str: ...

    def wall_clock_trust(self) -> ClockTrust: ...


@runtime_checkable
class ControlStorePort(Protocol):
    """Durable whole-image store port (S-04)."""

    @property
    def commit_revision(self) -> int: ...

    def commit(
        self, expected_commit_revision: int, next_state: Mapping[str, object]
    ) -> None: ...

    def load(self) -> Mapping[str, object] | None: ...

    def healthy(self) -> bool: ...


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
        self._confirm_keys: dict[str, tuple] = {}
        self._dispatching: dict[str, str] = {}
        self._receipt_times: dict[str, int] = {}
        self._id_floor = 0
        self._restored_operation_ids: set[str] = set()
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

    def receipt(self, request_id: str) -> ManualReceipt | None:
        return self._receipts.get(request_id)

    def _next_id(self, prefix: str) -> str:
        # Identifiers never repeat across restarts: restored operations
        # keep their ids, so new ids must stay above the restored maximum
        # (SEC-S04-M1).
        while True:
            candidate = f"{prefix}-{self._id_factory()}"
            suffix = candidate.rsplit("-", 1)[-1]
            if suffix.isdigit() and int(suffix) <= self._id_floor:
                self._id_floor = max(self._id_floor, int(suffix))
                continue
            if suffix.isdigit():
                self._id_floor = int(suffix)
            return candidate

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
            if fact.quality is Quality.INVALID:
                # An invalid value never matches, and it must never reach
                # the numeric comparison (SEC-FIRST-VAL-01-R2).
                return False
            if not _value_matches(
                fact.value, expected, profile.properties.get(name)
            ):
                return False
        return True

    def _facts_fresh(
        self, resource_id: str, target: Target, require_all: bool = False
    ) -> bool:
        """Freshness of the facts the decision relies on.

        Dispatch precondition: fresh trusted knowledge of the current
        switching state. ``no_op`` matching (``require_all``) proves every
        requested property, so every one of them must be fresh and
        trusted (quality and evidence included, SEC-FIRST-FACT-01)."""

        profile = self._adapter.capabilities(resource_id)
        if require_all:
            names = list(requested_properties(target))
        else:
            names = (
                ["on"] if "on" in requested_properties(target) else []
            )
        for name in names:
            rule = profile.properties.get(name)
            fact = self._facts[resource_id].get(name)
            if fact is None:
                return False
            if fact.quality is not Quality.FRESH:
                return False
            if fact.evidence_kind in (Evidence.OPTIMISTIC, Evidence.RESTORED):
                return False
            max_age = rule.max_age_ms if rule else FALLBACK_MAX_AGE_MS
            if self._clock.now_mono_ms() - fact.received_at_mono_ms > max_age:
                return False
        return True

    def _unresolved_sent_exists(self, resource_id: str) -> bool:
        """A dispatch with an undetermined outcome exists (K-08 no_op)."""

        for operation_id in self._resource_ops[resource_id]:
            operation = self._operations[operation_id]
            # The phase decides, never the dispatch timestamp: a durable
            # DISPATCHING record may exist without a recorded timestamp,
            # and a restored UNKNOWN is unresolved regardless of it
            # (SEC-S04-06).
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

    def _build_image(self) -> dict:
        """One whole image: rights, operations, receipts, replay keys and
        the critical protection history (S-04). The store stamps its own
        commit revision before becoming durable."""
        return {
            "commit_revision": self._store.commit_revision,
            "boot_id": self._clock.boot_id(),
            "restored_operation_ids": sorted(
                self._restored_operation_ids
            ),
            "records": dict(self._records),
            "operations": dict(self._operations),
            "receipts": dict(self._receipts),
            "receipt_times": dict(self._receipt_times),
            "receipt_fingerprints": {
                request_id: list(fingerprint)
                for request_id, fingerprint in self._receipt_fingerprints.items()
            },
            "confirm_keys": {
                operation_id: list(key)
                for operation_id, key in self._confirm_keys.items()
            },
            "episode_locks": {
                f"{resource}|{episode}": reason
                for (resource, episode), reason in self._episode_locks.items()
            },
            "scope_revisions": dict(self._scope_revisions),
            "config_revisions": dict(self._config_revisions),
        }

    def _commit_state(self) -> None:
        self._store.commit(self._store.commit_revision, self._build_image())

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
                origin=Origin.AUTOMATION,
                chosen=outcome.chosen,
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
        origin: Origin,
        chosen: Proposal | None = None,
        request_id: str | None = None,
        decision_id: str | None = None,
        no_op_decided: bool | None = None,
    ) -> Operation:
        """Queue one target as an operation; a fresh matching fact with no
        unresolved sent operation becomes an explicit no_op (K-08). The
        caller may pass a pre-computed ``no_op_decided`` (manual path) so
        the decision is taken against the world before any cancellation.
        Operation metadata (episode, TTL, module) comes from the proposal
        the arbitration actually selected, never from array position."""

        record = self.record(resource_id)
        spec = self.spec(resource_id)
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
        no_op_decided = (
            not self._unresolved_sent_exists(resource_id)
            and self._fact_matches(resource_id, target)
            and self._facts_fresh(resource_id, target, require_all=True)
        ) if no_op_decided is None else no_op_decided
        if not no_op_decided:
            # A queued target occupies store capacity until it resolves;
            # superseded pending targets freed their slot above, so the
            # check counts the true occupancy (SEC-S04-08).
            self._ensure_operation_capacity(
                self._count_unresolved_operations() + 1
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
        # A pending operation is part of the durable image the moment it
        # is accepted (S-04): after a restart it must be visible to be
        # cancelled honestly instead of being silently replayed. When the
        # store refuses the image, the in-memory registration is rolled
        # back so memory never keeps an acceptance the store declined.
        try:
            self._commit_state()
        except Exception:
            self._operations.pop(operation.operation_id, None)
            if operation.operation_id in self._resource_ops.get(
                resource_id, []
            ):
                self._resource_ops[resource_id].remove(
                    operation.operation_id
                )
            self._taken.pop(resource_id, None)
            raise

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
        """One permitted executor tick: timeouts, dequeue, durable
        dispatching record, transport call."""

        self.advance_timeouts()
        self.take_ready()
        self.run_taken()
        self.run_dispatching()

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
            clock_trusted=self._clock.wall_clock_trust().trusted,
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
        """Stage one: prepare and durably record the dispatching phase.
        The physical call happens only in the follow-up executor step
        ``run_dispatching``, which re-checks the deny barrier directly
        before the transport (S-04)."""

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
        operation.phase = OperationPhase.DISPATCHING
        operation.evidence_refs = operation.evidence_refs + (
            permit.permit_id,
        )
        # Persisting the dispatching phase precedes the physical call (K-08);
        # when the store refuses the record, the call must not happen and
        # the non-send is provable (SEC-FIRST-IDEM-01).
        try:
            self._commit_state()
        except (StorageUnavailable, OSError) as exc:
            self._taken.pop(resource_id, None)
            self._dispatching.pop(resource_id, None)
            self._mark_terminal(
                operation,
                OperationPhase.FAILED,
                REASON_STORAGE_BEFORE_SEND,
            )
            if isinstance(exc, StorageUnavailable):
                raise
            raise StorageUnavailable(str(exc)) from exc
        self._taken.pop(resource_id, None)
        self._dispatching[resource_id] = operation.operation_id

    def run_dispatching(self) -> None:
        """Stage two: the transport boundary. The deny barrier is re-checked
        here, after the durable dispatching record and directly before the
        physical call (S-04; D-15)."""

        if not self._store.healthy():
            # The store closed after an undefined write: prepared calls
            # are withdrawn until an explicit reload and verification
            # (SEC-S04-01); their persisted records stay DISPATCHING and
            # become unknown on recovery.
            for resource_id in list(self._dispatching):
                operation = self._operations[self._dispatching.pop(resource_id)]
                self._mark_terminal(
                    operation,
                    OperationPhase.CANCELLED,
                    "storage_unavailable",
                )
            return
        for resource_id in list(self._dispatching.keys()):
            operation = self._operations[self._dispatching.pop(resource_id)]
            if operation.phase is not OperationPhase.DISPATCHING:
                continue
            if resource_id in self._deny_barrier:
                # A newer manual intent closed the window after this
                # record became durable: the older automatic call is not
                # sent (S-04; D-15).
                self._mark_terminal(
                    operation,
                    OperationPhase.CANCELLED,
                    "deny_barrier_active",
                )
                continue
            # The full permission is re-issued directly at the transport
            # boundary: TTL, facts, revisions and ownership may have
            # changed since the durable record (SEC-S04-05).
            decision = self._final_permit(operation)
            if isinstance(decision, PermitDenial):
                self._apply_permit_denial(operation, decision)
                continue
            profile = self._adapter.capabilities(resource_id)
            rule = profile.properties.get("on")
            timeout = (
                rule.confirmation_timeout_ms
                if rule
                else FALLBACK_CONFIRMATION_TIMEOUT_MS
            )
            started = self._clock.now_mono_ms()
            operation.dispatch_started_at_mono_ms = started
            operation.confirmation_deadline_mono_ms = started + timeout
            operation.pre_dispatch_sequence = self._max_sequence(resource_id)
            prepared = self._adapter.prepare(
                operation.target,
                self._bindings[resource_id],
                profile,
                operation.operation_id,
                decision.permit_id,
            )
            if isinstance(prepared, ValidationIssue):
                self._mark_terminal(
                    operation, OperationPhase.REJECTED, "invalid_profile"
                )
                continue
            outcome = self._adapter.dispatch_once(prepared, self._transport)
            if outcome.status is TransportStatus.ACCEPTED:
                operation.phase = OperationPhase.OBSERVING
            elif outcome.proven_not_sent:
                self._mark_terminal(operation, OperationPhase.FAILED, "not_sent")
            else:
                operation.phase = OperationPhase.UNKNOWN
                operation.reason_code = "transport_error_unknown_outcome"
                if operation.origin_automation and operation.episode_id:
                    self._episode_locks[
                        (resource_id, operation.episode_id)
                    ] = "transport_error_unknown_outcome"
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
        if observation.attribution is ObservationAttribution.MANUAL:
            # Proven manual intervention is processed before any
            # confirmation correlation: it closes the automatic rights
            # (SEC-FIRST-OWN-02) without touching manual operations
            # (SEC-FIRST-OWN-02-R1); a manual-origin operation may then
            # still be confirmed by its own report.
            self._apply_manual_evidence(resource_id)
        confirmation_key = (
            resource_id,
            observation.source_sequence,
            observation.received_at_mono_ms,
            observation.source_id,
            observation.source_epoch,
        )
        self._report_groups.setdefault(confirmation_key, []).append(
            observation
        )
        result = "recorded"
        if observation.attribution is ObservationAttribution.MANUAL:
            result = "manual_evidence"
        for operation_id in reversed(self._resource_ops[resource_id]):
            operation = self._operations[operation_id]
            if operation.phase not in (
                OperationPhase.OBSERVING,
                OperationPhase.UNKNOWN,
            ):
                continue
            if operation.dispatch_started_at_mono_ms is None:
                continue
            if (
                observation.attribution is ObservationAttribution.MANUAL
                and operation.origin_automation
            ):
                # A manually attributed report never resolves an automatic
                # operation, in any phase (SEC-FIRST-OWN-02-R2).
                continue
            outcome = self._try_confirm(operation, confirmation_key)
            if outcome is not None:
                result = outcome
                break
        # A confirmation already issued for this report group must not
        # survive a later contradiction inside the same group
        # (SEC-FIRST-CONF-02, both arrival orders).
        self._revalidate_confirm_group(confirmation_key)
        self._commit_state()
        return result

    def _apply_manual_evidence(self, resource_id: str) -> None:
        """A manually attributed device report voids automatic ownership:
        pending and in-flight *automatic* operations are withdrawn, the
        proof is revoked and the rights become visibly uncertain until a
        proper manual intake or a fresh recalculation resolves them. An
        already manual resource keeps its hold; its own operation is not
        revoked by the report of its own action (SEC-FIRST-OWN-02-R1)."""

        for operation_id in list(self._resource_ops[resource_id]):
            operation = self._operations[operation_id]
            if not operation.origin_automation:
                continue
            if operation.phase in (OperationPhase.QUEUED, OperationPhase.TAKEN):
                self._mark_terminal(
                    operation,
                    OperationPhase.CANCELLED,
                    REASON_MANUAL_EVIDENCE,
                )
            elif operation.phase in (
                OperationPhase.DISPATCHING,
                OperationPhase.OBSERVING,
            ):
                self._mark_terminal(
                    operation,
                    OperationPhase.SUPERSEDED_AFTER_DISPATCH,
                    REASON_MANUAL_EVIDENCE,
                )
        record = self.record(resource_id)
        if record.authority is not Authority.MANUAL:
            self._records[resource_id] = replace(
                record,
                authority=Authority.UNCERTAIN,
                automatic_scope=AutomaticScope.NONE,
                auto_proof=None,
                uncertainty_reason=REASON_MANUAL_EVIDENCE,
            )
        else:
            self._records[resource_id] = replace(record, auto_proof=None)

    def _try_confirm(
        self, operation: Operation, confirmation_key: tuple
    ) -> str | None:
        """Five confirmation conditions of S-03, all mandatory and checked
        for every member of the report group (SEC-FIRST-CONF-02): a mixed
        or contradictory group never confirms. Lateness is decided by the
        acceptance time against the deadline, not only by the phase
        (SEC-FIRST-CONF-01), and an episode resolved after its deadline is
        locked the same way as a timeout (SEC-FIRST-CONF-01-R1)."""

        resource_id = operation.resource_id
        profile = self._adapter.capabilities(resource_id)
        group = self._report_groups.get(confirmation_key, [])
        if not self._group_members_valid(operation, group):
            return None
        seen = {
            observation.property: observation.value for observation in group
        }
        required = requested_properties(operation.target)
        if not all(name in seen for name in required):
            return None
        for name, expected in required.items():
            if not _value_matches(
                seen[name], expected, profile.properties.get(name)
            ):
                return None
        sample_received = group[0].received_at_mono_ms
        deadline = operation.confirmation_deadline_mono_ms
        late = operation.phase is OperationPhase.UNKNOWN or (
            deadline is not None and sample_received > deadline
        )
        operation.phase = (
            OperationPhase.CONFIRMED_LATE
            if late
            else OperationPhase.CONFIRMED
        )
        operation.reason_code = (
            "confirmed_late_by_device_report"
            if late
            else "confirmed_by_device_report"
        )
        # Every confirmed operation is linked to its report group, so a
        # later contradiction revokes the result regardless of origin or
        # proof creation (SEC-FIRST-CONF-02-R3).
        self._confirm_keys[operation.operation_id] = confirmation_key
        record = self.record(resource_id)
        if late:
            # The outcome stayed undetermined through the whole deadline
            # window: the episode is locked exactly like a timeout,
            # whatever order the timeout tick and the report arrive in
            # (SEC-FIRST-CONF-01-R1).
            if operation.origin_automation and operation.episode_id:
                self._episode_locks[
                    (resource_id, operation.episode_id)
                ] = REASON_CONFIRMATION_TIMEOUT
            if (
                operation.origin_automation
                and not operation.target_requires_on
                and record.generation == operation.generation
                and record.auto_proof is not None
            ):
                # A resolved automatic OFF — even confirmed after its
                # deadline — invalidates the previous turn-on proof of the
                # same generation (SEC-FIRST-OWN-01); a newer authority
                # change would have moved the generation and is kept.
                self._records[resource_id] = replace(record, auto_proof=None)
        elif operation.origin_automation and record.manual is None and (
            record.generation == operation.generation
        ):
            if operation.target_requires_on:
                # Ownership proof exists only for a confirmed automatic
                # ON (K-07; SEC-FIRST-OWN-01).
                by_property = {
                    observation.property: observation
                    for observation in group
                }
                self._records[resource_id] = replace(
                    record,
                    auto_proof=_build_auto_proof(operation, by_property),
                )
            else:
                # A confirmed automatic OFF leaves no ownership: the
                # lamp no longer matches any previous turn-on proof.
                self._records[resource_id] = replace(
                    record, auto_proof=None
                )
            self._last_state_change[resource_id] = sample_received
        self._commit_state()
        return "confirmed_late" if late else "confirmed"

    def _group_members_valid(
        self, operation: Operation, group: list[Observation]
    ) -> bool:
        """S-03 member checks applied to every observation of the group;
        a single invalid member makes the whole group unusable. A manually
        attributed member never contributes to confirming an *automatic*
        operation — in any phase, even combined with later non-manual
        members of the same group (SEC-FIRST-OWN-02-R2). Confirmations of
        operations restored from another boot are not attempted: their
        monotonic boundary data belongs to a dead process (K-10)."""

        restored = operation.operation_id in self._restored_operation_ids
        if restored:
            # K-10: the monotonic boundary data of a dead process is not
            # comparable with new observations; a restored operation is
            # never confirmed across a restart.
            return False
        if operation.origin_automation:
            for observation in group:
                if (
                    observation.attribution
                    is ObservationAttribution.MANUAL
                ):
                    return False
        profile = self._adapter.capabilities(operation.resource_id)
        rule = profile.properties.get("on")
        confirmation_mode = (
            rule.confirmation if rule else "post_dispatch_report"
        )
        seen: dict[str, object] = {}
        for observation in group:
            if (
                confirmation_mode == "post_dispatch_report"
                and observation.evidence_kind is not Evidence.DEVICE_REPORT
            ):
                return False
            if observation.quality is not Quality.FRESH:
                return False
            if observation.binding_revision != operation.binding_revision:
                return False
            if operation.dispatch_started_at_mono_ms is None:
                return False
            if (
                observation.received_at_mono_ms
                < operation.dispatch_started_at_mono_ms
            ):
                return False
            if observation.source_sequence is None:
                return False
            if (
                operation.pre_dispatch_sequence is not None
                and observation.source_sequence
                <= operation.pre_dispatch_sequence
            ):
                return False
            if observation.property in seen and (
                seen[observation.property] != observation.value
            ):
                # Contradictory values inside one report group: ambiguous
                # feedback cannot confirm anything (S-03 condition 5).
                return False
            seen.setdefault(observation.property, observation.value)
        return True

    def _revalidate_confirm_group(self, confirmation_key: tuple) -> None:
        """If a confirmed result was issued from a report group that later
        becomes contradictory or invalid, the result is revoked for **any**
        confirmed operation of that group — automatic ON with proof,
        automatic OFF, manual or late confirmation (SEC-FIRST-CONF-02-R3).
        The proof is cleared only when it belongs to the revoked
        operation; a later ambiguous outcome blocks the operation's
        episode."""

        for operation_id, key in list(self._confirm_keys.items()):
            if key != confirmation_key:
                continue
            operation = self._operations.get(operation_id)
            if operation is None or operation.phase not in (
                OperationPhase.CONFIRMED,
                OperationPhase.CONFIRMED_LATE,
            ):
                continue
            group = self._report_groups.get(confirmation_key, [])
            if self._group_members_valid(operation, group):
                continue
            operation.phase = OperationPhase.UNKNOWN
            operation.reason_code = REASON_AMBIGUOUS_FEEDBACK_REVOKED
            self._confirm_keys.pop(operation_id, None)
            record = self.record(operation.resource_id)
            if (
                record.auto_proof is not None
                and record.auto_proof.operation_id == operation_id
            ):
                self._records[operation.resource_id] = replace(
                    record, auto_proof=None
                )
            if operation.origin_automation and operation.episode_id:
                self._episode_locks[
                    (operation.resource_id, operation.episode_id)
                ] = REASON_AMBIGUOUS_FEEDBACK_REVOKED

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
        """Manual intake in the S-04 order: barrier first, then one commit
        that durably contains the accepted rights AND the acceptance
        receipt, then publication and permitted execution (SEC-FIRST-IDEM-01:
        the accepted request_id is never lost to a later storage failure).
        A no_op acceptance still records the manual hold and bumps
        generation/revision (K-07)."""

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
        self._ensure_receipt_capacity(self._clock.now_utc_ms())
        record = self.record(resource_id)
        if record.control_revision != expected_control_revision:
            raise RevisionConflict(
                f"expected {expected_control_revision}, "
                f"actual {record.control_revision}"
            )
        # The no_op decision is taken against the world before anything is
        # cancelled: an already-sent operation with an undetermined outcome
        # forbids it (K-08).
        no_op_now = (
            not self._unresolved_sent_exists(resource_id)
            and self._fact_matches(resource_id, target)
            and self._facts_fresh(resource_id, target, require_all=True)
        )
        # Capacity is judged by the FUTURE composition (SEC-S04-08
        # residual): the manual acceptance frees this resource's own
        # pending and in-flight slots and adds one unresolved operation
        # only for a dispatched (non-no_op) acceptance.
        unresolved_total = self._count_unresolved_operations()
        freed = self._pending_slots_of(
            resource_id,
            (
                OperationPhase.QUEUED,
                OperationPhase.TAKEN,
                OperationPhase.DISPATCHING,
                OperationPhase.OBSERVING,
            ),
        )
        added = 0 if no_op_now else 1
        self._ensure_operation_capacity(unresolved_total - freed + added)
        # 2. deny-only barrier closes automatic dispatch immediately.
        self._deny_barrier.add(resource_id)
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
        decision_id = self._next_id("dec")
        operation = Operation(
            operation_id=self._next_id("op"),
            resource_id=resource_id,
            decision_id=decision_id,
            origin=Origin.MANUAL,
            target=target,
            generation=next_record.generation,
            control_revision=next_record.control_revision,
            config_revision=self._config_revisions.get(resource_id, 0),
            binding_revision=spec.binding_revision,
            scope_revisions={
                group: next_scope[group]
                for group in spec.manual_scopes
                if group in next_scope
            },
            created_at_mono_ms=self._clock.now_mono_ms(),
            phase=(
                OperationPhase.NO_OP if no_op_now else OperationPhase.QUEUED
            ),
            reason_code=REASON_NO_OP if no_op_now else None,
            request_id=request_id,
            expires_at_mono_ms=None,
        )
        receipt = ManualReceipt(
            request_id=request_id,
            status=(
                ManualStatus.NO_OP if no_op_now else ManualStatus.ACCEPTED
            ),
            reason_code=REASON_NO_OP if no_op_now else None,
            operation_id=operation.operation_id,
            control_revision=next_record.control_revision,
            generation=next_record.generation,
        )
        # 4. One commit of the whole acceptance (S-04): rights, groups,
        # the accepted operation and its receipt are durable together, so
        # a later storage failure can never lose the accepted request.
        image = self._build_image()
        image["records"] = {**image["records"], resource_id: next_record}
        image["operations"] = {
            **image["operations"],
            operation.operation_id: operation,
        }
        image["receipts"] = {**image["receipts"], request_id: receipt}
        image["receipt_fingerprints"] = {
            **image["receipt_fingerprints"],
            request_id: list(fingerprint),
        }
        image["receipt_times"] = {
            **image["receipt_times"],
            request_id: now_utc,
        }
        image["scope_revisions"] = next_scope
        try:
            self._store.commit(self._store.commit_revision, image)
        except StorageUnavailable:
            # The internal auto ban stays; the request is not accepted.
            raise
        # 5. accepted: publish the whole accepted state (receipt included)
        # before the registration commit, so no follow-up commit can
        # overwrite the durable receipt with an older picture (S-04).
        self._records[resource_id] = next_record
        self._scope_revisions.clear()
        self._scope_revisions.update(next_scope)
        self._receipts[request_id] = receipt
        self._receipt_fingerprints[request_id] = fingerprint
        self._receipt_times[request_id] = now_utc
        # The acceptance image already contains the operation; publishing
        # it in memory must not fire a second, diverging commit.
        self._operations[operation.operation_id] = operation
        self._resource_ops[resource_id].append(operation.operation_id)
        self._deny_barrier.discard(resource_id)
        explanation_reason = REASON_NO_OP if no_op_now else (
            "manual_intent_accepted"
        )
        self._explanations[resource_id] = DecisionExplanation(
            decision_id=decision_id,
            resource_id=resource_id,
            accepted=(),
            withdrawn=(),
            rejected=(),
            target_result=target,
            result_reason=explanation_reason,
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
            operation_id=operation.operation_id,
            next_condition=None,
            next_check_at_mono_ms=None,
            evidence_refs=(),
            rule_versions={
                "config_revision": self._config_revisions.get(resource_id, 0),
                "control_revision": next_record.control_revision,
                "generation": next_record.generation,
            },
        )
        if not no_op_now:
            # Manual execution runs immediately: it does not wait behind an
            # observing foreign device, and the transport call has returned.
            decision = self._final_permit(operation)
            if isinstance(decision, PermitDenial):
                self._apply_permit_denial(operation, decision)
            else:
                self._dispatch_operation(operation, decision)
                self.run_dispatching()
        return receipt

    # ------------------------------------------------------------------
    # store capacity (S-05): exhausted capacity refuses new accepted
    # work instead of silently dropping protected records.

    def _ensure_receipt_capacity(self, now_utc_ms: int) -> None:
        self._prune_expired_receipts(now_utc_ms)
        active_receipts = sum(
            1
            for accepted_at in self._receipt_times.values()
            if now_utc_ms - accepted_at < RECEIPT_WINDOW_MS
        )
        if active_receipts >= MAX_RECEIPTS_IN_WINDOW:
            raise CapacityExceeded("receipt_window_full")

    def _count_unresolved_operations(self) -> int:
        return sum(
            1
            for operation in self._operations.values()
            if operation.phase
            in (
                OperationPhase.QUEUED,
                OperationPhase.TAKEN,
                OperationPhase.DISPATCHING,
                OperationPhase.OBSERVING,
                OperationPhase.UNKNOWN,
            )
        )

    def _ensure_operation_capacity(self, future_unresolved: int) -> None:
        if future_unresolved > MAX_UNRESOLVED_OPERATIONS:
            raise CapacityExceeded("unresolved_operations_full")

    def _pending_slots_of(
        self, resource_id: str, phases: tuple[OperationPhase, ...]
    ) -> int:
        return sum(
            1
            for operation_id in self._resource_ops.get(resource_id, ())
            if self._operations[operation_id].phase in phases
        )

    def _prune_expired_receipts(self, now_utc_ms: int) -> None:
        if not self._clock.wall_clock_trust().trusted:
            # Untrusted time never deletes protection history (S-05,
            # SEC-S04-07).
            return
        expired = [
            request_id
            for request_id, accepted_at in self._receipt_times.items()
            if now_utc_ms - accepted_at >= RECEIPT_WINDOW_MS
        ]
        for request_id in expired:
            operation_id = (
                self._receipts[request_id].operation_id
                if request_id in self._receipts
                else None
            )
            operation = (
                self._operations.get(operation_id)
                if operation_id is not None
                else None
            )
            if operation is not None and (
                not operation.is_terminal()
                or operation.phase is OperationPhase.UNKNOWN
            ):
                # Active and unknown outcomes keep their replay keys even
                # after 24 hours; UNKNOWN is terminal in the phase enum
                # but its outcome is not resolved (S-05, SEC-S04-09).
                continue
            self._receipts.pop(request_id, None)
            self._receipt_fingerprints.pop(request_id, None)
            self._receipt_times.pop(request_id, None)

    # restore after restart (K-10, S-04/S-05)

    @classmethod
    def restore(
        cls,
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
        clock_trust: ClockTrust,
    ) -> "CoreCommandService":
        """Build a fresh service from the durable image of the store and
        apply the K-10 recovery: pending work is cancelled, dispatched
        work without a durable outcome becomes unknown. An untrusted
        clock never extends rights (S-05)."""

        loaded = store.load() or {}
        service = cls(
            clock=clock,
            store=store,
            adapter=adapter,
            transport=transport,
            specs=specs,
            bindings=bindings,
            modules=modules,
            stability_profiles=stability_profiles,
            config_revisions=loaded.get("config_revisions")
            or dict(config_revisions),
            scope_revisions=loaded.get("scope_revisions")
            or dict(scope_revisions),
            episode_of_proposal=episode_of_proposal,
            id_factory=id_factory,
            initial_records=dict(loaded.get("records", {})),
        )
        if loaded:
            service._operations = dict(loaded.get("operations", {}))
            service._image_boot_id = loaded.get("boot_id")
            # Provenance: every operation of the loaded image originates
            # from the previous process, plus the persisted restored
            # markers; this survives later re-commits (SEC-S04-11).
            service._restored_operation_ids = set(
                loaded.get("restored_operation_ids", [])
            ) | set(service._operations)
            # The id floor is computed from the loaded operations, not
            # from an empty map (SEC-S04-10).
            max_suffix = 0
            for identifier in list(service._operations) + list(
                loaded.get("receipts", {})
            ):
                for part in str(identifier).split("-"):
                    if part.isdigit():
                        max_suffix = max(max_suffix, int(part))
            service._id_floor = max_suffix
            for operation_id, operation in service._operations.items():
                service._resource_ops.setdefault(
                    operation.resource_id, []
                ).append(operation_id)
            service._receipts = dict(loaded.get("receipts", {}))
            service._receipt_times = dict(loaded.get("receipt_times", {}))
            service._receipt_fingerprints = {
                request_id: tuple(fingerprint)
                for request_id, fingerprint in loaded.get(
                    "receipt_fingerprints", {}
                ).items()
            }
            service._confirm_keys = {
                operation_id: tuple(key)
                for operation_id, key in loaded.get("confirm_keys", {}).items()
            }
            service._episode_locks = dict(loaded.get("episode_locks", {}))
            for operation in list(service._operations.values()):
                decision = classify_restored_operation(operation.phase)
                if decision.phase is not operation.phase:
                    operation.phase = decision.phase
                    operation.reason_code = decision.reason_code
                    if (
                        operation.origin_automation
                        and operation.episode_id
                        and decision.reason_code == REASON_RESTART_UNCERTAIN
                    ):
                        service._episode_locks[
                            (operation.resource_id, operation.episode_id)
                        ] = REASON_RESTART_UNCERTAIN
            if not clock_trust.trusted:
                for resource_id, record in list(service._records.items()):
                    if (
                        record.authority is Authority.AUTOMATIC_ELIGIBLE
                        and record.manual is None
                    ):
                        service._records[resource_id] = replace(
                            record,
                            uncertainty_reason=(
                                "clock_uncertain"
                                if record.uncertainty_reason is None
                                else record.uncertainty_reason
                            ),
                        )
            for resource_id, record in list(service._records.items()):
                if (
                    record.auto_proof is not None
                    and record.uncertainty_reason is None
                ):
                    # Restored ownership cannot prove observation
                    # continuity across the restart: the proof stays as
                    # history, but automatic actions stay closed until a
                    # fresh trusted recalculation re-establishes them
                    # (SEC-S04-03). Manual commands are not affected.
                    service._records[resource_id] = replace(
                        record,
                        uncertainty_reason="restart_continuity_unproven",
                    )
        # Persist the recovery outcomes so the visible state matches the
        # durable truth before any new work is accepted.
        service._commit_state()
        return service

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


def _value_matches(
    actual: object, expected: object, rule: PropertyRule | None
) -> bool:
    """Strict typed comparison (SEC-FIRST-VAL-01): a bool expectation
    matches only a real bool, numeric expectations only finite real
    numbers within the passport tolerance. Strings and other foreign
    types never confirm anything, and NaN never compares as equal."""

    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(actual, bool) or not isinstance(
        actual, (int, float, Decimal)
    ):
        return False
    try:
        actual_number = Decimal(str(actual))
        expected_number = Decimal(str(expected))
    except Exception:
        return False
    if not actual_number.is_finite() or not expected_number.is_finite():
        return False
    if rule is not None and rule.tolerance is not None:
        try:
            # The subtraction itself can overflow the decimal context for
            # huge finite values; that is a mismatch, not a crash
            # (SEC-FIRST-VAL-01-R2).
            return abs(actual_number - expected_number) <= rule.tolerance
        except (ArithmeticError, ValueError):
            return False
    return actual == expected


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
