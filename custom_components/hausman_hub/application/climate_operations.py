"""Bounded public lifecycle for typed HASC climate operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import re
import secrets
import time
from typing import Any

from .climate_commands import ClimateCommandPlan, ClimateCommandViolation
from .climate_import import ClimateImportSnapshot


OPERATION_QUERY_CONTRACT_NAME = "hausman-hasc-climate-operation-query"
OPERATION_CONTRACT_NAME = "hausman-hasc-operation"
OPERATION_CONTRACT_VERSION = 1
MAX_OPERATION_RECORDS = 256
OPERATION_TIMEOUT_MS = 30_000
_REQUEST_HISTORY_BITS = 65_536
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")


class ClimateOperationStatus(StrEnum):
    """Coarse states exposed to Android without backend command details."""

    ACCEPTED = "accepted"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ClimateOperationReceipt:
    """Versioned Android receipt containing public HASC identifiers only."""

    operation_id: str
    request_id: str | None
    action: str | None
    room_id: str | None
    device_id: str | None
    status: ClimateOperationStatus
    execution: str | None
    created_at: int | None
    updated_at: int | None
    known: bool = True

    def as_payload(self) -> dict[str, object]:
        """Return the exact machine-readable receipt contract."""

        return {
            "contract": {
                "name": OPERATION_CONTRACT_NAME,
                "version": OPERATION_CONTRACT_VERSION,
            },
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "action": self.action,
            "room_id": self.room_id,
            "device_id": self.device_id,
            "status": self.status.value,
            "execution": self.execution,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "known": self.known,
        }


@dataclass(frozen=True, slots=True)
class _OperationRecord:
    receipt: ClimateOperationReceipt
    canonical_intent: str
    intent: Mapping[str, Any]
    confirmation_source_id: str | None


class _ClimateOperationLedger:
    """Keep a bounded ledger; callers must serialize every method invocation."""

    def __init__(
        self,
        *,
        operation_id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._operation_id_factory = operation_id_factory or (
            lambda: secrets.token_hex(16)
        )
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._records: dict[str, _OperationRecord] = {}
        self._operation_ids_by_request: dict[str, str] = {}
        # Evicted receipts become unknown, but their request IDs remain in a
        # bounded fail-closed filter so eviction cannot permit a second POST.
        self._forgotten_request_bits = bytearray(_REQUEST_HISTORY_BITS // 8)

    def parse_action(self, payload: object) -> tuple[str, dict[str, Any], str]:
        """Split the public idempotency key from the existing typed intent."""

        if not isinstance(payload, Mapping) or any(
            not isinstance(key, str) for key in payload
        ):
            raise ClimateCommandViolation("climate action must be an object")
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise ClimateCommandViolation("request id must be one stable public id")
        intent = {key: value for key, value in payload.items() if key != "request_id"}
        try:
            canonical = json.dumps(
                intent,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as error:
            raise ClimateCommandViolation("climate action must contain JSON values") from error
        return request_id, intent, canonical

    def duplicate(self, request_id: str, canonical_intent: str) -> ClimateOperationReceipt | None:
        """Return an identical prior result or reject request-id reuse."""

        operation_id = self._operation_ids_by_request.get(request_id)
        if operation_id is None:
            if self._request_was_forgotten(request_id):
                raise ClimateCommandViolation("request id lifecycle is unavailable")
            return None
        record = self._records.get(operation_id)
        if record is None:
            # Both indexes are pruned together. Fail closed if that invariant is
            # ever broken instead of risking a second physical submission.
            raise ClimateCommandViolation("request id lifecycle is unavailable")
        if record.canonical_intent != canonical_intent:
            raise ClimateCommandViolation("request id was already used for another action")
        return record.receipt

    def record(
        self,
        *,
        request_id: str,
        canonical_intent: str,
        intent: Mapping[str, Any],
        plan: ClimateCommandPlan,
    ) -> ClimateOperationReceipt:
        """Create one opaque receipt after shadow validation or accepted POST."""

        operation_id = self._operation_id_factory()
        if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
            raise RuntimeError("operation id factory returned an unsafe id")
        if operation_id in self._records:
            raise RuntimeError("operation id factory returned a duplicate id")
        now = self._safe_now()
        receipt = ClimateOperationReceipt(
            operation_id=operation_id,
            request_id=request_id,
            action=plan.action,
            room_id=plan.room_id,
            device_id=plan.device_id,
            status=(
                ClimateOperationStatus.PENDING
                if plan.execute
                else ClimateOperationStatus.ACCEPTED
            ),
            execution="canary" if plan.execute else "shadow",
            created_at=now,
            updated_at=now,
        )
        self._records[operation_id] = _OperationRecord(
            receipt=receipt,
            canonical_intent=canonical_intent,
            intent=dict(intent),
            confirmation_source_id=plan.confirmation_source_id,
        )
        self._operation_ids_by_request[request_id] = operation_id
        self._prune()
        return receipt

    def ensure_submission_available(self, room_id: str) -> None:
        """Reject a second canary POST while one room result is still pending."""

        now = self._safe_now()
        for operation_id, record in tuple(self._records.items()):
            receipt = record.receipt
            if (
                receipt.room_id != room_id
                or receipt.status is not ClimateOperationStatus.PENDING
            ):
                continue
            if receipt.created_at is not None and now - receipt.created_at >= OPERATION_TIMEOUT_MS:
                timed_out = replace(
                    receipt,
                    status=ClimateOperationStatus.TIMED_OUT,
                    updated_at=now,
                )
                self._records[operation_id] = replace(record, receipt=timed_out)
                continue
            raise ClimateCommandViolation("room already has a pending climate operation")

    def reject(self, operation_id: str) -> ClimateOperationReceipt:
        """Terminally reject one reserved operation after an explicit response."""

        record = self._records.get(operation_id)
        if record is None or record.receipt.status is not ClimateOperationStatus.PENDING:
            raise RuntimeError("pending climate operation is unavailable")
        updated = replace(
            record.receipt,
            status=ClimateOperationStatus.REJECTED,
            updated_at=self._safe_now(),
        )
        self._records[operation_id] = replace(record, receipt=updated)
        return updated

    def lookup(
        self,
        payload: object,
        snapshot: ClimateImportSnapshot | None = None,
    ) -> ClimateOperationReceipt:
        """Return a known receipt, updating only observable pending outcomes."""

        operation_id = _operation_query_id(payload)
        record = self._records.get(operation_id)
        if record is None:
            return ClimateOperationReceipt(
                operation_id=operation_id,
                request_id=None,
                action=None,
                room_id=None,
                device_id=None,
                status=ClimateOperationStatus.UNKNOWN,
                execution=None,
                created_at=None,
                updated_at=None,
                known=False,
            )
        receipt = record.receipt
        if receipt.status is not ClimateOperationStatus.PENDING:
            return receipt
        now = self._safe_now()
        status = receipt.status
        if snapshot is not None and _intent_is_confirmed(record, snapshot):
            status = ClimateOperationStatus.CONFIRMED
        elif receipt.created_at is not None and now - receipt.created_at >= OPERATION_TIMEOUT_MS:
            status = ClimateOperationStatus.TIMED_OUT
        if status is receipt.status:
            return receipt
        updated = replace(receipt, status=status, updated_at=now)
        self._records[operation_id] = replace(record, receipt=updated)
        return updated

    def pending(self, payload: object) -> bool:
        """Return whether a well-formed query refers to a pending operation."""

        operation_id = _operation_query_id(payload)
        record = self._records.get(operation_id)
        return (
            record is not None
            and record.receipt.status is ClimateOperationStatus.PENDING
        )

    def room_has_pending(self, room_id: str) -> bool:
        """Inspect one public room without exposing or mutating its receipts."""

        if not isinstance(room_id, str):
            raise ClimateCommandViolation("operation room id is invalid")
        now = self._safe_now()
        for record in self._records.values():
            receipt = record.receipt
            if (
                receipt.room_id != room_id
                or receipt.status is not ClimateOperationStatus.PENDING
            ):
                continue
            created_at = receipt.created_at
            if (
                created_at is None
                or now < created_at
                or now - created_at < OPERATION_TIMEOUT_MS
            ):
                return True
        return False

    def _prune(self) -> None:
        while len(self._records) > MAX_OPERATION_RECORDS:
            operation_id = next(iter(self._records))
            record = self._records.pop(operation_id)
            if record.receipt.request_id is not None:
                self._operation_ids_by_request.pop(record.receipt.request_id, None)
                self._remember_forgotten_request(record.receipt.request_id)

    def _remember_forgotten_request(self, request_id: str) -> None:
        for index in _request_history_indexes(request_id):
            self._forgotten_request_bits[index // 8] |= 1 << (index % 8)

    def _request_was_forgotten(self, request_id: str) -> bool:
        return all(
            self._forgotten_request_bits[index // 8] & (1 << (index % 8))
            for index in _request_history_indexes(request_id)
        )

    def _safe_now(self) -> int:
        value = self._now_ms()
        if type(value) is not int or value < 0:
            raise RuntimeError("operation clock returned an unsafe timestamp")
        return value


def _operation_query_id(payload: object) -> str:
    if not isinstance(payload, Mapping) or set(payload) != {"operation_id"}:
        raise ClimateCommandViolation("operation query must contain only operation_id")
    operation_id = payload.get("operation_id")
    if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
        raise ClimateCommandViolation("operation id is invalid")
    return operation_id


def _request_history_indexes(request_id: str) -> tuple[int, int, int]:
    digest = hashlib.blake2s(request_id.encode("ascii"), digest_size=12).digest()
    return tuple(
        int.from_bytes(digest[offset : offset + 4], "big") % _REQUEST_HISTORY_BITS
        for offset in (0, 4, 8)
    )  # type: ignore[return-value]


def _intent_is_confirmed(
    record: _OperationRecord,
    snapshot: ClimateImportSnapshot,
) -> bool:
    """Confirm only values represented explicitly by the imported state."""

    receipt = record.receipt
    intent = record.intent
    action = receipt.action
    room = snapshot.room(receipt.room_id) if receipt.room_id is not None else None
    if action == "set_room_target" and room is not None:
        return room.target_temperature == float(intent["target_temperature"])
    if action == "set_room_mode" and room is not None:
        return room.mode == intent["mode"]
    if action == "turn_room_off":
        device = (
            snapshot.device(record.confirmation_source_id)
            if record.confirmation_source_id is not None
            else None
        )
        return bool(
            device is not None
            and device.room_id == receipt.room_id
            and device.state in {"off", "idle"}
        )
    if action == "set_device_power" and receipt.device_id is not None:
        # The imported contract has source-private identifiers while receipts
        # deliberately do not. Device-specific physical confirmation therefore
        # stays pending until the climate contract exposes a public correlation.
        return False
    return False
