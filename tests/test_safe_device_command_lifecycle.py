from __future__ import annotations

import asyncio
import copy

import pytest

from custom_components.hausman_hub.application.safe_device_command_lifecycle import (
    CommandDeadline,
    SafeDeviceCommandLifecycle,
    SafeDeviceCommandOperation,
)


class MemoryStore:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None
        self.coordination_key = f"memory:{id(self)}"

    async def async_load(self) -> object | None:
        return copy.deepcopy(self.payload)

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = copy.deepcopy(payload)


class BlockingMarkerStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.marker_entered = asyncio.Event()
        self.release_marker = asyncio.Event()
        self._saves = 0

    async def async_save(self, payload: dict[str, object]) -> None:
        self._saves += 1
        if self._saves == 3:
            self.marker_entered.set()
            try:
                await self.release_marker.wait()
            except asyncio.CancelledError:
                # A storage adapter may finish a write in a worker even when its
                # awaiting task is cancelled. Reproduce that old-instance race.
                await self.release_marker.wait()
        await super().async_save(payload)


class FailingSaveStore(MemoryStore):
    def __init__(self, *failed_saves: int) -> None:
        super().__init__()
        self.failed_saves = set(failed_saves)
        self.saves = 0

    async def async_save(self, payload: dict[str, object]) -> None:
        self.saves += 1
        if self.saves in self.failed_saves:
            raise RuntimeError(f"save {self.saves} failed")
        await super().async_save(payload)


class FailingObservingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.failed = asyncio.Event()
        self._did_fail = False

    async def async_save(self, payload: dict[str, object]) -> None:
        operations = payload.get("operations", [])
        if (
            not self._did_fail
            and isinstance(operations, list)
            and any(item.get("phase") == "observing" for item in operations)
        ):
            self._did_fail = True
            self.failed.set()
            raise RuntimeError("observing marker failed")
        await super().async_save(payload)


def operation(
    *,
    request_id: str,
    target_id: str = "bedroom_climate",
    entity_id: str = "climate.bedroom",
) -> SafeDeviceCommandOperation:
    return SafeDeviceCommandOperation(
        request_fingerprint=f"fingerprint:{request_id}",
        request_id=request_id,
        target_id=target_id,
        entity_id=entity_id,
        domain="climate",
        service="set_temperature",
        action_id="set_temperature",
        response_media_type="application/json",
    )


def accepted(request_id: str) -> dict[str, object]:
    return {
        "requestId": request_id,
        "accepted": True,
        "confirmed": False,
        "status": "accepted",
    }


@pytest.mark.asyncio
async def test_same_entity_alias_is_busy_until_original_execution_finishes() -> None:
    store = MemoryStore()
    lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    await lifecycle.async_load()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_execute(mark_dispatch, validate_dispatch):
        await validate_dispatch()
        mark_dispatch()
        entered.set()
        await release.wait()
        return accepted("first")

    first = await lifecycle.async_start(operation(request_id="first"), slow_execute)
    await entered.wait()

    with pytest.raises(RuntimeError, match="target is busy"):
        await lifecycle.async_start(
            operation(request_id="alias", target_id="bedroom_climate_alias"),
            slow_execute,
        )

    release.set()
    assert first.task is not None
    await first.task

    second = await lifecycle.async_start(
        operation(request_id="after", target_id="bedroom_climate_alias"),
        lambda mark, validate: _immediate_execute(mark, validate, "after"),
    )
    assert second.task is not None
    await second.task
    await lifecycle.async_close()


@pytest.mark.asyncio
async def test_new_coordinator_keeps_entity_busy_while_old_call_ignores_cancel() -> None:
    store = MemoryStore()
    first_lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    await first_lifecycle.async_load()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def cancellation_resistant(mark_dispatch, validate_dispatch):
        await validate_dispatch()
        mark_dispatch()
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return accepted("old")

    old = await first_lifecycle.async_start(
        operation(request_id="old"), cancellation_resistant
    )
    await entered.wait()
    await first_lifecycle.async_close()

    second_lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    await second_lifecycle.async_load()
    with pytest.raises(RuntimeError, match="target is busy"):
        await second_lifecycle.async_start(
            operation(request_id="new"),
            lambda mark, validate: _immediate_execute(mark, validate, "new"),
        )

    release.set()
    assert old.task is not None
    await old.task
    new = await second_lifecycle.async_start(
        operation(request_id="new"),
        lambda mark, validate: _immediate_execute(mark, validate, "new"),
    )
    assert new.task is not None
    await new.task
    await second_lifecycle.async_close()


@pytest.mark.asyncio
async def test_old_blocked_marker_write_cannot_overwrite_new_instance_state() -> None:
    store = BlockingMarkerStore()
    old_lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    await old_lifecycle.async_load()
    entered = asyncio.Event()
    release_call = asyncio.Event()

    async def slow_execute(mark_dispatch, validate_dispatch):
        await validate_dispatch()
        mark_dispatch()
        entered.set()
        try:
            await release_call.wait()
        except asyncio.CancelledError:
            await release_call.wait()
        return accepted("old-marker")

    old = await old_lifecycle.async_start(
        operation(request_id="old-marker"), slow_execute
    )
    await entered.wait()
    await store.marker_entered.wait()

    close_task = asyncio.create_task(old_lifecycle.async_close())
    new_lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    load_task = asyncio.create_task(new_lifecycle.async_load())
    await asyncio.sleep(0)
    assert not close_task.done()
    assert not load_task.done()

    store.release_marker.set()
    await close_task
    await load_task
    newer = await new_lifecycle.async_start(
        operation(
            request_id="newer",
            target_id="hall_climate",
            entity_id="climate.hall",
        ),
        lambda mark, validate: _immediate_execute(mark, validate, "newer"),
    )
    assert newer.task is not None
    await newer.task
    new_written = copy.deepcopy(store.payload)

    release_call.set()
    assert old.task is not None
    await old.task
    await asyncio.sleep(0)
    assert store.payload == new_written
    await new_lifecycle.async_close()


@pytest.mark.asyncio
async def test_restart_quarantines_existing_dispatch_unknown_without_redispatch() -> None:
    store = MemoryStore()
    first = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    await first.async_load()
    now = first._now_ms()
    handle = await first.async_start(
        operation(request_id="restart"),
        lambda mark, validate: _immediate_execute(mark, validate, "restart"),
    )
    assert handle.task is not None
    await handle.task
    assert store.payload is not None
    record = store.payload["operations"][0]
    record["phase"] = "dispatch_unknown"
    record["terminalAtMs"] = now
    record["expiresAtMs"] = now + 30_000

    restarted_store = MemoryStore()
    restarted_store.payload = copy.deepcopy(store.payload)
    restarted = SafeDeviceCommandLifecycle(restarted_store, observation_seconds=30)
    await restarted.async_load()
    calls = 0

    async def execute(mark_dispatch, validate_dispatch):
        nonlocal calls
        calls += 1
        return await _immediate_execute(mark_dispatch, validate_dispatch, "duplicate")

    with pytest.raises(RuntimeError, match="target is busy"):
        await restarted.async_start(operation(request_id="duplicate"), execute)
    assert calls == 0
    await restarted.async_close()


@pytest.mark.asyncio
async def test_restart_releases_durable_prepared_record_as_failed_before_dispatch() -> None:
    old_store = FailingSaveStore(2)
    old = SafeDeviceCommandLifecycle(old_store, observation_seconds=30)
    await old.async_load()
    calls = 0

    async def execute(mark_dispatch, validate_dispatch):
        nonlocal calls
        calls += 1
        return await _immediate_execute(mark_dispatch, validate_dispatch, "prepared")

    with pytest.raises(RuntimeError, match="save 2 failed"):
        await old.async_start(operation(request_id="prepared"), execute)
    assert calls == 0
    assert old_store.payload["operations"][0]["phase"] == "prepared"

    restarted_store = MemoryStore()
    restarted_store.payload = copy.deepcopy(old_store.payload)
    restarted = SafeDeviceCommandLifecycle(restarted_store, observation_seconds=30)
    await restarted.async_load()
    assert restarted._payload["operations"][0]["phase"] == "failed_before_dispatch"
    retry = await restarted.async_start(operation(request_id="retry-prepared"), execute)
    assert retry.task is not None
    await retry.task
    assert calls == 1
    await restarted.async_close()


@pytest.mark.asyncio
async def test_prepare_store_failure_releases_entity_without_dispatch() -> None:
    store = FailingSaveStore(1)
    lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    await lifecycle.async_load()
    calls = 0

    async def execute(mark_dispatch, validate_dispatch):
        nonlocal calls
        calls += 1
        return await _immediate_execute(mark_dispatch, validate_dispatch, "failed")

    with pytest.raises(RuntimeError, match="save 1 failed"):
        await lifecycle.async_start(operation(request_id="failed"), execute)
    assert calls == 0

    recovered = await lifecycle.async_start(operation(request_id="recovered"), execute)
    assert recovered.task is not None
    await recovered.task
    assert calls == 1
    await lifecycle.async_close()


@pytest.mark.asyncio
async def test_dispatch_intent_store_failure_never_dispatches_and_releases_entity() -> None:
    store = FailingSaveStore(2)
    lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    await lifecycle.async_load()
    calls = 0

    async def execute(mark_dispatch, validate_dispatch):
        nonlocal calls
        calls += 1
        return await _immediate_execute(mark_dispatch, validate_dispatch, "intent")

    with pytest.raises(RuntimeError, match="save 2 failed"):
        await lifecycle.async_start(operation(request_id="intent"), execute)
    assert calls == 0

    recovered = await lifecycle.async_start(operation(request_id="after-intent"), execute)
    assert recovered.task is not None
    await recovered.task
    assert calls == 1
    await lifecycle.async_close()


@pytest.mark.asyncio
async def test_dispatch_marker_store_failure_finishes_unconfirmed_without_redispatch() -> None:
    store = FailingObservingStore()
    lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    await lifecycle.async_load()
    calls = 0

    async def execute(mark_dispatch, validate_dispatch):
        nonlocal calls
        await validate_dispatch()
        calls += 1
        mark_dispatch()
        await store.failed.wait()
        return accepted("marker-failure")

    handle = await lifecycle.async_start(
        operation(request_id="marker-failure"), execute
    )
    assert handle.task is not None
    result = await handle.task
    assert result == accepted("marker-failure")
    assert calls == 1
    record = next(
        item
        for item in store.payload["operations"]
        if item["operationId"] == handle.operation.operation_id
    )
    assert record["phase"] == "unconfirmed"
    await lifecycle.async_close()


@pytest.mark.asyncio
async def test_disconnect_after_dispatch_does_not_cancel_execution_and_publishes_late() -> None:
    store = MemoryStore()
    lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=30)
    await lifecycle.async_load()
    entered = asyncio.Event()
    release = asyncio.Event()
    published: list[dict[str, object]] = []

    async def execute(mark_dispatch, validate_dispatch):
        await validate_dispatch()
        mark_dispatch()
        entered.set()
        await release.wait()
        return accepted("disconnect")

    async def publish(_handle, receipt):
        published.append(dict(receipt))

    handle = await lifecycle.async_start(
        operation(request_id="disconnect"), execute, late_callback=publish
    )
    response = asyncio.create_task(
        lifecycle.async_response(
            handle,
            CommandDeadline.start(budget_seconds=30),
        )
    )
    await entered.wait()
    response.cancel()
    with pytest.raises(asyncio.CancelledError):
        await response
    await lifecycle.async_note_http_abandoned(handle)
    release.set()
    assert handle.task is not None
    await handle.task
    assert published == [accepted("disconnect")]
    await lifecycle.async_close()


@pytest.mark.asyncio
async def test_late_receipt_requires_current_generation_and_unexpired_window() -> None:
    now = 1_000
    store = MemoryStore()
    lifecycle = SafeDeviceCommandLifecycle(
        store,
        now_ms=lambda: now,
        observation_seconds=1,
    )
    await lifecycle.async_load()
    first = await lifecycle.async_start(
        operation(request_id="first-late"),
        lambda mark, validate: _immediate_execute(mark, validate, "first-late"),
    )
    assert first.task is not None
    await first.task
    assert await lifecycle.async_record_late_receipt(
        first, {"eventId": "late-success", "confirmed": True}
    )

    second = await lifecycle.async_start(
        operation(request_id="second-late"),
        lambda mark, validate: _immediate_execute(mark, validate, "second-late"),
    )
    assert second.task is not None
    await second.task
    first_record = next(
        item
        for item in store.payload["operations"]
        if item["operationId"] == first.operation.operation_id
    )
    assert first_record["phase"] == "superseded"
    assert not await lifecycle.async_record_late_receipt(
        first, {"eventId": "superseded", "confirmed": False}
    )

    failure = await lifecycle.async_start(
        operation(
            request_id="late-failure",
            target_id="hall_climate",
            entity_id="climate.hall",
        ),
        lambda mark, validate: _immediate_execute(mark, validate, "late-failure"),
    )
    assert failure.task is not None
    await failure.task
    assert await lifecycle.async_record_late_receipt(
        failure,
        {"eventId": "late-failure", "accepted": True, "confirmed": False},
    )

    now = 2_001
    assert not await lifecycle.async_record_late_receipt(
        second, {"eventId": "expired", "confirmed": True}
    )
    await lifecycle.async_close()


@pytest.mark.asyncio
async def test_watchdog_publishes_one_unknown_and_late_execution_cannot_replace_it() -> None:
    store = MemoryStore()
    lifecycle = SafeDeviceCommandLifecycle(store, observation_seconds=0.01)
    await lifecycle.async_load()
    entered = asyncio.Event()
    release = asyncio.Event()
    published: list[dict[str, object]] = []

    async def slow(mark_dispatch, validate_dispatch):
        await validate_dispatch()
        mark_dispatch()
        entered.set()
        await release.wait()
        return accepted("watchdog")

    async def publish(_handle, receipt):
        published.append(dict(receipt))

    handle = await lifecycle.async_start(
        operation(request_id="watchdog"), slow, late_callback=publish
    )
    await entered.wait()
    handle.response_released = True
    await lifecycle.async_note_first_response_persisted(handle)
    await asyncio.sleep(0.03)
    assert len(published) == 1
    assert published[0]["error"] == "observation_window_expired"

    release.set()
    assert handle.task is not None
    await handle.task
    await asyncio.sleep(0)
    assert len(published) == 1
    await lifecycle.async_close()


@pytest.mark.asyncio
async def test_capacity_is_reserved_before_any_second_dispatch() -> None:
    store = MemoryStore()
    lifecycle = SafeDeviceCommandLifecycle(
        store, observation_seconds=30, max_active=1
    )
    await lifecycle.async_load()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(mark_dispatch, validate_dispatch):
        await validate_dispatch()
        mark_dispatch()
        entered.set()
        await release.wait()
        return accepted("capacity-first")

    first = await lifecycle.async_start(operation(request_id="capacity-first"), slow)
    await entered.wait()
    with pytest.raises(RuntimeError, match="capacity is full"):
        await lifecycle.async_start(
            operation(
                request_id="capacity-second",
                target_id="hall_climate",
                entity_id="climate.hall",
            ),
            lambda mark, validate: _immediate_execute(
                mark, validate, "capacity-second"
            ),
        )
    release.set()
    assert first.task is not None
    await first.task
    await lifecycle.async_close()


@pytest.mark.asyncio
async def test_reload_capacity_counts_cancellation_resistant_old_process_lease() -> None:
    store = MemoryStore()
    old_lifecycle = SafeDeviceCommandLifecycle(
        store, observation_seconds=30, max_active=1
    )
    await old_lifecycle.async_load()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def cancellation_resistant(mark_dispatch, validate_dispatch):
        await validate_dispatch()
        mark_dispatch()
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return accepted("old-capacity")

    old = await old_lifecycle.async_start(
        operation(request_id="old-capacity"), cancellation_resistant
    )
    await entered.wait()
    await old_lifecycle.async_close()
    reloaded = SafeDeviceCommandLifecycle(
        store, observation_seconds=30, max_active=1
    )
    await reloaded.async_load()

    with pytest.raises(RuntimeError, match="capacity is full"):
        await reloaded.async_start(
            operation(
                request_id="different-entity",
                target_id="hall_climate",
                entity_id="climate.hall",
            ),
            lambda mark, validate: _immediate_execute(
                mark, validate, "different-entity"
            ),
        )

    release.set()
    assert old.task is not None
    await old.task
    after = await reloaded.async_start(
        operation(
            request_id="after-old-finished",
            target_id="hall_climate",
            entity_id="climate.hall",
        ),
        lambda mark, validate: _immediate_execute(
            mark, validate, "after-old-finished"
        ),
    )
    assert after.task is not None
    await after.task
    await reloaded.async_close()


async def _immediate_execute(mark_dispatch, validate_dispatch, request_id: str):
    await validate_dispatch()
    mark_dispatch()
    return accepted(request_id)
