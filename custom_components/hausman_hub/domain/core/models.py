"""Pure shared types of the Hausman core (K-02, K-03, K-05, K-07, S-02).

Nothing in this package may import Home Assistant, application or adapter
code, read the disk/network or read the system clock. All time values are
explicit integer inputs so the same decision is reproducible on virtual
clocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
from typing import Mapping, Protocol, runtime_checkable

_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_MAX_REVISION = 2**63 - 1


class CoreTypeError(TypeError):
    """A value violates the strict core DTO contract (K-02)."""


def require_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise CoreTypeError(f"{field_name} must be an Id: {value!r}")
    return value


def require_int(value: object, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoreTypeError(f"{field_name} must be int, not bool: {value!r}")
    if value < minimum or value > _MAX_REVISION:
        raise CoreTypeError(f"{field_name} out of range: {value!r}")
    return value


def require_optional_int(
    value: object, field_name: str, minimum: int = 0
) -> int | None:
    return None if value is None else require_int(value, field_name, minimum)


def require_decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise CoreTypeError(f"{field_name} must not be bool")
    try:
        number = Decimal(value)  # type: ignore[arg-type]
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CoreTypeError(f"{field_name} is not a finite number: {value!r}") from exc
    if not number.is_finite():
        raise CoreTypeError(f"{field_name} is not finite: {value!r}")
    return number


def require_optional_decimal(
    value: object, field_name: str
) -> Decimal | None:
    return None if value is None else require_decimal(value, field_name)


def require_enum(value: object, enum_type: type, field_name: str):
    if not isinstance(value, enum_type):
        raise CoreTypeError(f"{field_name} must be {enum_type.__name__}: {value!r}")
    return value


class Quality(Enum):
    """Observation quality (K-02)."""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class Evidence(Enum):
    """Where a fact comes from (K-02)."""

    DEVICE_REPORT = "device_report"
    VERIFIED_READBACK = "verified_readback"
    ADAPTER_STATE = "adapter_state"
    OPTIMISTIC = "optimistic"
    RESTORED = "restored"


class ModuleMode(Enum):
    """Deployment mode of a module or of the core itself (K-04)."""

    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"
    ERROR = "error"


class Authority(Enum):
    """Current control authority of a resource (K-02, K-07)."""

    AUTOMATIC_ELIGIBLE = "automatic_eligible"
    MANUAL = "manual"
    UNCERTAIN = "uncertain"
    DISABLED = "disabled"


class AutomaticScope(Enum):
    """What automation may still do while a hold is active (K-02, L-03)."""

    NONE = "none"
    SAME_SOURCE_BRIGHTNESS_DOWN = "same_source_brightness_down"
    NORMAL = "normal"


class Origin(Enum):
    """Who initiated an operation; assigned by the server only (K-02)."""

    MANUAL = "manual"
    AUTOMATION = "automation"
    PROTECTION = "protection"


class ObservationAttribution(Enum):
    """Attribution of an incoming observation (S-02)."""

    AUTOMATION = "automation"
    MANUAL = "manual"
    UNKNOWN = "unknown"


class ProposalKind(Enum):
    """Kinds of needs a module may propose (K-05)."""

    REQUIRE_ON = "require_on"
    TARGET = "target"


class ArbitrationMode(Enum):
    """Resource demand policy (K-05)."""

    ANY_ON = "any_on"
    RANKED_TARGET = "ranked_target"


# Named explanation/decision reason codes (K-11). Tests match on these.
REASON_MANUAL_PRIORITY = "manual_priority"
REASON_AUTOMATION_DISABLED = "automation_disabled"
REASON_UNCERTAIN_RIGHTS = "uncertain_rights"
REASON_TTL_EXPIRED = "ttl_expired"
REASON_GENERATION_CHANGED = "generation_changed"
REASON_BINDING_CHANGED = "binding_changed"
REASON_CONFIG_CHANGED = "config_changed"
REASON_SCOPE_CHANGED = "scope_changed"
REASON_CORE_NOT_ACTIVE = "core_not_active"
REASON_REPEAT_BLOCKED_UNKNOWN = "repeat_blocked_unknown_outcome"
REASON_FACTS_STALE = "required_facts_stale"
REASON_STABILITY_HOLD = "stability_candidate_hold"
REASON_MIN_STATE_TIME = "stability_min_state_time"
REASON_OWNERSHIP_UNPROVEN = "ownership_unproven"
REASON_NO_DEMAND = "no_demand"
REASON_NO_OP = "no_op"
REASON_SUPERSEDED = "superseded"
REASON_MANUAL_OVERRIDE = "manual_override"
REASON_CONFIRMATION_TIMEOUT = "confirmation_timeout"
REASON_STORAGE_UNAVAILABLE = "storage_unavailable"
REASON_TARGET_UNCHANGED = "target_unchanged"

MIN_POLICY_PRIORITY = 0
MAX_POLICY_PRIORITY = 100
MAX_PROPOSALS_PER_RESOURCE = 16


@dataclass(frozen=True)
class SwitchStateTarget:
    """Typed target ``switch_state(on)`` (K-05)."""

    on: bool

    def __post_init__(self) -> None:
        if not isinstance(self.on, bool):
            raise CoreTypeError("SwitchStateTarget.on must be bool")


@dataclass(frozen=True)
class LightStateTarget:
    """Typed target ``light_state(on, brightness_pct, color_temperature_k)``.

    ``None`` means "leave the property unchanged", never "unknown". An OFF
    target must carry ``None`` brightness and colour (K-05).
    """

    on: bool
    brightness_pct: Decimal | None = None
    color_temperature_k: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.on, bool):
            raise CoreTypeError("LightStateTarget.on must be bool")
        brightness = require_optional_decimal(self.brightness_pct, "brightness_pct")
        object.__setattr__(self, "brightness_pct", brightness)
        cct = require_optional_int(self.color_temperature_k, "color_temperature_k")
        object.__setattr__(self, "color_temperature_k", cct)
        if not self.on and (brightness is not None or cct is not None):
            raise CoreTypeError("OFF light target must carry null brightness/CCT")


Target = LightStateTarget | SwitchStateTarget


def requested_properties(target: Target) -> Mapping[str, object]:
    """Properties a confirmation must prove for this target (S-03)."""
    if isinstance(target, SwitchStateTarget):
        return {"on": target.on}
    if isinstance(target, LightStateTarget):
        required: dict[str, object] = {"on": target.on}
        if target.on and target.brightness_pct is not None:
            required["brightness_pct"] = target.brightness_pct
        if target.on and target.color_temperature_k is not None:
            required["color_temperature_k"] = target.color_temperature_k
        return required
    raise CoreTypeError(f"unsupported target: {target!r}")


@dataclass(frozen=True)
class ResourceSpec:
    """Physical resource and its links (K-03)."""

    resource_id: str
    owner_module: str
    adapter_id: str
    capability_profile_id: str
    binding_revision: int
    aliases: tuple[str, ...] = ()
    power_dependencies: tuple[str, ...] = ()
    manual_scopes: tuple[str, ...] = ()
    arbitration: ArbitrationMode = ArbitrationMode.RANKED_TARGET
    idle_target: Target | None = None
    stability_profile_id: str = "default"

    def __post_init__(self) -> None:
        require_id(self.resource_id, "resource_id")
        require_id(self.owner_module, "owner_module")
        require_id(self.adapter_id, "adapter_id")
        require_id(self.capability_profile_id, "capability_profile_id")
        require_int(self.binding_revision, "binding_revision")
        for alias in self.aliases:
            require_id(alias, "alias")
        for dependency in self.power_dependencies:
            require_id(dependency, "power_dependency")
        for scope in self.manual_scopes:
            require_id(scope, "manual_scope")
        require_enum(self.arbitration, ArbitrationMode, "arbitration")
        if self.idle_target is not None and not isinstance(
            self.idle_target, (LightStateTarget, SwitchStateTarget)
        ):
            raise CoreTypeError("idle_target has an unsupported type")


@dataclass(frozen=True)
class ManualHold:
    """Active manual priority record (K-07)."""

    action_id: str
    action: str
    source: str
    proven_at_utc_ms: int
    priority_until_utc_ms: int
    minimum_off_until_utc_ms: int | None
    policy_revision: int

    def __post_init__(self) -> None:
        require_id(self.action_id, "action_id")
        require_id(self.action, "action")
        require_id(self.source, "source")
        require_int(self.proven_at_utc_ms, "proven_at_utc_ms")
        require_int(self.priority_until_utc_ms, "priority_until_utc_ms")
        require_optional_int(
            self.minimum_off_until_utc_ms, "minimum_off_until_utc_ms"
        )
        require_int(self.policy_revision, "policy_revision")

    def active_at_utc(self, now_utc_ms: int) -> bool:
        return now_utc_ms < self.priority_until_utc_ms


@dataclass(frozen=True)
class ObservationSnapshot:
    """Minimal confirmed property values kept inside auto_proof (K-07)."""

    observation_id: str
    values: Mapping[str, str]
    evidence: Evidence

    def __post_init__(self) -> None:
        require_id(self.observation_id, "observation_id")
        require_enum(self.evidence, Evidence, "evidence")


@dataclass(frozen=True)
class AutoProof:
    """Proof that automation itself turned the resource on (K-07)."""

    operation_id: str
    observation_id: str
    confirmed_at_utc_ms: int
    generation: int
    binding_revision: int
    confirmed_target: Target
    confirmed_observation: ObservationSnapshot
    source_evidence: Evidence

    def __post_init__(self) -> None:
        require_id(self.operation_id, "operation_id")
        require_id(self.observation_id, "observation_id")
        require_int(self.confirmed_at_utc_ms, "confirmed_at_utc_ms")
        require_int(self.generation, "generation")
        require_int(self.binding_revision, "binding_revision")
        require_enum(self.source_evidence, Evidence, "source_evidence")


@dataclass(frozen=True)
class ControlRecord:
    """Single authority state per physical resource (K-07)."""

    resource_id: str
    control_revision: int
    generation: int
    binding_revision: int
    authority: Authority
    automatic_scope: AutomaticScope
    manual: ManualHold | None = None
    auto_proof: AutoProof | None = None
    uncertainty_reason: str | None = None

    def __post_init__(self) -> None:
        require_id(self.resource_id, "resource_id")
        require_int(self.control_revision, "control_revision")
        require_int(self.generation, "generation")
        require_int(self.binding_revision, "binding_revision")
        require_enum(self.authority, Authority, "authority")
        require_enum(self.automatic_scope, AutomaticScope, "automatic_scope")
        if self.uncertainty_reason is not None and not isinstance(
            self.uncertainty_reason, str
        ):
            raise CoreTypeError("uncertainty_reason must be str or null")

    def manual_active_at_utc(self, now_utc_ms: int) -> bool:
        return self.manual is not None and self.manual.active_at_utc(now_utc_ms)


@dataclass(frozen=True)
class Proposal:
    """Typed need of one module for one resource (K-05)."""

    proposal_id: str
    module_id: str
    resource_id: str
    kind: ProposalKind
    target: Target
    policy_priority: int
    created_at_mono_ms: int
    valid_until_mono_ms: int
    input_revision: int
    config_revision: int
    reason_code: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_id(self.proposal_id, "proposal_id")
        require_id(self.module_id, "module_id")
        require_id(self.resource_id, "resource_id")
        require_enum(self.kind, ProposalKind, "kind")
        if not isinstance(self.target, (LightStateTarget, SwitchStateTarget)):
            raise CoreTypeError("Proposal.target has an unsupported type")
        require_int(self.policy_priority, "policy_priority")
        if not (
            MIN_POLICY_PRIORITY
            <= self.policy_priority
            <= MAX_POLICY_PRIORITY
        ):
            raise CoreTypeError(
                f"policy_priority out of range: {self.policy_priority!r}"
            )
        require_int(self.created_at_mono_ms, "created_at_mono_ms")
        require_int(self.valid_until_mono_ms, "valid_until_mono_ms")
        require_int(self.input_revision, "input_revision")
        require_int(self.config_revision, "config_revision")
        require_id(self.reason_code, "reason_code")

    def expired_at(self, now_mono_ms: int) -> bool:
        """``now == valid_until`` is already invalid (K-05 step 2)."""
        return now_mono_ms >= self.valid_until_mono_ms


@dataclass(frozen=True)
class Constraint:
    """Typed restriction on actions for a resource or group (K-05)."""

    constraint_id: str
    source: str
    resource_id: str | None
    group_id: str | None
    forbidden_action: str | None
    reason_code: str
    config_revision: int
    review_at_mono_ms: int | None = None

    def __post_init__(self) -> None:
        require_id(self.constraint_id, "constraint_id")
        require_id(self.source, "source")
        require_id(self.reason_code, "reason_code")
        require_int(self.config_revision, "config_revision")
        require_optional_int(self.review_at_mono_ms, "review_at_mono_ms")


@dataclass(frozen=True)
class Observation:
    """Normalized observation (S-02). One property per instance."""

    observation_id: str
    resource_id: str
    property: str
    value: object
    unit: str
    received_at_utc_ms: int
    received_at_mono_ms: int
    boot_id: str
    source_at_utc_ms: int | None
    source_sequence: int | None
    source_id: str
    source_epoch: str
    quality: Quality
    evidence_kind: Evidence
    binding_revision: int
    observation_revision: int
    related_operation_id: str | None
    attribution: ObservationAttribution

    def __post_init__(self) -> None:
        require_id(self.observation_id, "observation_id")
        require_id(self.resource_id, "resource_id")
        require_id(self.property, "property")
        require_id(self.unit, "unit")
        require_int(self.received_at_utc_ms, "received_at_utc_ms")
        require_int(self.received_at_mono_ms, "received_at_mono_ms")
        require_id(self.boot_id, "boot_id")
        require_optional_int(self.source_at_utc_ms, "source_at_utc_ms")
        require_optional_int(self.source_sequence, "source_sequence")
        require_id(self.source_id, "source_id")
        require_id(self.source_epoch, "source_epoch")
        require_enum(self.quality, Quality, "quality")
        require_enum(self.evidence_kind, Evidence, "evidence_kind")
        require_int(self.binding_revision, "binding_revision")
        require_int(self.observation_revision, "observation_revision")
        if self.related_operation_id is not None:
            require_id(self.related_operation_id, "related_operation_id")
        require_enum(self.attribution, ObservationAttribution, "attribution")


@dataclass(frozen=True)
class StabilityClass:
    """Classification of one state interval (K-06)."""

    property: str
    kind: str  # "comfort" | "equipment"

    def __post_init__(self) -> None:
        require_id(self.property, "property")
        if self.kind not in ("comfort", "equipment"):
            raise CoreTypeError("StabilityClass.kind must be comfort|equipment")


@dataclass(frozen=True)
class StabilityProfile:
    """Stability limits; zero means an explicitly confirmed absence (K-06)."""

    profile_id: str
    candidate_hold_ms: int
    min_on_ms: int
    min_off_ms: int
    min_mode_change_ms: int
    classifications: tuple[StabilityClass, ...] = ()

    def __post_init__(self) -> None:
        require_id(self.profile_id, "profile_id")
        for name in (
            "candidate_hold_ms",
            "min_on_ms",
            "min_off_ms",
            "min_mode_change_ms",
        ):
            require_int(getattr(self, name), name)


class OperationPhase(Enum):
    """Lifecycle of one operation (K-08)."""

    ACCEPTED = "accepted"
    QUEUED = "queued"
    TAKEN = "taken"
    DISPATCHING = "dispatching"
    OBSERVING = "observing"
    CONFIRMED = "confirmed"
    CONFIRMED_LATE = "confirmed_late"
    UNKNOWN = "unknown"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    SUPERSEDED_AFTER_DISPATCH = "superseded_after_dispatch"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    NO_OP = "no_op"


TERMINAL_PHASES = frozenset(
    {
        OperationPhase.CONFIRMED,
        OperationPhase.CONFIRMED_LATE,
        OperationPhase.UNKNOWN,
        OperationPhase.FAILED,
        OperationPhase.SUPERSEDED,
        OperationPhase.SUPERSEDED_AFTER_DISPATCH,
        OperationPhase.CANCELLED,
        OperationPhase.EXPIRED,
        OperationPhase.REJECTED,
        OperationPhase.NO_OP,
    }
)

# Snapshot of the manual-scope group revisions compared before dispatch
# (K-07 scope_revision, K-08 final check).
ScopeSnapshot = Mapping[str, int]


@dataclass
class Operation:
    """One dispatched unit of state control per resource (K-08).

    Identity fields (ids, origin, target, revision snapshot) are immutable
    after acceptance; only the phase, timings and evidence evolve.
    """

    operation_id: str
    resource_id: str
    decision_id: str
    origin: Origin
    target: Target
    generation: int
    control_revision: int
    config_revision: int
    binding_revision: int
    scope_revisions: ScopeSnapshot
    created_at_mono_ms: int
    phase: OperationPhase
    request_id: str | None = None
    module_id: str | None = None
    episode_id: str | None = None
    expires_at_mono_ms: int | None = None
    dispatch_started_at_mono_ms: int | None = None
    confirmation_deadline_mono_ms: int | None = None
    pre_dispatch_sequence: int | None = None
    reason_code: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_id(self.operation_id, "operation_id")
        require_id(self.resource_id, "resource_id")
        require_id(self.decision_id, "decision_id")
        require_enum(self.origin, Origin, "origin")
        if not isinstance(self.target, (LightStateTarget, SwitchStateTarget)):
            raise CoreTypeError("Operation.target has an unsupported type")
        require_int(self.generation, "generation")
        require_int(self.control_revision, "control_revision")
        require_int(self.config_revision, "config_revision")
        require_int(self.binding_revision, "binding_revision")
        require_int(self.created_at_mono_ms, "created_at_mono_ms")
        require_enum(self.phase, OperationPhase, "phase")
        require_optional_int(self.expires_at_mono_ms, "expires_at_mono_ms")
        require_optional_int(
            self.dispatch_started_at_mono_ms, "dispatch_started_at_mono_ms"
        )
        require_optional_int(
            self.confirmation_deadline_mono_ms, "confirmation_deadline_mono_ms"
        )
        require_optional_int(self.pre_dispatch_sequence, "pre_dispatch_sequence")

    @property
    def origin_automation(self) -> bool:
        return self.origin is Origin.AUTOMATION

    @property
    def target_requires_on(self) -> bool:
        on = getattr(self.target, "on", None)
        return on is True

    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES


@dataclass(frozen=True)
class RejectedNeed:
    """A need that did not survive arbitration, with its reason (K-11)."""

    module_id: str
    proposal_id: str
    reason_code: str


@dataclass(frozen=True)
class FactView:
    """One confirmed fact with quality and age for explanations/UI (K-11)."""

    property: str
    value: object
    quality: Quality
    evidence: Evidence
    observed_at_mono_ms: int


@dataclass(frozen=True)
class DecisionExplanation:
    """Server-built explanation of one calculation (K-11)."""

    decision_id: str
    resource_id: str
    accepted: tuple[Proposal, ...]
    withdrawn: tuple[Proposal, ...]
    rejected: tuple[RejectedNeed, ...]
    target_result: Target | None
    result_reason: str | None
    current_facts: tuple[FactView, ...]
    operation_id: str | None
    next_condition: str | None
    next_check_at_mono_ms: int | None
    evidence_refs: tuple[str, ...]
    rule_versions: Mapping[str, int]


class ValidationIssue:
    """A concrete rejection produced before any external call (S-03)."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = require_id(code, "code")
        self.detail = detail


@runtime_checkable
class ContourModule(Protocol):
    """Pure module contract (K-04). Modules never call HA or the queue."""

    def validate_config(self, config: Mapping[str, object], catalog: object):
        ...

    def evaluate(self, frame: object, previous_memory: object):
        ...


@dataclass(frozen=True)
class ModuleManifest:
    """Server-side manifest of a trusted built-in module (K-04)."""

    module_id: str
    api_version: int
    config_schema_version: int
    resource_roles: tuple[str, ...]
    allowed_proposal_kinds: tuple[ProposalKind, ...]
    observation_subscriptions: tuple[str, ...]
    policy_priority: int
    allowed_constraint_kinds: tuple[str, ...] = ()
    max_proposals_per_resource: int = MAX_PROPOSALS_PER_RESOURCE

    def __post_init__(self) -> None:
        require_id(self.module_id, "module_id")
        if self.api_version != 1:
            raise CoreTypeError("api_version must be 1 for v1 modules")
        require_int(self.config_schema_version, "config_schema_version")
        for role in self.resource_roles:
            require_id(role, "resource_role")
        for kind in self.allowed_proposal_kinds:
            require_enum(kind, ProposalKind, "allowed_proposal_kind")
        for subscription in self.observation_subscriptions:
            require_id(subscription, "observation_subscription")
        if not (
            MIN_POLICY_PRIORITY
            <= self.policy_priority
            <= MAX_POLICY_PRIORITY
        ):
            raise CoreTypeError("manifest policy_priority out of range")
        require_int(self.max_proposals_per_resource, "max_proposals_per_resource")


@dataclass(frozen=True)
class InputFrame:
    """Input of one module evaluation (K-04)."""

    now_utc_ms: int
    now_mono_ms: int
    boot_id: str
    event_id: str
    config_revision: int
    observations: tuple[Observation, ...]
    resource_controls: tuple[ControlRecord, ...]
    related_scope_revisions: Mapping[str, int]
    inbound_needs: tuple[Proposal, ...]

    def __post_init__(self) -> None:
        require_int(self.now_utc_ms, "now_utc_ms")
        require_int(self.now_mono_ms, "now_mono_ms")
        require_id(self.boot_id, "boot_id")
        require_id(self.event_id, "event_id")
        require_int(self.config_revision, "config_revision")


@dataclass(frozen=True)
class Evaluation:
    """Whole replacement of one module's proposals for its scope (K-04)."""

    module_id: str
    config_revision: int
    input_revision: int
    proposals: tuple[Proposal, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    next_wakeup_mono_ms: int | None = None
    next_memory: object = None
    explanation: str | None = None

    def __post_init__(self) -> None:
        require_id(self.module_id, "module_id")
        require_int(self.config_revision, "config_revision")
        require_int(self.input_revision, "input_revision")
        for proposal in self.proposals:
            if proposal.module_id != self.module_id:
                raise CoreTypeError(
                    "Evaluation carries a proposal of another module"
                )
        require_optional_int(self.next_wakeup_mono_ms, "next_wakeup_mono_ms")
