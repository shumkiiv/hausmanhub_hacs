"""Single-owner lifecycle for bounded safe climate and humidifier commands."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


_LOGGER = logging.getLogger(__name__)


SAFE_RESPONSE_BUDGET_SECONDS = 2.15
SAFE_OBSERVATION_SECONDS = 30.0
MAX_ACTIVE_SAFE_COMMANDS = 16
MAX_SAFE_COMMAND_RECORDS = 128

_SAFE_PHASES = frozenset(
    {
        "prepared",
        "dispatch_intent",
        "observing",
        "confirmed",
        "unconfirmed",
        "superseded",
        "dispatch_unknown",
        "failed_before_dispatch",
    }
)
_ACTIVE_PHASES = frozenset({"prepared", "dispatch_intent", "observing"})
_TERMINAL_PHASES = _SAFE_PHASES - _ACTIVE_PHASES
_SAFE_ACTION_SERVICES = frozenset(
    {
        ("climate", "turn_on", "turn_on"),
        ("climate", "turn_off", "turn_off"),
        ("climate", "set_temperature", "set_temperature"),
        ("climate", "set_hvac_mode", "set_hvac_mode"),
        ("climate", "set_fan_mode", "set_fan_mode"),
        ("humidifier", "turn_on", "turn_on"),
        ("humidifier", "turn_off", "turn_off"),
        ("humidifier", "set_humidity", "set_humidity"),
        ("humidifier", "set_operation_mode", "set_operation_mode"),
    }
)


@dataclass(frozen=True, slots=True)
class CommandDeadline:
    """One monotonic end-to-end response deadline created by the HTTP handler."""

    started_at: float
    response_at: float

    @classmethod
    def start(
        cls,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        budget_seconds: float = SAFE_RESPONSE_BUDGET_SECONDS,
    ) -> CommandDeadline:
        started = monotonic()
        return cls(started_at=started, response_at=started + budget_seconds)

    def remaining(self, *, monotonic: Callable[[], float] = time.monotonic) -> float:
        return max(0.0, self.response_at - monotonic())


@dataclass(frozen=True, slots=True)
class SafeDeviceCommandOperation:
    """Immutable dispatch identity retained without storing a public payload."""

    request_fingerprint: str
    request_id: str
    target_id: str
    entity_id: str
    domain: str
    service: str
    action_id: str
    response_media_type: str
    pre_evidence_revision: str | None = None
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def identity_digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [self.target_id, self.entity_id, self.domain, self.service],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(slots=True)
class SafeDeviceCommandHandle:
    """Mutable in-process ownership token for exactly one execute_once call."""

    operation: SafeDeviceCommandOperation
    generation: int
    created_at_ms: int
    expires_at_ms: int
    task: asyncio.Task[dict[str, Any]] | None = None
    watchdog: asyncio.Task[None] | None = None
    dispatch_crossed: bool = False
    dispatch_at_ms: int | None = None
    revoked: bool = False
    response_released: bool = False
    first_response_persisted: asyncio.Event = field(default_factory=asyncio.Event)
    first_response_resolved: asyncio.Event = field(default_factory=asyncio.Event)
    http_abandoned: bool = False
    late_published: bool = False


@dataclass(slots=True)
class _ProcessCoordination:
    """Same-loop lease and persistence fence shared across reload instances."""

    persist_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    epoch: int = 0
    leases: dict[str, tuple[str, str]] = field(default_factory=dict)
    completed_operations: set[str] = field(default_factory=set)


_PROCESS_COORDINATION: dict[tuple[int, str], _ProcessCoordination] = {}


ExecuteOnce = Callable[
    [Callable[[], None], Callable[[], Awaitable[None]]],
    Awaitable[Mapping[str, Any]],
]
LateCallback = Callable[[SafeDeviceCommandHandle, Mapping[str, Any]], Awaitable[None]]


def is_safe_device_descriptor(
    *, domain: object, service: object, action_id: object
) -> bool:
    """Accept only the exact climate/humidifier descriptors in the v1 catalog."""

    return (
        isinstance(domain, str)
        and isinstance(service, str)
        and isinstance(action_id, str)
        and (domain, action_id, service) in _SAFE_ACTION_SERVICES
    )


def valid_safe_device_command_payload(value: object) -> bool:
    """Validate the bounded durable image used for restart uncertainty only."""

    if not isinstance(value, Mapping) or set(value) != {
        "version",
        "nextGeneration",
        "targetGenerations",
        "operations",
    }:
        return False
    if value.get("version") != 1 or type(value.get("nextGeneration")) is not int:
        return False
    if not 0 <= int(value["nextGeneration"]) <= 2**63 - 1:
        return False
    generations = value.get("targetGenerations")
    operations = value.get("operations")
    if not isinstance(generations, Mapping) or not isinstance(operations, list):
        return False
    if len(generations) > MAX_SAFE_COMMAND_RECORDS or len(operations) > MAX_SAFE_COMMAND_RECORDS:
        return False
    for target_id, generation in generations.items():
        if not _valid_text(target_id) or type(generation) is not int or generation < 0:
            return False
    expected = {
        "operationId",
        "identityDigest",
        "requestFingerprint",
        "requestId",
        "targetId",
        "entityId",
        "domain",
        "service",
        "actionId",
        "responseMediaType",
        "preEvidenceRevision",
        "generation",
        "phase",
        "createdAtMs",
        "dispatchIntentAtMs",
        "dispatchedAtMs",
        "terminalAtMs",
        "expiresAtMs",
        "lateReceipt",
        "error",
    }
    operation_ids: set[str] = set()
    for record in operations:
        if not isinstance(record, Mapping) or set(record) != expected:
            return False
        if not all(
            _valid_text(record.get(key))
            for key in (
                "operationId",
                "identityDigest",
                "requestFingerprint",
                "requestId",
                "targetId",
                "entityId",
                "domain",
                "service",
                "actionId",
                "responseMediaType",
            )
        ):
            return False
        operation_id = str(record["operationId"])
        if operation_id in operation_ids:
            return False
        operation_ids.add(operation_id)
        if record.get("phase") not in _SAFE_PHASES:
            return False
        if type(record.get("generation")) is not int or record["generation"] < 1:
            return False
        for key in (
            "createdAtMs",
            "dispatchIntentAtMs",
            "dispatchedAtMs",
            "terminalAtMs",
            "expiresAtMs",
        ):
            item = record.get(key)
            if item is not None and (type(item) is not int or not 0 <= item <= 2**63 - 1):
                return False
        for key in ("preEvidenceRevision", "error"):
            item = record.get(key)
            if item is not None and not _valid_text(item):
                return False
        late = record.get("lateReceipt")
        if late is not None and not _json_mapping(late):
            return False
    return True


class SafeDeviceCommandLifecycle:
    """Own background execution, durable intent, deadline, and late evidence."""

    def __init__(
        self,
        store: object,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        now_ms: Callable[[], int] | None = None,
        observation_seconds: float = SAFE_OBSERVATION_SECONDS,
        max_active: int = MAX_ACTIVE_SAFE_COMMANDS,
    ) -> None:
        self._store = store
        self._monotonic = monotonic
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._observation_seconds = observation_seconds
        self._max_active = max_active
        self._lock = asyncio.Lock()
        self._payload = _empty_payload()
        self._handles: dict[str, SafeDeviceCommandHandle] = {}
        self._entities: dict[str, str] = {}
        self._restart_quarantine: dict[str, int] = {}
        self._closed = False
        self._instance_id = uuid.uuid4().hex
        self._epoch = 0
        self._coordination: _ProcessCoordination | None = None
        self._persistence_tasks: set[asyncio.Task[None]] = set()

    @property
    def closed(self) -> bool:
        """Return whether this owner has already completed unload fencing."""

        return self._closed

    async def async_load(self) -> None:
        coordination = self._process_coordination()
        async with coordination.persist_lock:
            loaded = await self._store.async_load()
            coordination.epoch += 1
            self._epoch = coordination.epoch
        if loaded is None:
            self._payload = _empty_payload()
            return
        if not valid_safe_device_command_payload(loaded):
            raise RuntimeError("safe device command store is invalid")
        self._payload = copy.deepcopy(dict(loaded))
        now = self._now_ms()
        for record in self._payload["operations"]:
            phase = record["phase"]
            if phase == "prepared":
                record["phase"] = "failed_before_dispatch"
                record["terminalAtMs"] = now
                record["error"] = "restart_before_dispatch"
            elif phase in {"dispatch_intent", "observing"}:
                record["phase"] = "dispatch_unknown"
                record["terminalAtMs"] = now
                record["error"] = "restart_dispatch_uncertain"
            expiry = record.get("expiresAtMs")
            if (
                record["phase"] == "dispatch_unknown"
                and type(expiry) is int
                and expiry > now
            ):
                self._restart_quarantine[str(record["entityId"])] = expiry

    async def async_start(
        self,
        operation: SafeDeviceCommandOperation,
        execute_once: ExecuteOnce,
        *,
        late_callback: LateCallback | None = None,
        deadline: CommandDeadline | None = None,
    ) -> SafeDeviceCommandHandle:
        """Persist intent and transfer exactly one execution task to this owner."""

        async with self._lock:
            self._prune_restart_quarantine()
            if self._closed:
                raise RuntimeError("safe device command lifecycle is closed")
            coordination = self._require_coordination()
            if len(coordination.leases) >= self._max_active:
                raise RuntimeError("safe device command capacity is full")
            quarantined_operation = self._quarantined_operation_id(
                operation.entity_id
            )
            if (
                operation.entity_id in self._restart_quarantine
                and quarantined_operation in coordination.completed_operations
                and operation.entity_id not in coordination.leases
            ):
                self._restart_quarantine.pop(operation.entity_id, None)
            if (
                operation.entity_id in self._entities
                or operation.entity_id in self._restart_quarantine
                or operation.entity_id in coordination.leases
            ):
                raise RuntimeError("safe device command target is busy")
            lease = (self._instance_id, operation.operation_id)
            coordination.leases[operation.entity_id] = lease
            generation = int(self._payload["nextGeneration"]) + 1
            now = self._now_ms()
            expires = now + max(1, int(self._observation_seconds * 1000))
            handle = SafeDeviceCommandHandle(
                operation=operation,
                generation=generation,
                created_at_ms=now,
                expires_at_ms=expires,
            )
            try:
                self._payload["nextGeneration"] = generation
                self._supersede_previous_generation(operation.entity_id)
                self._payload["targetGenerations"][operation.entity_id] = generation
                self._payload["operations"].append(
                    _operation_record(handle, phase="prepared")
                )
                self._prune_records()
                await self._save_locked()
                record = self._record(operation.operation_id)
                record["phase"] = "dispatch_intent"
                record["dispatchIntentAtMs"] = self._now_ms()
                await self._save_locked()
            except Exception:
                record = self._record_or_none(operation.operation_id)
                if record is not None:
                    record["phase"] = "failed_before_dispatch"
                    record["terminalAtMs"] = self._now_ms()
                    record["error"] = "dispatch_intent_persistence_failed"
                self._release_process_lease(handle)
                raise
            if deadline is not None and deadline.remaining(monotonic=self._monotonic) <= 0:
                record["phase"] = "failed_before_dispatch"
                record["terminalAtMs"] = self._now_ms()
                record["error"] = "response_budget_exhausted"
                try:
                    await self._save_locked()
                except Exception:
                    pass
                self._release_process_lease(handle)
                raise RuntimeError("safe device command response budget exhausted")
            self._handles[operation.operation_id] = handle
            self._entities[operation.entity_id] = operation.operation_id
            handle.task = asyncio.create_task(
                self._async_run(handle, execute_once, late_callback),
                name=f"hausman-safe-device-{operation.operation_id}",
            )
            handle.task.add_done_callback(lambda _task: self._task_finished(handle))
            handle.watchdog = asyncio.create_task(
                self._async_watchdog(handle, late_callback),
                name=f"hausman-safe-device-watch-{operation.operation_id}",
            )
            return handle

    async def async_response(
        self,
        handle: SafeDeviceCommandHandle,
        deadline: CommandDeadline,
    ) -> Mapping[str, Any] | None:
        """Return a finished result or None after a real dispatch crosses the SLA."""

        task = handle.task
        if task is None:
            raise RuntimeError("safe device command task is missing")
        try:
            done, _pending = await asyncio.wait(
                {task}, timeout=deadline.remaining(monotonic=self._monotonic)
            )
        except asyncio.CancelledError:
            if handle.dispatch_crossed:
                handle.response_released = True
            else:
                await self._async_revoke_pre_dispatch(handle)
            raise
        if done:
            return task.result()
        if handle.dispatch_crossed:
            handle.response_released = True
            if task.done():
                return task.result()
            return None
        await self._async_revoke_pre_dispatch(handle)
        return _failed_before_dispatch_result(handle, "response_budget_exhausted")

    async def async_note_first_response_persisted(
        self, handle: SafeDeviceCommandHandle
    ) -> None:
        """Release a late callback only after the immutable response is durable."""

        handle.first_response_persisted.set()
        handle.first_response_resolved.set()

    async def async_note_http_abandoned(
        self, handle: SafeDeviceCommandHandle
    ) -> None:
        """Let the coordinator finish a transferred reservation after disconnect."""

        handle.http_abandoned = True
        handle.first_response_resolved.set()

    async def async_record_late_receipt(
        self,
        handle: SafeDeviceCommandHandle,
        receipt: Mapping[str, Any],
    ) -> bool:
        """CAS one late receipt by operation identity and target generation."""

        async with self._lock:
            if self._closed or handle.late_published:
                return False
            record = self._record_or_none(handle.operation.operation_id)
            if (
                record is None
                or record.get("identityDigest") != handle.operation.identity_digest
                or record.get("generation") != handle.generation
                or self._payload["targetGenerations"].get(handle.operation.entity_id)
                != handle.generation
                or self._now_ms() > handle.expires_at_ms
            ):
                return False
            record["lateReceipt"] = copy.deepcopy(dict(receipt))
            await self._save_locked()
            handle.late_published = True
            return True

    async def async_close(self) -> None:
        """Fence this instance, retain uncertainty, and never redispatch on unload."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            now = self._now_ms()
            for handle in self._handles.values():
                record = self._record_or_none(handle.operation.operation_id)
                if record is None or record["phase"] in _TERMINAL_PHASES:
                    continue
                record["phase"] = (
                    "dispatch_unknown"
                    if handle.dispatch_crossed
                    else "failed_before_dispatch"
                )
                record["terminalAtMs"] = now
                record["error"] = (
                    "unload_dispatch_uncertain"
                    if handle.dispatch_crossed
                    else "unload_before_dispatch"
                )
            try:
                await self._save_locked(allow_closed=True)
            except Exception:
                pass
            tasks = [
                task
                for handle in self._handles.values()
                for task in (handle.task, handle.watchdog)
                if task is not None and not task.done()
            ]
            for task in tasks:
                task.cancel()
            persistence_tasks = [
                task for task in self._persistence_tasks if not task.done()
            ]
            for task in persistence_tasks:
                task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=0.25)
        if persistence_tasks:
            await asyncio.wait(persistence_tasks, timeout=0.25)

    async def _async_validate_dispatch(
        self, handle: SafeDeviceCommandHandle
    ) -> None:
        async with self._lock:
            if (
                self._closed
                or handle.revoked
                or self._handles.get(handle.operation.operation_id) is not handle
                or self._entities.get(handle.operation.entity_id)
                != handle.operation.operation_id
            ):
                raise RuntimeError("safe device command dispatch authority revoked")
            record = self._record(handle.operation.operation_id)
            if (
                record["phase"] != "dispatch_intent"
                or record["identityDigest"] != handle.operation.identity_digest
                or record["generation"] != handle.generation
            ):
                raise RuntimeError("safe device command dispatch identity changed")

    def _mark_dispatch(self, handle: SafeDeviceCommandHandle) -> None:
        if handle.revoked or self._closed:
            raise RuntimeError("safe device command dispatch authority revoked")
        if handle.dispatch_crossed:
            raise RuntimeError("safe device command dispatched more than once")
        handle.dispatch_crossed = True
        handle.dispatch_at_ms = self._now_ms()
        task = asyncio.create_task(
            self._async_persist_dispatch(handle),
            name=f"hausman-safe-device-marker-{handle.operation.operation_id}",
        )
        self._persistence_tasks.add(task)
        task.add_done_callback(self._persistence_tasks.discard)

    async def _async_persist_dispatch(self, handle: SafeDeviceCommandHandle) -> None:
        async with self._lock:
            if self._closed or not self._owns_epoch():
                return
            record = self._record_or_none(handle.operation.operation_id)
            if record is None or record["phase"] != "dispatch_intent":
                return
            record["phase"] = "observing"
            record["dispatchedAtMs"] = handle.dispatch_at_ms
            try:
                await self._save_locked()
            except Exception:
                record["error"] = "dispatch_marker_persistence_failed"

    async def _async_run(
        self,
        handle: SafeDeviceCommandHandle,
        execute_once: ExecuteOnce,
        late_callback: LateCallback | None,
    ) -> dict[str, Any]:
        try:
            result = dict(
                await execute_once(
                    lambda: self._mark_dispatch(handle),
                    lambda: self._async_validate_dispatch(handle),
                )
            )
        except asyncio.CancelledError:
            if self._closed:
                raise
            result = (
                _dispatch_unknown_result(handle, "execution_cancelled_after_dispatch")
                if handle.dispatch_crossed
                else _failed_before_dispatch_result(handle, "execution_cancelled")
            )
        except Exception as error:  # noqa: BLE001
            _LOGGER.warning("safe device command execution failed", exc_info=True)
            result = (
                _dispatch_unknown_result(handle, "execution_failed_after_dispatch")
                if handle.dispatch_crossed
                else _failed_before_dispatch_result(handle, "execution_failed_before_dispatch")
            )
            result["errorDetail"] = type(error).__name__

        terminal_saved = await self._async_finish(handle, result)
        if (
            terminal_saved
            and handle.response_released
            and late_callback is not None
            and not self._closed
        ):
            await handle.first_response_resolved.wait()
            if not self._closed:
                await late_callback(handle, result)
        return result

    async def _async_finish(
        self, handle: SafeDeviceCommandHandle, result: Mapping[str, Any]
    ) -> bool:
        async with self._lock:
            record = self._record_or_none(handle.operation.operation_id)
            if record is None:
                self._release_handle(handle)
                return False
            if record["phase"] in _TERMINAL_PHASES:
                self._release_handle(handle)
                return False
            if handle.revoked and not handle.dispatch_crossed:
                phase = "failed_before_dispatch"
            elif result.get("confirmed") is True:
                phase = "confirmed"
            elif handle.dispatch_crossed:
                phase = "unconfirmed"
            else:
                phase = "failed_before_dispatch"
            record["phase"] = phase
            record["terminalAtMs"] = self._now_ms()
            record["error"] = (
                str(result.get("error"))[:256]
                if isinstance(result.get("error"), str) and result.get("error")
                else None
            )
            try:
                await self._save_locked()
            except Exception:
                self._release_handle(handle)
                return False
            self._release_handle(handle)
            return True

    async def _async_watchdog(
        self,
        handle: SafeDeviceCommandHandle,
        late_callback: LateCallback | None,
    ) -> None:
        try:
            await asyncio.sleep(self._observation_seconds)
        except asyncio.CancelledError:
            return
        if handle.task is None or handle.task.done() or not handle.dispatch_crossed:
            return
        result = _dispatch_unknown_result(handle, "observation_window_expired")
        async with self._lock:
            record = self._record_or_none(handle.operation.operation_id)
            if record is None or record["phase"] in _TERMINAL_PHASES:
                return
            record["phase"] = "dispatch_unknown"
            record["terminalAtMs"] = self._now_ms()
            record["error"] = "observation_window_expired"
            try:
                await self._save_locked()
            except Exception:
                return
        if handle.response_released and late_callback is not None and not self._closed:
            await handle.first_response_resolved.wait()
            if not self._closed:
                await late_callback(handle, result)

    async def _async_revoke_pre_dispatch(
        self, handle: SafeDeviceCommandHandle
    ) -> None:
        handle.revoked = True
        task = handle.task
        if task is not None and not task.done():
            task.cancel()
        async with self._lock:
            record = self._record_or_none(handle.operation.operation_id)
            if record is not None and record["phase"] in _ACTIVE_PHASES:
                record["phase"] = "failed_before_dispatch"
                record["terminalAtMs"] = self._now_ms()
                record["error"] = "response_budget_exhausted"
                try:
                    await self._save_locked()
                except Exception:
                    pass

    def _release_handle(self, handle: SafeDeviceCommandHandle) -> None:
        self._handles.pop(handle.operation.operation_id, None)
        if self._entities.get(handle.operation.entity_id) == handle.operation.operation_id:
            self._entities.pop(handle.operation.entity_id, None)
        if handle.watchdog is not None and not handle.watchdog.done():
            handle.watchdog.cancel()
        self._release_process_lease(handle)

    def _task_finished(self, handle: SafeDeviceCommandHandle) -> None:
        """Release a process lease even when unload cancellation escaped the task."""

        self._release_handle(handle)

    def _release_process_lease(self, handle: SafeDeviceCommandHandle) -> None:
        coordination = self._coordination
        if coordination is None:
            return
        expected = (self._instance_id, handle.operation.operation_id)
        if coordination.leases.get(handle.operation.entity_id) == expected:
            coordination.leases.pop(handle.operation.entity_id, None)
            coordination.completed_operations.add(handle.operation.operation_id)
            while (
                len(coordination.completed_operations)
                > MAX_SAFE_COMMAND_RECORDS * 2
            ):
                coordination.completed_operations.pop()

    def _prune_restart_quarantine(self) -> None:
        now = self._now_ms()
        self._restart_quarantine = {
            target: expiry
            for target, expiry in self._restart_quarantine.items()
            if expiry > now
        }

    def _quarantined_operation_id(self, entity_id: str) -> str | None:
        matching = [
            record
            for record in self._payload["operations"]
            if record.get("entityId") == entity_id
            and record.get("phase") == "dispatch_unknown"
        ]
        if not matching:
            return None
        return str(max(matching, key=lambda item: int(item["generation"]))["operationId"])

    def _prune_records(self) -> None:
        records = self._payload["operations"]
        if len(records) <= MAX_SAFE_COMMAND_RECORDS:
            return
        active_ids = set(self._handles)
        removable = [
            record
            for record in records
            if record["operationId"] not in active_ids
            and record["phase"] in _TERMINAL_PHASES
        ]
        removable.sort(key=lambda item: int(item.get("terminalAtMs") or 0))
        remove_ids = {
            item["operationId"]
            for item in removable[: len(records) - MAX_SAFE_COMMAND_RECORDS]
        }
        self._payload["operations"] = [
            item for item in records if item["operationId"] not in remove_ids
        ]
        if len(self._payload["operations"]) > MAX_SAFE_COMMAND_RECORDS:
            raise RuntimeError("safe device command journal is full")

    def _supersede_previous_generation(self, entity_id: str) -> None:
        previous_generation = self._payload["targetGenerations"].get(entity_id)
        if type(previous_generation) is not int:
            return
        for record in self._payload["operations"]:
            if (
                record.get("entityId") == entity_id
                and record.get("generation") == previous_generation
                and record.get("phase") in {"unconfirmed", "dispatch_unknown"}
            ):
                record["phase"] = "superseded"
                record["terminalAtMs"] = self._now_ms()
                record["error"] = "newer_generation_started"

    def _record(self, operation_id: str) -> dict[str, Any]:
        record = self._record_or_none(operation_id)
        if record is None:
            raise RuntimeError("safe device command record is missing")
        return record

    def _record_or_none(self, operation_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self._payload["operations"]
                if item.get("operationId") == operation_id
            ),
            None,
        )

    async def _save_locked(self, *, allow_closed: bool = False) -> None:
        coordination = self._require_coordination()
        async with coordination.persist_lock:
            if not self._owns_epoch() or (self._closed and not allow_closed):
                raise RuntimeError("safe device command lifecycle write is fenced")
            await self._store.async_save(copy.deepcopy(self._payload))

    def _process_coordination(self) -> _ProcessCoordination:
        if self._coordination is not None:
            return self._coordination
        loop = asyncio.get_running_loop()
        identity = getattr(self._store, "coordination_key", None)
        if not isinstance(identity, str) or not identity:
            identity = f"object:{id(self._store)}"
        key = (id(loop), identity)
        self._coordination = _PROCESS_COORDINATION.setdefault(
            key, _ProcessCoordination()
        )
        return self._coordination

    def _require_coordination(self) -> _ProcessCoordination:
        coordination = self._coordination
        if coordination is None or self._epoch <= 0:
            raise RuntimeError("safe device command lifecycle is not loaded")
        return coordination

    def _owns_epoch(self) -> bool:
        coordination = self._coordination
        return coordination is not None and coordination.epoch == self._epoch


def _empty_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "nextGeneration": 0,
        "targetGenerations": {},
        "operations": [],
    }


def _operation_record(
    handle: SafeDeviceCommandHandle, *, phase: str
) -> dict[str, Any]:
    operation = handle.operation
    return {
        "operationId": operation.operation_id,
        "identityDigest": operation.identity_digest,
        "requestFingerprint": operation.request_fingerprint,
        "requestId": operation.request_id,
        "targetId": operation.target_id,
        "entityId": operation.entity_id,
        "domain": operation.domain,
        "service": operation.service,
        "actionId": operation.action_id,
        "responseMediaType": operation.response_media_type,
        "preEvidenceRevision": operation.pre_evidence_revision,
        "generation": handle.generation,
        "phase": phase,
        "createdAtMs": handle.created_at_ms,
        "dispatchIntentAtMs": None,
        "dispatchedAtMs": None,
        "terminalAtMs": None,
        "expiresAtMs": handle.expires_at_ms,
        "lateReceipt": None,
        "error": None,
    }


def _failed_before_dispatch_result(
    handle: SafeDeviceCommandHandle, error: str
) -> dict[str, Any]:
    operation = handle.operation
    return {
        "requestId": operation.request_id,
        "targetId": operation.target_id,
        "actionId": operation.action_id,
        "accepted": False,
        "confirmed": False,
        "status": "failed",
        "statusName": "Не выполнено",
        "message": "Команда не была отправлена устройству.",
        "confirmationWindowMs": 30000,
        "readBack": {
            "attempted": False,
            "matched": False,
            "observedAt": None,
            "observedState": None,
            "attempts": 0,
        },
        "error": error,
    }


def _dispatch_unknown_result(
    handle: SafeDeviceCommandHandle, error: str
) -> dict[str, Any]:
    operation = handle.operation
    return {
        "requestId": operation.request_id,
        "targetId": operation.target_id,
        "actionId": operation.action_id,
        "accepted": True,
        "confirmed": False,
        "status": "accepted",
        "statusName": "Проверяется",
        "appliedAt": handle.dispatch_at_ms or handle.created_at_ms,
        "message": "Команда принята, состояние ещё не подтверждено.",
        "confirmationWindowMs": 30000,
        "readBack": {
            "attempted": False,
            "matched": False,
            "observedAt": None,
            "observedState": None,
            "attempts": 0,
        },
        "reason": "dispatch_result_unknown",
        "error": error,
    }


def _valid_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 512


def _json_mapping(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True
