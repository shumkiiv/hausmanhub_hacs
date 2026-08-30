"""Durable fail-safe release for the configured intercom relay."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

INTERCOM_RELEASE_SECONDS = 15
INTERCOM_RELEASE_RETRY_SECONDS = 5


class IntercomReleaseStore(Protocol):
    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


class IntercomReleaseObligation:
    """Persist a relay-off deadline before a turn-on command is dispatched."""

    def __init__(
        self,
        store: IntercomReleaseStore,
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._record: dict[str, object] | None = None
        self._task: asyncio.Task[None] | None = None
        self._callback: Callable[[Mapping[str, object]], Awaitable[bool]] | None = None
        self._shutting_down = False
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        payload = await self._store.async_load()
        if payload is None:
            return
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            raise RuntimeError("intercom release obligation store is corrupt")
        record = payload.get("record")
        if record is None:
            self._record = None
            return
        validated = _validated_record(record)
        if validated is None:
            raise RuntimeError("intercom release obligation store is corrupt")
        if getattr(self._store, "recovered_previous", False):
            now_ms = self._now_ms()
            validated["createdAt"] = min(int(validated["createdAt"]), now_ms)
            validated["armedAt"] = now_ms
            validated["deadlineMs"] = now_ms
            await self._store.async_save(
                {"version": 1, "record": copy.deepcopy(validated)}
            )
        if validated["armedAt"] is None:
            self._record = None
            await self._store.async_save(self._payload())
            return
        now_ms = self._now_ms()
        armed_at = int(validated["armedAt"])
        deadline_ms = int(validated["deadlineMs"])
        if armed_at > now_ms or deadline_ms - now_ms > (
            INTERCOM_RELEASE_SECONDS * 1000
        ):
            validated["deadlineMs"] = now_ms
            await self._store.async_save(
                {"version": 1, "record": copy.deepcopy(validated)}
            )
        self._record = validated

    def start(
        self,
        callback: Callable[[Mapping[str, object]], Awaitable[bool]],
    ) -> Callable[[], None]:
        self._shutting_down = False
        self._callback = callback
        if self._record is not None and self._record.get("armedAt") is not None:
            self._schedule()
        return self.shutdown

    async def async_prepare(
        self,
        *,
        target_id: str,
        entity_id: str,
        correlation_id: str,
        request_id: str,
    ) -> int:
        """Persist one exclusive release obligation before relay dispatch."""

        async with self._lock:
            if self._record is not None:
                raise RuntimeError("another intercom release is pending")
            now_ms = self._now_ms()
            self._record = {
                "targetId": target_id,
                "entityId": entity_id,
                "correlationId": correlation_id,
                "requestId": request_id,
                "deadlineMs": now_ms + INTERCOM_RELEASE_SECONDS * 1000,
                "createdAt": now_ms,
                "armedAt": None,
            }
            try:
                await self._store.async_save(self._payload())
            except Exception:
                self._record = None
                raise
            return INTERCOM_RELEASE_SECONDS

    async def async_arm(
        self,
        target_id: str,
        *,
        expected_entity_id: str | None = None,
        expected_request_id: str | None = None,
    ) -> int:
        """Refresh and verify the deadline immediately before dispatch."""

        async with self._lock:
            if self._record is None or self._record.get("targetId") != target_id:
                raise RuntimeError("intercom release preparation is missing")
            if (
                expected_entity_id is not None
                and self._record.get("entityId") != expected_entity_id
            ) or (
                expected_request_id is not None
                and self._record.get("requestId") != expected_request_id
            ):
                raise RuntimeError("intercom release dispatch descriptor changed")
            if self._record.get("armedAt") is not None:
                raise RuntimeError("intercom release is already armed")
            previous = copy.deepcopy(self._record)
            now_ms = self._now_ms()
            self._record["createdAt"] = min(
                int(self._record["createdAt"]), now_ms
            )
            self._record["armedAt"] = now_ms
            self._record["deadlineMs"] = now_ms + INTERCOM_RELEASE_SECONDS * 1000
            try:
                await self._store.async_save(self._payload())
            except Exception:
                self._record = previous
                raise
            self._schedule()
            return INTERCOM_RELEASE_SECONDS

    async def async_cancel(
        self,
        target_id: str,
        *,
        expected_entity_id: str | None = None,
        expected_request_id: str | None = None,
        unarmed_only: bool = True,
    ) -> bool:
        """Clear a preparation that never reached the physical dispatch point."""

        async with self._lock:
            record = self._record
            if record is None or record.get("targetId") != target_id:
                return False
            if (
                expected_entity_id is not None
                and record.get("entityId") != expected_entity_id
            ) or (
                expected_request_id is not None
                and record.get("requestId") != expected_request_id
            ):
                return False
            if unarmed_only and record.get("armedAt") is not None:
                return False
            previous = self._record
            try:
                self._record = None
                await self._store.async_save(self._payload())
            except Exception:
                self._record = previous
                raise
            self._cancel_timer()
            return True

    def _cancel_timer(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()

    def shutdown(self) -> None:
        """Release an armed relay immediately and retain retries after unload."""

        self._shutting_down = True
        self._cancel_timer()
        if (
            self._record is not None
            and self._record.get("armedAt") is not None
            and self._callback is not None
        ):
            # Home Assistant unload callbacks are synchronous. Keep the
            # durable callback alive in one bounded background task and move
            # the in-memory deadline to now. A failed release is persisted and
            # retried by the normal reconciliation path.
            self._record["deadlineMs"] = self._now_ms()
            self._schedule()
            return
        self._callback = None

    def _schedule(self) -> None:
        if self._callback is None or self._record is None:
            return
        if self._task is not None:
            self._task.cancel()
        self._task = asyncio.create_task(self._async_reconcile())

    async def _async_reconcile(self) -> None:
        try:
            record = copy.deepcopy(self._record)
            if record is None:
                return
            delay = max(0.0, (int(record["deadlineMs"]) - self._now_ms()) / 1000)
            if delay:
                await asyncio.sleep(delay)
            callback = self._callback
            released = False
            if callback is not None:
                try:
                    released = await callback(record)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    released = False
            async with self._lock:
                if self._record != record:
                    return
                next_record: dict[str, object] | None
                if released:
                    next_record = None
                else:
                    next_record = copy.deepcopy(self._record)
                    next_record["deadlineMs"] = (
                        self._now_ms() + INTERCOM_RELEASE_RETRY_SECONDS * 1000
                    )
                try:
                    await self._store.async_save(
                        {
                            "version": 1,
                            "record": copy.deepcopy(next_record),
                        }
                    )
                except Exception:  # noqa: BLE001
                    next_record = copy.deepcopy(record)
                    next_record["deadlineMs"] = (
                        self._now_ms() + INTERCOM_RELEASE_RETRY_SECONDS * 1000
                    )
                self._record = next_record
                if self._record is not None:
                    self._task = None
                    self._schedule()
                elif self._shutting_down:
                    self._callback = None
        finally:
            current = asyncio.current_task()
            if self._task is current:
                self._task = None

    def _payload(self) -> dict[str, object]:
        return {
            "version": 1,
            "record": copy.deepcopy(self._record),
        }


def _validated_record(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    required = {
        "targetId",
        "entityId",
        "correlationId",
        "requestId",
        "deadlineMs",
        "createdAt",
        "armedAt",
    }
    if set(value) != required:
        return None
    if (
        any(
            not isinstance(value.get(field), str) or not value.get(field)
            for field in ("targetId", "entityId", "correlationId", "requestId")
        )
        or type(value.get("deadlineMs")) is not int
        or type(value.get("createdAt")) is not int
        or (
            value.get("armedAt") is not None
            and type(value.get("armedAt")) is not int
        )
        or int(value.get("createdAt")) < 0
        or int(value.get("deadlineMs")) < int(value.get("createdAt"))
        or (
            type(value.get("armedAt")) is int
            and (
                int(value["armedAt"]) < 0
                or int(value["deadlineMs"]) < int(value["armedAt"])
                or int(value["deadlineMs"]) - int(value["armedAt"])
                > INTERCOM_RELEASE_SECONDS * 1000
            )
        )
        or not str(value.get("entityId")).startswith("switch.")
    ):
        return None
    return copy.deepcopy(dict(value))


def valid_intercom_release_payload(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("version") == 1
        and (
            value.get("record") is None
            or _validated_record(value.get("record")) is not None
        )
    )
