"""Bounded durable journal for normalized cross-domain operation receipts."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Mapping
from typing import Protocol

OPERATION_JOURNAL_CONTRACT_NAME = "hausman-hub-operation-journal"
OPERATION_JOURNAL_CONTRACT_VERSION = 1
MAX_OPERATION_JOURNAL_RECORDS = 512
OPERATION_SOURCES = frozenset({"device", "climate", "scenario", "voice"})
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OPERATION_NAME = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SCENARIO_OUTCOMES = frozenset(
    {"completed", "skipped", "cancelled", "partial", "failed"}
)
_EXECUTION_MODES = frozenset({"single", "restart", "queued"})
_COMMAND_MODES = frozenset({"live", "shadow"})


class OperationJournalStore(Protocol):
    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


class OperationJournalService:
    """Persist redacted receipt outcomes and expose newest-first snapshots."""

    def __init__(
        self,
        store: OperationJournalStore,
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._lock = asyncio.Lock()
        self._sequence = 0
        self._records: list[dict[str, object]] = []

    async def async_load(self) -> None:
        """Load only valid bounded records; malformed journal data fails closed."""

        payload = await self._store.async_load()
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            return
        sequence = payload.get("sequence")
        records = payload.get("records")
        if type(sequence) is not int or sequence < 0 or not isinstance(records, list):
            return
        normalized: list[dict[str, object]] = []
        for item in records[:MAX_OPERATION_JOURNAL_RECORDS]:
            record = _validated_record(item)
            if record is not None:
                normalized.append(record)
        normalized.sort(key=lambda item: int(item["sequence"]), reverse=True)
        self._records = normalized
        self._sequence = max(
            sequence,
            *(int(item["sequence"]) for item in normalized),
        )

    async def async_append(self, receipt: Mapping[str, object]) -> dict[str, object]:
        """Append one already normalized receipt without private target details."""

        correlation_id = receipt.get("correlation_id") or receipt.get("request_id")
        operation = receipt.get("operation")
        accepted = receipt.get("accepted")
        confirmed = receipt.get("confirmed")
        status = receipt.get("status")
        if (
            not isinstance(correlation_id, str)
            or _CORRELATION_ID.fullmatch(correlation_id) is None
            or not isinstance(operation, str)
            or not operation
            or len(operation) > 64
            or type(accepted) is not bool
            or type(confirmed) is not bool
            or status not in {"accepted", "confirmed", "failed"}
        ):
            raise ValueError("normalized operation receipt is invalid")
        operation = re.sub(r"(?<!^)(?=[A-Z])", "_", operation).lower()
        if _OPERATION_NAME.fullmatch(operation) is None:
            raise ValueError("normalized operation name is invalid")
        source = _source_for_operation(operation)
        reason = receipt.get("reason")
        error_code = receipt.get("error_code")
        scenario = _validated_scenario_trace(receipt.get("scenario"))
        manual_off_protection = _validated_journal_manual_off_protection(
            receipt.get("manualOffProtection")
        )
        if receipt.get("manualOffProtection") is not None and manual_off_protection is None:
            raise ValueError("manual light-off protection trace is invalid")
        if receipt.get("scenario") is not None and scenario is None:
            raise ValueError("normalized scenario trace is invalid")
        if scenario is not None and (
            source != "scenario" or operation != "scenario_run"
        ):
            raise ValueError("scenario trace must belong to scenario_run")
        async with self._lock:
            self._sequence += 1
            record = {
                "sequence": self._sequence,
                "correlation_id": correlation_id,
                "source": source,
                "operation": operation,
                "accepted": accepted,
                "confirmed": confirmed,
                "status": status,
                "occurred_at": max(0, self._now_ms()),
                "reason": reason[:512] if isinstance(reason, str) else None,
                "error_code": (
                    error_code[:128] if isinstance(error_code, str) else None
                ),
            }
            if scenario is not None:
                record["scenario"] = scenario
            if manual_off_protection is not None:
                record["manualOffProtection"] = manual_off_protection
            self._records.insert(0, record)
            del self._records[MAX_OPERATION_JOURNAL_RECORDS:]
            await self._store.async_save(self._storage_payload())
            return dict(record)

    def snapshot(
        self,
        *,
        limit: int = 100,
        before_sequence: int | None = None,
        source: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, object]:
        """Return a bounded filtered copy without causing storage writes."""

        if not 1 <= limit <= MAX_OPERATION_JOURNAL_RECORDS:
            raise ValueError("operation journal limit is invalid")
        if before_sequence is not None and (
            type(before_sequence) is not int or before_sequence < 1
        ):
            raise ValueError("operation journal cursor is invalid")
        if source is not None and source not in OPERATION_SOURCES:
            raise ValueError("operation journal source is invalid")
        if correlation_id is not None and (
            _CORRELATION_ID.fullmatch(correlation_id) is None
        ):
            raise ValueError("operation journal correlation id is invalid")
        eligible = [
            dict(item)
            for item in self._records
            if (source is None or item["source"] == source)
            and (correlation_id is None or item["correlation_id"] == correlation_id)
            and (before_sequence is None or int(item["sequence"]) < before_sequence)
        ]
        records = eligible[:limit]
        has_more = len(eligible) > limit
        return {
            "contract": {
                "name": OPERATION_JOURNAL_CONTRACT_NAME,
                "version": OPERATION_JOURNAL_CONTRACT_VERSION,
            },
            "generated_at": max(0, self._now_ms()),
            "sequence": self._sequence,
            "page": {
                "order": "sequence_desc",
                "limit": limit,
                "returned": len(records),
                "has_more": has_more,
                "next_before_sequence": (
                    int(records[-1]["sequence"]) if has_more and records else None
                ),
                "retained_records": len(self._records),
                "retention_limit": MAX_OPERATION_JOURNAL_RECORDS,
            },
            "records": records,
        }

    def _storage_payload(self) -> dict[str, object]:
        return {
            "version": 1,
            "sequence": self._sequence,
            "records": [dict(item) for item in self._records],
        }


def _source_for_operation(operation: str) -> str:
    if operation.startswith("voice"):
        return "voice"
    if operation.startswith("scenario"):
        return "scenario"
    if operation.startswith("climate") or operation in {
        "contour_apply",
        "temporary_temperature",
        "home_climate_targets",
    }:
        return "climate"
    return "device"


def _validated_record(value: object) -> dict[str, object] | None:
    required_keys = {
        "sequence",
        "correlation_id",
        "source",
        "operation",
        "accepted",
        "confirmed",
        "status",
        "occurred_at",
        "reason",
        "error_code",
    }
    if (
        not isinstance(value, Mapping)
        or not required_keys.issubset(value)
        or not set(value).issubset(required_keys | {"scenario", "manualOffProtection"})
    ):
        return None
    sequence = value.get("sequence")
    correlation_id = value.get("correlation_id")
    source = value.get("source")
    operation = value.get("operation")
    occurred_at = value.get("occurred_at")
    reason = value.get("reason")
    error_code = value.get("error_code")
    scenario_present = "scenario" in value
    manual_off_protection = _validated_journal_manual_off_protection(value.get("manualOffProtection"))
    scenario = _validated_scenario_trace(value.get("scenario"))
    if (
        type(sequence) is not int
        or sequence < 1
        or not isinstance(correlation_id, str)
        or _CORRELATION_ID.fullmatch(correlation_id) is None
        or source not in OPERATION_SOURCES
        or not isinstance(operation, str)
        or _OPERATION_NAME.fullmatch(operation) is None
        or type(value.get("accepted")) is not bool
        or type(value.get("confirmed")) is not bool
        or value.get("status") not in {"accepted", "confirmed", "failed"}
        or type(occurred_at) is not int
        or occurred_at < 0
        or (reason is not None and not isinstance(reason, str))
        or (error_code is not None and not isinstance(error_code, str))
        or (isinstance(reason, str) and len(reason) > 512)
        or (isinstance(error_code, str) and len(error_code) > 128)
        or (value.get("confirmed") is True and value.get("accepted") is not True)
        or ((value.get("status") == "confirmed") != (value.get("confirmed") is True))
        or (scenario_present and scenario is None)
        or ("manualOffProtection" in value and manual_off_protection is None)
        or (scenario_present and (source != "scenario" or operation != "scenario_run"))
        or (
            scenario is not None
            and scenario.get("command_mode") == "shadow"
            and value.get("confirmed") is not False
        )
    ):
        return None
    record = dict(value)
    if scenario is not None:
        record["scenario"] = scenario
    if manual_off_protection is not None:
        record["manualOffProtection"] = manual_off_protection
    return record


def scenario_operation_receipt(result: Mapping[str, object]) -> dict[str, object]:
    """Redact one executor result into the durable scenario journal contract."""

    run_id = result.get("run_id")
    scenario_id = result.get("scenario_id")
    execution_mode = result.get("execution_mode")
    command_mode = result.get("command_mode", "live")
    outcome = result.get("status")
    if (
        not isinstance(run_id, str)
        or _CORRELATION_ID.fullmatch(run_id) is None
        or not isinstance(scenario_id, str)
        or _STABLE_ID.fullmatch(scenario_id) is None
        or execution_mode not in _EXECUTION_MODES
        or command_mode not in _COMMAND_MODES
        or outcome not in _SCENARIO_OUTCOMES
    ):
        raise ValueError("scenario execution result is invalid")
    decisions: list[dict[str, object]] = []
    condition_results = result.get("condition_results")
    if not isinstance(condition_results, list):
        condition_results = []
    for item in condition_results:
        if not isinstance(item, Mapping):
            continue
        rule_id = item.get("condition_id")
        if not isinstance(rule_id, str) or _STABLE_ID.fullmatch(rule_id) is None:
            continue
        decision_outcome = item.get("outcome")
        if decision_outcome not in {"passed", "skipped", "failed"}:
            decision_outcome = "passed" if item.get("passed") is True else "skipped"
        reason = _safe_trace_reason(
            item.get("reason"),
            "condition_not_met" if decision_outcome != "passed" else None,
        )
        decisions.append(
            {
                "rule_id": rule_id,
                "outcome": decision_outcome,
                "reason": reason,
            }
        )
    actions: list[dict[str, object]] = []
    receipts = result.get("receipts")
    if not isinstance(receipts, list):
        receipts = []
    for item in receipts:
        if not isinstance(item, Mapping):
            continue
        action_id = item.get("action_id")
        if not isinstance(action_id, str) or _STABLE_ID.fullmatch(action_id) is None:
            continue
        item_status = item.get("status")
        action_outcome = (
            "failed"
            if item_status == "failed"
            else "skipped"
            if item.get("skipped") is True
            else "completed"
        )
        confirmed = item.get("confirmed")
        reason = _safe_trace_reason(
            item.get("reason") or item.get("error"),
            "action_failed" if action_outcome == "failed" else None,
        )
        # A guard denial is a completed, command-free scenario action. Keep
        # its stable reason in the durable trace so operators can distinguish
        # it from a failed device dispatch without exposing protection state.
        if (
            item.get("physicalAttempted") is False
            and item.get("reason") == "manual_off_protection_active"
        ):
            reason = "manual_off_protection_active"
        manual_off_protection = _journal_manual_off_protection(item.get("protection"))
        if command_mode == "shadow":
            confirmed = None
            if action_outcome != "failed" and manual_off_protection is None:
                reason = "shadow_plan"
        action_trace: dict[str, object] = {
            "action_id": action_id,
            "outcome": action_outcome,
            "confirmed": confirmed if type(confirmed) is bool else None,
            "reason": reason,
        }
        target_id = item.get("target_id")
        if isinstance(target_id, str) and _CORRELATION_ID.fullmatch(target_id):
            action_trace["target_id"] = target_id
        actions.append(action_trace)
    completed = outcome == "completed"
    confirmed = command_mode == "live" and completed and result.get("confirmed") is True
    reason = _safe_trace_reason(
        result.get("reason") or result.get("error"),
        "scenario_failed"
        if not completed
        else "shadow_plan"
        if command_mode == "shadow"
        else None,
    )
    scenario_trace: dict[str, object] = {
        "scenario_id": scenario_id,
        "run_id": run_id,
        "execution_mode": execution_mode,
        "command_mode": command_mode,
        "outcome": outcome,
        "evidence_revision": (
            result.get("evidence_revision")
            if isinstance(result.get("evidence_revision"), str)
            else None
        ),
        "decisions": decisions[:64],
        "actions": actions[:64],
    }
    trigger = _validated_scenario_trigger(result.get("trigger_context"))
    if trigger is not None:
        scenario_trace["trigger"] = trigger
    receipt = {
        "correlation_id": run_id,
        "operation": "scenario_run",
        "accepted": completed,
        "confirmed": confirmed,
        "status": "confirmed" if confirmed else "accepted" if completed else "failed",
        "reason": reason,
        "error_code": reason,
        "scenario": scenario_trace,
    }
    for item in receipts:
        if isinstance(item, Mapping) and (
            protection := _journal_manual_off_protection(item.get("protection"))
        ) is not None:
            receipt["manualOffProtection"] = protection
            break
    return receipt


def _safe_trace_reason(value: object, fallback: str | None) -> str | None:
    """Keep stable reason codes, never raw entity names or exception text."""

    if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value):
        return value
    return fallback


def _journal_manual_off_protection(value: object) -> dict[str, object] | None:
    """Keep an operator-useful guard result without durable private IDs."""

    if not isinstance(value, Mapping) or value.get("allowed") is not False:
        return None
    room_id = value.get("roomId")
    profile_id = value.get("profileId")
    state = value.get("state")
    if not all(isinstance(item, str) and item for item in (room_id, profile_id, state)):
        return None
    return {"roomId": room_id, "profileId": profile_id, "state": state, "reason": "manual_off_protection_active"}


def _validated_journal_manual_off_protection(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or set(value) != {"roomId", "profileId", "state", "reason"}:
        return None
    if (
        not all(isinstance(value.get(key), str) and value.get(key) for key in ("roomId", "profileId"))
        or value.get("state") not in {"active", "ready_to_release", "released", "cancelled_by_manual_on"}
        or value.get("reason") not in {"manual_off", "manual_off_protection_active"}
    ):
        return None
    return dict(value)


def _validated_scenario_trace(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    required = {
        "scenario_id",
        "run_id",
        "execution_mode",
        "outcome",
        "evidence_revision",
        "decisions",
        "actions",
    }
    if (
        not isinstance(value, Mapping)
        or not required.issubset(value)
        or not set(value).issubset(required | {"command_mode", "trigger"})
    ):
        return None
    scenario_id = value.get("scenario_id")
    run_id = value.get("run_id")
    evidence_revision = value.get("evidence_revision")
    decisions = value.get("decisions")
    actions = value.get("actions")
    trigger_present = "trigger" in value
    trigger = _validated_scenario_trigger(value.get("trigger"))
    if (
        not isinstance(scenario_id, str)
        or _STABLE_ID.fullmatch(scenario_id) is None
        or not isinstance(run_id, str)
        or _CORRELATION_ID.fullmatch(run_id) is None
        or value.get("execution_mode") not in _EXECUTION_MODES
        or value.get("command_mode", "live") not in _COMMAND_MODES
        or value.get("outcome") not in _SCENARIO_OUTCOMES
        or (
            evidence_revision is not None
            and (not isinstance(evidence_revision, str) or len(evidence_revision) > 128)
        )
        or not isinstance(decisions, list)
        or len(decisions) > 64
        or not isinstance(actions, list)
        or len(actions) > 64
        or (trigger_present and trigger is None)
    ):
        return None
    normalized_decisions: list[dict[str, object]] = []
    for item in decisions:
        if not isinstance(item, Mapping) or set(item) != {
            "rule_id",
            "outcome",
            "reason",
        }:
            return None
        rule_id = item.get("rule_id")
        reason = item.get("reason")
        if (
            not isinstance(rule_id, str)
            or _STABLE_ID.fullmatch(rule_id) is None
            or item.get("outcome") not in {"passed", "skipped", "failed"}
            or (
                reason is not None
                and (not isinstance(reason, str) or len(reason) > 256)
            )
        ):
            return None
        normalized_decisions.append(dict(item))
    normalized_actions: list[dict[str, object]] = []
    for item in actions:
        required_action = {"action_id", "outcome", "confirmed", "reason"}
        if (
            not isinstance(item, Mapping)
            or not required_action.issubset(item)
            or not set(item).issubset(required_action | {"target_id"})
        ):
            return None
        action_id = item.get("action_id")
        confirmed = item.get("confirmed")
        reason = item.get("reason")
        target_id = item.get("target_id")
        if (
            not isinstance(action_id, str)
            or _STABLE_ID.fullmatch(action_id) is None
            or item.get("outcome") not in {"completed", "skipped", "failed"}
            or (confirmed is not None and type(confirmed) is not bool)
            or (value.get("command_mode") == "shadow" and confirmed is not None)
            or (
                target_id is not None
                and (
                    not isinstance(target_id, str)
                    or _CORRELATION_ID.fullmatch(target_id) is None
                )
            )
            or (
                reason is not None
                and (not isinstance(reason, str) or len(reason) > 256)
            )
        ):
            return None
        normalized_actions.append(dict(item))
    normalized = {
        "scenario_id": scenario_id,
        "run_id": run_id,
        "execution_mode": value.get("execution_mode"),
        "outcome": value.get("outcome"),
        "evidence_revision": evidence_revision,
        "decisions": normalized_decisions,
        "actions": normalized_actions,
    }
    if "command_mode" in value:
        normalized["command_mode"] = value.get("command_mode")
    if trigger is not None:
        normalized["trigger"] = trigger
    return normalized


def _validated_scenario_trigger(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    required = {"source", "trigger_id", "recovery"}
    allowed = required | {"target_id", "old_value", "new_value"}
    if not required.issubset(value) or not set(value).issubset(allowed):
        return None
    trigger_id = value.get("trigger_id")
    target_id = value.get("target_id")
    if (
        value.get("source")
        not in {"manual", "schedule", "device_state", "custom_event", "nested"}
        or (trigger_id is not None and (
            not isinstance(trigger_id, str) or _STABLE_ID.fullmatch(trigger_id) is None
        ))
        or (target_id is not None and (
            not isinstance(target_id, str)
            or _CORRELATION_ID.fullmatch(target_id) is None
        ))
        or type(value.get("recovery")) is not bool
        or any(
            item is not None and not isinstance(item, (str, int, float, bool))
            for item in (value.get("old_value"), value.get("new_value"))
        )
    ):
        return None
    return dict(value)
