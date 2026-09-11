"""Named-risk tests for the durable Tambur decision bridge."""

from __future__ import annotations

import asyncio
import copy
import gc
import inspect
import unittest
import weakref
from collections.abc import Mapping
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.hausman_hub.application import scenario_decision_bridge
from custom_components.hausman_hub.application.scenario_decision_bridge import (
    ScenarioDecisionBridge,
    ScenarioDecisionConflict,
    ScenarioDecisionRejected,
    TamburHaObservationCoordinator,
    valid_scenario_decision_bridge_payload,
)
from custom_components.hausman_hub.application.scenario_executor import ScenarioExecutor
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)


SCENARIO_ID = "system-tambur-adaptive-controller"
CHAND = "lamp_chandelier_demo"
POINTS = "lamp_points_demo"
MIRROR = "lamp_mirror_demo"
POWER = "power_demo"
SENSOR = "sensor_demo"
NOW = 1_800_000_000_000
SETTINGS = {
    "morningStart": "09:00",
    "morningEnd": "10:00",
    "eveningLatestStart": "21:00",
    "mainOff": "23:00",
    "mirrorOff": "01:00",
    "minPercent": 5,
    "maxPercent": 80,
    "dayKelvin": 3000,
    "eveningKelvin": 2200,
    "absenceDaySeconds": 600,
    "absenceNightSeconds": 180,
    "fadeSeconds": 20,
    "manualOffMinSeconds": 600,
    "manualOffAbsenceSeconds": 30,
    "manualOnHoldSeconds": 3600,
}


class MemoryStore:
    def __init__(
        self, value: object | None = None, *, recovered_previous: bool = False
    ) -> None:
        self.value = copy.deepcopy(value)
        self.saved: list[dict[str, object]] = []
        self.fail_saves = False
        self.recovered_previous = recovered_previous

    async def async_load(self) -> object | None:
        return copy.deepcopy(self.value)

    async def async_save(self, value: dict[str, object]) -> None:
        if self.fail_saves:
            raise OSError("store unavailable")
        self.value = copy.deepcopy(value)
        self.saved.append(copy.deepcopy(value))


class BlockingSaveStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.block_next_save = False
        self.save_entered = asyncio.Event()
        self.release_save = asyncio.Event()

    async def async_save(self, value: dict[str, object]) -> None:
        if self.block_next_save:
            self.block_next_save = False
            self.save_entered.set()
            await self.release_save.wait()
        await super().async_save(value)


def authority_snapshot(
    *,
    generation: int = 2,
    revision: int = 11,
    owner: str = "none",
    evidence_revision: str = "evidence.initial",
) -> dict[str, object]:
    return {
        "generation": generation,
        "observedRevision": revision,
        "observedAtMs": NOW,
        "evidenceRevision": evidence_revision,
        "observationEpoch": 1,
        "fresh": True,
        "owner": owner,
        "protectionActive": False,
    }


class SnapshotSource:
    def __init__(self) -> None:
        self.states = {CHAND: "off", POINTS: "off", MIRROR: "off"}
        self.authorities = {
            CHAND: authority_snapshot(),
            POINTS: authority_snapshot(generation=3, revision=12),
            MIRROR: authority_snapshot(generation=4, revision=13),
        }

    async def authority(self, target_id: str) -> object:
        return copy.deepcopy(self.authorities.get(target_id))

    async def snapshot(
        self, scenario_id: str, event: object, observation_epoch: int
    ) -> dict[str, object]:
        assert scenario_id == SCENARIO_ID
        return {
            "settingsRevision": 7,
            "issuedAtMs": NOW,
            "expiresAtMs": NOW + 60_000,
            "event": copy.deepcopy(event),
            "clock": {
                "nowMs": NOW,
                "timezone": "Asia/Omsk",
                "localDate": "2027-01-15",
                "minutesOfDay": 660,
                "sunsetAtMs": NOW + 20_000_000,
            },
            "bindings": {
                "chandelier": CHAND,
                "points": POINTS,
                "mirror": MIRROR,
                "power": POWER,
                "presenceSensors": [SENSOR],
            },
            "settings": copy.deepcopy(SETTINGS),
            "observations": {
                CHAND: {
                    "state": self.states[CHAND],
                    "revision": 11,
                    "observedAtMs": NOW,
                    "fresh": True,
                    "continuityEpoch": observation_epoch,
                },
                POINTS: {
                    "state": self.states[POINTS],
                    "revision": 12,
                    "observedAtMs": NOW,
                    "fresh": True,
                    "continuityEpoch": observation_epoch,
                },
                MIRROR: {
                    "state": self.states[MIRROR],
                    "revision": 13,
                    "observedAtMs": NOW,
                    "fresh": True,
                    "continuityEpoch": observation_epoch,
                },
                SENSOR: {
                    "state": "on",
                    "revision": 20,
                    "observedAtMs": NOW,
                    "fresh": True,
                    "continuityEpoch": observation_epoch,
                },
            },
            "authority": {
                target: {
                    "owner": current["owner"],
                    "generation": current["generation"],
                    "protectionActive": current["protectionActive"],
                }
                for target, current in self.authorities.items()
            },
        }


def event(*, ident: str = "presence.1", kind: str = "sensor") -> dict[str, object]:
    result: dict[str, object] = {
        "id": ident,
        "kind": kind,
        "observedAtMs": NOW,
    }
    if kind in {"sensor", "manual"}:
        result["targetId"] = SENSOR if kind == "sensor" else CHAND
    return result


def decision(request: dict[str, object], *, plan_id: str | None = None) -> dict[str, object]:
    correlation_id = str(request["correlationId"])
    return {
        "contract": {"name": "hausman-node-red-decision", "version": 1},
        "correlationId": correlation_id,
        "scenarioId": SCENARIO_ID,
        "planId": plan_id or correlation_id,
        "controllerVersion": 1,
        "settingsRevision": 7,
        "baseRevision": request["durable"]["revision"],
        "snapshotRevision": request["snapshotRevision"],
        "observationEpoch": request["observationEpoch"],
        "expiresAtMs": NOW + 60_000,
        "status": "decided",
        "reasonCode": "presence_day",
        "trace": [],
        "nextState": {
            "phase": "occupied",
            "phaseStartedAtMs": NOW,
            "absenceSinceMs": None,
            "absenceEpoch": None,
            "fadeStartPercent": None,
            "fadeStartedAtMs": None,
            "fadeReason": None,
        },
        "wakeups": [],
        "action": {
            "id": f"{correlation_id[:119]}.act",
            "targetId": CHAND,
            "actionId": "turn_on",
            "authorityGeneration": 2,
            "observedRevision": 11,
        },
    }


async def loaded_bridge(
    store: MemoryStore | None = None,
    source: SnapshotSource | None = None,
    *,
    now_ms=lambda: NOW,
    executor: ScenarioExecutor | None = None,
) -> tuple[ScenarioDecisionBridge, MemoryStore, SnapshotSource]:
    actual_store = store or MemoryStore()
    actual_source = source or SnapshotSource()
    bridge_options: dict[str, object] = {}
    if executor is not None:
        bridge_options["executor"] = executor
    bridge = ScenarioDecisionBridge(
        actual_store,
        snapshot_provider=actual_source.snapshot,
        authority_provider=actual_source.authority,
        now_ms=now_ms,
        **bridge_options,
    )
    await bridge.async_recover()
    return bridge, actual_store, actual_source


class ScenarioDecisionBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_night_mirror_is_cancelled_at_dispatch_when_fresh_snapshot_is_after_sunrise(self) -> None:
        class NightSource(SnapshotSource):
            after_sunrise = False

            async def snapshot(self, scenario_id: str, event_value: object, observation_epoch: int) -> dict[str, object]:
                result = await super().snapshot(scenario_id, event_value, observation_epoch)
                now = NOW + 4 * 60 * 60_000 if self.after_sunrise else NOW
                result["issuedAtMs"] = now - 1_000
                result["expiresAtMs"] = now + 60_000
                result["clock"] = {
                    **result["clock"], "nowMs": now,
                    "minutesOfDay": 6 * 60 if self.after_sunrise else 2 * 60,
                    "sunriseAtMs": NOW + 4 * 60 * 60_000,
                }
                for observation in result["observations"].values():
                    observation["observedAtMs"] = now
                return result

        source = NightSource()
        bridge, store, _ = await loaded_bridge(source=source)
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        plan["reasonCode"] = "night_mirror_on"
        plan["action"].update(targetId=MIRROR, authorityGeneration=4, observedRevision=13)
        accepted = await bridge.async_accept(plan)
        source.after_sunrise = True

        with self.assertRaises(ScenarioDecisionRejected):
            await bridge.async_before_dispatch(accepted["planId"], plan["action"]["id"])
        self.assertEqual("cancelled", store.value["history"][-1]["status"])

    async def test_accept_persists_next_state_wakeups_and_prepared_ledger_before_dispatch(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        accepted = await bridge.async_accept(decision(request))

        self.assertEqual("prepared", accepted["status"])
        payload = store.value
        self.assertEqual("occupied", payload["durable"]["phase"])
        self.assertEqual([], payload["durable"]["wakeups"])
        self.assertEqual("prepared", payload["history"][-1]["status"])
        self.assertEqual(CHAND, payload["history"][-1]["action"]["targetId"])

    async def test_repeated_plan_is_not_redispatched_and_conflicting_content_is_rejected(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        first = await bridge.async_accept(plan)
        save_count = len(store.saved)

        replay = await bridge.async_accept(copy.deepcopy(plan))
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["planId"], replay["planId"])
        self.assertEqual(first["status"], replay["status"])
        self.assertEqual(save_count, len(store.saved))
        changed = copy.deepcopy(plan)
        changed["reasonCode"] = "changed"
        with self.assertRaises(ScenarioDecisionConflict):
            await bridge.async_accept(changed)

    async def test_repeated_prepared_plan_never_enters_executor_again(self) -> None:
        states = _ExecutorStates()
        executor = ScenarioExecutor(
            SimpleNamespace(states=states, services=_ExecutorServices(states)),
            ScenarioCatalog(devices={}, scenarios={}),
            lambda *_args, **_kwargs: None,
        )
        bridge, store, _source = await loaded_bridge(executor=executor)
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        await bridge.async_accept(plan)
        saved = len(store.saved)

        result = await bridge.async_execute_decision(copy.deepcopy(plan))

        self.assertEqual("prepared", result["status"])
        self.assertTrue(result["replayed"])
        self.assertEqual(saved, len(store.saved))

    async def test_recovery_marks_crossed_dispatch_uncertain_and_blocks_new_plan_for_target(self) -> None:
        bridge, store, source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        await bridge.async_accept(plan)
        await bridge.async_before_dispatch(plan["planId"], plan["action"]["id"])

        restarted = ScenarioDecisionBridge(
            store,
            snapshot_provider=source.snapshot,
            authority_provider=source.authority,
            now_ms=lambda: NOW + 1,
        )
        recovered = await restarted.async_recover()
        self.assertEqual(2, recovered["observationEpoch"])
        self.assertEqual("uncertain", store.value["history"][-1]["status"])
        recovery_receipt = store.value["history"][-1]["receipt"]
        self.assertEqual("uncertain", recovery_receipt["status"])
        self.assertEqual(
            recovery_receipt,
            next(
                item
                for item in store.value["receipts"]
                if item["id"] == recovery_receipt["id"]
            ),
        )
        save_count = len(store.saved)
        replay = await restarted.async_accept(copy.deepcopy(plan))
        self.assertTrue(replay["replayed"])
        self.assertEqual("uncertain", replay["status"])
        self.assertEqual(save_count, len(store.saved))
        source.states[CHAND] = "on"
        request2 = await restarted.async_snapshot(
            SCENARIO_ID, event(ident="presence.2")
        )
        plan2 = decision(request2)
        plan2["action"]["authorityGeneration"] = source.authorities[CHAND][
            "generation"
        ]
        with self.assertRaises(ScenarioDecisionRejected):
            await restarted.async_accept(plan2)

    async def test_prepared_target_blocks_a_different_concurrent_plan(self) -> None:
        bridge, _store, _source = await loaded_bridge()
        first_request = await bridge.async_snapshot(SCENARIO_ID, event())
        await bridge.async_accept(decision(first_request))
        second_request = await bridge.async_snapshot(
            SCENARIO_ID, event(ident="presence.concurrent")
        )

        with self.assertRaisesRegex(ScenarioDecisionRejected, "active plan"):
            await bridge.async_accept(decision(second_request))

    async def test_bounded_history_never_evicts_uncertain_dispatch(self) -> None:
        bridge, store, source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        await bridge.async_accept(plan)
        await bridge.async_before_dispatch(plan["planId"], plan["action"]["id"])
        restarted = ScenarioDecisionBridge(
            store,
            snapshot_provider=source.snapshot,
            authority_provider=source.authority,
            now_ms=lambda: NOW + 1,
        )
        await restarted.async_recover()
        uncertain_plan_id = plan["planId"]

        for index in range(128):
            skipped_request = await restarted.async_snapshot(
                SCENARIO_ID,
                event(ident=f"clock.{index}", kind="clock"),
            )
            skipped = decision(skipped_request)
            skipped.update(
                status="skipped",
                reasonCode="clock_no_change",
                action=None,
            )
            await restarted.async_accept(skipped)

        self.assertEqual(128, len(store.value["history"]))
        uncertain = [
            item
            for item in store.value["history"]
            if item["planId"] == uncertain_plan_id
        ]
        self.assertEqual(1, len(uncertain))
        self.assertEqual("uncertain", uncertain[0]["status"])

    async def test_before_dispatch_rejects_stale_authority_without_crossing_side_effect(self) -> None:
        bridge, store, source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        await bridge.async_accept(plan)
        source.authorities[CHAND]["generation"] = 3

        with self.assertRaises(ScenarioDecisionRejected):
            await bridge.async_before_dispatch(plan["planId"], plan["action"]["id"])
        self.assertEqual("cancelled", store.value["history"][-1]["status"])

    async def test_before_dispatch_rejects_manual_owner_without_generation_change(self) -> None:
        bridge, store, source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        await bridge.async_accept(plan)
        source.authorities[CHAND]["owner"] = "manual"
        source.authorities[CHAND]["protectionActive"] = True

        with self.assertRaises(ScenarioDecisionRejected):
            await bridge.async_before_dispatch(plan["planId"], plan["action"]["id"])

        self.assertEqual("cancelled", store.value["history"][-1]["status"])

    async def test_superseded_snapshot_and_expired_response_are_rejected(self) -> None:
        bridge, _store, _source = await loaded_bridge()
        first = await bridge.async_snapshot(SCENARIO_ID, event(ident="presence.old"))
        await bridge.async_snapshot(SCENARIO_ID, event(ident="presence.new"))
        with self.assertRaises(ScenarioDecisionRejected):
            await bridge.async_accept(decision(first))

        clock = [NOW]
        expiring, _store, _source = await loaded_bridge(now_ms=lambda: clock[0])
        request = await expiring.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        clock[0] = NOW + 60_001
        with self.assertRaises(ScenarioDecisionRejected):
            await expiring.async_accept(plan)

    async def test_direct_forged_confirmed_receipt_never_authorizes_confirmation(self) -> None:
        bridge, store, source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        accepted = await bridge.async_accept(plan)
        await bridge.async_before_dispatch(plan["planId"], plan["action"]["id"])
        bridge.mark_dispatch_crossed(plan["planId"], plan["action"]["id"])
        source.authorities[CHAND].update(
            observedRevision=23,
            observedAtMs=NOW + 555,
            evidenceRevision="evidence.after.dispatch",
        )

        forged = {
            "id": accepted["receiptId"],
            "planId": plan["planId"],
            "actionId": "turn_on",
            "targetId": CHAND,
            "status": "confirmed",
            "observedRevision": 999,
            "observedAtMs": NOW + 999,
        }

        result = await bridge.async_record_receipt(plan["planId"], forged)

        self.assertEqual("uncertain", result["status"])
        self.assertEqual("uncertain", store.value["history"][-1]["status"])
        self.assertNotIn("observedRevision", result["receipt"])

    async def test_receipt_binding_is_checked_before_executor_evidence(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        accepted = await bridge.async_accept(plan)
        await bridge.async_before_dispatch(plan["planId"], plan["action"]["id"])

        with self.assertRaisesRegex(ScenarioDecisionRejected, "binding"):
            await bridge.async_record_receipt(
                str(plan["planId"]),
                {
                    "id": accepted["receiptId"],
                    "planId": plan["planId"],
                    "actionId": "turn_off",
                    "targetId": CHAND,
                    "status": "failed",
                },
            )

        self.assertEqual("dispatching", store.value["history"][-1]["status"])

    async def test_proof_writer_token_and_registry_are_not_exposed(self) -> None:
        states = _ExecutorStates()
        executor = ScenarioExecutor(
            SimpleNamespace(states=states, services=_ExecutorServices(states)),
            ScenarioCatalog(devices={}, scenarios={}),
            lambda *_args, **_kwargs: None,
        )
        bridge, _store, _source = await loaded_bridge(executor=executor)

        self.assertFalse(
            hasattr(scenario_decision_bridge, "_record_trusted_executor_evidence")
        )
        self.assertFalse(hasattr(bridge, "_trusted_executor_token"))
        self.assertFalse(hasattr(bridge, "_trusted_executor_evidence"))
        self.assertFalse(hasattr(ScenarioDecisionBridge, "_trusted_executor_token"))
        self.assertFalse(
            hasattr(ScenarioDecisionBridge, "_trusted_executor_evidence")
        )
        self.assertFalse(hasattr(bridge, "_clear_executor_evidence"))
        self.assertFalse(hasattr(ScenarioDecisionBridge, "_clear_executor_evidence"))
        self.assertFalse(
            hasattr(scenario_decision_bridge, "_clear_executor_evidence")
        )
        self.assertEqual(
            ["decision"],
            list(inspect.signature(bridge.async_execute_decision).parameters),
        )

    async def test_recovery_clears_multiple_preexisting_executor_proofs(self) -> None:
        source = SnapshotSource()
        states = _MultiExecutorStates()
        marker_one = _EvidenceText("evidence.proof.one")
        marker_two = _EvidenceText("evidence.proof.two")
        marker_one_ref = weakref.ref(marker_one)
        marker_two_ref = weakref.ref(marker_two)
        stamp_one = _EvidenceDateTime(marker_one, NOW + 1_001)
        stamp_two = _EvidenceDateTime(marker_two, NOW + 1_002)

        def advance(target_id: str, stamp: datetime) -> None:
            source.authorities[target_id].update(
                observedRevision=23 if target_id == CHAND else 24,
                observedAtMs=NOW + 555,
                evidenceRevision=stamp.isoformat(),
            )

        services = _BarrierExecutorServices(
            states,
            (stamp_one, stamp_two),
            advance,
        )
        executor = _multi_target_executor(states, services)
        note_entered = [asyncio.Event(), asyncio.Event()]
        note_call = 0

        async def block_after_proof(*_args: object, **_kwargs: object) -> None:
            nonlocal note_call
            current = note_call
            note_call += 1
            note_entered[current].set()
            await asyncio.Event().wait()

        executor._light_priority.note_results = block_after_proof
        bridge, _store, _source = await loaded_bridge(
            source=source, executor=executor
        )

        tasks: list[asyncio.Task[dict[str, object]]] = []
        for index, target_id in enumerate((CHAND, POINTS)):
            request = await bridge.async_snapshot(
                SCENARIO_ID, event(ident=f"presence.proof.{index}")
            )
            plan = decision(request)
            plan["action"]["targetId"] = target_id
            plan["action"]["authorityGeneration"] = 2 + index
            plan["action"]["observedRevision"] = 11 + index
            task = asyncio.create_task(bridge.async_execute_decision(plan))
            await asyncio.wait_for(note_entered[index].wait(), 0.5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            tasks.append(task)

        await bridge.async_recover()
        states.release_evidence()
        source.authorities[CHAND]["evidenceRevision"] = "released.one"
        source.authorities[POINTS]["evidenceRevision"] = "released.two"
        services.release_evidence()
        del tasks, task, stamp_one, stamp_two, marker_one, marker_two
        gc.collect()

        self.assertIsNone(marker_one_ref())
        self.assertIsNone(marker_two_ref())

    async def test_late_executor_proof_after_recovery_is_ignored(self) -> None:
        source = SnapshotSource()
        states = _MultiExecutorStates()
        marker = _EvidenceText("evidence.late.response")
        marker_ref = weakref.ref(marker)
        stamp = _EvidenceDateTime(marker, NOW + 1_003)
        response_entered = asyncio.Event()
        release_response = asyncio.Event()
        note_entered = asyncio.Event()

        def advance(target_id: str, evidence_stamp: datetime) -> None:
            source.authorities[target_id].update(
                observedRevision=23,
                observedAtMs=NOW + 555,
                evidenceRevision=evidence_stamp.isoformat(),
            )

        services = _BarrierExecutorServices(
            states,
            (stamp,),
            advance,
            response_entered=response_entered,
            release_response=release_response,
        )
        executor = _multi_target_executor(states, services, targets=(CHAND,))

        async def block_after_writer(*_args: object, **_kwargs: object) -> None:
            note_entered.set()
            await asyncio.Event().wait()

        executor._light_priority.note_results = block_after_writer
        bridge, _store, _source = await loaded_bridge(
            source=source, executor=executor
        )
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        execution = asyncio.create_task(bridge.async_execute_decision(plan))
        await asyncio.wait_for(response_entered.wait(), 0.5)

        await bridge.async_recover()
        release_response.set()
        await asyncio.wait_for(note_entered.wait(), 0.5)
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execution

        states.release_evidence()
        source.authorities[CHAND]["evidenceRevision"] = "released.late"
        services.release_evidence()
        del execution, stamp, marker
        gc.collect()

        self.assertIsNone(marker_ref())

    async def test_response_during_blocked_recovery_save_is_cleared(self) -> None:
        source = SnapshotSource()
        store = BlockingSaveStore()
        states = _MultiExecutorStates()
        marker = _EvidenceText("evidence.blocked.recovery")
        marker_ref = weakref.ref(marker)
        stamp = _EvidenceDateTime(marker, NOW + 1_004)
        response_entered = asyncio.Event()
        release_response = asyncio.Event()
        note_entered = asyncio.Event()

        def advance(target_id: str, evidence_stamp: datetime) -> None:
            source.authorities[target_id].update(
                observedRevision=23,
                observedAtMs=NOW + 555,
                evidenceRevision=evidence_stamp.isoformat(),
            )

        services = _BarrierExecutorServices(
            states,
            (stamp,),
            advance,
            response_entered=response_entered,
            release_response=release_response,
        )
        executor = _multi_target_executor(states, services, targets=(CHAND,))

        async def block_after_writer(*_args: object, **_kwargs: object) -> None:
            note_entered.set()
            await asyncio.Event().wait()

        executor._light_priority.note_results = block_after_writer
        bridge, _store, _source = await loaded_bridge(
            store=store, source=source, executor=executor
        )
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        execution = asyncio.create_task(
            bridge.async_execute_decision(decision(request))
        )
        await asyncio.wait_for(response_entered.wait(), 0.5)
        store.block_next_save = True
        recovery = asyncio.create_task(bridge.async_recover())
        await asyncio.wait_for(store.save_entered.wait(), 0.5)

        release_response.set()
        await asyncio.wait_for(note_entered.wait(), 0.5)
        store.release_save.set()
        await asyncio.wait_for(recovery, 0.5)
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execution

        states.release_evidence()
        source.authorities[CHAND]["evidenceRevision"] = "released.blocked"
        services.release_evidence()
        del execution, recovery, stamp, marker
        gc.collect()

        self.assertIsNone(marker_ref())

    async def test_failed_recovery_save_keeps_epoch_and_valid_executor_proof(self) -> None:
        source = SnapshotSource()
        store = MemoryStore()
        states = _MultiExecutorStates()
        marker = _EvidenceText("evidence.failed.recovery")
        stamp = _EvidenceDateTime(marker, NOW + 1_005)
        note_entered = asyncio.Event()

        def advance(target_id: str, evidence_stamp: datetime) -> None:
            source.authorities[target_id].update(
                observedRevision=23,
                observedAtMs=NOW + 555,
                evidenceRevision=evidence_stamp.isoformat(),
            )

        services = _BarrierExecutorServices(states, (stamp,), advance)
        executor = _multi_target_executor(states, services, targets=(CHAND,))

        async def block_after_writer(*_args: object, **_kwargs: object) -> None:
            note_entered.set()
            await asyncio.Event().wait()

        executor._light_priority.note_results = block_after_writer
        bridge, _store, _source = await loaded_bridge(
            store=store, source=source, executor=executor
        )
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        execution = asyncio.create_task(bridge.async_execute_decision(plan))
        await asyncio.wait_for(note_entered.wait(), 0.5)
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execution

        store.fail_saves = True
        with self.assertRaises(OSError):
            await bridge.async_recover()
        store.fail_saves = False
        after_failure = await bridge.async_snapshot(
            SCENARIO_ID, event(ident="presence.after.failed.recovery")
        )
        self.assertEqual(request["observationEpoch"], after_failure["observationEpoch"])

        record = store.value["history"][-1]
        result = await bridge.async_record_receipt(
            str(plan["planId"]),
            {
                "id": record["receiptId"],
                "planId": plan["planId"],
                "actionId": "turn_on",
                "targetId": CHAND,
                "status": "confirmed",
            },
        )

        self.assertEqual("confirmed", result["status"])
        self.assertEqual("confirmed", store.value["history"][-1]["status"])

    async def test_repeated_recovery_allows_one_new_epoch_execution_once(self) -> None:
        source = SnapshotSource()
        states = _ExecutorStates()
        current_epoch = [1]

        def advance_bridge_observation() -> None:
            source.authorities[CHAND].update(
                observedRevision=23,
                observedAtMs=NOW + 555,
                evidenceRevision=states.value.last_updated.isoformat(),
                observationEpoch=current_epoch[0],
            )

        services = _ExecutorServices(states, advance_bridge_observation)
        executor = ScenarioExecutor(
            SimpleNamespace(states=states, services=services),
            ScenarioCatalog(
                devices={
                    CHAND: ScenarioDeviceEntry(
                        CHAND,
                        "Люстра",
                        "light.chandelier",
                        (
                            ScenarioDeviceAction(
                                "turn_on",
                                "Включить",
                                "light",
                                "turn_on",
                                frozenset(),
                            ),
                        ),
                    )
                },
                scenarios={},
            ),
            lambda *_args, **_kwargs: None,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        bridge, store, _source = await loaded_bridge(
            source=source, executor=executor
        )
        await bridge.async_recover()
        recovered = await bridge.async_recover()
        current_epoch[0] = int(recovered["observationEpoch"])
        source.authorities[CHAND]["observationEpoch"] = current_epoch[0]
        request = await bridge.async_snapshot(
            SCENARIO_ID, event(ident="presence.new.epoch")
        )
        plan = decision(request)

        first = await bridge.async_execute_decision(plan)
        replay = await bridge.async_execute_decision(copy.deepcopy(plan))

        self.assertEqual("confirmed", first["status"])
        self.assertEqual("confirmed", replay["status"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(1, len(services.calls))
        self.assertEqual("confirmed", store.value["history"][-1]["status"])

    async def test_fake_executor_or_factory_cannot_bind_a_bridge(self) -> None:
        source = SnapshotSource()
        store = MemoryStore()

        with self.assertRaises(TypeError):
            ScenarioDecisionBridge(
                store,
                snapshot_provider=source.snapshot,
                authority_provider=source.authority,
                now_ms=lambda: NOW,
                executor=SimpleNamespace(
                    _build_tambur_execution=lambda *_args: lambda _decision: None
                ),
            )

        with self.assertRaises(TypeError):
            ScenarioDecisionBridge(
                store,
                snapshot_provider=source.snapshot,
                authority_provider=source.authority,
                now_ms=lambda: NOW,
                execution=lambda _decision: None,
            )

        states = _ExecutorStates()
        executor = ScenarioExecutor(
            SimpleNamespace(states=states, services=_ExecutorServices(states)),
            ScenarioCatalog(devices={}, scenarios={}),
            lambda *_args, **_kwargs: None,
        )
        fake_factory_calls: list[object] = []
        executor._build_tambur_execution = lambda *_args: fake_factory_calls.append(
            object()
        )
        bridge, _store, _source = await loaded_bridge(executor=executor)

        self.assertEqual([], fake_factory_calls)
        with self.assertRaises(TypeError):
            await bridge.async_execute_decision({}, proof=object())
        with self.assertRaises(TypeError):
            await bridge.async_record_receipt("plan.fake", {}, proof=object())

    async def test_unbound_bridge_cannot_execute_or_confirm_a_decision(self) -> None:
        bridge, _store, _source = await loaded_bridge()

        with self.assertRaisesRegex(ScenarioDecisionRejected, "unavailable"):
            await bridge.async_execute_decision({})

    async def test_confirmation_requires_fresh_matching_authority_evidence(self) -> None:
        authority_mismatches = {
            "fresh": False,
            "observationEpoch": 2,
            "generation": 3,
            "owner": "manual",
            "protectionActive": True,
            "observedRevision": 11,
            "observedAtMs": "not-a-number",
            "evidenceRevision": "forged.opaque.revision",
        }
        for field, bad_value in authority_mismatches.items():
            with self.subTest(field=field):
                source = SnapshotSource()
                states = _ExecutorStates()

                def advance_bridge_observation() -> None:
                    source.authorities[CHAND].update(
                        observedRevision=23,
                        observedAtMs=NOW + 555,
                        evidenceRevision=states.value.last_updated.isoformat(),
                    )
                    source.authorities[CHAND][field] = bad_value

                hass = SimpleNamespace(
                    states=states,
                    services=_ExecutorServices(states, advance_bridge_observation),
                )
                action = ScenarioDeviceAction(
                    "turn_on", "Включить", "light", "turn_on", frozenset()
                )
                executor = ScenarioExecutor(
                    hass,
                    ScenarioCatalog(
                        devices={
                            CHAND: ScenarioDeviceEntry(
                                CHAND, "Люстра", "light.chandelier", (action,)
                            )
                        },
                        scenarios={},
                    ),
                    lambda *_args, **_kwargs: None,
                    readback_window_seconds=0.02,
                    readback_interval_seconds=0.01,
                )
                bridge, store, _source = await loaded_bridge(
                    source=source, executor=executor
                )
                request = await bridge.async_snapshot(SCENARIO_ID, event())
                plan = decision(request)

                result = await bridge.async_execute_decision(plan)

                self.assertEqual("uncertain", result["status"])
                self.assertEqual("uncertain", store.value["history"][-1]["status"])

    async def test_confirmed_observation_is_not_a_public_bridge_api(self) -> None:
        bridge, _store, _source = await loaded_bridge()
        self.assertFalse(hasattr(bridge, "async_confirmed_observation"))

    async def test_manual_fence_during_authority_fetch_forces_uncertain(self) -> None:
        source = SnapshotSource()
        store = MemoryStore()
        states = _ExecutorStates()
        authority_entered = asyncio.Event()
        release_authority = asyncio.Event()
        block_authority = False

        async def authority(target_id: str) -> object:
            if block_authority:
                authority_entered.set()
                await release_authority.wait()
            return await source.authority(target_id)

        def advance_bridge_observation() -> None:
            nonlocal block_authority
            source.authorities[CHAND].update(
                observedRevision=23,
                observedAtMs=NOW + 555,
                evidenceRevision=states.value.last_updated.isoformat(),
            )
            block_authority = True

        hass = SimpleNamespace(
            states=states,
            services=_ExecutorServices(states, advance_bridge_observation),
        )
        action = ScenarioDeviceAction(
            "turn_on", "Включить", "light", "turn_on", frozenset()
        )
        executor = ScenarioExecutor(
            hass,
            ScenarioCatalog(
                devices={
                    CHAND: ScenarioDeviceEntry(
                        CHAND, "Люстра", "light.chandelier", (action,)
                    )
                },
                scenarios={},
            ),
            lambda *_args, **_kwargs: None,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        bridge = ScenarioDecisionBridge(
            store,
            snapshot_provider=source.snapshot,
            authority_provider=authority,
            now_ms=lambda: NOW,
            executor=executor,
        )
        await bridge.async_recover()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        recording = asyncio.create_task(
            bridge.async_execute_decision(plan)
        )
        await asyncio.wait_for(authority_entered.wait(), 0.2)
        manual = asyncio.create_task(
            bridge.async_register_manual_intent(
                "manual.confirm.race", CHAND, "turn_off", None
            )
        )
        await asyncio.sleep(0)
        release_authority.set()

        result, _ = await asyncio.wait_for(
            asyncio.gather(recording, manual), 0.5
        )

        self.assertEqual("uncertain", result["status"])
        self.assertEqual("uncertain", store.value["history"][-1]["status"])

    async def test_skipped_decision_is_persisted_before_executor_returns_skipped(self) -> None:
        states = _ExecutorStates()
        executor = ScenarioExecutor(
            SimpleNamespace(states=states, services=_ExecutorServices(states)),
            ScenarioCatalog(devices={}, scenarios={}),
            lambda *_args, **_kwargs: None,
        )
        bridge, store, _source = await loaded_bridge(executor=executor)
        request = await bridge.async_snapshot(SCENARIO_ID, event(kind="clock"))
        plan = decision(request)
        plan.update(status="skipped", reasonCode="absence_waiting", action=None)
        result = await bridge.async_execute_decision(plan)
        self.assertEqual("skipped", result["status"])
        self.assertEqual("cancelled", store.value["history"][-1]["status"])
        self.assertEqual("absence_waiting", store.value["history"][-1]["reasonCode"])

    async def test_decided_action_runs_through_real_executor_emulator_and_records_receipt(self) -> None:
        source = SnapshotSource()
        states = _ExecutorStates()

        def advance_bridge_observation() -> None:
            source.authorities[CHAND].update(
                observedRevision=23,
                observedAtMs=NOW + 555,
                evidenceRevision=states.value.last_updated.isoformat(),
            )

        hass = SimpleNamespace(
            states=states,
            services=_ExecutorServices(states, advance_bridge_observation),
        )
        action = ScenarioDeviceAction(
            "turn_on", "Включить", "light", "turn_on", frozenset()
        )
        catalog = ScenarioCatalog(
            devices={
                CHAND: ScenarioDeviceEntry(
                    CHAND, "Люстра", "light.chandelier", (action,)
                )
            },
            scenarios={},
        )
        executor = ScenarioExecutor(
            hass,
            catalog,
            lambda *_args, **_kwargs: None,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        bridge, store, _source = await loaded_bridge(
            source=source, executor=executor
        )
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)

        observed_at_ms = NOW + 777
        with patch(
            "custom_components.hausman_hub.application.scenario_executor.time.time",
            return_value=observed_at_ms / 1000,
        ):
            result = await bridge.async_execute_decision(plan)

        self.assertEqual("confirmed", result["status"])
        self.assertEqual([("light", "turn_on", {"entity_id": "light.chandelier"})], hass.services.calls)
        self.assertEqual("confirmed", store.value["history"][-1]["status"])
        self.assertEqual("receipt", result["event"]["kind"])
        stored_receipt = store.value["history"][-1]["receipt"]
        self.assertEqual(23, stored_receipt["observedRevision"])
        self.assertEqual(NOW + 555, stored_receipt["observedAtMs"])
        self.assertNotIn(
            stored_receipt["observedRevision"],
            {NOW + 1, observed_at_ms},
        )

        saved = len(store.saved)
        replay = await executor.async_execute_tambur_decision(
            copy.deepcopy(plan), bridge
        )
        self.assertEqual("confirmed", replay["status"])
        self.assertEqual(1, len(hass.services.calls))
        self.assertEqual(saved, len(store.saved))

    async def test_confirmed_readback_without_new_bridge_observation_is_uncertain(self) -> None:
        states = _ExecutorStates()
        hass = SimpleNamespace(states=states, services=_ExecutorServices(states))
        action = ScenarioDeviceAction(
            "turn_on", "Включить", "light", "turn_on", frozenset()
        )
        executor = ScenarioExecutor(
            hass,
            ScenarioCatalog(
                devices={
                    CHAND: ScenarioDeviceEntry(
                        CHAND, "Люстра", "light.chandelier", (action,)
                    )
                },
                scenarios={},
            ),
            lambda *_args, **_kwargs: None,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        bridge, store, _source = await loaded_bridge(executor=executor)
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)

        result = await bridge.async_execute_decision(plan)

        self.assertEqual("uncertain", result["status"])
        self.assertEqual("uncertain", store.value["history"][-1]["status"])
        self.assertNotIn(
            "observedRevision", store.value["history"][-1]["receipt"]
        )

    async def test_store_failure_blocks_dispatch_and_manual_fence_stays_live(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        await bridge.async_accept(plan)
        store.fail_saves = True

        with self.assertRaises(OSError):
            await bridge.async_register_manual_intent(
                "manual.off.1", CHAND, "turn_off", None
            )
        self.assertTrue(bridge.cancellation_event(plan["planId"]).is_set())
        with self.assertRaises(ScenarioDecisionRejected):
            await bridge.async_before_dispatch(plan["planId"], plan["action"]["id"])

    async def test_manual_intent_invalidates_older_snapshot_even_when_save_fails(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        store.fail_saves = True

        with self.assertRaises(OSError):
            await bridge.async_register_manual_intent(
                "manual.snapshot.fence", CHAND, "turn_off", None
            )
        store.fail_saves = False

        with self.assertRaisesRegex(ScenarioDecisionRejected, "snapshot is stale"):
            await bridge.async_accept(plan)

    async def test_manual_intent_cancels_old_wakeups_in_same_durable_save(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        plan["wakeups"] = [
            {"id": "profile.1", "kind": "profile", "dueAtMs": NOW + 1_000}
        ]
        await bridge.async_accept(plan)

        await bridge.async_register_manual_intent(
            "manual.off.2", CHAND, "turn_off", None
        )

        self.assertEqual([], store.value["durable"]["wakeups"])
        self.assertEqual("cancelled", store.value["history"][-1]["status"])

    async def test_manual_intent_rechecks_plan_accepted_while_waiting_for_store_lock(self) -> None:
        store = BlockingSaveStore()
        bridge, _store, _source = await loaded_bridge(store=store)
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        store.block_next_save = True
        accepting = asyncio.create_task(bridge.async_accept(plan))
        await asyncio.wait_for(store.save_entered.wait(), 0.2)

        manual = asyncio.create_task(
            bridge.async_register_manual_intent(
                "manual.accept.race", CHAND, "turn_off", None
            )
        )
        await asyncio.sleep(0)
        store.release_save.set()
        await asyncio.wait_for(asyncio.gather(accepting, manual), 0.5)

        self.assertEqual("cancelled", store.value["history"][-1]["status"])
        self.assertTrue(bridge.cancellation_event(plan["planId"]).is_set())

    async def test_repeated_manual_request_does_not_extend_or_duplicate_intent(self) -> None:
        clock = [NOW]
        bridge, store, _source = await loaded_bridge(now_ms=lambda: clock[0])

        await bridge.async_register_manual_intent(
            "manual.off.idempotent", CHAND, "turn_off", None
        )
        first = copy.deepcopy(store.value["manualIntents"])
        save_count = len(store.saved)
        clock[0] += 30_000

        await bridge.async_register_manual_intent(
            "manual.off.idempotent", CHAND, "turn_off", None
        )

        self.assertEqual(first, store.value["manualIntents"])
        self.assertEqual(save_count, len(store.saved))

    async def test_manual_request_identity_conflict_is_rejected(self) -> None:
        bridge, store, _source = await loaded_bridge()
        await bridge.async_register_manual_intent(
            "manual.same", CHAND, "turn_off", None
        )

        with self.assertRaises(ScenarioDecisionConflict):
            await bridge.async_register_manual_intent(
                "manual.same", CHAND, "turn_on", None
            )

        self.assertEqual(1, len(store.value["manualIntents"]))

    async def test_invalid_or_incomplete_storage_blocks_recovery_instead_of_resetting(self) -> None:
        store = MemoryStore({"version": 2, "history": []})
        source = SnapshotSource()
        bridge = ScenarioDecisionBridge(
            store,
            snapshot_provider=source.snapshot,
            authority_provider=source.authority,
            now_ms=lambda: NOW,
        )
        with self.assertRaises(RuntimeError):
            await bridge.async_recover()
        self.assertEqual([], store.saved)

    async def test_terminal_history_status_requires_matching_receipt(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        await bridge.async_accept(decision(request))

        for status in ("confirmed", "failed", "uncertain"):
            with self.subTest(status=status):
                incomplete = copy.deepcopy(store.value)
                incomplete["history"][-1]["status"] = status
                incomplete["history"][-1]["receipt"] = None
                self.assertFalse(valid_scenario_decision_bridge_payload(incomplete))

    async def test_priority_failures_are_persisted_without_leaving_active_plan(self) -> None:
        for failure_point, expected_status in (
            ("plan", "failed"),
            ("note_results", "uncertain"),
        ):
            with self.subTest(failure_point=failure_point):
                states = _ExecutorStates()
                hass = SimpleNamespace(
                    states=states, services=_ExecutorServices(states)
                )
                action = ScenarioDeviceAction(
                    "turn_on", "Включить", "light", "turn_on", frozenset()
                )
                executor = ScenarioExecutor(
                    hass,
                    ScenarioCatalog(
                        devices={
                            CHAND: ScenarioDeviceEntry(
                                CHAND, "Люстра", "light.chandelier", (action,)
                            )
                        },
                        scenarios={},
                    ),
                    lambda *_args, **_kwargs: None,
                    readback_window_seconds=0.02,
                    readback_interval_seconds=0.01,
                )

                if failure_point == "plan":
                    def fail_plan(*_args: object, **_kwargs: object) -> object:
                        raise RuntimeError("priority plan failed")

                    executor._light_priority.plan = fail_plan
                else:
                    async def fail_note_results(
                        *_args: object, **_kwargs: object
                    ) -> None:
                        raise RuntimeError("priority result save failed")

                    executor._light_priority.note_results = fail_note_results

                bridge, store, _source = await loaded_bridge(
                    executor=executor
                )
                request = await bridge.async_snapshot(SCENARIO_ID, event())
                plan = decision(request)
                result = await bridge.async_execute_decision(plan)

                self.assertEqual(expected_status, result["status"])
                self.assertEqual(expected_status, store.value["history"][-1]["status"])
                self.assertEqual(
                    expected_status,
                    store.value["history"][-1]["receipt"]["status"],
                )
                self.assertEqual("RuntimeError", result["error"])

    async def test_previous_generation_recovery_blocks_ambiguous_dispatch(self) -> None:
        original, store, source = await loaded_bridge()
        request = await original.async_snapshot(SCENARIO_ID, event())
        await original.async_accept(decision(request))
        store.recovered_previous = True
        restarted = ScenarioDecisionBridge(
            store,
            snapshot_provider=source.snapshot,
            authority_provider=source.authority,
            now_ms=lambda: NOW + 1,
        )
        save_count = len(store.saved)

        with self.assertRaisesRegex(RuntimeError, "previous generation"):
            await restarted.async_recover()

        self.assertEqual(save_count, len(store.saved))

    async def test_storage_validator_rejects_unbounded_or_open_nested_records(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        accepted = await bridge.async_accept(plan)
        await bridge.async_before_dispatch(plan["planId"], plan["action"]["id"])
        receipt = {
            "id": accepted["receiptId"],
            "planId": plan["planId"],
            "actionId": "turn_on",
            "targetId": CHAND,
            "status": "failed",
        }
        await bridge.async_record_receipt(plan["planId"], receipt)
        self.assertTrue(valid_scenario_decision_bridge_payload(store.value))

        open_receipt = copy.deepcopy(store.value)
        open_receipt["receipts"][0]["unexpected"] = True
        self.assertFalse(valid_scenario_decision_bridge_payload(open_receipt))

        open_wakeup = copy.deepcopy(store.value)
        open_wakeup["durable"]["wakeups"] = [
            {
                "id": "profile.unsafe",
                "kind": "profile",
                "dueAtMs": NOW + 1,
                "unexpected": True,
            }
        ]
        self.assertFalse(valid_scenario_decision_bridge_payload(open_wakeup))

        unbounded_manual = copy.deepcopy(store.value)
        unbounded_manual["manualIntents"] = [
            {
                "requestId": "x" * 129,
                "targetId": CHAND,
                "actionId": "turn_off",
                "value": None,
                "registeredAtMs": NOW,
            }
        ]
        self.assertFalse(valid_scenario_decision_bridge_payload(unbounded_manual))

    async def test_receipt_rejects_unknown_fields_before_persistence(self) -> None:
        bridge, _store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        accepted = await bridge.async_accept(plan)
        receipt = {
            "id": accepted["receiptId"],
            "planId": plan["planId"],
            "actionId": "turn_on",
            "targetId": CHAND,
            "status": "failed",
            "unexpected": True,
        }

        with self.assertRaises(ScenarioDecisionRejected):
            await bridge.async_record_receipt(plan["planId"], receipt)


class _ObservationStates:
    def __init__(self) -> None:
        stale = datetime.fromtimestamp((NOW - 86_400_000) / 1000, timezone.utc)
        self.values = {
            "light.chandelier": SimpleNamespace(
                state="off", attributes={}, last_changed=stale,
                last_updated=stale, last_reported=stale,
            ),
            "switch.points": SimpleNamespace(
                state="off", attributes={}, last_changed=stale,
                last_updated=stale, last_reported=stale,
            ),
            "switch.mirror": SimpleNamespace(
                state="off", attributes={}, last_changed=stale,
                last_updated=stale, last_reported=stale,
            ),
            "binary_sensor.presence": SimpleNamespace(
                state="off", attributes={}, last_changed=stale,
                last_updated=stale, last_reported=stale,
            ),
        }

    def get(self, entity_id: str) -> object | None:
        return self.values.get(entity_id)


class _ExecutorStates:
    def __init__(self) -> None:
        stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
        self.value = SimpleNamespace(
            entity_id="light.chandelier",
            state="off",
            attributes={},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )

    def get(self, entity_id: str) -> object | None:
        return self.value if entity_id == "light.chandelier" else None


class _ExecutorServices:
    def __init__(self, states: _ExecutorStates, on_dispatch=None) -> None:
        self._states = states
        self._on_dispatch = on_dispatch
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def async_call(
        self, domain: str, service: str, data: dict[str, object], **_kwargs: object
    ) -> None:
        self.calls.append((domain, service, copy.deepcopy(data)))
        stamp = datetime.fromtimestamp((NOW + 1) / 1000, timezone.utc)
        self._states.value = SimpleNamespace(
            entity_id="light.chandelier",
            state="on",
            attributes={},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )
        if self._on_dispatch is not None:
            self._on_dispatch()


class _EvidenceText(str):
    __slots__ = ("__weakref__",)


class _EvidenceDateTime(datetime):
    def __new__(cls, marker: _EvidenceText, observed_at_ms: int):
        stamp = datetime.fromtimestamp(observed_at_ms / 1000, timezone.utc)
        value = super().__new__(
            cls,
            stamp.year,
            stamp.month,
            stamp.day,
            stamp.hour,
            stamp.minute,
            stamp.second,
            stamp.microsecond,
            tzinfo=timezone.utc,
        )
        value._marker = marker
        return value

    def isoformat(self, *args: object, **kwargs: object) -> str:
        return self._marker


class _MultiExecutorStates:
    def __init__(self) -> None:
        stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
        self.values = {
            "light.chandelier": self._state("light.chandelier", "off", stamp),
            "light.points": self._state("light.points", "off", stamp),
        }

    @staticmethod
    def _state(entity_id: str, state: str, stamp: datetime) -> object:
        return SimpleNamespace(
            entity_id=entity_id,
            state=state,
            attributes={},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )

    def get(self, entity_id: str) -> object | None:
        return self.values.get(entity_id)

    def set_on(self, entity_id: str, stamp: datetime) -> None:
        self.values[entity_id] = self._state(entity_id, "on", stamp)

    def release_evidence(self) -> None:
        stamp = datetime.fromtimestamp((NOW + 2_000) / 1000, timezone.utc)
        for entity_id, state in tuple(self.values.items()):
            self.values[entity_id] = self._state(entity_id, state.state, stamp)


class _BarrierExecutorServices:
    def __init__(
        self,
        states: _MultiExecutorStates,
        evidence_stamps: tuple[datetime, ...],
        on_dispatch,
        *,
        response_entered: asyncio.Event | None = None,
        release_response: asyncio.Event | None = None,
    ) -> None:
        self._states = states
        self._evidence_stamps = list(evidence_stamps)
        self._on_dispatch = on_dispatch
        self._response_entered = response_entered
        self._release_response = release_response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def async_call(
        self, domain: str, service: str, data: dict[str, object], **_kwargs: object
    ) -> None:
        self.calls.append((domain, service, copy.deepcopy(data)))
        entity_id = str(data["entity_id"])
        stamp = self._evidence_stamps.pop(0)
        self._states.set_on(entity_id, stamp)
        target_id = CHAND if entity_id == "light.chandelier" else POINTS
        self._on_dispatch(target_id, stamp)
        if self._response_entered is not None:
            self._response_entered.set()
        if self._release_response is not None:
            await self._release_response.wait()

    def release_evidence(self) -> None:
        self._evidence_stamps.clear()


def _multi_target_executor(
    states: _MultiExecutorStates,
    services: _BarrierExecutorServices,
    *,
    targets: tuple[str, ...] = (CHAND, POINTS),
) -> ScenarioExecutor:
    action = ScenarioDeviceAction(
        "turn_on", "Включить", "light", "turn_on", frozenset()
    )
    entries = {
        CHAND: ScenarioDeviceEntry(
            CHAND, "Люстра", "light.chandelier", (action,)
        ),
        POINTS: ScenarioDeviceEntry(
            POINTS, "Точки", "light.points", (action,)
        ),
    }
    return ScenarioExecutor(
        SimpleNamespace(states=states, services=services),
        ScenarioCatalog(
            devices={target_id: entries[target_id] for target_id in targets},
            scenarios={},
        ),
        lambda *_args, **_kwargs: None,
        readback_window_seconds=0.02,
        readback_interval_seconds=0.01,
    )


class _ObservationHass:
    def __init__(self) -> None:
        self.states = _ObservationStates()


class TamburHaObservationCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.hass = _ObservationHass()
        self.change_callbacks: list[object] = []
        self.report_callbacks: list[object] = []
        self.unsubscribed: list[str] = []
        self.entities = {
            CHAND: "light.chandelier",
            POINTS: "switch.points",
            MIRROR: "switch.mirror",
            POWER: "switch.power",
            SENSOR: "binary_sensor.presence",
        }

    def _track_change(self, _hass: object, _entities: object, callback: object):
        self.change_callbacks.append(callback)
        return lambda: self.unsubscribed.append("change")

    def _track_report(self, _hass: object, _entities: object, callback: object):
        self.report_callbacks.append(callback)
        return lambda: self.unsubscribed.append("report")

    def _coordinator(
        self, deadline_provider, *, sunrise_provider=None
    ) -> TamburHaObservationCoordinator:
        return TamburHaObservationCoordinator(
            self.hass,
            bindings={
                "chandelier": CHAND,
                "points": POINTS,
                "mirror": MIRROR,
                "power": POWER,
                "presenceSensors": [SENSOR],
            },
            entity_id_provider=lambda target_id: self.entities.get(target_id),
            settings=SETTINGS,
            settings_revision=7,
            authority_provider=SnapshotSource().authority,
            freshness_deadline_provider=deadline_provider,
            timezone_name="Asia/Omsk",
            sunset_provider=lambda local_date: NOW + 20_000_000
            if local_date == "2027-01-15"
            else None,
            sunrise_provider=sunrise_provider,
            now_ms=lambda: NOW,
            track_state_changes=self._track_change,
            track_state_reports=self._track_report,
        )

    async def test_snapshot_includes_only_the_actual_ha_sunrise_for_its_local_date(self) -> None:
        coordinator = self._coordinator(
            lambda _target, _entity, reported: reported + 60_000,
            sunrise_provider=lambda local_date: NOW + 12_000
            if local_date == "2027-01-15"
            else None,
        )
        coordinator.start()

        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )

        self.assertEqual(NOW + 12_000, snapshot["clock"]["sunriseAtMs"])

    async def test_snapshot_preserves_verified_automatic_ownership_proof(self) -> None:
        coordinator = self._coordinator(
            lambda _target, _entity, reported: reported + 60_000
        )

        async def authority(target_id: str) -> dict[str, object]:
            if target_id == MIRROR:
                return {
                    "owner": "automatic",
                    "generation": 4,
                    "protectionActive": False,
                    "confirmedReceiptId": "ownership.receipt.4",
                }
            return {"owner": "none", "generation": 1, "protectionActive": False}

        coordinator._authority_provider = authority  # noqa: SLF001
        coordinator.start()
        stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
        state = SimpleNamespace(
            entity_id="switch.mirror", state="on", attributes={}, last_changed=stamp,
            last_updated=stamp, last_reported=stamp,
        )
        self.hass.states.values["switch.mirror"] = state
        self.report_callbacks[0](
            SimpleNamespace(data={"entity_id": "switch.mirror", "new_state": state, "last_reported": stamp})
        )

        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )
        self.assertEqual("ownership.receipt.4", snapshot["authority"][MIRROR]["confirmedReceiptId"])
        self.assertEqual(
            snapshot["observations"][MIRROR]["revision"],
            snapshot["authority"][MIRROR]["confirmedStateRevision"],
        )

    async def test_last_known_light_state_is_trusted_but_sensors_are_not(self) -> None:
        coordinator = self._coordinator(
            lambda _target, _entity, reported: reported + 60_000
        )
        stop = coordinator.start()
        before = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )
        self.assertTrue(before["observations"][CHAND]["fresh"])
        self.assertEqual(
            "last_known_light_state", coordinator.freshness_reason(CHAND)
        )
        self.assertFalse(before["observations"][SENSOR]["fresh"])
        self.assertEqual(
            "continuity_not_observed", coordinator.freshness_reason(SENSOR)
        )

        stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
        state = SimpleNamespace(
            entity_id="light.chandelier",
            state="off", attributes={}, last_changed=stamp,
            last_updated=stamp, last_reported=stamp,
        )
        self.report_callbacks[0](
            SimpleNamespace(
                data={
                    "entity_id": "light.chandelier",
                    "new_state": state,
                    "last_reported": stamp,
                },
                time_fired=stamp,
            )
        )
        after = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(ident="presence.2"), observation_epoch=1
        )
        self.assertTrue(after["observations"][CHAND]["fresh"])
        self.assertEqual(NOW, after["observations"][CHAND]["observedAtMs"])
        stop()
        self.assertEqual(["change", "report"], self.unsubscribed)

    async def test_stale_presence_on_is_trusted_but_stale_off_is_not(self) -> None:
        coordinator = self._coordinator(
            lambda _target, _entity, reported: reported + 60_000
        )
        coordinator.start()
        stamp = datetime.fromtimestamp((NOW - 10_000_000) / 1000, timezone.utc)
        self.hass.states.values["binary_sensor.presence"] = SimpleNamespace(
            entity_id="binary_sensor.presence",
            state="on",
            attributes={},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )
        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )
        self.assertTrue(snapshot["observations"][SENSOR]["fresh"])
        self.assertEqual(
            "last_known_presence_on", coordinator.freshness_reason(SENSOR)
        )

        self.hass.states.values["binary_sensor.presence"].state = "off"
        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(ident="presence.2"), observation_epoch=1
        )
        self.assertFalse(snapshot["observations"][SENSOR]["fresh"])
        self.assertEqual(
            "continuity_not_observed", coordinator.freshness_reason(SENSOR)
        )

    async def test_unpowered_chandelier_is_reported_as_off(self) -> None:
        coordinator = self._coordinator(
            lambda _target, _entity, reported: reported + 60_000
        )
        coordinator.start()
        stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
        self.hass.states.values["light.chandelier"] = SimpleNamespace(
            entity_id="light.chandelier",
            state="on",
            attributes={"brightness": 128},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )
        self.hass.states.values["switch.power"] = SimpleNamespace(
            entity_id="switch.power",
            state="off",
            attributes={},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )
        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )
        self.assertEqual("off", snapshot["observations"][CHAND]["state"])
        self.assertTrue(snapshot["observations"][CHAND]["fresh"])

        self.hass.states.values["switch.power"] = SimpleNamespace(
            entity_id="switch.power",
            state="on",
            attributes={},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )
        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(ident="presence.3"), observation_epoch=1
        )
        self.assertEqual("on", snapshot["observations"][CHAND]["state"])

    async def test_unpowered_chandelier_after_report_stays_available_as_off(self) -> None:
        coordinator = self._coordinator(
            lambda _target, _entity, reported: reported + 60_000
        )
        coordinator.start()
        stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
        chandelier = SimpleNamespace(
            entity_id="light.chandelier",
            state="on",
            attributes={"brightness": 128},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )
        self.hass.states.values["light.chandelier"] = chandelier
        self.hass.states.values["switch.power"] = SimpleNamespace(
            entity_id="switch.power",
            state="on",
            attributes={},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )
        self.report_callbacks[0](
            SimpleNamespace(
                data={
                    "entity_id": "light.chandelier",
                    "new_state": chandelier,
                    "last_reported": stamp,
                },
                time_fired=stamp,
            )
        )
        self.hass.states.values["switch.power"].state = "off"

        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(ident="presence.4"), observation_epoch=1
        )

        self.assertEqual("off", snapshot["observations"][CHAND]["state"])
        self.assertTrue(snapshot["observations"][CHAND]["fresh"])
        self.assertEqual(
            "last_known_light_state", coordinator.freshness_reason(CHAND)
        )

    async def test_missing_deadline_and_unavailable_event_fail_closed_with_distinct_reasons(self) -> None:
        coordinator = self._coordinator(lambda *_args: None)
        coordinator.start()
        stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
        state = SimpleNamespace(
            entity_id="light.chandelier",
            state="off", attributes={}, last_changed=stamp,
            last_updated=stamp, last_reported=stamp,
        )
        self.change_callbacks[0](
            SimpleNamespace(
                data={"entity_id": "light.chandelier", "new_state": state},
                time_fired=stamp,
            )
        )
        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )
        self.assertFalse(snapshot["observations"][CHAND]["fresh"])
        self.assertEqual("freshness_deadline_missing", coordinator.freshness_reason(CHAND))

        unavailable = copy.copy(state)
        unavailable.state = "unavailable"
        self.change_callbacks[0](
            SimpleNamespace(
                data={"entity_id": "light.chandelier", "new_state": unavailable},
                time_fired=stamp,
            )
        )
        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(ident="presence.3"), observation_epoch=1
        )
        self.assertFalse(snapshot["observations"][CHAND]["fresh"])
        self.assertEqual("state_unavailable", coordinator.freshness_reason(CHAND))

    async def test_past_light_deadline_falls_back_but_invalid_stays_closed(self) -> None:
        for returned, expected_fresh, expected_reason in (
            (NOW - 1, True, "last_known_light_state"),
            ("bad", False, "freshness_deadline_invalid"),
        ):
            with self.subTest(returned=returned):
                coordinator = self._coordinator(lambda *_args, value=returned: value)
                coordinator.start()
                stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
                state = SimpleNamespace(
                    entity_id="light.chandelier",
                    state="off", attributes={}, last_changed=stamp,
                    last_updated=stamp, last_reported=stamp,
                )
                self.report_callbacks[-1](
                    SimpleNamespace(
                        data={
                            "entity_id": "light.chandelier",
                            "new_state": state,
                            "last_reported": stamp,
                        },
                        time_fired=stamp,
                    )
                )
                snapshot = await coordinator.async_snapshot_source(
                    SCENARIO_ID, event(), observation_epoch=1
                )
                self.assertEqual(
                    expected_fresh, snapshot["observations"][CHAND]["fresh"]
                )
                self.assertEqual(
                    expected_reason, coordinator.freshness_reason(CHAND)
                )

    async def test_deadline_provider_failure_is_reported_and_fails_closed(self) -> None:
        def unavailable_deadline(*_args: object) -> int:
            raise RuntimeError("freshness service unavailable")

        coordinator = self._coordinator(unavailable_deadline)
        coordinator.start()
        stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
        state = SimpleNamespace(
            entity_id="light.chandelier",
            state="off",
            attributes={},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )
        self.report_callbacks[0](
            SimpleNamespace(
                data={
                    "entity_id": "light.chandelier",
                    "new_state": state,
                    "last_reported": stamp,
                },
                time_fired=stamp,
            )
        )

        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )

        self.assertFalse(snapshot["observations"][CHAND]["fresh"])
        self.assertEqual(
            "freshness_deadline_unavailable", coordinator.freshness_reason(CHAND)
        )

    async def test_missing_last_reported_and_broken_continuity_have_distinct_reasons(self) -> None:
        coordinator = self._coordinator(
            lambda _target, _entity, reported: reported + 60_000
        )
        stop = coordinator.start()
        state = SimpleNamespace(
            entity_id="light.chandelier",
            state="off",
            attributes={},
            last_changed=None,
            last_updated=None,
            last_reported=None,
        )
        self.change_callbacks[0](
            SimpleNamespace(
                data={"entity_id": "light.chandelier", "new_state": state},
                time_fired=None,
            )
        )
        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )
        self.assertFalse(snapshot["observations"][CHAND]["fresh"])
        self.assertEqual("last_reported_missing", coordinator.freshness_reason(CHAND))

        stop()
        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(ident="presence.4"), observation_epoch=1
        )
        self.assertFalse(snapshot["observations"][CHAND]["fresh"])
        self.assertEqual("continuity_broken", coordinator.freshness_reason(CHAND))

    async def test_unreported_state_divergence_breaks_continuity(self) -> None:
        coordinator = self._coordinator(
            lambda _target, _entity, reported: reported + 60_000
        )
        coordinator.start()
        stamp = datetime.fromtimestamp(NOW / 1000, timezone.utc)
        observed = SimpleNamespace(
            entity_id="binary_sensor.presence",
            state="on",
            attributes={},
            last_changed=stamp,
            last_updated=stamp,
            last_reported=stamp,
        )
        self.report_callbacks[0](
            SimpleNamespace(
                data={
                    "entity_id": "binary_sensor.presence",
                    "new_state": observed,
                    "last_reported": stamp,
                },
                time_fired=stamp,
            )
        )
        self.hass.states.values["binary_sensor.presence"] = copy.copy(observed)
        self.hass.states.values["binary_sensor.presence"].state = "off"

        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )

        self.assertFalse(snapshot["observations"][SENSOR]["fresh"])
        self.assertEqual("continuity_broken", coordinator.freshness_reason(SENSOR))
