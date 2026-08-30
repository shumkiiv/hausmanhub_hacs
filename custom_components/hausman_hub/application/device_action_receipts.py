"""Evidence-aware full receipt projection for the typed device-action API."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from .scenario_light_priority import _state_is_fresh, _state_revision

_LIGHT_ON_ACTIONS = [
    "turn_off",
    "set_brightness",
    "set_adaptive_brightness",
    "set_brightness_percent",
    "set_color_temperature",
    "set_night_light",
    "set_rgb_color",
]


def evidence_snapshot(
    *,
    target_id: str,
    state: object | None,
    allowed_actions: Sequence[str],
    reason_code: str | None = None,
    ownership: str = "unknown",
    request_id: str | None = None,
    action_id: str | None = None,
    command_sent_at: int | None = None,
    confirmed: bool = False,
    effective_state: str | None = None,
    power_preparation: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build one bounded server-owned evidence snapshot from an HA state."""

    now_ms = time.time_ns() // 1_000_000
    raw = str(getattr(state, "state", "unavailable")) if state is not None else None
    reported = raw if raw in {"on", "off", "unknown", "unavailable"} else "unknown"
    observed_at = _observed_at_ms(state)
    revision_source = _state_revision(state)
    sequence = observed_at if observed_at is not None else now_ms
    revision = _bounded_revision(revision_source, sequence)
    fresh = state is not None and _state_is_fresh(state)

    if reason_code == "power_source_off" and effective_state == "off":
        confidence = "confirmed"
        effective = "off"
        ownership = "unknown"
        reason = "power_source_off"
        blocked = ["power_source_off"]
        published_actions = []
        ownership_source = "unknown"
    elif confirmed and reported in {"on", "off"} and ownership in {
        "manual",
        "automation",
    }:
        confidence = "confirmed"
        effective = reported
        reason = reason_code or (
            "automatic_on_confirmed"
            if ownership == "automation" and reported == "on"
            else "manual_state_confirmed"
            if reported == "on"
            else "manual_off_confirmed"
        )
        blocked: list[str] = []
        published_actions = _LIGHT_ON_ACTIONS if reported == "on" else ["turn_on"]
        ownership_source = (
            "automation_command" if ownership == "automation" else "manual_command"
        )
    elif reported == "off" and fresh:
        confidence = "confirmed"
        effective = "off"
        ownership = "unknown"
        reason = reason_code or "device_report_off_confirmed"
        blocked = []
        published_actions = ["turn_on"]
        ownership_source = "device_report"
    elif reported == "on" and fresh and ownership == "manual":
        confidence = "fresh"
        effective = "on"
        reason = reason_code or "manual_state_fresh"
        blocked = []
        published_actions = _LIGHT_ON_ACTIONS
        ownership_source = "manual_command"
    else:
        confidence = "unavailable" if reported == "unavailable" else "suspect"
        effective = "unavailable" if reported == "unavailable" else "unknown"
        ownership = "unknown"
        reason = reason_code or (
            "power_source_unavailable" if reported == "unavailable" else "state_suspect"
        )
        blocked = [reason]
        published_actions = []
        ownership_source = "unknown"

    if effective_state in {"on", "off", "unknown", "unavailable"}:
        effective = effective_state

    reassert_allowed = (
        reported == "on"
        and not fresh
        and ownership == "unknown"
        and "turn_on" in allowed_actions
    )
    if reassert_allowed:
        reason = "state_stale"
        blocked = []
        published_actions = ["turn_on"]

    snapshot: dict[str, object] = {
        "contract": {
            "name": "hausman-hub-device-state-evidence",
            "version": 1,
        },
        "targetId": target_id,
        "reportedState": reported,
        "effectiveState": effective,
        "confidence": confidence,
        "ownership": ownership,
        "observedAt": observed_at,
        "freshUntil": (
            observed_at + 300_000 if observed_at is not None else None
        ),
        "freshnessWindowMs": 300_000,
        "ownershipSource": ownership_source,
        "evidenceSequence": sequence,
        "evidenceRevision": revision,
        "reasonCode": reason,
        "reassertPolicy": "light_turn_on" if reassert_allowed else "none",
        "reassertBudget": 1 if reassert_allowed else 0,
        "allowedActions": [
            item for item in published_actions if item in set(allowed_actions)
        ],
        "blockedReasons": blocked,
    }
    if ownership in {"manual", "automation"}:
        proof_request_id = request_id or f"device-report.{sequence}"
        proof_action_id = action_id or ("turn_on" if reported == "on" else "turn_off")
        proof_sent_at = command_sent_at or max(0, (observed_at or now_ms) - 1)
        provenance = {
            "requestId": proof_request_id,
            "targetId": target_id,
            "actionId": proof_action_id,
            "commandSentAt": proof_sent_at,
            "readBackEvidenceRevision": revision,
            "readBackEvidenceSequence": sequence,
        }
        snapshot["ownershipEvidence"] = {
            "receiptId": proof_request_id,
            **provenance,
            "provenanceId": "sha256:"
            + hashlib.sha256(
                json.dumps(
                    provenance,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
    if isinstance(power_preparation, Mapping):
        snapshot["powerPreparation"] = dict(power_preparation)
    return snapshot


def full_action_receipt(
    *,
    payload: Mapping[str, object],
    result: Mapping[str, object],
    target_type: str,
    state: object | None,
    allowed_actions: Sequence[str],
    pre_command_evidence: Mapping[str, object],
    decision_at: int,
    action_index: int | None = None,
) -> dict[str, object]:
    """Project the legacy executor result into the strict full receipt view."""

    target_id = str(payload["targetId"])
    action_id = str(payload["actionId"])
    request_id = str(result.get("requestId") or f"device-action.{decision_at}")
    dry_run = payload.get("dryRun") is True
    accepted = result.get("accepted") is True
    skipped = result.get("skipped") is True
    confirmed = result.get("confirmed") is True and not dry_run and not skipped
    reasserted = isinstance(payload.get("reassertKey"), str)
    command_sent = (
        accepted
        and not dry_run
        and not skipped
        and result.get("reason") != "already_in_target_state"
    )
    decision = (
        "skipped"
        if skipped
        else
        "reasserted"
        if reasserted and command_sent
        else "executed"
        if command_sent or dry_run
        else "skipped"
        if accepted
        else "blocked"
    )
    command_sent_at = (
        int(result.get("appliedAt") or time.time_ns() // 1_000_000)
        if command_sent
        else None
    )
    if target_type == "light":
        reason_code = (
            "reasserted_light_on"
            if reasserted and confirmed
            else "power_source_off"
            if skipped and result.get("reason") == "already_effectively_off"
            else "manual_state_confirmed"
            if confirmed and action_id != "turn_off"
            else "manual_off_confirmed"
            if confirmed
            else "read_back_failed"
            if command_sent
            else str(pre_command_evidence.get("reasonCode") or "state_suspect")
        )
        ownership = (
            "automation"
            if reasserted and confirmed
            else "manual"
            if confirmed
            else str(pre_command_evidence.get("ownership") or "unknown")
        )
        final_evidence = evidence_snapshot(
            target_id=target_id,
            state=state,
            allowed_actions=allowed_actions,
            reason_code=reason_code,
            ownership=ownership,
            request_id=request_id,
            action_id=action_id,
            command_sent_at=command_sent_at,
            confirmed=confirmed,
            effective_state=(
                str(result.get("effectiveState"))
                if result.get("effectiveState") in {"on", "off", "unknown", "unavailable"}
                else None
            ),
            power_preparation=(
                result.get("powerPreparation")
                if isinstance(result.get("powerPreparation"), Mapping)
                else (
                    result.get("power_precondition", {}).get("powerPreparation")
                    if isinstance(result.get("power_precondition"), Mapping)
                    and isinstance(
                        result.get("power_precondition", {}).get("powerPreparation"),
                        Mapping,
                    )
                    else None
                )
            ),
        )
        if command_sent and not confirmed:
            accepted = True
    else:
        reason_code = None
        ownership = None
        final_evidence = None

    receipt: dict[str, object] = {
        "contract": {
            "name": "hausman-hub-device-action-receipt",
            "version": 1,
        },
        "correlationId": str(result.get("correlationId") or payload.get("correlationId")),
        "requestId": request_id,
        "accepted": accepted,
        "confirmed": confirmed,
        "status": (
            "confirmed"
            if confirmed
            else "failed"
            if command_sent and not dry_run
            else "accepted"
            if accepted
            else "failed"
        ),
        "statusName": (
            "Выполнено"
            if confirmed
            else "Проверяется"
            if accepted and not command_sent
            else "Не выполнено"
        ),
        "targetId": target_id,
        "targetType": target_type,
        "actionId": action_id,
        "decision": decision,
        "decisionAt": decision_at,
        "commandSent": command_sent,
        "message": result.get("message"),
        "dryRun": dry_run,
        "reason": result.get("reason"),
        "error": result.get("error"),
    }
    if "value" in payload:
        receipt["actionValue"] = payload["value"]
    if action_index is not None:
        receipt["actionIndex"] = action_index
    if command_sent_at is not None:
        receipt["commandSentAt"] = command_sent_at
        receipt["confirmationWindowMs"] = int(
            result.get("confirmationWindowMs") or 8000
        )
    if decision in {"skipped", "blocked"}:
        receipt["decisionTerminal"] = True
    if reasserted:
        receipt["reassertKey"] = payload["reassertKey"]
        receipt["reassertAttempt"] = 1
    if target_type == "light" and final_evidence is not None:
        receipt.update(
            {
                "commandSource": "automation" if reasserted else "manual",
                "reasonCode": reason_code,
                "evidenceRevision": final_evidence["evidenceRevision"],
                "preCommandEvidenceRevision": pre_command_evidence["evidenceRevision"],
                "preCommandOwnership": pre_command_evidence["ownership"],
                "ownership": ownership,
                "blockedReasons": final_evidence["blockedReasons"],
                "evidence": final_evidence,
                "preCommandEvidence": dict(pre_command_evidence),
            }
        )
    read_back = result.get("readBack")
    if command_sent and isinstance(read_back, Mapping):
        normalized_read_back: dict[str, object] = {
            "targetId": target_id,
            "attempted": read_back.get("attempted") is True,
            "matched": confirmed,
            "commandRequestId": request_id,
            "observedAt": read_back.get("observedAt"),
            "observedState": read_back.get("observedState"),
            "attempts": int(read_back.get("attempts") or 0),
        }
        if target_type == "light":
            normalized_read_back.update(
                {
                    "isNewEvidence": read_back.get("isNewEvidence") is True,
                    "evidenceRevision": final_evidence["evidenceRevision"],
                    "evidenceSequence": final_evidence["evidenceSequence"],
                }
            )
        if "value" in payload and confirmed:
            normalized_read_back["observedValue"] = payload["value"]
        receipt["readBack"] = normalized_read_back
    return receipt


def _observed_at_ms(state: object | None) -> int | None:
    if state is None:
        return None
    observed = getattr(state, "last_changed", None) or getattr(
        state, "last_updated", None
    )
    if isinstance(observed, datetime):
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return max(0, int(observed.timestamp() * 1000))
    return time.time_ns() // 1_000_000


def _bounded_revision(value: str | None, sequence: int) -> str:
    if isinstance(value, str):
        normalized = "".join(
            character if character.isalnum() or character in "._:-" else "."
            for character in value
        )
        if normalized and normalized[0].isalnum():
            return normalized[:128]
    return f"evidence.{sequence}"
