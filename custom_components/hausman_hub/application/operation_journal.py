"""Bounded durable journal for normalized cross-domain operation receipts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import re
import time
from typing import Callable, Protocol


OPERATION_JOURNAL_CONTRACT_NAME = "hausman-hub-operation-journal"
OPERATION_JOURNAL_CONTRACT_VERSION = 1
MAX_OPERATION_JOURNAL_RECORDS = 512
OPERATION_SOURCES = frozenset({"device", "climate", "scenario", "voice"})
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OPERATION_NAME = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


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

        correlation_id = receipt.get("request_id")
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
            self._records.insert(0, record)
            del self._records[MAX_OPERATION_JOURNAL_RECORDS:]
            await self._store.async_save(self._storage_payload())
            return dict(record)

    def snapshot(
        self,
        *,
        limit: int = 100,
        source: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, object]:
        """Return a bounded filtered copy without causing storage writes."""

        if not 1 <= limit <= MAX_OPERATION_JOURNAL_RECORDS:
            raise ValueError("operation journal limit is invalid")
        if source is not None and source not in OPERATION_SOURCES:
            raise ValueError("operation journal source is invalid")
        if correlation_id is not None and (
            _CORRELATION_ID.fullmatch(correlation_id) is None
        ):
            raise ValueError("operation journal correlation id is invalid")
        records = [
            dict(item)
            for item in self._records
            if (source is None or item["source"] == source)
            and (
                correlation_id is None
                or item["correlation_id"] == correlation_id
            )
        ][:limit]
        return {
            "contract": {
                "name": OPERATION_JOURNAL_CONTRACT_NAME,
                "version": OPERATION_JOURNAL_CONTRACT_VERSION,
            },
            "generated_at": max(0, self._now_ms()),
            "sequence": self._sequence,
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
    if not isinstance(value, Mapping) or set(value) != {
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
    }:
        return None
    sequence = value.get("sequence")
    correlation_id = value.get("correlation_id")
    source = value.get("source")
    operation = value.get("operation")
    occurred_at = value.get("occurred_at")
    reason = value.get("reason")
    error_code = value.get("error_code")
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
        or (
            (value.get("status") == "confirmed")
            != (value.get("confirmed") is True)
        )
    ):
        return None
    return dict(value)
