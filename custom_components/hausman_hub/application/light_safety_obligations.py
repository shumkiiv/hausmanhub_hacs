"""Durable delayed light-off obligations with fail-closed reconciliation."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

MAX_LIGHT_SAFETY_OBLIGATIONS = 128
LIGHT_SAFETY_RETRY_BASE_SECONDS = 5
LIGHT_SAFETY_RETRY_MAX_SECONDS = 300
MAX_LIGHT_SAFETY_RECONCILE_ATTEMPTS = 2
RECONCILE_CONFIRMED = "confirmed"
RECONCILE_INVALIDATED = "invalidated"
RECONCILE_RETRY = "retry"
_LOGGER = logging.getLogger(__name__)


class LightSafetyObligationStore(Protocol):
    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


class LightSafetyObligationIssueReporter(Protocol):
    async def async_report_failure(self, record: Mapping[str, object]) -> None: ...

    async def async_clear(self, target_id: str) -> None: ...


class LightSafetyObligations:
    """Persist deadlines and reconcile only the exact owned light revision."""

    def __init__(
        self,
        store: LightSafetyObligationStore,
        *,
        now_ms: Callable[[], int] | None = None,
        issue_reporter: LightSafetyObligationIssueReporter | None = None,
    ) -> str:
        self._store = store
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._issue_reporter = issue_reporter
        self._records: dict[str, dict[str, object]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._callback: (
            Callable[[Mapping[str, object]], Awaitable[str | bool]] | None
        ) = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        payload = await self._store.async_load()
        if payload is None:
            return
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            raise RuntimeError("light safety obligation store is corrupt")
        records = payload.get("records")
        if not isinstance(records, list):
            raise RuntimeError("light safety obligation store is corrupt")
        if len(records) > MAX_LIGHT_SAFETY_OBLIGATIONS:
            raise RuntimeError("light safety obligation store is over capacity")
        normalized: dict[str, dict[str, object]] = {}
        exhausted: list[dict[str, object]] = []
        migrated = False
        for item in records:
            record = _validated_record(item)
            if record is None:
                raise RuntimeError("light safety obligation store is corrupt")
            target_id = str(record["targetId"])
            if target_id in normalized:
                raise RuntimeError("light safety obligation store has duplicates")
            if "generationId" not in item or "attempt" not in item:
                migrated = True
            if int(record.get("attempt", 0)) >= MAX_LIGHT_SAFETY_RECONCILE_ATTEMPTS:
                # A terminal accounting record may remain after a failed
                # transition save. It must never be scheduled for a third
                # physical attempt after restart.
                exhausted.append(record)
                migrated = True
                continue
            normalized[target_id] = record
        if getattr(self._store, "recovered_previous", False):
            self._records = {}
            await self._store.async_save(self._payload())
            if self._issue_reporter is not None:
                for record in (*normalized.values(), *exhausted):
                    await self._issue_reporter.async_report_failure(record)
            return
        self._records = normalized
        if migrated:
            await self._store.async_save(self._payload())

    def start(
        self,
        callback: Callable[[Mapping[str, object]], Awaitable[str | bool]],
    ) -> Callable[[], None]:
        """Schedule only obligations restored from storage after executor readiness."""

        self._callback = callback
        for target_id, record in self._records.items():
            self._schedule(target_id, record)
        return self.cancel_all

    async def async_arm(
        self,
        *,
        target_id: str,
        entity_id: str,
        scenario_id: str,
        run_id: str,
        deadline_ms: int,
        ownership_revision: str | None,
        guard_entity_ids: tuple[str, ...] = (),
        guard_evidence: Mapping[str, str] | None = None,
        kind: str = "owned_light",
    ) -> None:
        """Persist before the in-process delay; a restart will schedule it."""

        if kind not in {"owned_light", "state_on"}:
            raise ValueError("light safety obligation kind is invalid")
        if (
            kind == "state_on"
            and scenario_id != "system-shower-comfort-controller"
        ):
            raise ValueError("state-on obligation is limited to the shower controller")

        async with self._lock:
            if (
                target_id not in self._records
                and len(self._records) >= MAX_LIGHT_SAFETY_OBLIGATIONS
            ):
                record = {
                    "targetId": target_id,
                    "entityId": entity_id,
                    "scenarioId": scenario_id,
                    "runId": run_id,
                    "deadlineMs": deadline_ms,
                    "ownershipRevision": ownership_revision,
                    "createdAt": self._now_ms(),
                }
                if self._issue_reporter is not None:
                    await self._issue_reporter.async_report_failure(record)
                raise RuntimeError("light safety obligation store is full")
            record = {
                "targetId": target_id,
                "entityId": entity_id,
                "scenarioId": scenario_id,
                "runId": run_id,
                "deadlineMs": deadline_ms,
                "ownershipRevision": ownership_revision,
                "guardEntityIds": list(guard_entity_ids),
                "guardEvidence": dict(guard_evidence or {}),
                "createdAt": self._now_ms(),
                "generationId": uuid.uuid4().hex,
                "attempt": 0,
                "kind": kind,
            }
            next_records = copy.deepcopy(self._records)
            next_records[target_id] = record
            await self._store.async_save(self._payload(next_records))
            self._records = next_records
            self._cancel_task(target_id)
            return str(record["generationId"])

    async def async_complete(self, target_id: str, *, expected_generation: str | None = None) -> None:
        removed = False
        async with self._lock:
            if target_id in self._records and (
                expected_generation is None
                or self._records[target_id].get("generationId") == expected_generation
            ):
                next_records = copy.deepcopy(self._records)
                next_records.pop(target_id, None)
                await self._store.async_save(self._payload(next_records))
                self._records = next_records
                self._cancel_task(target_id)
                removed = True
        if removed and self._issue_reporter is not None:
            await self._issue_reporter.async_clear(target_id)

    async def async_cancel(self, target_id: str) -> None:
        """A new presence or manual action invalidates the delayed off."""

        await self.async_complete(target_id)

    async def async_cancel_scenario(self, scenario_id: str) -> None:
        """Cancel every pending off owned by a newly occupied scenario."""

        if not scenario_id:
            return
        cancelled: list[str] = []
        async with self._lock:
            cancelled = [
                target_id
                for target_id, record in self._records.items()
                if record.get("scenarioId") == scenario_id
            ]
            if cancelled:
                next_records = copy.deepcopy(self._records)
                for target_id in cancelled:
                    next_records.pop(target_id, None)
                await self._store.async_save(self._payload(next_records))
                self._records = next_records
                for target_id in cancelled:
                    self._cancel_task(target_id)
        if self._issue_reporter is not None:
            for target_id in cancelled:
                await self._issue_reporter.async_clear(target_id)

    async def async_retry(
        self, target_id: str, *, physical_attempted: bool = False, expected_generation: str | None = None
    ) -> None:
        """Retry one due failed off immediately and publish Repairs on failure."""

        async with self._lock:
            record = self._records.get(target_id)
            if (
                record is None
                or self._callback is None
                or (expected_generation is not None and record.get("generationId") != expected_generation)
            ):
                return
            retry_record = copy.deepcopy(record)
            retry_record["deadlineMs"] = self._now_ms()
            if physical_attempted:
                # The scenario action itself is the first physical attempt.
                # Keep that fact durable before scheduling the bounded retry.
                retry_record["attempt"] = max(int(retry_record.get("attempt", 0)), 1)
            next_records = copy.deepcopy(self._records)
            if int(retry_record["attempt"]) >= MAX_LIGHT_SAFETY_RECONCILE_ATTEMPTS:
                next_records.pop(target_id, None)
            else:
                next_records[target_id] = retry_record
            await self._store.async_save(self._payload(next_records))
            self._records = next_records
            self._cancel_task(target_id)
            if target_id in next_records:
                self._schedule(target_id, retry_record)

    async def async_is_current(self, target_id: str, generation_id: object) -> bool:
        """Confirm a delayed action still owns the exact persisted generation."""

        async with self._lock:
            record = self._records.get(target_id)
            return bool(
                record is not None
                and isinstance(generation_id, str)
                and record.get("generationId") == generation_id
            )

    def cancel_all(self) -> None:
        for task in tuple(self._tasks.values()):
            task.cancel()
        self._tasks.clear()
        self._callback = None

    def _schedule(self, target_id: str, record: Mapping[str, object]) -> None:
        if self._callback is None:
            return
        task = asyncio.create_task(
            self._async_reconcile(target_id, copy.deepcopy(dict(record)))
        )
        self._tasks[target_id] = task

    async def _async_reconcile(
        self, target_id: str, record: Mapping[str, object]
    ) -> None:
        try:
            delay = max(0.0, (int(record["deadlineMs"]) - self._now_ms()) / 1000)
            if delay > 0:
                await asyncio.sleep(delay)
            outcome = RECONCILE_RETRY
            persisted = False
            callback_record: dict[str, object] | None = None
            async with self._lock:
                current = self._records.get(target_id)
                if (
                    current is None
                    or current.get("generationId") != record.get("generationId")
                ):
                    return
                callback = self._callback
                attempt = int(current.get("attempt", 0)) + 1
                if callback is None or attempt > MAX_LIGHT_SAFETY_RECONCILE_ATTEMPTS:
                    return
                reserved = copy.deepcopy(current)
                reserved["attempt"] = attempt
                reservation_records = copy.deepcopy(self._records)
                reservation_records[target_id] = reserved
                try:
                    # Reserve the physical-attempt budget before invoking the
                    # callback. A successful save makes the budget restart
                    # durable, while a failed save is accounted for in memory
                    # and cannot trigger an unbounded callback loop.
                    await self._store.async_save(self._payload(reservation_records))
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Light safety obligation attempt reservation was not persisted"
                    )
                    if attempt >= MAX_LIGHT_SAFETY_RECONCILE_ATTEMPTS:
                        self._records.pop(target_id, None)
                    else:
                        retry_record = copy.deepcopy(reserved)
                        retry_record["deadlineMs"] = (
                            self._now_ms() + LIGHT_SAFETY_RETRY_BASE_SECONDS * 1000
                        )
                        self._records[target_id] = retry_record
                        self._schedule(target_id, retry_record)
                    return
                self._records = reservation_records
                callback_record = copy.deepcopy(reserved)
            if callback is not None:
                try:
                    assert callback_record is not None
                    result = await callback(callback_record)
                    outcome = _reconcile_outcome(result)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Light safety obligation reconciliation failed"
                    )
            async with self._lock:
                current = self._records.get(target_id)
                if (
                    current is None
                    or current.get("generationId") != record.get("generationId")
                ):
                    return
                next_records = copy.deepcopy(self._records)
                attempt = int(current.get("attempt", 0))
                if outcome in {RECONCILE_CONFIRMED, RECONCILE_INVALIDATED}:
                    next_records.pop(target_id, None)
                else:
                    retry_record = copy.deepcopy(current)
                    if attempt >= MAX_LIGHT_SAFETY_RECONCILE_ATTEMPTS:
                        next_records.pop(target_id, None)
                    else:
                        retry_seconds = min(
                            LIGHT_SAFETY_RETRY_MAX_SECONDS,
                            LIGHT_SAFETY_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                        )
                        retry_record["attempt"] = attempt
                        retry_record["deadlineMs"] = (
                            self._now_ms() + retry_seconds * 1000
                        )
                        next_records[target_id] = retry_record
                try:
                    await self._store.async_save(self._payload(next_records))
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Light safety obligation transition was not persisted"
                    )
                    if attempt >= MAX_LIGHT_SAFETY_RECONCILE_ATTEMPTS:
                        self._records.pop(target_id, None)
                    else:
                        retry_record = copy.deepcopy(current)
                        retry_record["deadlineMs"] = (
                            self._now_ms() + LIGHT_SAFETY_RETRY_BASE_SECONDS * 1000
                        )
                        self._records[target_id] = retry_record
                        self._schedule(target_id, retry_record)
                else:
                    persisted = True
                    self._records = next_records
                    if outcome == RECONCILE_RETRY and target_id in next_records:
                        self._schedule(target_id, next_records[target_id])
            if self._issue_reporter is not None and persisted:
                try:
                    if outcome == RECONCILE_CONFIRMED:
                        await self._issue_reporter.async_clear(target_id)
                    else:
                        await self._issue_reporter.async_report_failure(record)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Light safety obligation Repairs update failed"
                    )
        finally:
            task = asyncio.current_task()
            if self._tasks.get(target_id) is task:
                self._tasks.pop(target_id, None)

    def _cancel_task(self, target_id: str) -> None:
        task = self._tasks.pop(target_id, None)
        if task is not None:
            task.cancel()

    def _payload(
        self, records: Mapping[str, Mapping[str, object]] | None = None
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
        "targetId",
        "entityId",
        "scenarioId",
        "runId",
        "deadlineMs",
        "ownershipRevision",
        "createdAt",
    }
    optional = {"generationId", "attempt", "kind", "guardEntityIds", "guardEvidence"}
    if not required.issubset(value) or not set(value).issubset(required | optional):
        return None
    if (
        any(
            not isinstance(value.get(field), str)
            for field in ("targetId", "entityId", "scenarioId", "runId")
        )
        or (
            value.get("ownershipRevision") is not None
            and not isinstance(value.get("ownershipRevision"), str)
        )
        or (
            value.get("guardEvidence") is not None
            and (
                not isinstance(value.get("guardEvidence"), Mapping)
                or not all(isinstance(key, str) and key and isinstance(item, str) and item for key, item in value["guardEvidence"].items())
            )
        )
        or (
            value.get("guardEntityIds") is not None
            and (
                not isinstance(value.get("guardEntityIds"), list)
                or (
                    bool(value["guardEntityIds"])
                    and (
                        not all(isinstance(item, str) and item for item in value["guardEntityIds"])
                        or len(value["guardEntityIds"]) != len(set(value["guardEntityIds"]))
                    )
                )
            )
        )
        or type(value.get("deadlineMs")) is not int
        or type(value.get("createdAt")) is not int
        or (
            value.get("generationId") is not None
            and (
                not isinstance(value.get("generationId"), str)
                or not value.get("generationId")
            )
        )
        or (
            value.get("attempt") is not None
            and (type(value.get("attempt")) is not int or int(value["attempt"]) < 0)
        )
        or value.get("kind", "owned_light")
        not in {"owned_light", "state_on"}
        or (
            value.get("kind", "owned_light") == "state_on"
            and value.get("scenarioId") != "system-shower-comfort-controller"
        )
    ):
        return None
    result = copy.deepcopy(dict(value))
    result.setdefault("generationId", uuid.uuid4().hex)
    result.setdefault("attempt", 0)
    result.setdefault("kind", "owned_light")
    result.setdefault("guardEntityIds", [])
    result.setdefault("guardEvidence", {})
    return result


def valid_light_safety_obligation_payload(value: object) -> bool:
    if not isinstance(value, Mapping) or value.get("version") != 1:
        return False
    records = value.get("records")
    if not isinstance(records, list) or len(records) > MAX_LIGHT_SAFETY_OBLIGATIONS:
        return False
    normalized = [_validated_record(item) for item in records]
    target_ids = [
        str(item["targetId"]) for item in normalized if item is not None
    ]
    return (
        all(item is not None for item in normalized)
        and len(target_ids) == len(set(target_ids))
    )


def _reconcile_outcome(value: str | bool) -> str:
    if value is True or value == RECONCILE_CONFIRMED:
        return RECONCILE_CONFIRMED
    if value == RECONCILE_INVALIDATED:
        return RECONCILE_INVALIDATED
    return RECONCILE_RETRY
