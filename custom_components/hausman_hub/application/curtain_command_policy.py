"""Server-owned planning policy for all Hausman curtain commands."""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Callable, Mapping

from ..domain.scenario_controls import (
    ScenarioControlDocument,
    ScenarioControlPolicy,
)


LIVING_CURTAIN_TARGET = "entity_8746cfd7f6f7103d"
KITCHEN_CURTAIN_TARGET = "entity_2da2065add6e2168"
ALICE_CURTAIN_TARGET = "entity_1e0b476b7d082cc0"
OFFICE_CURTAIN_TARGET = "entity_9164132c7692d6f5"

_CURTAIN_ENTITY_IDS = MappingProxyType(
    {
        LIVING_CURTAIN_TARGET: "cover.shtory_gostinaia",
        KITCHEN_CURTAIN_TARGET: "cover.0xa4c1385a4bcce3d6",
        ALICE_CURTAIN_TARGET: "cover.0xa4c138b23cb850b4",
        OFFICE_CURTAIN_TARGET: "cover.0xa4c1381b3fb1c985",
    }
)

_CURTAIN_TARGETS = frozenset(
    {
        LIVING_CURTAIN_TARGET,
        KITCHEN_CURTAIN_TARGET,
        ALICE_CURTAIN_TARGET,
        OFFICE_CURTAIN_TARGET,
    }
)
_SCALE_DEPENDENT_TARGETS = frozenset(
    {KITCHEN_CURTAIN_TARGET, OFFICE_CURTAIN_TARGET}
)
_ACTION_SERVICES = {
    "open_cover": "open_cover",
    "close_cover": "close_cover",
    "stop_cover": "stop_cover",
    "set_position": "set_cover_position",
}
_UNAVAILABLE_STATES = frozenset({"unknown", "unavailable"})
_STOP_POLICY_REVISION = "curtain-safe-stop.v1"
CURTAIN_POSITION_PROVENANCE_UNVERIFIED = "unverified_optimistic"
CURTAIN_POSITION_PROVENANCE_VERIFIED = "verified_device_report"


class CurtainPolicyError(ValueError):
    """Reject a curtain command before any physical dispatch."""

    def __init__(self, code: str, *, skipped: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.skipped = skipped


@dataclass(frozen=True, slots=True)
class CurtainTargetPolicy:
    """Immutable server-side limit for one exact catalog target."""

    target_id: str
    maximum_position: int
    scale_confirmed: bool = False
    generation: int = 0


@dataclass(frozen=True, slots=True)
class CurtainPositionEvidence:
    """Exact provenance snapshot bound into a dispatch plan."""

    provenance: str
    identity_digest: str


class CurtainPositionEvidencePolicy:
    """Server-owned provenance for exact curtain target/entity identities."""

    def __init__(
        self,
        *,
        verified_device_reports: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        exact_identities = frozenset(_CURTAIN_ENTITY_IDS.items())
        if not verified_device_reports.issubset(exact_identities):
            raise ValueError("verified curtain evidence identity is invalid")
        self._verified_device_reports = verified_device_reports

    @classmethod
    def with_verified_device_reports(
        cls, identities: set[tuple[str, str]] | frozenset[tuple[str, str]]
    ) -> CurtainPositionEvidencePolicy:
        """Create an explicit synthetic adapter for provenance tests."""

        return cls(verified_device_reports=frozenset(identities))

    def snapshot(self, target_id: str, entity_id: str) -> CurtainPositionEvidence:
        """Bind provenance to the exact current target/entity pair."""

        identity_digest = hashlib.sha256(
            json.dumps(
                [target_id, entity_id],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        verified = (
            _CURTAIN_ENTITY_IDS.get(target_id) == entity_id
            and (target_id, entity_id) in self._verified_device_reports
        )
        return CurtainPositionEvidence(
            provenance=(
                CURTAIN_POSITION_PROVENANCE_VERIFIED
                if verified
                else CURTAIN_POSITION_PROVENANCE_UNVERIFIED
            ),
            identity_digest=identity_digest,
        )


@dataclass(frozen=True, slots=True)
class CurtainDispatchPlan:
    """Immutable separation between public intent and physical dispatch."""

    target_id: str
    entity_id: str
    public_action_id: str
    requested: int | None
    domain: str
    service: str
    service_data: Mapping[str, object]
    applied: int | None
    confirmation_action_id: str
    confirmation_value: int | None
    policy_revision: str
    generation: int
    limited: bool
    pre_command_evidence_revision: str | None
    pre_command_position: int | None
    position_provenance: str
    position_identity_digest: str


@dataclass(frozen=True, slots=True)
class CurtainCalibrationGrant:
    """Opaque one-use server capability, never accepted from request JSON."""

    token: str
    target_id: str
    operation: str
    expires_at_ms: int


class CurtainCalibrationAuthority:
    """Issue and consume narrowly scoped full-travel calibration grants."""

    _OPERATIONS = frozenset({"full_open", "full_close"})

    def __init__(self, *, now_ms: Callable[[], int]) -> None:
        self._now_ms = now_ms
        self._grants: dict[str, CurtainCalibrationGrant] = {}

    def issue(
        self, target_id: str, operation: str, *, expires_at_ms: int
    ) -> CurtainCalibrationGrant:
        if target_id not in _CURTAIN_TARGETS:
            raise ValueError("unknown curtain calibration target")
        if operation not in self._OPERATIONS:
            raise ValueError("unknown curtain calibration operation")
        if type(expires_at_ms) is not int or expires_at_ms <= self._now_ms():
            raise ValueError("curtain calibration expiry is invalid")
        grant = CurtainCalibrationGrant(
            token=secrets.token_urlsafe(32),
            target_id=target_id,
            operation=operation,
            expires_at_ms=expires_at_ms,
        )
        self._grants[grant.token] = grant
        return grant

    def revoke(self, grant: CurtainCalibrationGrant) -> None:
        if isinstance(grant, CurtainCalibrationGrant):
            self._grants.pop(grant.token, None)

    def consume(
        self,
        grant: CurtainCalibrationGrant,
        *,
        target_id: str,
        operation: str,
    ) -> bool:
        """Consume before planning, so reuse is impossible even after failure."""

        if not isinstance(grant, CurtainCalibrationGrant):
            return False
        current = self._grants.pop(grant.token, None)
        return bool(
            current == grant
            and grant.target_id == target_id
            and grant.operation == operation
            and grant.expires_at_ms > self._now_ms()
        )


class CurtainCommandPolicy:
    """Build trusted dispatch plans for the four configured curtains.

    Kitchen and office calibration is deliberately not enabled by default.
    Only server-side composition may construct a policy with confirmed scales;
    no request field is consulted here.
    """

    def __init__(
        self,
        control_document_provider: Callable[[], ScenarioControlDocument] | None = None,
        *,
        confirmed_scale_targets: frozenset[str] = frozenset(),
        scale_authorization_provider: Callable[[str], object] | None = None,
        position_evidence_policy: CurtainPositionEvidencePolicy | None = None,
    ) -> None:
        unknown = set(confirmed_scale_targets).difference(_SCALE_DEPENDENT_TARGETS)
        if unknown:
            raise ValueError("unknown curtain calibration target")
        self._control_document_provider = (
            control_document_provider
            if control_document_provider is not None
            else lambda: ScenarioControlDocument()
        )
        self._confirmed_scale_targets = confirmed_scale_targets
        self._scale_authorization_provider = scale_authorization_provider
        if (
            position_evidence_policy is not None
            and not isinstance(position_evidence_policy, CurtainPositionEvidencePolicy)
        ):
            raise TypeError("curtain position evidence policy is invalid")
        self._position_evidence_policy = (
            position_evidence_policy or CurtainPositionEvidencePolicy()
        )

    @classmethod
    def with_confirmed_scales(
        cls,
        confirmed_targets: set[str] | frozenset[str],
        *,
        control_document_provider: Callable[[], ScenarioControlDocument] | None = None,
        position_evidence_policy: CurtainPositionEvidencePolicy | None = None,
    ) -> CurtainCommandPolicy:
        """Create a server-side policy for a physically confirmed test/setup."""

        unknown = set(confirmed_targets).difference(_SCALE_DEPENDENT_TARGETS)
        if unknown:
            raise ValueError("unknown curtain calibration target")
        return cls(
            control_document_provider,
            confirmed_scale_targets=frozenset(confirmed_targets),
            position_evidence_policy=position_evidence_policy,
        )

    @property
    def revision(self) -> str:
        _targets, revision = self._snapshot()
        return revision

    def target(self, target_id: str) -> CurtainTargetPolicy | None:
        if target_id not in _CURTAIN_TARGETS:
            return None
        targets, _revision = self._snapshot()
        return targets.get(target_id)

    @staticmethod
    def manages_target(target_id: str) -> bool:
        """Return whether the exact stable target belongs to curtain policy."""

        return target_id in _CURTAIN_TARGETS

    def plan(
        self,
        *,
        device: object,
        action_id: str,
        requested: object | None,
        current_state: object | None,
    ) -> CurtainDispatchPlan | None:
        """Resolve one exact target and fail closed before service dispatch."""

        target_id = getattr(device, "target_id", None)
        if target_id not in _CURTAIN_TARGETS:
            return None
        entity_id = getattr(device, "entity_id", None)
        if not isinstance(entity_id, str) or not entity_id:
            raise CurtainPolicyError("curtain_identity_invalid")
        expected_service = _ACTION_SERVICES.get(action_id)
        if expected_service is None:
            return None
        action = getattr(device, "action", lambda _value: None)(action_id)
        if (
            action is None
            or getattr(action, "domain", None) != "cover"
            or getattr(action, "service", None) != expected_service
        ):
            raise CurtainPolicyError("curtain_dispatch_descriptor_invalid")

        # Stopping movement is a fail-safe operation and does not consume a
        # position cap. Keep it available even while the editable cap document
        # is unavailable, without weakening exact target/descriptor checks.
        if action_id == "stop_cover":
            if requested is not None:
                raise CurtainPolicyError("curtain_action_does_not_accept_value")
            position_evidence = self._position_evidence_policy.snapshot(
                target_id, entity_id
            )
            return CurtainDispatchPlan(
                target_id=target_id,
                entity_id=entity_id,
                public_action_id=action_id,
                requested=None,
                domain="cover",
                service=expected_service,
                service_data=MappingProxyType({"entity_id": entity_id}),
                applied=None,
                confirmation_action_id=action_id,
                confirmation_value=None,
                policy_revision=_STOP_POLICY_REVISION,
                generation=0,
                limited=False,
                pre_command_evidence_revision=_state_revision(current_state),
                pre_command_position=trusted_curtain_position(current_state),
                position_provenance=position_evidence.provenance,
                position_identity_digest=position_evidence.identity_digest,
            )

        targets, policy_revision = self._snapshot()
        target = targets[target_id]

        position = trusted_curtain_position(current_state)
        normalized: int | None = None
        if action_id == "set_position":
            normalized = _position(requested)
        elif requested is not None:
            raise CurtainPolicyError("curtain_action_does_not_accept_value")

        if target_id == OFFICE_CURTAIN_TARGET:
            _enforce_office_close_guard(
                action_id=action_id,
                requested=normalized,
                current_position=position,
            )

        applied = normalized
        limited = False
        service = expected_service
        confirmation_action_id = action_id
        confirmation_value = normalized
        service_data: dict[str, object] = {"entity_id": entity_id}

        scale_dependent_target = target_id in _SCALE_DEPENDENT_TARGETS
        if action_id == "open_cover" and scale_dependent_target:
            if not target.scale_confirmed:
                raise CurtainPolicyError("curtain_scale_unconfirmed")
            if target.maximum_position < 100:
                set_position = getattr(device, "action", lambda _value: None)(
                    "set_position"
                )
                if (
                    set_position is None
                    or getattr(set_position, "domain", None) != "cover"
                    or getattr(set_position, "service", None)
                    != "set_cover_position"
                ):
                    raise CurtainPolicyError("curtain_dispatch_descriptor_invalid")
                applied = target.maximum_position
                service = "set_cover_position"
                service_data["position"] = applied
                confirmation_action_id = "set_position"
                confirmation_value = applied
                limited = True
        elif action_id == "set_position":
            assert normalized is not None
            depends_on_scale = (
                normalized > target.maximum_position
                or (position is None and normalized > 0)
                or (position is not None and normalized > position)
            )
            if (
                scale_dependent_target
                and depends_on_scale
                and not target.scale_confirmed
            ):
                raise CurtainPolicyError("curtain_scale_unconfirmed")
            applied = min(normalized, target.maximum_position)
            service_data["position"] = applied
            confirmation_value = applied
            limited = applied != normalized

        position_evidence = self._position_evidence_policy.snapshot(
            target_id, entity_id
        )
        return CurtainDispatchPlan(
            target_id=target_id,
            entity_id=entity_id,
            public_action_id=action_id,
            requested=normalized,
            domain="cover",
            service=service,
            service_data=MappingProxyType(service_data),
            applied=applied,
            confirmation_action_id=confirmation_action_id,
            confirmation_value=confirmation_value,
            policy_revision=policy_revision,
            generation=target.generation,
            limited=limited,
            pre_command_evidence_revision=_state_revision(current_state),
            pre_command_position=position,
            position_provenance=position_evidence.provenance,
            position_identity_digest=position_evidence.identity_digest,
        )

    def plan_calibration(
        self,
        *,
        device: object,
        operation: str,
        grant: CurtainCalibrationGrant,
        authority: CurtainCalibrationAuthority,
        current_state: object | None,
    ) -> CurtainDispatchPlan:
        """Build one full-travel plan only from an in-process server grant."""

        target_id = getattr(device, "target_id", None)
        if (
            target_id not in _CURTAIN_TARGETS
            or not isinstance(authority, CurtainCalibrationAuthority)
            or not authority.consume(
                grant, target_id=str(target_id), operation=operation
            )
        ):
            raise CurtainPolicyError("curtain_calibration_unauthorized")
        action_id, service, applied = (
            ("open_cover", "open_cover", 100)
            if operation == "full_open"
            else ("close_cover", "close_cover", 0)
        )
        entity_id = getattr(device, "entity_id", None)
        action = getattr(device, "action", lambda _value: None)(action_id)
        if (
            not isinstance(entity_id, str)
            or not entity_id
            or action is None
            or getattr(action, "domain", None) != "cover"
            or getattr(action, "service", None) != service
        ):
            raise CurtainPolicyError("curtain_dispatch_descriptor_invalid")
        position_evidence = self._position_evidence_policy.snapshot(
            str(target_id), entity_id
        )
        return CurtainDispatchPlan(
            target_id=str(target_id),
            entity_id=entity_id,
            public_action_id=action_id,
            requested=None,
            domain="cover",
            service=service,
            service_data=MappingProxyType({"entity_id": entity_id}),
            applied=applied,
            confirmation_action_id=action_id,
            confirmation_value=None,
            policy_revision=f"curtain-calibration.{grant.token[:16]}",
            generation=0,
            limited=False,
            pre_command_evidence_revision=_state_revision(current_state),
            pre_command_position=trusted_curtain_position(current_state),
            position_provenance=position_evidence.provenance,
            position_identity_digest=position_evidence.identity_digest,
        )

    def _snapshot(
        self,
    ) -> tuple[Mapping[str, CurtainTargetPolicy], str]:
        """Read one complete editable document and bind its CAS generation."""

        try:
            document = self._control_document_provider()
        except Exception as error:  # noqa: BLE001
            raise CurtainPolicyError("curtain_policy_unavailable") from error
        if not isinstance(document, ScenarioControlDocument):
            raise CurtainPolicyError("curtain_policy_unavailable")
        if (
            type(document.policy_revision) is not int
            or not 0 <= document.policy_revision < 2**31
        ):
            raise CurtainPolicyError("curtain_policy_unavailable")
        policy = document.policy
        if not isinstance(policy, ScenarioControlPolicy):
            raise CurtainPolicyError("curtain_policy_unavailable")
        if any(
            type(value) is not int or not 1 <= value <= 100
            for value in (
                policy.kitchen_cover_cap_percent,
                policy.cabinet_cover_cap_percent,
            )
        ):
            raise CurtainPolicyError("curtain_policy_unavailable")
        caps = {
            LIVING_CURTAIN_TARGET: 100,
            KITCHEN_CURTAIN_TARGET: policy.kitchen_cover_cap_percent,
            ALICE_CURTAIN_TARGET: 100,
            OFFICE_CURTAIN_TARGET: policy.cabinet_cover_cap_percent,
        }
        authorizations = self._scale_authorizations()
        targets = MappingProxyType(
            {
                target_id: CurtainTargetPolicy(
                    target_id=target_id,
                    maximum_position=maximum,
                    scale_confirmed=(
                        target_id not in _SCALE_DEPENDENT_TARGETS
                        or target_id in self._confirmed_scale_targets
                        or authorizations[target_id][0]
                    ),
                    generation=document.policy_revision,
                )
                for target_id, maximum in caps.items()
            }
        )
        digest = hashlib.sha256(
            json.dumps(
                [
                    document.policy_revision,
                    policy.kitchen_cover_cap_percent,
                    policy.cabinet_cover_cap_percent,
                    sorted(self._confirmed_scale_targets),
                    [
                        [target_id, *authorizations[target_id]]
                        for target_id in sorted(_SCALE_DEPENDENT_TARGETS)
                    ],
                ],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return targets, (
            f"scenario-control.{document.policy_revision}.curtain.{digest[:24]}"
        )

    def _scale_authorizations(
        self,
    ) -> dict[str, tuple[bool, int, str | None]]:
        """Read exact per-target authority without trusting request payloads."""

        result: dict[str, tuple[bool, int, str | None]] = {}
        for target_id in _SCALE_DEPENDENT_TARGETS:
            if target_id in self._confirmed_scale_targets:
                result[target_id] = (True, 0, "test-confirmed-scale")
                continue
            try:
                snapshot = (
                    self._scale_authorization_provider(target_id)
                    if self._scale_authorization_provider is not None
                    else None
                )
                confirmed = getattr(snapshot, "confirmed", None)
                revision = getattr(snapshot, "revision", None)
                identity_digest = getattr(snapshot, "identity_digest", None)
                if (
                    type(confirmed) is not bool
                    or type(revision) is not int
                    or not 0 <= revision < 2**31
                    or (
                        identity_digest is not None
                        and (
                            not isinstance(identity_digest, str)
                            or not identity_digest
                            or len(identity_digest) > 256
                        )
                    )
                ):
                    raise ValueError("invalid curtain scale authority")
                result[target_id] = (confirmed, revision, identity_digest)
            except Exception:  # noqa: BLE001
                result[target_id] = (False, 0, None)
        return result


def _position(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CurtainPolicyError("curtain_position_invalid")
    numeric = float(value)
    if (
        not math.isfinite(numeric)
        or numeric != round(numeric)
        or not 0 <= numeric <= 100
    ):
        raise CurtainPolicyError("curtain_position_invalid")
    return int(numeric)


def trusted_curtain_position(state: object | None) -> int | None:
    """Return a fresh, non-restored whole HA cover position."""

    if (
        state is None
        or _state_is_restored_or_cached(state)
        or not _state_is_fresh(state)
    ):
        return None
    if str(getattr(state, "state", "unknown")) in _UNAVAILABLE_STATES:
        return None
    attributes = getattr(state, "attributes", {})
    if not isinstance(attributes, Mapping):
        return None
    raw = attributes.get("current_position")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    numeric = float(raw)
    if not math.isfinite(numeric) or not 0 <= numeric <= 100:
        return None
    return int(numeric) if numeric == round(numeric) else None


def _enforce_office_close_guard(
    *,
    action_id: str,
    requested: int | None,
    current_position: int | None,
) -> None:
    closing = action_id == "close_cover"
    if action_id == "set_position":
        if current_position is None:
            raise CurtainPolicyError("office_position_unknown")
        closing = requested is not None and requested < current_position
    if not closing:
        return
    if current_position is None:
        raise CurtainPolicyError("office_position_unknown")
    if current_position <= 20:
        raise CurtainPolicyError("office_close_guard", skipped=True)


def _state_revision(state: object | None) -> str | None:
    if state is None:
        return None
    observed = getattr(state, "last_reported", None) or getattr(
        state, "last_updated", None
    )
    return observed.isoformat() if isinstance(observed, datetime) else None


def _state_is_fresh(state: object) -> bool:
    observed = getattr(state, "last_reported", None) or getattr(
        state, "last_updated", None
    ) or getattr(state, "last_changed", None)
    if not isinstance(observed, datetime):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = (
        datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
    ).total_seconds()
    return 0 <= age <= 300


def _state_is_restored_or_cached(state: object) -> bool:
    if any(
        getattr(state, marker, False) is True
        for marker in ("restored", "assumed_state", "is_assumed_state")
    ):
        return True
    attributes = getattr(state, "attributes", {})
    if not isinstance(attributes, Mapping):
        return False
    return any(
        attributes.get(marker) is True
        for marker in (
            "restored",
            "_restored",
            "cached",
            "cache",
            "assumed_state",
        )
    ) or attributes.get("evidence_source") in {"restore", "cache"}
