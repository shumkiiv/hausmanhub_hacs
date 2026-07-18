"""Build one redacted operator preflight without activating climate control."""

from __future__ import annotations

from collections.abc import Mapping

from ..domain.climate import ClimateRegistry
from ..domain.climate_bridge import ClimateBridgeMode
from .climate_evidence import SHADOW_EVIDENCE_REQUIRED_ACTIONS
from .climate_import import (
    MAX_CLIMATE_STATE_AGE_MS,
    MAX_FUTURE_SKEW_MS,
    ClimateImportSnapshot,
)
from .climate_registry import reconcile_climate_registry


CANARY_PREFLIGHT_CONTRACT_NAME = "hausman-hasc-climate-canary-preflight"
CANARY_PREFLIGHT_CONTRACT_VERSION = 1

_SHADOW_STATUSES = frozenset({"ready", "collecting", "blocked"})
_SHADOW_REASONS = frozenset(
    {
        "bridge_disabled",
        "candidate_not_registered",
        "climate_state_unavailable",
        "state_stale",
        "registry_mismatch",
        "authority_not_ready",
        "required_actions_unsupported",
        "insufficient_matching_observations",
        "required_shadow_intents_missing",
        "shadow_anomalies_observed",
    }
)


class ClimateCanaryPreflightViolation(ValueError):
    """The internal preflight inputs cannot form a safe redacted result."""


def climate_canary_preflight(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot | None,
    evidence_payload: object,
    *,
    bridge_mode: ClimateBridgeMode,
    room_id: object,
    pending_operation: object,
    checked_at: object,
) -> dict[str, object]:
    """Return preparation state while keeping physical activation impossible."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateCanaryPreflightViolation("preflight registry is invalid")
    if not isinstance(bridge_mode, ClimateBridgeMode):
        raise ClimateCanaryPreflightViolation("preflight bridge mode is invalid")
    if not isinstance(room_id, str) or registry.room(room_id) is None:
        raise ClimateCanaryPreflightViolation("preflight room is not registered")
    if type(pending_operation) is not bool:
        raise ClimateCanaryPreflightViolation("preflight operation state is invalid")
    if type(checked_at) is not int or checked_at < 0:
        raise ClimateCanaryPreflightViolation("preflight timestamp is invalid")
    if snapshot is not None and not isinstance(snapshot, ClimateImportSnapshot):
        raise ClimateCanaryPreflightViolation("preflight snapshot is invalid")

    root = _mapping(evidence_payload, "shadow evidence")
    candidate = _mapping(root.get("candidate"), "shadow candidate")
    if candidate.get("room_id") != room_id:
        raise ClimateCanaryPreflightViolation("shadow candidate room changed")
    shadow_status = candidate.get("status")
    if shadow_status not in _SHADOW_STATUSES:
        raise ClimateCanaryPreflightViolation("shadow candidate status is invalid")
    shadow_ready = candidate.get("ready") is True
    matched = _count(candidate.get("matched_observation_count"), "matched observations")
    required_matched = _count(
        candidate.get("required_matched_observation_count"),
        "required matched observations",
    )
    translated = _count(candidate.get("translated_action_count"), "translated actions")
    anomalies = _count(candidate.get("anomaly_count"), "shadow anomalies")
    required_actions = candidate.get("required_actions")
    if required_actions != list(SHADOW_EVIDENCE_REQUIRED_ACTIONS):
        raise ClimateCanaryPreflightViolation("shadow command scope is invalid")
    evidence_reasons = candidate.get("reasons")
    if (
        not isinstance(evidence_reasons, list)
        or any(value not in _SHADOW_REASONS for value in evidence_reasons)
        or len(evidence_reasons) != len(set(evidence_reasons))
    ):
        raise ClimateCanaryPreflightViolation("shadow candidate reasons are invalid")
    if shadow_ready != (shadow_status == "ready" and not evidence_reasons):
        raise ClimateCanaryPreflightViolation("shadow candidate readiness is inconsistent")

    reconciliation = None
    registry_reconciled = False
    if snapshot is not None:
        result = reconcile_climate_registry(registry, snapshot)
        registry_reconciled = result.matches
        reconciliation = {
            "matches": result.matches,
            "matched_device_count": len(result.matched_device_ids),
            "missing_device_count": len(result.missing_device_ids),
            "room_mismatch_device_count": len(result.room_mismatch_device_ids),
            "unregistered_source_count": len(result.unregistered_source_ids),
        }

    scope_qualified = (
        snapshot is not None
        and "required_actions_unsupported" not in evidence_reasons
    )
    state_generated_at = snapshot.generated_at if snapshot is not None else None
    state_valid_until = (
        snapshot.generated_at + MAX_CLIMATE_STATE_AGE_MS
        if snapshot is not None
        else None
    )
    state_fresh = (
        snapshot is not None
        and snapshot.runtime_fresh
        and snapshot.generated_at - MAX_FUTURE_SKEW_MS
        <= checked_at
        <= snapshot.generated_at + MAX_CLIMATE_STATE_AGE_MS
    )
    rollback_ready = (
        bridge_mode in {ClimateBridgeMode.DISABLED, ClimateBridgeMode.SHADOW}
        and not pending_operation
    )
    reasons = list(evidence_reasons)
    if bridge_mode is not ClimateBridgeMode.SHADOW:
        reasons.append("preflight_requires_shadow")
    if not registry_reconciled:
        reasons.append("registry_not_reconciled")
    if not scope_qualified:
        reasons.append("command_scope_not_qualified")
    if not state_fresh:
        reasons.append("preflight_state_not_fresh")
    if pending_operation:
        reasons.append("pending_operation")
    if not rollback_ready:
        reasons.append("rollback_not_ready")
    reasons = list(dict.fromkeys(reasons))

    ready = (
        bridge_mode is ClimateBridgeMode.SHADOW
        and registry_reconciled
        and shadow_ready
        and scope_qualified
        and state_fresh
        and not pending_operation
        and rollback_ready
    )
    status = (
        "ready"
        if ready
        else "collecting"
        if bridge_mode is ClimateBridgeMode.SHADOW
        and shadow_status == "collecting"
        and registry_reconciled
        and scope_qualified
        and state_fresh
        and not pending_operation
        and rollback_ready
        else "blocked"
    )
    rollback_status = (
        "effective"
        if rollback_ready and bridge_mode is ClimateBridgeMode.DISABLED
        else "ready"
        if rollback_ready
        else "blocked"
    )
    room_device_count = sum(
        device.room_id == room_id for device in registry.devices
    )
    return {
        "contract": {
            "name": CANARY_PREFLIGHT_CONTRACT_NAME,
            "version": CANARY_PREFLIGHT_CONTRACT_VERSION,
        },
        "room_id": room_id,
        "status": status,
        "ready_for_authorization": ready,
        "bridge_mode": bridge_mode.value,
        "freshness": {
            "checked_at": checked_at,
            "state_generated_at": state_generated_at,
            "state_valid_until": state_valid_until,
            "state_fresh": state_fresh,
        },
        "registry": {
            "room_device_count": room_device_count,
            "reconciliation": reconciliation,
        },
        "shadow": {
            "status": shadow_status,
            "ready": shadow_ready,
            "matched_observation_count": matched,
            "required_matched_observation_count": required_matched,
            "translated_action_count": translated,
            "required_action_count": len(SHADOW_EVIDENCE_REQUIRED_ACTIONS),
            "anomaly_count": anomalies,
        },
        "command_scope": {
            "actions": list(SHADOW_EVIDENCE_REQUIRED_ACTIONS),
            "qualified": scope_qualified,
        },
        "operation": {
            "status": "pending" if pending_operation else "clear",
            "pending": pending_operation,
        },
        "rollback": {
            "mode": ClimateBridgeMode.DISABLED.value,
            "status": rollback_status,
            "ready": rollback_ready,
        },
        "activation": {
            "allowed": False,
            "separate_authorization_required": True,
        },
        "reasons": reasons,
    }


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise ClimateCanaryPreflightViolation(f"{label} is invalid")
    return value


def _count(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ClimateCanaryPreflightViolation(f"{label} is invalid")
    return value
