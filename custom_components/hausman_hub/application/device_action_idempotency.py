"""Restart-safe global idempotency for dangerous physical device actions."""

from __future__ import annotations

import asyncio
import copy
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

MAX_DANGEROUS_IDEMPOTENCY_RECORDS = 256
_STATES = frozenset({"reserved", "pending", "dispatch_unknown", "completed"})
_PHASES = frozenset({"not_started", "dispatching", "dispatched"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,511}$")
_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/vnd.hausmanhub.device-action-receipt.full+json",
        "application/vnd.hausmanhub.device-action-batch-receipt.full+json",
    }
)
_RECEIPT_CONTRACTS = frozenset(
    {
        "hausman-hub-device-action-receipt",
        "hausman-hub-device-action-batch-receipt",
    }
)


class DeviceActionIdempotencyStore(Protocol):
    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


@dataclass(frozen=True, slots=True)
class ReservationResult:
    """Result of an atomic global idempotency reservation."""

    outcome: str
    state: str
    receipt: dict[str, object] | None = None
    existing_fingerprint: str | None = None
    response_media_type: str | None = None


class DangerousActionIdempotency:
    """Persist reservation before dispatch and never redispatch uncertain work."""

    def __init__(self, store: DeviceActionIdempotencyStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._records: dict[str, dict[str, object]] = {}
        self._load_error = False

    async def async_load(self) -> None:
        payload = await self._store.async_load()
        if payload is None:
            return
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            self._load_error = True
            return
        records = payload.get("records")
        if not isinstance(records, list):
            self._load_error = True
            return
        if len(records) > MAX_DANGEROUS_IDEMPOTENCY_RECORDS:
            self._load_error = True
            return
        normalized: dict[str, dict[str, object]] = {}
        changed = False
        for item in records:
            record = _validated_record(item)
            if record is None:
                self._load_error = True
                return
            if (
                record["state"] == "pending"
                and record["dispatchPhase"] in {"dispatching", "dispatched"}
            ):
                record["state"] = "dispatch_unknown"
                changed = True
            key = str(record["key"])
            if key in normalized:
                self._load_error = True
                return
            normalized[key] = record
        self._records = normalized
        if changed:
            await self._store.async_save(self._payload())

    async def async_reserve(
        self,
        *,
        key: str,
        fingerprint: str,
        dispatch_id: str,
        bindings: list[dict[str, object]],
        response_media_type: str = "application/json",
    ) -> ReservationResult:
        """Reserve one key durably before any physical dispatch."""

        async with self._lock:
            if self._load_error:
                raise RuntimeError("dangerous action idempotency store is corrupt")
            existing = self._records.get(key)
            if existing is not None:
                return self._existing_result(existing, fingerprint)
            normalized_bindings = [
                {**copy.deepcopy(binding), "dispatchId": dispatch_id}
                for binding in bindings
            ]
            if (
                not normalized_bindings
                or not all(_valid_binding(item) for item in normalized_bindings)
                or len(
                    {item["actionIndex"] for item in normalized_bindings}
                )
                != len(normalized_bindings)
                or len({item["requestId"] for item in normalized_bindings})
                != len(normalized_bindings)
                or len({item["correlationId"] for item in normalized_bindings})
                != 1
            ):
                raise RuntimeError("dangerous action dispatch binding is invalid")
            next_records = copy.deepcopy(self._records)
            # Completed physical and contextual actions are retained as long
            # as the journal exists. Forgetting one at capacity would turn a
            # later replay into a new physical dispatch.
            if len(next_records) >= MAX_DANGEROUS_IDEMPOTENCY_RECORDS:
                raise RuntimeError("dangerous action idempotency store is full")
            next_records[key] = {
                "key": key,
                "hash": fingerprint,
                "state": "reserved",
                "receipt": None,
                "itemJournal": [],
                "dispatchPhase": "not_started",
                "dispatchId": dispatch_id,
                "dispatchBindings": normalized_bindings,
                "responseMediaType": response_media_type,
                "updatedAt": time.time_ns() // 1_000_000,
            }
            await self._store.async_save(self._payload(next_records))
            self._records = next_records
            return ReservationResult("reserved", "reserved")

    async def async_lookup(self, *, key: str, fingerprint: str) -> ReservationResult:
        """Inspect an existing key without creating authority for new work."""

        async with self._lock:
            if self._load_error:
                raise RuntimeError("dangerous action idempotency store is corrupt")
            existing = self._records.get(key)
            if existing is None:
                return ReservationResult("missing", "missing")
            return self._existing_result(existing, fingerprint)

    async def async_mark_pending(self, key: str) -> None:
        async with self._lock:
            next_records = copy.deepcopy(self._records)
            record = self._required(key, next_records)
            record["state"] = "pending"
            record["dispatchPhase"] = "not_started"
            record["updatedAt"] = time.time_ns() // 1_000_000
            await self._store.async_save(self._payload(next_records))
            self._records = next_records

    async def async_mark_dispatching(self, key: str) -> None:
        async with self._lock:
            next_records = copy.deepcopy(self._records)
            record = self._required(key, next_records)
            record["state"] = "pending"
            record["dispatchPhase"] = "dispatching"
            record["updatedAt"] = time.time_ns() // 1_000_000
            await self._store.async_save(self._payload(next_records))
            self._records = next_records

    async def async_complete(
        self,
        key: str,
        receipt: Mapping[str, object],
        *,
        item_journal: list[dict[str, object]] | None = None,
    ) -> None:
        """Persist terminal receipt only after execution and read-back finish."""

        async with self._lock:
            next_records = copy.deepcopy(self._records)
            record = self._required(key, next_records)
            media_type = str(record["responseMediaType"])
            bindings = record["dispatchBindings"]
            if not _valid_receipt(receipt, media_type, bindings):
                raise RuntimeError("dangerous action receipt is invalid")
            journal = item_journal or []
            if not _valid_item_journal(journal, receipt, bindings):
                raise RuntimeError("dangerous action item journal is invalid")
            record["state"] = "completed"
            record["dispatchPhase"] = "dispatched"
            record["receipt"] = copy.deepcopy(dict(receipt))
            record["itemJournal"] = copy.deepcopy(journal)
            record["updatedAt"] = time.time_ns() // 1_000_000
            await self._store.async_save(self._payload(next_records))
            self._records = next_records

    def _required(
        self,
        key: str,
        records: Mapping[str, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        record = (self._records if records is None else records).get(key)
        if record is None:
            raise RuntimeError("dangerous action idempotency reservation is missing")
        return record

    @staticmethod
    def _existing_result(
        existing: Mapping[str, object], fingerprint: str
    ) -> ReservationResult:
        state = str(existing["state"])
        if state == "pending" and existing.get("dispatchPhase") in {
            "dispatching",
            "dispatched",
        }:
            # The dispatch boundary was crossed in this process. A replay
            # cannot safely assume that the service call did not reach HA.
            state = "dispatch_unknown"
        if existing["hash"] != fingerprint:
            return ReservationResult(
                "conflict",
                state,
                existing_fingerprint=str(existing["hash"]),
            )
        if existing["state"] == "completed":
            receipt = existing.get("receipt")
            return ReservationResult(
                "replay",
                "completed",
                copy.deepcopy(receipt) if isinstance(receipt, dict) else None,
                response_media_type=str(existing["responseMediaType"]),
            )
        return ReservationResult("in_progress", state)

    def _payload(
        self,
        records: Mapping[str, Mapping[str, object]] | None = None,
    ) -> dict[str, object]:
        selected = self._records if records is None else records
        return {
            "version": 1,
            "records": [copy.deepcopy(dict(item)) for item in selected.values()],
        }


def _validated_record(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    required = {
        "key",
        "hash",
        "state",
        "receipt",
        "itemJournal",
        "dispatchPhase",
        "dispatchId",
        "dispatchBindings",
        "responseMediaType",
        "updatedAt",
    }
    if set(value) != required:
        return None
    if (
        not isinstance(value.get("key"), str)
        or _KEY.fullmatch(str(value.get("key"))) is None
        or not isinstance(value.get("hash"), str)
        or _SHA256.fullmatch(str(value.get("hash"))) is None
        or value.get("state") not in _STATES
        or value.get("dispatchPhase") not in _PHASES
        or not isinstance(value.get("dispatchId"), str)
        or not value.get("dispatchId")
        or not isinstance(value.get("dispatchBindings"), list)
        or value.get("responseMediaType") not in _MEDIA_TYPES
        or not isinstance(value.get("itemJournal"), list)
        or type(value.get("updatedAt")) is not int
        or (
            value.get("receipt") is not None
            and not isinstance(value.get("receipt"), Mapping)
        )
    ):
        return None
    bindings = value["dispatchBindings"]
    if (
        not all(_valid_binding(item) for item in bindings)
        or len({item["actionIndex"] for item in bindings}) != len(bindings)
        or len({item["requestId"] for item in bindings}) != len(bindings)
        or len({item["correlationId"] for item in bindings}) != 1
        or any(item["dispatchId"] != value["dispatchId"] for item in bindings)
    ):
        return None
    state = value["state"]
    phase = value["dispatchPhase"]
    receipt = value["receipt"]
    if state == "completed":
        if (
            not isinstance(receipt, Mapping)
            or phase != "dispatched"
            or not _valid_receipt(
                receipt,
                str(value["responseMediaType"]),
                bindings,
            )
            or not _valid_item_journal(value["itemJournal"], receipt, bindings)
        ):
            return None
    elif receipt is not None or value["itemJournal"]:
        return None
    if state == "reserved" and phase != "not_started":
        return None
    if state == "dispatch_unknown" and phase not in {"dispatching", "dispatched"}:
        return None
    return copy.deepcopy(dict(value))


def _valid_receipt(
    receipt: Mapping[str, object],
    media_type: str,
    bindings: list[Mapping[str, object]],
) -> bool:
    contract = receipt.get("contract")
    if not isinstance(contract, Mapping) or set(contract) != {"name", "version"}:
        return False
    name = contract.get("name")
    if name not in _RECEIPT_CONTRACTS or contract.get("version") != 1:
        return False
    if media_type.endswith("batch-receipt.full+json") and name != (
        "hausman-hub-device-action-batch-receipt"
    ):
        return False
    if media_type.endswith("device-action-receipt.full+json") and name != (
        "hausman-hub-device-action-receipt"
    ):
        return False
    if name == "hausman-hub-device-action-receipt":
        return len(bindings) == 1 and _valid_single_receipt(
            receipt, bindings[0], require_action_index=False
        )
    receipts = receipt.get("receipts")
    total = receipt.get("total")
    counts = (
        receipt.get("acceptedCount"),
        receipt.get("confirmedCount"),
        receipt.get("failedCount"),
    )
    return bool(
        isinstance(receipt.get("correlationId"), str)
        and receipt.get("correlationId")
        and bool(bindings)
        and receipt.get("correlationId") == bindings[0].get("correlationId")
        and isinstance(receipt.get("status"), str)
        and receipt.get("status")
        and type(total) is int
        and total >= 0
        and isinstance(receipts, list)
        and len(receipts) == total
        and all(type(count) is int and 0 <= count <= total for count in counts)
        and len(bindings) == total
        and all(
            isinstance(item, Mapping)
            and _valid_single_receipt(
                item, bindings[index], require_action_index=True
            )
            for index, item in enumerate(receipts)
        )
        and counts[0] == sum(item.get("accepted") is True for item in receipts)
        and counts[1] == sum(item.get("confirmed") is True for item in receipts)
        and counts[2] == sum(item.get("status") == "failed" for item in receipts)
        and receipt.get("status")
        == (
            "confirmed"
            if counts[1] == total
            else "failed"
            if counts[2] == total
            else "partial"
            if counts[2]
            else "accepted"
        )
    )


def _valid_item_journal(
    journal: object,
    receipt: Mapping[str, object],
    bindings: list[Mapping[str, object]],
) -> bool:
    if not isinstance(journal, list):
        return False
    contract = receipt.get("contract")
    name = contract.get("name") if isinstance(contract, Mapping) else None
    if name == "hausman-hub-device-action-receipt":
        return not journal
    receipts = receipt.get("receipts")
    return bool(
        isinstance(receipts, list)
        and len(journal) == len(receipts)
        and journal == receipts
        and all(
            isinstance(item, Mapping)
            and _valid_single_receipt(
                item, bindings[index], require_action_index=True
            )
            for index, item in enumerate(journal)
        )
    )


def _valid_single_receipt(
    receipt: Mapping[str, object],
    binding: Mapping[str, object],
    *,
    require_action_index: bool,
) -> bool:
    contract = receipt.get("contract")
    accepted = receipt.get("accepted")
    confirmed = receipt.get("confirmed")
    status = receipt.get("status")
    if not (
        isinstance(contract, Mapping)
        and contract.get("name") == "hausman-hub-device-action-receipt"
        and contract.get("version") == 1
        and isinstance(receipt.get("correlationId"), str)
        and receipt.get("correlationId")
        and isinstance(receipt.get("requestId"), str)
        and receipt.get("requestId")
        and receipt.get("targetId") == binding.get("targetId")
        and receipt.get("targetType") == binding.get("targetType")
        and receipt.get("actionId") == binding.get("actionId")
        and receipt.get("correlationId") == binding.get("correlationId")
        and receipt.get("requestId") == binding.get("requestId")
        and type(accepted) is bool
        and type(confirmed) is bool
        and status in {"accepted", "confirmed", "failed"}
        and (not confirmed or accepted)
        and (status != "confirmed" or confirmed)
        and (not confirmed or status == "confirmed")
        and (accepted or status == "failed")
    ):
        return False
    action_index = receipt.get("actionIndex")
    if require_action_index:
        return action_index == binding.get("actionIndex")
    return action_index is None or action_index == binding.get("actionIndex")


def _valid_binding(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value)
        == {
            "actionIndex",
            "targetId",
            "targetType",
            "actionId",
            "dispatchId",
            "correlationId",
            "requestId",
        }
        and type(value.get("actionIndex")) is int
        and int(value["actionIndex"]) >= 0
        and isinstance(value.get("targetId"), str)
        and bool(value["targetId"])
        and isinstance(value.get("targetType"), str)
        and bool(value["targetType"])
        and isinstance(value.get("actionId"), str)
        and bool(value["actionId"])
        and isinstance(value.get("dispatchId"), str)
        and bool(value["dispatchId"])
        and isinstance(value.get("correlationId"), str)
        and bool(value["correlationId"])
        and isinstance(value.get("requestId"), str)
        and bool(value["requestId"])
    )
