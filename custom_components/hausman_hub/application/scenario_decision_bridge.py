"""Durable, fail-closed execution bridge for the staged Tambur controller.

The module is intentionally inactive.  Task 4 creates it only after the exact
Tambur bindings and settings migration has committed.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, timezone
import hashlib
import inspect
import json
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

from .scenario_node_red_decision import (
    TAMBUR_DECISION_SCENARIO_ID,
    _validate_mirror_turn_on_semantics,
    _validate_schema as _validate_tambur_schema,
    validate_tambur_decision,
    validate_tambur_decision_input,
)


_STORE_VERSION = 1
_MAX_HISTORY = 128
_MAX_MANUAL_INTENTS = 64
_MAX_RECEIPTS = 4
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ACTION_IDS = frozenset(
    {"turn_on", "turn_off", "set_brightness_percent", "set_color_temperature"}
)
_LEDGER_STATUSES = frozenset(
    {"prepared", "dispatching", "confirmed", "failed", "uncertain", "cancelled"}
)
_RECEIPT_STATUSES = frozenset({"confirmed", "failed", "uncertain"})
_UNAVAILABLE_STATES = frozenset({"unknown", "unavailable"})


class ScenarioDecisionRejected(RuntimeError):
    """The request cannot safely cross the physical dispatch boundary."""


class ScenarioDecisionConflict(ScenarioDecisionRejected):
    """A plan ID was reused with different content."""


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None


def _valid_safe_integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_SAFE_INTEGER


def _valid_wakeup(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"id", "kind", "dueAtMs"}
        and _valid_id(value.get("id"))
        and value.get("kind") in {"profile", "absence", "fade", "mirror", "hold"}
        and _valid_safe_integer(value.get("dueAtMs"))
    )


def _valid_receipt(value: object) -> bool:
    if not _valid_receipt_input(value):
        return False
    assert isinstance(value, Mapping)
    optional = {"observedRevision", "observedAtMs"}
    return value.get("status") != "confirmed" or optional.issubset(value)


def _valid_receipt_input(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    required = {"id", "planId", "actionId", "targetId", "status"}
    optional = {"observedRevision", "observedAtMs"}
    if not required.issubset(value) or not set(value).issubset(required | optional):
        return False
    if (
        not all(_valid_id(value.get(key)) for key in ("id", "planId", "targetId"))
        or value.get("actionId") not in _ACTION_IDS
        or value.get("status") not in {"confirmed", "failed", "uncertain"}
    ):
        return False
    if any(
        key in value and not _valid_safe_integer(value.get(key))
        for key in optional
    ):
        return False
    return True


def _valid_action(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    required = {"id", "targetId", "actionId", "authorityGeneration", "observedRevision"}
    if not required.issubset(value) or not set(value).issubset(required | {"value"}):
        return False
    action_id = value.get("actionId")
    if (
        not _valid_id(value.get("id"))
        or not _valid_id(value.get("targetId"))
        or action_id not in _ACTION_IDS
        or not _valid_safe_integer(value.get("authorityGeneration"))
        or not _valid_safe_integer(value.get("observedRevision"))
    ):
        return False
    if action_id in {"turn_on", "turn_off"}:
        return "value" not in value
    action_value = value.get("value")
    if type(action_value) is not int:
        return False
    if action_id == "set_brightness_percent":
        return 0 <= action_value <= 100
    return 1_500 <= action_value <= 10_000


def _valid_manual_value(value: object, *, depth: int = 0) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return len(value) <= 256
    if type(value) is int:
        return -1_000_000 <= value <= 1_000_000
    if type(value) is float:
        return math.isfinite(value) and -1_000_000 <= value <= 1_000_000
    if depth >= 3:
        return False
    if isinstance(value, list):
        return len(value) <= 32 and all(
            _valid_manual_value(item, depth=depth + 1) for item in value
        )
    if isinstance(value, Mapping):
        return len(value) <= 32 and all(
            isinstance(key, str)
            and len(key) <= 256
            and _valid_manual_value(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def _valid_stored_decision(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        _validate_tambur_schema(
            value,
            "scenario-node-red-decision.schema.json",
            "stored response",
        )
    except ValueError:
        return False
    return True


def _initial_durable() -> dict[str, object]:
    return {
        "revision": 0,
        "phase": "idle",
        "phaseStartedAtMs": None,
        "absenceSinceMs": None,
        "absenceEpoch": None,
        "fadeStartPercent": None,
        "fadeStartedAtMs": None,
        "fadeReason": None,
        "pendingReceiptId": None,
        "wakeups": [],
    }


def _initial_payload() -> dict[str, object]:
    return {
        "version": _STORE_VERSION,
        "scenarioId": TAMBUR_DECISION_SCENARIO_ID,
        "observationEpoch": 1,
        "snapshotRevision": 0,
        "durable": _initial_durable(),
        "receipts": [],
        "history": [],
        "manualIntents": [],
    }


def valid_scenario_decision_bridge_payload(value: object) -> bool:
    """Return whether one storage generation is complete and bounded."""

    if not isinstance(value, Mapping) or set(value) != {
        "version",
        "scenarioId",
        "observationEpoch",
        "snapshotRevision",
        "durable",
        "receipts",
        "history",
        "manualIntents",
    }:
        return False
    if (
        value.get("version") != _STORE_VERSION
        or value.get("scenarioId") != TAMBUR_DECISION_SCENARIO_ID
        or not _valid_safe_integer(value.get("observationEpoch"))
        or int(value["observationEpoch"]) < 1
        or not _valid_safe_integer(value.get("snapshotRevision"))
    ):
        return False
    durable = value.get("durable")
    if not isinstance(durable, Mapping) or set(durable) != set(_initial_durable()):
        return False
    if not _valid_safe_integer(durable.get("revision")):
        return False
    if durable.get("phase") not in {
        "idle", "occupied", "absent", "fade", "mirror_handover", "night", "blocked"
    }:
        return False
    nullable_ints = (
        "phaseStartedAtMs", "absenceSinceMs", "absenceEpoch",
        "fadeStartPercent", "fadeStartedAtMs",
    )
    if any(
        durable.get(key) is not None and not _valid_safe_integer(durable.get(key))
        for key in nullable_ints
    ):
        return False
    if (
        durable.get("fadeStartPercent") is not None
        and int(durable["fadeStartPercent"]) > 100
    ):
        return False
    if durable.get("fadeReason") not in {None, "absence", "night"}:
        return False
    if durable.get("pendingReceiptId") is not None and not _valid_id(
        durable.get("pendingReceiptId")
    ):
        return False
    wakeups = durable.get("wakeups")
    if (
        not isinstance(wakeups, list)
        or len(wakeups) > 4
        or not all(_valid_wakeup(item) for item in wakeups)
        or len({item["id"] for item in wakeups}) != len(wakeups)
    ):
        return False
    receipts = value.get("receipts")
    history = value.get("history")
    manual = value.get("manualIntents")
    if (
        not isinstance(receipts, list)
        or len(receipts) > _MAX_RECEIPTS
        or not all(_valid_receipt(item) for item in receipts)
        or len({item["id"] for item in receipts}) != len(receipts)
        or not isinstance(history, list)
        or len(history) > _MAX_HISTORY
        or not isinstance(manual, list)
        or len(manual) > _MAX_MANUAL_INTENTS
    ):
        return False
    history_keys = {
        "planId", "fingerprint", "status", "reasonCode", "action", "receiptId",
        "receipt", "decision", "preparedAtMs", "updatedAtMs",
    }
    plan_ids: set[str] = set()
    for item in history:
        if (
            not isinstance(item, Mapping)
            or set(item) != history_keys
            or not _valid_id(item.get("planId"))
            or not isinstance(item.get("fingerprint"), str)
            or re.fullmatch(r"[0-9a-f]{64}", str(item["fingerprint"])) is None
            or item.get("status") not in _LEDGER_STATUSES
            or not isinstance(item.get("reasonCode"), str)
            or not 1 <= len(str(item["reasonCode"])) <= 120
            or not _valid_stored_decision(item.get("decision"))
            or not _valid_safe_integer(item.get("preparedAtMs"))
            or not _valid_safe_integer(item.get("updatedAtMs"))
        ):
            return False
        plan_id = str(item["planId"])
        if plan_id in plan_ids:
            return False
        plan_ids.add(plan_id)
        action = item.get("action")
        decision = item["decision"]
        if (
            not _valid_action(action)
            or item["fingerprint"] != _digest(decision)
            or decision.get("planId") != plan_id
            or decision.get("reasonCode") != item["reasonCode"]
            or decision.get("action") != action
        ):
            return False
        receipt = item.get("receipt")
        if item.get("status") in _RECEIPT_STATUSES and receipt is None:
            return False
        if receipt is not None and (
            not _valid_receipt(receipt)
            or receipt.get("planId") != plan_id
            or not isinstance(action, Mapping)
            or receipt.get("id") != item.get("receiptId")
            or receipt.get("targetId") != action.get("targetId")
            or receipt.get("actionId") != action.get("actionId")
            or receipt.get("status") != item.get("status")
        ):
            return False
        if (
            not _valid_id(item.get("receiptId"))
            or item.get("receiptId") != f"receipt.{_digest(plan_id)[:24]}"
        ):
            return False
    manual_keys = {"requestId", "targetId", "actionId", "value", "registeredAtMs"}
    if any(
        not isinstance(item, Mapping)
        or set(item) != manual_keys
        or not all(
            _valid_id(item.get(key))
            for key in ("requestId", "targetId", "actionId")
        )
        or not _valid_manual_value(item.get("value"))
        or not _valid_safe_integer(item.get("registeredAtMs"))
        for item in manual
    ):
        return False
    return len({item["requestId"] for item in manual}) == len(manual)


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


class ScenarioDecisionBridge:
    """Persist decisions and receipts before and after physical execution."""

    def __init__(
        self,
        store: object,
        *,
        snapshot_provider: Callable[[str, object, int], object],
        authority_provider: Callable[[str], object],
        now_ms: Callable[[], int],
        executor: object | None = None,
    ) -> None:
        if not all(
            callable(candidate)
            for candidate in (snapshot_provider, authority_provider, now_ms)
        ):
            raise TypeError("scenario decision bridge providers are invalid")
        self._store = store
        self._snapshot_provider = snapshot_provider
        self._authority_provider = authority_provider
        self._now_ms = now_ms
        self._lock = asyncio.Lock()
        self._payload: dict[str, object] | None = None
        self._snapshots: dict[str, dict[str, object]] = {}
        self._cancellations: dict[str, asyncio.Event] = {}
        self._dispatch_crossed: set[str] = set()
        proof_state: dict[str, dict[str, object]] = {}

        def clear_evidence() -> None:
            proof_state.clear()

        def record_evidence(
            *,
            decision_observation_epoch: object,
            plan_id: str,
            decision_action_id: str,
            receipt_id: str,
            target_id: str,
            action_id: str,
            value: object,
            result: Mapping[str, object],
        ) -> None:
            payload = self._loaded()
            record = self._find(payload, plan_id)
            action = record.get("action") if record is not None else None
            persistent_decision = (
                record.get("decision") if record is not None else None
            )
            if (
                type(decision_observation_epoch) is not int
                or decision_observation_epoch != payload["observationEpoch"]
                or record is None
                or record.get("status") != "dispatching"
                or not isinstance(action, Mapping)
                or not isinstance(persistent_decision, Mapping)
                or persistent_decision.get("observationEpoch")
                != decision_observation_epoch
                or record.get("planId") != plan_id
                or action.get("id") != decision_action_id
                or record.get("receiptId") != receipt_id
                or action.get("targetId") != target_id
                or action.get("actionId") != action_id
                or action.get("value") != value
                or plan_id not in self._dispatch_crossed
            ):
                return
            read_back = result.get("read_back")
            proof_state[plan_id] = {
                "observationEpoch": decision_observation_epoch,
                "planId": plan_id,
                "decisionActionId": decision_action_id,
                "receiptId": receipt_id,
                "targetId": target_id,
                "actionId": action_id,
                "value": copy.deepcopy(value),
                "completed": result.get("status") == "completed",
                "confirmed": result.get("confirmed") is True,
                "evidenceRevision": (
                    read_back.get("evidenceRevision")
                    if isinstance(read_back, Mapping)
                    else None
                ),
            }

        async def async_record_receipt(
            plan_id: str, receipt: Mapping[str, object]
        ) -> dict[str, object]:
            """Consume closed executor evidence and persist one outcome."""

            if not _valid_receipt_input(receipt):
                raise ScenarioDecisionRejected("scenario receipt is invalid")
            async with self._lock:
                current = self._loaded()
                record = self._find(current, plan_id)
                action = record.get("action") if record is not None else None
                if record is None or not isinstance(action, Mapping):
                    raise ScenarioDecisionRejected(
                        "scenario receipt plan is invalid"
                    )
                expected = {
                    "id": record["receiptId"],
                    "planId": plan_id,
                    "actionId": action["actionId"],
                    "targetId": action["targetId"],
                }
                if any(
                    receipt.get(key) != expected_value
                    for key, expected_value in expected.items()
                ):
                    raise ScenarioDecisionRejected(
                        "scenario receipt binding is invalid"
                    )
                status = receipt.get("status")
                if status not in {"confirmed", "failed", "uncertain"}:
                    raise ScenarioDecisionRejected(
                        "scenario receipt status is invalid"
                    )
                crossed = plan_id in self._dispatch_crossed
                if status == "confirmed" and not crossed:
                    raise ScenarioDecisionRejected(
                        "scenario receipt has no physical dispatch evidence"
                    )
                proof = proof_state.pop(plan_id, None)
                authority: object = None
                if status == "confirmed" and record["status"] == "dispatching":
                    try:
                        authority = await _maybe_await(
                            self._authority_provider(str(action["targetId"]))
                        )
                    except Exception:  # noqa: BLE001 - fail-closed evidence
                        authority = None
                proof_matches = (
                    isinstance(proof, Mapping)
                    and proof.get("observationEpoch")
                    == current["observationEpoch"]
                    and proof.get("planId") == plan_id
                    and proof.get("decisionActionId") == action.get("id")
                    and proof.get("receiptId") == record["receiptId"]
                    and proof.get("targetId") == action.get("targetId")
                    and proof.get("actionId") == action.get("actionId")
                    and proof.get("value") == action.get("value")
                    and proof.get("completed") is True
                    and proof.get("confirmed") is True
                    and isinstance(proof.get("evidenceRevision"), str)
                    and bool(proof.get("evidenceRevision"))
                )
                authority_matches = (
                    isinstance(authority, Mapping)
                    and authority.get("fresh") is True
                    and authority.get("observationEpoch")
                    == current["observationEpoch"]
                    and authority.get("generation")
                    == action.get("authorityGeneration")
                    and authority.get("owner") in {"none", "automatic"}
                    and authority.get("protectionActive") is False
                    and _valid_safe_integer(
                        authority.get("observedRevision")
                    )
                    and int(authority["observedRevision"])
                    > int(action["observedRevision"])
                    and _valid_safe_integer(authority.get("observedAtMs"))
                    and isinstance(authority.get("evidenceRevision"), str)
                    and isinstance(proof, Mapping)
                    and authority.get("evidenceRevision")
                    == proof.get("evidenceRevision")
                )
                if status == "confirmed" and not (
                    record["status"] == "dispatching"
                    and crossed
                    and not self.cancellation_event(plan_id).is_set()
                    and proof_matches
                    and authority_matches
                ):
                    status = "uncertain"
                stored_receipt = self._terminal_receipt(
                    record,
                    str(status),
                    observed_revision=(
                        int(authority["observedRevision"])
                        if status == "confirmed"
                        and isinstance(authority, Mapping)
                        else None
                    ),
                    observed_at_ms=(
                        int(authority["observedAtMs"])
                        if status == "confirmed"
                        and isinstance(authority, Mapping)
                        else None
                    ),
                )
                updated = copy.deepcopy(current)
                changed = self._find(updated, plan_id)
                assert changed is not None
                changed["status"] = status
                changed["receipt"] = copy.deepcopy(stored_receipt)
                changed["updatedAtMs"] = self._now_ms()
                durable = dict(updated["durable"])
                durable["pendingReceiptId"] = None
                updated["durable"] = durable
                self._replace_recent_receipt(updated, stored_receipt)
                await self._save(updated)
                self._payload = updated
                event = {
                    "id": f"event.{record['receiptId']}",
                    "kind": "receipt",
                    "observedAtMs": self._now_ms(),
                    "targetId": action["targetId"],
                    "receiptId": record["receiptId"],
                }
                return {
                    "status": status,
                    "event": event,
                    "receipt": copy.deepcopy(stored_receipt),
                }

        async def async_recover() -> dict[str, object]:
            """Load exact storage and invalidate work that crossed a restart."""

            async with self._lock:
                load = getattr(self._store, "async_load", None)
                if not callable(load):
                    raise RuntimeError(
                        "scenario decision bridge store is unavailable"
                    )
                loaded = await load()
                if getattr(self._store, "recovered_previous", False):
                    raise RuntimeError(
                        "scenario decision bridge previous generation is ambiguous"
                    )
                if loaded is None:
                    payload = _initial_payload()
                    await self._save(payload)
                else:
                    if not valid_scenario_decision_bridge_payload(loaded):
                        raise RuntimeError(
                            "scenario decision bridge store is corrupt"
                        )
                    payload = copy.deepcopy(dict(loaded))
                    payload["observationEpoch"] = (
                        int(payload["observationEpoch"]) + 1
                    )
                    durable = dict(payload["durable"])
                    durable.update(
                        absenceSinceMs=None,
                        absenceEpoch=None,
                        pendingReceiptId=None,
                        wakeups=[],
                    )
                    payload["durable"] = durable
                    for record in payload["history"]:
                        if record["status"] == "prepared":
                            record["status"] = "cancelled"
                            record["updatedAtMs"] = self._now_ms()
                        elif record["status"] == "dispatching":
                            record["status"] = "uncertain"
                            record["receipt"] = self._terminal_receipt(
                                record, "uncertain"
                            )
                            self._replace_recent_receipt(
                                payload, record["receipt"]
                            )
                            record["updatedAtMs"] = self._now_ms()
                    await self._save(payload)
                self._payload = payload
                clear_evidence()
                self._snapshots.clear()
                self._dispatch_crossed.clear()
                self._cancellations = {
                    str(item["planId"]): asyncio.Event()
                    for item in payload["history"]
                    if item["status"] in {"prepared", "dispatching"}
                }
                return copy.deepcopy(payload)

        self.async_record_receipt = async_record_receipt
        self.async_recover = async_recover
        self._tambur_execution: Callable[
            [Mapping[str, object]], Awaitable[dict[str, object]]
        ] | None = None
        if executor is not None:
            from .scenario_executor import ScenarioExecutor

            if type(executor) is not ScenarioExecutor:
                raise TypeError("scenario decision executor is invalid")
            execution = ScenarioExecutor._build_tambur_execution(
                executor, self, record_evidence
            )
            if not callable(execution):
                raise TypeError("scenario decision executor channel is invalid")
            self._tambur_execution = execution

    async def _save(self, payload: dict[str, object]) -> None:
        if not valid_scenario_decision_bridge_payload(payload):
            raise RuntimeError("scenario decision bridge store is invalid")
        save = getattr(self._store, "async_save", None)
        if not callable(save):
            raise RuntimeError("scenario decision bridge store is unavailable")
        await save(copy.deepcopy(payload))

    def _loaded(self) -> dict[str, object]:
        if self._payload is None:
            raise RuntimeError("scenario decision bridge is not recovered")
        return self._payload

    async def async_snapshot(
        self, scenario_id: str, event: object
    ) -> dict[str, object]:
        """Capture one validator-bound request from real HA observations."""

        if scenario_id != TAMBUR_DECISION_SCENARIO_ID:
            raise ScenarioDecisionRejected("scenario decision bridge scenario is invalid")
        async with self._lock:
            current = self._loaded()
            revision = int(current["snapshotRevision"]) + 1
            epoch = int(current["observationEpoch"])
            source = await _maybe_await(
                self._snapshot_provider(scenario_id, copy.deepcopy(event), epoch)
            )
            if not isinstance(source, Mapping):
                raise ScenarioDecisionRejected("scenario snapshot source is invalid")
            event_digest = _digest(event)[:16]
            correlation_id = f"tambur.{epoch}.{revision}.{event_digest}"
            request = {
                **copy.deepcopy(dict(source)),
                "contract": {
                    "name": "hausman-node-red-decision-input",
                    "version": 1,
                },
                "correlationId": correlation_id,
                "scenarioId": scenario_id,
                "controllerVersion": 1,
                "snapshotRevision": revision,
                "observationEpoch": epoch,
                "event": copy.deepcopy(event),
                "durable": copy.deepcopy(current["durable"]),
                "receipts": copy.deepcopy(current["receipts"]),
            }
            try:
                validate_tambur_decision_input(request)
            except ValueError as error:
                raise ScenarioDecisionRejected(str(error)) from error
            updated = copy.deepcopy(current)
            updated["snapshotRevision"] = revision
            await self._save(updated)
            self._payload = updated
            self._snapshots = {correlation_id: copy.deepcopy(request)}
            return request

    def _history(self, payload: Mapping[str, object]) -> list[dict[str, object]]:
        return payload["history"]  # type: ignore[return-value]

    def _find(self, payload: Mapping[str, object], plan_id: str) -> dict[str, object] | None:
        return next(
            (item for item in reversed(self._history(payload)) if item["planId"] == plan_id),
            None,
        )

    @staticmethod
    def _terminal_receipt(
        record: Mapping[str, object],
        status: str,
        *,
        observed_revision: int | None = None,
        observed_at_ms: int | None = None,
    ) -> dict[str, object]:
        action = record.get("action")
        if not isinstance(action, Mapping) or status not in _RECEIPT_STATUSES:
            raise RuntimeError("scenario terminal receipt source is invalid")
        receipt: dict[str, object] = {
            "id": record["receiptId"],
            "planId": record["planId"],
            "actionId": action["actionId"],
            "targetId": action["targetId"],
            "status": status,
        }
        if observed_revision is not None:
            receipt["observedRevision"] = observed_revision
        if observed_at_ms is not None:
            receipt["observedAtMs"] = observed_at_ms
        return receipt

    @staticmethod
    def _replace_recent_receipt(
        payload: dict[str, object], receipt: Mapping[str, object]
    ) -> None:
        payload["receipts"] = [
            *(
                item
                for item in payload["receipts"]
                if item["id"] != receipt["id"]
            ),
            copy.deepcopy(dict(receipt)),
        ][-_MAX_RECEIPTS:]

    def _accepted(
        self, record: Mapping[str, object], *, replayed: bool
    ) -> dict[str, object]:
        return {
            "planId": record["planId"],
            "receiptId": record["receiptId"],
            "status": record["status"],
            "action": copy.deepcopy(record["action"]),
            "replayed": replayed,
        }

    def _append_history(
        self,
        history: list[dict[str, object]],
        record: dict[str, object],
    ) -> list[dict[str, object]]:
        overflow = len(history) + 1 - _MAX_HISTORY
        if overflow <= 0:
            return [*history, record]
        terminal_indexes = [
            index
            for index, item in enumerate(history)
            if item["status"] not in {"prepared", "dispatching", "uncertain"}
        ]
        if len(terminal_indexes) < overflow:
            raise ScenarioDecisionRejected(
                "scenario history is blocked by unresolved plans"
            )
        removed = set(terminal_indexes[:overflow])
        return [
            *(item for index, item in enumerate(history) if index not in removed),
            record,
        ]

    def _resolve_uncertain(
        self, payload: Mapping[str, object], target_id: str
    ) -> bool:
        """Keep ambiguous physical outcomes blocked until explicit reconciliation."""

        return not any(
            item["status"] == "uncertain"
            and isinstance(item.get("action"), Mapping)
            and item["action"].get("targetId") == target_id
            for item in self._history(payload)
        )

    async def async_accept(self, decision: Mapping[str, object]) -> dict[str, object]:
        """Persist one validated decision before any device side effect."""

        if not isinstance(decision, Mapping):
            raise ScenarioDecisionRejected("scenario decision is invalid")
        plan_id = decision.get("planId")
        correlation_id = decision.get("correlationId")
        if not isinstance(plan_id, str) or not isinstance(correlation_id, str):
            raise ScenarioDecisionRejected("scenario decision identity is invalid")
        fingerprint = _digest(decision)
        async with self._lock:
            current = self._loaded()
            existing = self._find(current, plan_id)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise ScenarioDecisionConflict("scenario plan content conflicts")
                return self._accepted(existing, replayed=True)
            request = self._snapshots.get(correlation_id)
            if request is None:
                raise ScenarioDecisionRejected("scenario decision snapshot is stale")
            try:
                validate_tambur_decision(request, decision)
            except ValueError as error:
                raise ScenarioDecisionRejected(str(error)) from error
            if self._now_ms() > int(decision["expiresAtMs"]):
                raise ScenarioDecisionRejected("scenario decision has expired")
            updated = copy.deepcopy(current)
            action = decision.get("action")
            if isinstance(action, Mapping):
                target_id = str(action["targetId"])
                if not self._resolve_uncertain(updated, target_id):
                    raise ScenarioDecisionRejected(
                        "uncertain target requires explicit reconciliation"
                    )
                if any(
                    item["status"] in {"prepared", "dispatching"}
                    and isinstance(item.get("action"), Mapping)
                    and item["action"].get("targetId") == target_id
                    for item in self._history(updated)
                ):
                    raise ScenarioDecisionRejected(
                        "scenario target already has an active plan"
                    )
            durable = {
                **copy.deepcopy(dict(decision["nextState"])),
                "revision": int(current["durable"]["revision"]) + 1,
                "pendingReceiptId": None,
                "wakeups": copy.deepcopy(decision["wakeups"]),
            }
            receipt_id = f"receipt.{_digest(plan_id)[:24]}"
            status = "prepared" if isinstance(action, Mapping) else "cancelled"
            if isinstance(action, Mapping):
                durable["pendingReceiptId"] = receipt_id
            now = self._now_ms()
            record = {
                "planId": plan_id,
                "fingerprint": fingerprint,
                "status": status,
                "reasonCode": str(decision["reasonCode"]),
                "action": copy.deepcopy(action),
                "receiptId": receipt_id,
                "receipt": None,
                "decision": copy.deepcopy(dict(decision)),
                "preparedAtMs": now,
                "updatedAtMs": now,
            }
            updated["durable"] = durable
            updated["history"] = self._append_history(
                self._history(updated), record
            )
            await self._save(updated)
            self._payload = updated
            self._cancellations[plan_id] = asyncio.Event()
            return self._accepted(record, replayed=False)

    def cancellation_event(self, plan_id: str) -> asyncio.Event:
        """Return the live fence used to interrupt a dependency warmup."""

        return self._cancellations.setdefault(plan_id, asyncio.Event())

    def mark_dispatch_crossed(self, plan_id: str, action_id: str) -> None:
        """Mark the in-process physical boundary without pretending it is durable."""

        payload = self._loaded()
        record = self._find(payload, plan_id)
        action = record.get("action") if record is not None else None
        if isinstance(action, Mapping) and action.get("id") == action_id:
            self._dispatch_crossed.add(plan_id)

    async def async_before_dispatch(self, plan_id: str, action_id: str) -> None:
        """Revalidate authority and persist dispatching before every side effect."""

        async with self._lock:
            current = self._loaded()
            record = self._find(current, plan_id)
            action = record.get("action") if record is not None else None
            if (
                record is None
                or not isinstance(action, Mapping)
                or action.get("id") != action_id
                or record["status"] not in {"prepared", "dispatching"}
            ):
                raise ScenarioDecisionRejected("scenario plan is not dispatchable")
            cancellation = self.cancellation_event(plan_id)
            if cancellation.is_set():
                raise ScenarioDecisionRejected("scenario plan was cancelled manually")
            night_mirror_safe = True
            persistent_decision = record.get("decision")
            if (
                action.get("actionId") == "turn_on"
                and isinstance(persistent_decision, Mapping)
                and persistent_decision.get("reasonCode")
                in {"night_mirror_on", "mirror_schedule_on"}
            ):
                request = self._snapshots.get(plan_id)
                if request is None:
                    night_mirror_safe = False
                else:
                    try:
                        source = await _maybe_await(
                            self._snapshot_provider(
                                TAMBUR_DECISION_SCENARIO_ID,
                                copy.deepcopy(request["event"]),
                                int(current["observationEpoch"]),
                            )
                        )
                        if not isinstance(source, Mapping):
                            raise ValueError("dispatch snapshot is invalid")
                        current_request = {
                            **copy.deepcopy(request),
                            **copy.deepcopy(dict(source)),
                            "event": copy.deepcopy(request["event"]),
                            "observationEpoch": current["observationEpoch"],
                        }
                        _validate_mirror_turn_on_semantics(
                            current_request, persistent_decision, action
                        )
                    except (KeyError, TypeError, ValueError):
                        night_mirror_safe = False
            authority = await _maybe_await(self._authority_provider(str(action["targetId"])))
            if (
                not night_mirror_safe
                or not isinstance(authority, Mapping)
                or authority.get("fresh") is not True
                or authority.get("observationEpoch") != current["observationEpoch"]
                or authority.get("generation") != action.get("authorityGeneration")
                or authority.get("owner") not in {"none", "automatic"}
                or authority.get("protectionActive") is not False
                or type(authority.get("observedRevision")) is not int
                or int(authority["observedRevision"]) < int(action["observedRevision"])
            ):
                updated = copy.deepcopy(current)
                changed = self._find(updated, plan_id)
                assert changed is not None
                changed["status"] = "cancelled"
                changed["updatedAtMs"] = self._now_ms()
                await self._save(updated)
                self._payload = updated
                cancellation.set()
                raise ScenarioDecisionRejected("scenario authority changed before dispatch")
            if record["status"] == "prepared":
                updated = copy.deepcopy(current)
                changed = self._find(updated, plan_id)
                assert changed is not None
                changed["status"] = "dispatching"
                changed["updatedAtMs"] = self._now_ms()
                await self._save(updated)
                self._payload = updated
            if cancellation.is_set():
                raise ScenarioDecisionRejected("scenario plan was cancelled manually")

    async def async_register_manual_intent(
        self,
        request_id: str,
        target_id: str,
        action_id: str,
        value: object | None,
    ) -> None:
        """Fence automatic work before the public manual path waits on light lock."""

        await self.async_register_manual_intents(
            request_id,
            (
                {
                    "targetId": target_id,
                    "actionId": action_id,
                    "value": value,
                },
            ),
        )

    async def async_register_manual_intents(
        self,
        request_id: str,
        actions: tuple[Mapping[str, object], ...],
    ) -> None:
        """Persist a bounded multi-target manual fence in one durable save."""

        if (
            not _valid_id(request_id)
            or not 1 <= len(actions) <= 3
            or any(
                not isinstance(item, Mapping)
                or set(item) != {"targetId", "actionId", "value"}
                or not _valid_id(item.get("targetId"))
                or not _valid_id(item.get("actionId"))
                or not _valid_manual_value(item.get("value"))
                for item in actions
            )
            or len({str(item["targetId"]) for item in actions}) != len(actions)
        ):
            raise ScenarioDecisionRejected("manual intent is invalid")

        normalized = tuple(
            {
                "requestId": (
                    request_id
                    if len(actions) == 1
                    else f"{request_id}.fence.{index + 1}"
                ),
                "targetId": str(item["targetId"]),
                "actionId": str(item["actionId"]),
                "value": copy.deepcopy(item.get("value")),
            }
            for index, item in enumerate(actions)
        )
        if any(not _valid_id(str(item["requestId"])) for item in normalized):
            raise ScenarioDecisionRejected("manual intent is invalid")
        target_ids = {str(item["targetId"]) for item in normalized}

        def matching_intents(
            payload: Mapping[str, object],
        ) -> dict[str, Mapping[str, object]]:
            expected_ids = {str(item["requestId"]) for item in normalized}
            return {
                str(item["requestId"]): item
                for item in reversed(payload["manualIntents"])
                if item["requestId"] in expected_ids
            }

        def is_same(
            item: Mapping[str, object], expected: Mapping[str, object]
        ) -> bool:
            return (
                item.get("targetId") == expected["targetId"]
                and item.get("actionId") == expected["actionId"]
                and item.get("value") == expected["value"]
            )

        def affected_plans(payload: Mapping[str, object]) -> set[str]:
            return {
                str(record["planId"])
                for record in self._history(payload)
                if record["status"] in {"prepared", "dispatching"}
                and isinstance(record.get("action"), Mapping)
                and record["action"].get("targetId") in target_ids
            }

        payload = self._loaded()
        existing = matching_intents(payload)
        if existing:
            if len(existing) == len(normalized) and all(
                is_same(existing[str(item["requestId"])], item)
                for item in normalized
            ):
                return
            raise ScenarioDecisionConflict("manual request content conflicts")
        affected = affected_plans(payload)
        for plan_id in affected:
            self.cancellation_event(plan_id).set()
        async with self._lock:
            current = self._loaded()
            existing = matching_intents(current)
            if existing:
                if len(existing) == len(normalized) and all(
                    is_same(existing[str(item["requestId"])], item)
                    for item in normalized
                ):
                    return
                raise ScenarioDecisionConflict("manual request content conflicts")
            affected.update(affected_plans(current))
            for plan_id in affected:
                self.cancellation_event(plan_id).set()
            # A response derived from any older snapshot cannot overwrite a
            # manual fence or reintroduce wakeups, even if persistence fails.
            self._snapshots.clear()
            updated = copy.deepcopy(current)
            for plan_id in affected:
                record = self._find(updated, plan_id)
                if record is not None and record["status"] in {"prepared", "dispatching"}:
                    record["status"] = (
                        "uncertain" if plan_id in self._dispatch_crossed else "cancelled"
                    )
                    if record["status"] == "uncertain":
                        record["receipt"] = self._terminal_receipt(
                            record, "uncertain"
                        )
                        self._replace_recent_receipt(updated, record["receipt"])
                    record["updatedAtMs"] = self._now_ms()
            registered_at = self._now_ms()
            manuals = [
                {**item, "registeredAtMs": registered_at}
                for item in normalized
            ]
            updated["manualIntents"] = [
                *updated["manualIntents"], *manuals
            ][-_MAX_MANUAL_INTENTS:]
            durable = dict(updated["durable"])
            durable["wakeups"] = []
            updated["durable"] = durable
            await self._save(updated)
            self._payload = updated

    async def async_execute_decision(
        self, decision: Mapping[str, object]
    ) -> dict[str, object]:
        """Execute through the closed channel bound during bridge creation."""

        if self._tambur_execution is None:
            raise ScenarioDecisionRejected(
                "scenario decision executor is unavailable"
            )
        return await self._tambur_execution(decision)


class TamburHaObservationCoordinator:
    """Translate real HA state reports into contract observations, never policy."""

    def __init__(
        self,
        hass: object,
        *,
        bindings: Mapping[str, object],
        entity_id_provider: Callable[[str], str | None],
        settings: Mapping[str, object],
        settings_revision: int,
        authority_provider: Callable[[str], object],
        freshness_deadline_provider: Callable[[str, str, int], object],
        now_ms: Callable[[], int],
        timezone_name: str | None = None,
        sunset_provider: Callable[[str], object] | None = None,
        sunrise_provider: Callable[[str], object] | None = None,
        track_state_changes: Callable[..., Callable[[], None]] | None = None,
        track_state_reports: Callable[..., Callable[[], None]] | None = None,
    ) -> None:
        if not all(
            callable(candidate)
            for candidate in (
                entity_id_provider,
                authority_provider,
                freshness_deadline_provider,
                now_ms,
            )
        ):
            raise TypeError("Tambur observation providers are invalid")
        configured_timezone = timezone_name or getattr(
            getattr(hass, "config", None), "time_zone", None
        )
        if not isinstance(configured_timezone, str) or not configured_timezone:
            raise ValueError("Tambur Home Assistant timezone is unavailable")
        self._timezone = ZoneInfo(configured_timezone)
        self._hass = hass
        self._bindings = copy.deepcopy(dict(bindings))
        self._settings = copy.deepcopy(dict(settings))
        self._settings_revision = settings_revision
        self._entity_id_provider = entity_id_provider
        self._authority_provider = authority_provider
        self._freshness_deadline_provider = freshness_deadline_provider
        self._now_ms = now_ms
        self._sunset_provider = sunset_provider or self._ha_sunset_for_date
        self._sunrise_provider = sunrise_provider or self._ha_sunrise_for_date
        self._track_changes = track_state_changes
        self._track_reports = track_state_reports
        self._running = False
        self._continuity_generation = 0
        self._sequence = 0
        self._observed: dict[str, dict[str, object]] = {}
        self._reasons: dict[str, str] = {}
        self._unsubscribers: list[Callable[[], None]] = []
        targets = [
            self._bindings.get("chandelier"),
            self._bindings.get("points"),
            self._bindings.get("mirror"),
            *(self._bindings.get("presenceSensors") or []),
        ]
        self._target_entities = {
            str(target): entity
            for target in targets
            if isinstance(target, str)
            and isinstance((entity := entity_id_provider(target)), str)
        }
        self._entity_targets = {
            entity: target for target, entity in self._target_entities.items()
        }
        self._light_targets = frozenset(
            str(self._bindings[name])
            for name in ("chandelier", "points", "mirror")
            if isinstance(self._bindings.get(name), str)
        )
        self._presence_targets = frozenset(
            str(item)
            for item in (self._bindings.get("presenceSensors") or [])
            if isinstance(item, str)
        )
        self._chandelier_target = (
            str(self._bindings["chandelier"])
            if isinstance(self._bindings.get("chandelier"), str)
            else None
        )
        self._power_entity = (
            entity_id_provider(str(self._bindings["power"]))
            if isinstance(self._bindings.get("power"), str)
            else None
        )

    def _ha_sunset_for_date(self, local_date: str) -> int | None:
        try:
            from homeassistant.const import SUN_EVENT_SUNSET
            from homeassistant.helpers.sun import get_astral_event_date

            requested = date.fromisoformat(local_date)
            observed = get_astral_event_date(self._hass, SUN_EVENT_SUNSET, requested)
        except (ImportError, RuntimeError, TypeError, ValueError):
            return None
        if not isinstance(observed, datetime):
            return None
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return int(observed.timestamp() * 1000)

    def _ha_sunrise_for_date(self, local_date: str) -> int | None:
        try:
            from homeassistant.const import SUN_EVENT_SUNRISE
            from homeassistant.helpers.sun import get_astral_event_date

            requested = date.fromisoformat(local_date)
            observed = get_astral_event_date(self._hass, SUN_EVENT_SUNRISE, requested)
        except (ImportError, RuntimeError, TypeError, ValueError):
            return None
        if not isinstance(observed, datetime):
            return None
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return int(observed.timestamp() * 1000)

    def start(self) -> Callable[[], None]:
        """Subscribe explicitly; construction alone never activates the controller."""

        if self._running:
            raise RuntimeError("Tambur observation coordinator is already active")
        if self._track_changes is None or self._track_reports is None:
            from homeassistant.helpers.event import (
                async_track_state_change_event,
                async_track_state_report_event,
            )

            self._track_changes = async_track_state_change_event
            self._track_reports = async_track_state_report_event
        self._running = True
        self._continuity_generation += 1
        entities = tuple(self._entity_targets)
        self._unsubscribers = [
            self._track_changes(self._hass, entities, self._record_event),
            self._track_reports(self._hass, entities, self._record_event),
        ]

        def stop() -> None:
            if not self._running:
                return
            self._running = False
            self._continuity_generation += 1
            for unsubscribe in self._unsubscribers:
                unsubscribe()
            self._unsubscribers.clear()

        return stop

    def _record_event(self, event: object) -> None:
        data = getattr(event, "data", None)
        if not self._running or not isinstance(data, Mapping):
            return
        state = data.get("new_state")
        entity_id = data.get("entity_id") or getattr(state, "entity_id", None)
        target_id = self._entity_targets.get(str(entity_id))
        if target_id is None or state is None:
            return
        reported = data.get("last_reported") or getattr(state, "last_reported", None)
        reported_ms: int | None = None
        if isinstance(reported, datetime):
            if reported.tzinfo is None:
                reported = reported.replace(tzinfo=timezone.utc)
            reported_ms = int(reported.timestamp() * 1000)
        self._sequence += 1
        self._observed[target_id] = {
            "state": str(getattr(state, "state", "unknown")),
            "attributes": copy.deepcopy(getattr(state, "attributes", {})),
            "reportedAtMs": reported_ms,
            "revision": self._sequence,
            "continuityGeneration": self._continuity_generation,
        }

    def freshness_reason(self, target_id: str) -> str:
        return self._reasons.get(target_id, "continuity_not_observed")

    async def async_authority_snapshot(
        self, target_id: str, observation_epoch: int
    ) -> dict[str, object]:
        """Bind ownership to this coordinator's fresh observed state."""

        entity_id = self._target_entities.get(target_id)
        if entity_id is None:
            return {
                "owner": "uncertain",
                "generation": 0,
                "protectionActive": True,
                "observedRevision": 0,
                "observedAtMs": 0,
                "observationEpoch": observation_epoch,
                "fresh": False,
            }
        observation = await self._observation(
            target_id, entity_id, observation_epoch
        )
        ownership = await _maybe_await(self._authority_provider(target_id))
        if not isinstance(ownership, Mapping):
            ownership = {}
        state = getattr(
            getattr(self._hass, "states", None), "get", lambda _id: None
        )(entity_id)
        updated = getattr(state, "last_updated", None)
        evidence_revision = (
            updated.isoformat()
            if isinstance(updated, datetime)
            else None
        )
        result = {
            "owner": ownership.get("owner", "uncertain"),
            "generation": ownership.get("generation", 0),
            "protectionActive": ownership.get("protectionActive", True),
            "observedRevision": observation["revision"],
            "observedAtMs": observation["observedAtMs"],
            "observationEpoch": observation_epoch,
            "fresh": observation["fresh"],
            "evidenceRevision": evidence_revision,
        }
        confirmed_receipt = ownership.get("confirmedReceiptId")
        if (
            result["owner"] == "automatic"
            and result["protectionActive"] is False
            and isinstance(confirmed_receipt, str)
            and confirmed_receipt
        ):
            result["confirmedReceiptId"] = confirmed_receipt
            result["confirmedStateRevision"] = observation["revision"]
        return result

    async def _observation(
        self, target_id: str, entity_id: str, observation_epoch: int
    ) -> dict[str, object]:
        recorded = self._observed.get(target_id)
        current_state = getattr(getattr(self._hass, "states", None), "get", lambda _id: None)(entity_id)
        state_value = str(getattr(current_state, "state", "unknown"))
        effective = self._effective_chandelier_state(target_id, current_state)
        if effective is not None:
            state_value = effective
        revision = int(recorded["revision"]) if recorded is not None else 0
        observed_at = int(recorded["reportedAtMs"] or 0) if recorded is not None else 0
        reason = "continuity_not_observed"
        fresh = False
        if not self._running:
            reason = "continuity_broken"
        elif recorded is None or recorded.get("continuityGeneration") != self._continuity_generation:
            fallback = self._last_known_observation(
                target_id,
                current_state,
                state_value,
                observation_epoch,
                allow=self._light_targets,
                only_on=False,
                reason="last_known_light_state",
            )
            if fallback is not None:
                self._reasons[target_id] = str(fallback.pop("_reason"))
                return fallback
            reason = "continuity_not_observed"
        elif str(recorded.get("state")) in _UNAVAILABLE_STATES or state_value in _UNAVAILABLE_STATES:
            reason = f"state_{state_value if state_value in _UNAVAILABLE_STATES else recorded['state']}"
        elif str(recorded.get("state")) != state_value:
            reason = "continuity_broken"
        elif recorded.get("reportedAtMs") is None:
            reason = "last_reported_missing"
        else:
            try:
                deadline = await _maybe_await(
                    self._freshness_deadline_provider(
                        target_id, entity_id, observed_at
                    )
                )
            except Exception:  # noqa: BLE001 - provider failure is safety evidence
                reason = "freshness_deadline_unavailable"
            else:
                if deadline is None:
                    reason = "freshness_deadline_missing"
                elif type(deadline) is not int:
                    reason = "freshness_deadline_invalid"
                elif int(deadline) < self._now_ms():
                    reason = "freshness_deadline_expired"
                else:
                    reason = "fresh"
                    fresh = True
        if reason == "freshness_deadline_expired" and target_id in self._light_targets:
            fallback = self._last_known_observation(
                target_id,
                current_state,
                state_value,
                observation_epoch,
                allow=self._light_targets,
                only_on=False,
                reason="last_known_light_state",
            )
            if fallback is not None:
                self._reasons[target_id] = str(fallback.pop("_reason"))
                return fallback
        if not fresh and target_id in self._presence_targets:
            fallback = self._last_known_observation(
                target_id,
                current_state,
                state_value,
                observation_epoch,
                allow=self._presence_targets,
                only_on=True,
                reason="last_known_presence_on",
            )
            if fallback is not None:
                self._reasons[target_id] = str(fallback.pop("_reason"))
                return fallback
        self._reasons[target_id] = reason
        attributes = recorded.get("attributes", {}) if recorded is not None else {}
        result: dict[str, object] = {
            "state": state_value,
            "revision": revision,
            "observedAtMs": observed_at,
            "fresh": fresh,
            "continuityEpoch": observation_epoch if fresh else 0,
        }
        if isinstance(attributes, Mapping):
            brightness = attributes.get("brightness")
            if type(brightness) is int and 0 <= brightness <= 255:
                result["brightnessPercent"] = round(brightness * 100 / 255)
            kelvin = attributes.get("color_temp_kelvin")
            if type(kelvin) is int and 1500 <= kelvin <= 10000:
                result["colorTemperatureKelvin"] = kelvin
        return result

    def _effective_chandelier_state(
        self, target_id: str, current_state: object | None
    ) -> str | None:
        """Report an unpowered chandelier as off so profiles can enable power."""

        if target_id != self._chandelier_target or self._power_entity is None:
            return None
        reported = str(getattr(current_state, "state", "unknown")).strip().casefold()
        if reported != "on":
            return None
        power = getattr(getattr(self._hass, "states", None), "get", lambda _id: None)(
            self._power_entity
        )
        value = str(getattr(power, "state", "unknown")).strip().casefold()
        if value != "off":
            return None
        attributes = getattr(power, "attributes", None)
        if isinstance(attributes, Mapping) and (
            attributes.get("restored") is True
            or attributes.get("cached") is True
            or attributes.get("assumed_state") is True
        ):
            return None
        return "off"

    def _last_known_observation(
        self,
        target_id: str,
        current_state: object | None,
        state_value: str,
        observation_epoch: int,
        *,
        allow: frozenset[str],
        only_on: bool,
        reason: str,
    ) -> dict[str, object] | None:
        """Trust the last known state when a device only reports on change."""

        if target_id not in allow:
            return None
        value = state_value.strip().casefold()
        if value not in {"on", "off"}:
            return None
        if only_on and value != "on":
            return None
        attributes = getattr(current_state, "attributes", None)
        if isinstance(attributes, Mapping) and (
            attributes.get("restored") is True
            or attributes.get("cached") is True
            or attributes.get("assumed_state") is True
        ):
            return None
        observed = (
            getattr(current_state, "last_reported", None)
            or getattr(current_state, "last_updated", None)
            or getattr(current_state, "last_changed", None)
        )
        if isinstance(observed, datetime):
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            observed_at = int(observed.timestamp() * 1000)
        else:
            observed_at = self._now_ms()
        revision = observed_at if observed_at > 0 else self._now_ms()
        result: dict[str, object] = {
            "state": value,
            "revision": revision,
            "observedAtMs": observed_at,
            "fresh": True,
            "continuityEpoch": observation_epoch,
            "_reason": reason,
        }
        if isinstance(attributes, Mapping):
            brightness = attributes.get("brightness")
            if type(brightness) is int and 0 <= brightness <= 255:
                result["brightnessPercent"] = round(brightness * 100 / 255)
            kelvin = attributes.get("color_temp_kelvin")
            if type(kelvin) is int and 1500 <= kelvin <= 10000:
                result["colorTemperatureKelvin"] = kelvin
        return result

    async def async_snapshot_source(
        self, scenario_id: str, event: object, observation_epoch: int
    ) -> dict[str, object]:
        """Build only evidence and configuration fields; Node-RED owns policy."""

        if scenario_id != TAMBUR_DECISION_SCENARIO_ID:
            raise ScenarioDecisionRejected("Tambur snapshot scenario is invalid")
        now = self._now_ms()
        local = datetime.fromtimestamp(now / 1000, timezone.utc).astimezone(self._timezone)
        local_date = local.date().isoformat()
        sunset = await _maybe_await(self._sunset_provider(local_date))
        sunset_ms = sunset if type(sunset) is int and sunset >= 0 else None
        sunrise = await _maybe_await(self._sunrise_provider(local_date))
        sunrise_ms = sunrise if type(sunrise) is int and sunrise >= 0 else None
        observations = {
            target: await self._observation(target, entity, observation_epoch)
            for target, entity in self._target_entities.items()
        }
        light_targets = [
            self._bindings[name] for name in ("chandelier", "points", "mirror")
        ]
        authority: dict[str, object] = {}
        allowed_authority_keys = {
            "owner", "generation", "protectionActive", "confirmedReceiptId",
            "confirmedStateRevision", "lastManualAtMs", "protectedUntilMs",
            "manualOnHoldUntilMs",
        }
        for target in light_targets:
            value = await _maybe_await(self._authority_provider(str(target)))
            if not isinstance(value, Mapping):
                value = {"owner": "uncertain", "generation": 0, "protectionActive": True}
            authority[str(target)] = {
                key: copy.deepcopy(item)
                for key, item in value.items()
                if key in allowed_authority_keys
            }
            if (
                authority[str(target)].get("owner") == "automatic"
                and authority[str(target)].get("protectionActive") is False
                and isinstance(authority[str(target)].get("confirmedReceiptId"), str)
            ):
                authority[str(target)]["confirmedStateRevision"] = observations[str(target)]["revision"]
        return {
            "settingsRevision": self._settings_revision,
            "issuedAtMs": now,
            "expiresAtMs": now + 60_000,
            "clock": {
                "nowMs": now,
                "timezone": self._timezone.key,
                "localDate": local_date,
                "minutesOfDay": local.hour * 60 + local.minute,
                "sunsetAtMs": sunset_ms,
                "sunriseAtMs": sunrise_ms,
            },
            "bindings": copy.deepcopy(self._bindings),
            "settings": copy.deepcopy(self._settings),
            "observations": observations,
            "authority": authority,
        }
