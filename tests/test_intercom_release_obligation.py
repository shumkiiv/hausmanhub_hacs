"""The intercom relay-off deadline survives dispatch failures and restart."""

from __future__ import annotations

import asyncio
import json
import unittest

from custom_components.hausman_hub.application import (
    intercom_release_obligation as intercom_release_module,
)
from custom_components.hausman_hub.application.intercom_release_obligation import (
    IntercomReleaseObligation,
)


class MemoryStore:
    def __init__(self) -> None:
        self.payload: object | None = None
        self.recovered_previous = False

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = json.loads(json.dumps(payload))


class FailingClearStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_clear = False

    async def async_save(self, payload: dict[str, object]) -> None:
        if self.fail_clear and payload.get("record") is None:
            raise OSError("store unavailable")
        await super().async_save(payload)


async def _wait_until(predicate, *, attempts: int = 20) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not reached")


def test_release_is_persisted_before_dispatch_and_restored_after_restart() -> None:
    async def exercise() -> None:
        now = [0]
        store = MemoryStore()
        first = IntercomReleaseObligation(store, now_ms=lambda: now[0])
        await first.async_load()
        seconds = await first.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.1",
            request_id="intercom.1.release",
        )
        assert seconds == 15
        assert store.payload["record"]["deadlineMs"] == 15_000
        assert store.payload["record"]["armedAt"] is None
        await first.async_arm("intercom")
        assert store.payload["record"]["armedAt"] == 0

        now[0] = 15_000
        calls: list[str] = []

        async def release(record) -> bool:
            calls.append(record["entityId"])
            return True

        restarted = IntercomReleaseObligation(store, now_ms=lambda: now[0])
        await restarted.async_load()
        cancel = restarted.start(release)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert calls == ["switch.intercom"]
        assert store.payload == {"version": 1, "record": None}

    asyncio.run(exercise())


def test_unconfirmed_release_remains_durable_for_retry() -> None:
    async def exercise() -> None:
        now = [0]
        store = MemoryStore()
        obligation = IntercomReleaseObligation(store, now_ms=lambda: now[0])
        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.2",
            request_id="intercom.2.release",
        )
        await obligation.async_arm("intercom")
        now[0] = 15_000

        async def not_released(_record) -> bool:
            return False

        cancel = obligation.start(not_released)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert store.payload["record"]["deadlineMs"] == 20_000

    asyncio.run(exercise())


def test_failed_clear_keeps_in_memory_retry_and_persisted_deadline() -> None:
    async def exercise() -> None:
        now = [0]
        store = FailingClearStore()
        obligation = IntercomReleaseObligation(store, now_ms=lambda: now[0])
        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.3",
            request_id="intercom.3.release",
        )
        await obligation.async_arm("intercom")
        store.fail_clear = True
        now[0] = 15_000

        async def released(_record) -> bool:
            return True

        shutdown = obligation.start(released)
        await _wait_until(
            lambda: obligation._record is not None
            and obligation._record["deadlineMs"] == 20_000
        )

        assert obligation._record["deadlineMs"] == 20_000
        assert store.payload["record"]["deadlineMs"] == 15_000

        store.fail_clear = False
        shutdown()
        await _wait_until(lambda: obligation._record is None)

    asyncio.run(exercise())


def test_arm_refreshes_deadline_at_the_actual_dispatch_boundary() -> None:
    async def exercise() -> None:
        now = [0]
        store = MemoryStore()
        obligation = IntercomReleaseObligation(store, now_ms=lambda: now[0])
        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.batch",
            request_id="intercom.batch.release",
        )

        now[0] = 14_900
        seconds = await obligation.async_arm("intercom")

        assert seconds == 15
        assert store.payload["record"]["deadlineMs"] == 29_900
        assert store.payload["record"]["armedAt"] == 14_900

    asyncio.run(exercise())


def test_repeated_command_is_rejected_while_release_is_armed() -> None:
    async def exercise() -> None:
        now = [0]
        store = MemoryStore()
        obligation = IntercomReleaseObligation(store, now_ms=lambda: now[0])

        async def release(_record) -> bool:
            return True

        cancel = obligation.start(release)
        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.first",
            request_id="intercom.first.release",
        )
        await obligation.async_arm("intercom")
        original_task = obligation._task

        now[0] = 14_900
        try:
            await obligation.async_prepare(
                target_id="intercom",
                entity_id="switch.intercom",
                correlation_id="intercom.second",
                request_id="intercom.second.release",
            )
        except RuntimeError as error:
            assert "pending" in str(error)
        else:
            raise AssertionError("a second relay-on dispatch must be rejected")

        assert store.payload["record"]["deadlineMs"] == 15_000
        assert store.payload["record"]["armedAt"] == 0
        assert store.payload["record"]["correlationId"] == "intercom.first"
        assert store.payload["record"]["requestId"] == "intercom.first.release"
        assert obligation._task is original_task
        cancel()

    asyncio.run(exercise())


def test_concurrent_prepare_is_rejected_before_the_first_arm() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        obligation = IntercomReleaseObligation(store, now_ms=lambda: 0)
        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.first",
            request_id="intercom.first.release",
        )

        try:
            await obligation.async_prepare(
                target_id="intercom",
                entity_id="switch.intercom",
                correlation_id="intercom.concurrent",
                request_id="intercom.concurrent.release",
            )
        except RuntimeError as error:
            assert "pending" in str(error)
        else:
            raise AssertionError("a concurrent relay-on preparation must fail")

        assert store.payload["record"]["armedAt"] is None
        assert store.payload["record"]["correlationId"] == "intercom.first"

    asyncio.run(exercise())


def test_prepare_does_not_start_countdown_during_a_long_batch() -> None:
    async def exercise() -> None:
        now = [0]
        store = MemoryStore()
        calls: list[str] = []
        obligation = IntercomReleaseObligation(store, now_ms=lambda: now[0])

        async def release(record) -> bool:
            calls.append(str(record["targetId"]))
            return True

        obligation.start(release)
        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.long-batch",
            request_id="intercom.long-batch.release",
        )
        now[0] = 20_000
        await asyncio.sleep(0)

        assert calls == []
        await obligation.async_arm("intercom")
        assert store.payload["record"]["deadlineMs"] == 35_000

    asyncio.run(exercise())


def test_restored_unarmed_preparation_is_cleared_without_release() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        prepared = IntercomReleaseObligation(store, now_ms=lambda: 1_000)
        await prepared.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.unarmed",
            request_id="intercom.unarmed.release",
        )

        calls: list[str] = []
        restarted = IntercomReleaseObligation(store, now_ms=lambda: 2_000)
        await restarted.async_load()

        async def release(record) -> bool:
            calls.append(str(record["targetId"]))
            return True

        cancel = restarted.start(release)
        await asyncio.sleep(0)
        cancel()

        assert calls == []
        assert store.payload == {"version": 1, "record": None}

    asyncio.run(exercise())


def test_restored_clock_rollback_releases_immediately() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        store.payload = {
            "version": 1,
            "record": {
                "targetId": "intercom",
                "entityId": "switch.intercom",
                "correlationId": "intercom.rollback",
                "requestId": "intercom.rollback.release",
                "deadlineMs": 115_000,
                "createdAt": 100_000,
                "armedAt": 100_000,
            },
        }
        calls: list[str] = []
        obligation = IntercomReleaseObligation(store, now_ms=lambda: 0)
        await obligation.async_load()

        async def release(record) -> bool:
            calls.append(str(record["targetId"]))
            return True

        cancel = obligation.start(release)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert calls == ["intercom"]
        assert store.payload == {"version": 1, "record": None}

    asyncio.run(exercise())


def test_arm_fails_closed_when_preparation_was_already_cleared() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        obligation = IntercomReleaseObligation(store, now_ms=lambda: 0)
        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.missing",
            request_id="intercom.missing.release",
        )
        obligation._record = None
        store.payload = {"version": 1, "record": None}

        try:
            await obligation.async_arm("intercom")
        except RuntimeError as error:
            assert "preparation is missing" in str(error)
        else:
            raise AssertionError("dispatch authority must not be recreated")

    asyncio.run(exercise())


def test_arm_rejects_changed_dispatch_identity() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        obligation = IntercomReleaseObligation(store, now_ms=lambda: 0)
        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.identity",
            request_id="intercom.identity.release",
        )

        with_unexpected_entity = obligation.async_arm(
            "intercom",
            expected_entity_id="switch.remapped",
            expected_request_id="intercom.identity.release",
        )
        try:
            await with_unexpected_entity
        except RuntimeError as error:
            assert "descriptor changed" in str(error)
        else:
            raise AssertionError("changed intercom identity must fail closed")

        assert store.payload["record"]["armedAt"] is None

    asyncio.run(exercise())


def test_recovered_unarmed_generation_releases_uncertain_relay() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        prepared = IntercomReleaseObligation(store, now_ms=lambda: 1_000)
        await prepared.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.recovered",
            request_id="intercom.recovered.release",
        )
        store.recovered_previous = True
        restarted = IntercomReleaseObligation(store, now_ms=lambda: 2_000)
        await restarted.async_load()
        calls: list[str] = []

        async def release(record) -> bool:
            calls.append(str(record["entityId"]))
            return True

        cancel = restarted.start(release)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert calls == ["switch.intercom"]
        assert store.payload == {"version": 1, "record": None}

    asyncio.run(exercise())


def test_cancelled_unarmed_attempt_keeps_callback_for_next_success() -> None:
    async def exercise() -> None:
        now = [0]
        store = MemoryStore()
        calls: list[str] = []
        obligation = IntercomReleaseObligation(store, now_ms=lambda: now[0])

        async def release(record) -> bool:
            calls.append(str(record["requestId"]))
            return True

        shutdown = obligation.start(release)
        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.failed",
            request_id="intercom.failed.release",
        )
        assert await obligation.async_cancel(
            "intercom", expected_request_id="intercom.failed.release"
        )
        assert obligation._callback is release

        await obligation.async_prepare(
            target_id="intercom",
            entity_id="switch.intercom",
            correlation_id="intercom.success",
            request_id="intercom.success.release",
        )
        await obligation.async_arm("intercom")
        now[0] = 15_000
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert calls == ["intercom.success.release"]
        assert store.payload == {"version": 1, "record": None}
        shutdown()

    asyncio.run(exercise())


def test_shutdown_releases_armed_relay_immediately_and_retries() -> None:
    async def exercise() -> None:
        now = [1_000]
        store = MemoryStore()
        calls: list[int] = []
        second_attempt_started = asyncio.Event()
        finish_second_attempt = asyncio.Event()
        obligation = IntercomReleaseObligation(store, now_ms=lambda: now[0])

        async def release(_record) -> bool:
            calls.append(now[0])
            if len(calls) == 1:
                return False
            second_attempt_started.set()
            await finish_second_attempt.wait()
            return True

        original_retry_seconds = intercom_release_module.INTERCOM_RELEASE_RETRY_SECONDS
        intercom_release_module.INTERCOM_RELEASE_RETRY_SECONDS = 0
        try:
            shutdown = obligation.start(release)
            await obligation.async_prepare(
                target_id="intercom",
                entity_id="switch.intercom",
                correlation_id="intercom.unload",
                request_id="intercom.unload.release",
            )
            await obligation.async_arm("intercom")

            shutdown()
            await second_attempt_started.wait()
            assert calls == [1_000, 1_000]
            assert store.payload["record"]["deadlineMs"] == 1_000

            finish_second_attempt.set()
            await _wait_until(lambda: obligation._record is None)
            assert store.payload == {"version": 1, "record": None}
            assert obligation._callback is None
        finally:
            intercom_release_module.INTERCOM_RELEASE_RETRY_SECONDS = (
                original_retry_seconds
            )

    asyncio.run(exercise())


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case))
    return suite
