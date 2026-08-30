"""Delayed light-off deadlines survive reload without unsafe replay."""

from __future__ import annotations

import asyncio
import json

import pytest

from custom_components.hausman_hub.application.light_safety_obligations import (
    MAX_LIGHT_SAFETY_OBLIGATIONS,
    RECONCILE_INVALIDATED,
    LightSafetyObligations,
)


class MemoryStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload
        self.recovered_previous = False

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = json.loads(json.dumps(payload))


class MemoryIssueReporter:
    def __init__(self) -> None:
        self.reported: list[dict[str, object]] = []
        self.cleared: list[str] = []

    async def async_report_failure(self, record) -> None:
        self.reported.append(dict(record))

    async def async_clear(self, target_id: str) -> None:
        self.cleared.append(target_id)


class FailingTransitionStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_transition = False

    async def async_save(self, payload: dict[str, object]) -> None:
        if self.fail_transition:
            raise OSError("store unavailable")
        await super().async_save(payload)


def test_restored_due_obligation_reconciles_once_without_in_process_duplicate() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        current = LightSafetyObligations(store, now_ms=lambda: 1_000)
        await current.async_load()
        await current.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.1",
            deadline_ms=1_000,
            ownership_revision="revision.1",
        )
        calls: list[dict[str, object]] = []

        async def reconcile(record):
            calls.append(dict(record))
            return True

        assert calls == []
        restarted = LightSafetyObligations(store, now_ms=lambda: 1_000)
        await restarted.async_load()
        cancel = restarted.start(reconcile)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert [item["targetId"] for item in calls] == ["shower_light"]
        assert store.payload == {"version": 1, "records": []}

    asyncio.run(exercise())


def test_new_presence_cancels_persisted_obligation_before_restart() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        service = LightSafetyObligations(store, now_ms=lambda: 1_000)
        await service.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.2",
            deadline_ms=301_000,
            ownership_revision="revision.2",
        )
        await service.async_cancel("shower_light")

        assert store.payload == {"version": 1, "records": []}

    asyncio.run(exercise())


def test_corrupt_obligation_document_fails_closed() -> None:
    async def exercise() -> None:
        store = MemoryStore(
            {
                "version": 1,
                "records": [{"targetId": "light", "deadlineMs": "soon"}],
            }
        )
        service = LightSafetyObligations(store, now_ms=lambda: 1_000)
        with pytest.raises(RuntimeError, match="corrupt"):
            await service.async_load()

    asyncio.run(exercise())


def test_state_on_obligation_is_limited_to_system_shower_controller() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        service = LightSafetyObligations(store, now_ms=lambda: 1_000)

        with pytest.raises(ValueError, match="shower controller"):
            await service.async_arm(
                target_id="fan",
                entity_id="switch.fan",
                scenario_id="forged-controller",
                run_id="run.forged",
                deadline_ms=1_000,
                ownership_revision=None,
                kind="state_on",
            )

        assert store.payload is None

    asyncio.run(exercise())


def test_forged_persisted_state_on_obligation_fails_closed() -> None:
    async def exercise() -> None:
        store = MemoryStore(
            {
                "version": 1,
                "records": [
                    {
                        "targetId": "fan",
                        "entityId": "switch.fan",
                        "scenarioId": "forged-controller",
                        "runId": "run.forged",
                        "deadlineMs": 1_000,
                        "ownershipRevision": None,
                        "createdAt": 1,
                        "generationId": "forged-generation",
                        "attempt": 0,
                        "kind": "state_on",
                    }
                ],
            }
        )
        service = LightSafetyObligations(store, now_ms=lambda: 1_000)

        with pytest.raises(RuntimeError, match="corrupt"):
            await service.async_load()

    asyncio.run(exercise())


def test_capacity_rejects_new_obligation_before_mutation_and_reports_issue() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        reporter = MemoryIssueReporter()
        service = LightSafetyObligations(store, issue_reporter=reporter)
        for index in range(MAX_LIGHT_SAFETY_OBLIGATIONS):
            await service.async_arm(
                target_id=f"light_{index}",
                entity_id=f"light.room_{index}",
                scenario_id="capacity",
                run_id=f"run.{index}",
                deadline_ms=10_000,
                ownership_revision=f"revision.{index}",
            )
        with pytest.raises(RuntimeError, match="full"):
            await service.async_arm(
                target_id="overflow",
                entity_id="light.overflow",
                scenario_id="capacity",
                run_id="run.overflow",
                deadline_ms=10_000,
                ownership_revision="revision.overflow",
            )
        assert "overflow" not in service._records
        assert reporter.reported[-1]["targetId"] == "overflow"

    asyncio.run(exercise())


def test_lost_ownership_removes_deadline_and_reports_manual_repair() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        reporter = MemoryIssueReporter()
        current = LightSafetyObligations(store, now_ms=lambda: 1_000)
        await current.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.lost",
            deadline_ms=1_000,
            ownership_revision="revision.lost",
        )
        restarted = LightSafetyObligations(
            store,
            now_ms=lambda: 1_000,
            issue_reporter=reporter,
        )
        await restarted.async_load()

        async def reconcile(_record):
            return RECONCILE_INVALIDATED

        cancel = restarted.start(reconcile)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert store.payload == {"version": 1, "records": []}
        assert [item["targetId"] for item in reporter.reported] == [
            "shower_light"
        ]
        assert reporter.cleared == []

    asyncio.run(exercise())


def test_due_failed_off_can_retry_immediately_without_restart() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        reporter = MemoryIssueReporter()
        service = LightSafetyObligations(
            store,
            now_ms=lambda: 1_000,
            issue_reporter=reporter,
        )
        await service.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.retry",
            deadline_ms=1_000,
            ownership_revision="revision.retry",
        )
        calls: list[str] = []

        async def reconcile(record) -> bool:
            calls.append(str(record["targetId"]))
            return False

        cancel = service.start(reconcile)
        await service.async_retry("shower_light")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert calls == ["shower_light"]
        assert reporter.reported[0]["targetId"] == "shower_light"
        records = store.payload["records"]
        assert len(records) == 1
        assert records[0]["targetId"] == "shower_light"
        assert records[0]["attempt"] == 1
        assert records[0]["deadlineMs"] == 6_000

    asyncio.run(exercise())


def test_failed_off_stops_after_two_total_reconcile_attempts() -> None:
    async def exercise() -> None:
        now = [1_000]
        store = MemoryStore()
        reporter = MemoryIssueReporter()
        service = LightSafetyObligations(
            store,
            now_ms=lambda: now[0],
            issue_reporter=reporter,
        )
        await service.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.bounded",
            deadline_ms=1_000,
            ownership_revision="revision.bounded",
        )
        calls: list[str] = []

        async def reconcile(record) -> bool:
            calls.append(str(record["targetId"]))
            return False

        cancel = service.start(reconcile)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        now[0] = 6_000
        await service.async_retry("shower_light")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert calls == ["shower_light", "shower_light"]
        assert store.payload == {"version": 1, "records": []}
        assert service._records == {}
        assert len(reporter.reported) == 2

    asyncio.run(exercise())


def test_initial_physical_failure_allows_only_one_durable_retry() -> None:
    async def exercise() -> None:
        now = [1_000]
        store = MemoryStore()
        service = LightSafetyObligations(store, now_ms=lambda: now[0])
        await service.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.initial-counted",
            deadline_ms=301_000,
            ownership_revision="revision.initial-counted",
        )
        calls: list[str] = []

        async def reconcile(record) -> bool:
            calls.append(str(record["targetId"]))
            return False

        cancel = service.start(reconcile)
        await service.async_retry("shower_light", physical_attempted=True)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert calls == ["shower_light"]
        assert store.payload == {"version": 1, "records": []}

    asyncio.run(exercise())


def test_positive_presence_cancels_every_obligation_for_scenario() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        service = LightSafetyObligations(store, now_ms=lambda: 1_000)
        for target_id in ("shower_main", "shower_night"):
            await service.async_arm(
                target_id=target_id,
                entity_id=f"light.{target_id}",
                scenario_id="shower_comfort",
                run_id=f"run.{target_id}",
                deadline_ms=301_000,
                ownership_revision=f"revision.{target_id}",
            )
        await service.async_arm(
            target_id="hall",
            entity_id="light.hall",
            scenario_id="hall_comfort",
            run_id="run.hall",
            deadline_ms=301_000,
            ownership_revision="revision.hall",
        )

        await service.async_cancel_scenario("shower_comfort")

        assert [item["targetId"] for item in store.payload["records"]] == ["hall"]

    asyncio.run(exercise())


def test_failed_transition_keeps_original_generation_for_retry() -> None:
    async def exercise() -> None:
        store = FailingTransitionStore()
        service = LightSafetyObligations(store, now_ms=lambda: 1_000)
        await service.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.atomic",
            deadline_ms=1_000,
            ownership_revision="revision.atomic",
        )
        original = json.loads(json.dumps(store.payload["records"][0]))

        async def reconcile(record) -> bool:
            assert record["attempt"] == 1
            store.fail_transition = True
            return True

        cancel = service.start(reconcile)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel()

        assert service._records["shower_light"]["generationId"] == (
            original["generationId"]
        )
        assert store.payload["records"][0]["generationId"] == original["generationId"]
        assert store.payload["records"][0]["attempt"] == 1

    asyncio.run(exercise())


def test_transition_storage_failure_never_replays_after_attempt_budget() -> None:
    async def exercise() -> None:
        store = FailingTransitionStore()
        service = LightSafetyObligations(store, now_ms=lambda: 1_000)
        await service.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.storage-budget",
            deadline_ms=1_000,
            ownership_revision="revision.storage-budget",
        )
        calls = 0

        async def reconcile(_record) -> bool:
            nonlocal calls
            calls += 1
            store.fail_transition = True
            return False

        original_sleep = asyncio.sleep

        async def yield_without_wait(_delay: float) -> None:
            await original_sleep(0)

        with patch(
            "custom_components.hausman_hub.application.light_safety_obligations.asyncio.sleep",
            side_effect=yield_without_wait,
        ):
            cancel = service.start(reconcile)
            for _ in range(20):
                await original_sleep(0)
            cancel()

        assert calls <= 2
        assert service._records == {}

    from unittest.mock import patch

    asyncio.run(exercise())


def test_cancel_can_invalidate_generation_while_reconcile_callback_is_running() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        service = LightSafetyObligations(store, now_ms=lambda: 1_000)
        await service.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.race",
            deadline_ms=1_000,
            ownership_revision="revision.race",
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def reconcile(_record) -> bool:
            entered.set()
            await release.wait()
            return True

        cancel_all = service.start(reconcile)
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.wait_for(service.async_cancel("shower_light"), timeout=1)
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        cancel_all()

        assert store.payload == {"version": 1, "records": []}
        assert service._records == {}

    asyncio.run(exercise())


def test_recovered_previous_generation_never_rearms_cancelled_light_off() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        first = LightSafetyObligations(store, now_ms=lambda: 1_000)
        await first.async_arm(
            target_id="shower_light",
            entity_id="light.shower",
            scenario_id="shower_absence",
            run_id="run.shower.recovered",
            deadline_ms=301_000,
            ownership_revision="revision.recovered",
        )
        store.recovered_previous = True
        reporter = MemoryIssueReporter()
        restarted = LightSafetyObligations(
            store,
            now_ms=lambda: 2_000,
            issue_reporter=reporter,
        )

        await restarted.async_load()
        calls: list[str] = []

        async def reconcile(record) -> bool:
            calls.append(str(record["targetId"]))
            return True

        cancel = restarted.start(reconcile)
        await asyncio.sleep(0)
        cancel()

        assert calls == []
        assert store.payload == {"version": 1, "records": []}
        assert reporter.reported[0]["targetId"] == "shower_light"

    asyncio.run(exercise())
