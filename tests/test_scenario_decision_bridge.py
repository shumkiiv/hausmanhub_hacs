"""Named-risk tests for the durable Tambur decision bridge."""

from __future__ import annotations

import asyncio
import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

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
    *, generation: int = 2, revision: int = 11, owner: str = "none"
) -> dict[str, object]:
    return {
        "generation": generation,
        "observedRevision": revision,
        "observedAtMs": NOW,
        "observationEpoch": 1,
        "fresh": True,
        "owner": owner,
        "protectionActive": False,
    }


class SnapshotSource:
    def __init__(self) -> None:
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
                    "state": "off",
                    "revision": 11,
                    "observedAtMs": NOW,
                    "fresh": True,
                    "continuityEpoch": observation_epoch,
                },
                POINTS: {
                    "state": "off",
                    "revision": 12,
                    "observedAtMs": NOW,
                    "fresh": True,
                    "continuityEpoch": observation_epoch,
                },
                MIRROR: {
                    "state": "off",
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
) -> tuple[ScenarioDecisionBridge, MemoryStore, SnapshotSource]:
    actual_store = store or MemoryStore()
    actual_source = source or SnapshotSource()
    bridge = ScenarioDecisionBridge(
        actual_store,
        snapshot_provider=actual_source.snapshot,
        authority_provider=actual_source.authority,
        now_ms=now_ms,
    )
    await bridge.async_recover()
    return bridge, actual_store, actual_source


class ScenarioDecisionBridgeTests(unittest.IsolatedAsyncioTestCase):
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
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        await bridge.async_accept(plan)
        saved = len(store.saved)

        result = await ScenarioExecutor.async_execute_tambur_decision(
            SimpleNamespace(), copy.deepcopy(plan), bridge
        )

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

    async def test_receipt_must_bind_plan_action_target_and_fresh_observed_revision(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        accepted = await bridge.async_accept(plan)
        await bridge.async_before_dispatch(plan["planId"], plan["action"]["id"])

        wrong = {
            "id": accepted["receiptId"],
            "planId": plan["planId"],
            "actionId": "wrong",
            "targetId": CHAND,
            "status": "confirmed",
            "observedRevision": 12,
            "observedAtMs": NOW + 1,
        }
        with self.assertRaises(ScenarioDecisionRejected):
            await bridge.async_record_receipt(plan["planId"], wrong)
        self.assertEqual("dispatching", store.value["history"][-1]["status"])

        receipt = {**wrong, "actionId": "turn_on"}
        with self.assertRaisesRegex(ScenarioDecisionRejected, "dispatch evidence"):
            await bridge.async_record_receipt(plan["planId"], receipt)
        bridge.mark_dispatch_crossed(plan["planId"], plan["action"]["id"])
        result = await bridge.async_record_receipt(plan["planId"], receipt)
        self.assertEqual("confirmed", result["status"])
        self.assertEqual("confirmed", store.value["history"][-1]["status"])
        self.assertEqual("receipt", result["event"]["kind"])

    async def test_skipped_decision_is_persisted_before_executor_returns_skipped(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event(kind="clock"))
        plan = decision(request)
        plan.update(status="skipped", reasonCode="absence_waiting", action=None)
        executor = SimpleNamespace()

        result = await ScenarioExecutor.async_execute_tambur_decision(
            executor, plan, bridge
        )
        self.assertEqual("skipped", result["status"])
        self.assertEqual("cancelled", store.value["history"][-1]["status"])
        self.assertEqual("absence_waiting", store.value["history"][-1]["reasonCode"])

    async def test_decided_action_runs_through_real_executor_emulator_and_records_receipt(self) -> None:
        bridge, store, _source = await loaded_bridge()
        request = await bridge.async_snapshot(SCENARIO_ID, event())
        plan = decision(request)
        states = _ExecutorStates()
        hass = SimpleNamespace(states=states, services=_ExecutorServices(states))
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

        result = await executor.async_execute_tambur_decision(plan, bridge)

        self.assertEqual("confirmed", result["status"])
        self.assertEqual([("light", "turn_on", {"entity_id": "light.chandelier"})], hass.services.calls)
        self.assertEqual("confirmed", store.value["history"][-1]["status"])
        self.assertEqual("receipt", result["event"]["kind"])

        saved = len(store.saved)
        replay = await executor.async_execute_tambur_decision(
            copy.deepcopy(plan), bridge
        )
        self.assertEqual("confirmed", replay["status"])
        self.assertEqual(1, len(hass.services.calls))
        self.assertEqual(saved, len(store.saved))

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
    def __init__(self, states: _ExecutorStates) -> None:
        self._states = states
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
            SENSOR: "binary_sensor.presence",
        }

    def _track_change(self, _hass: object, _entities: object, callback: object):
        self.change_callbacks.append(callback)
        return lambda: self.unsubscribed.append("change")

    def _track_report(self, _hass: object, _entities: object, callback: object):
        self.report_callbacks.append(callback)
        return lambda: self.unsubscribed.append("report")

    def _coordinator(self, deadline_provider) -> TamburHaObservationCoordinator:
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
            now_ms=lambda: NOW,
            track_state_changes=self._track_change,
            track_state_reports=self._track_report,
        )

    async def test_old_ha_state_is_not_fresh_until_actual_state_event_in_current_epoch(self) -> None:
        coordinator = self._coordinator(
            lambda _target, _entity, reported: reported + 60_000
        )
        stop = coordinator.start()
        before = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )
        self.assertFalse(before["observations"][CHAND]["fresh"])
        self.assertEqual("continuity_not_observed", coordinator.freshness_reason(CHAND))

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

    async def test_past_or_invalid_deadline_never_becomes_fresh(self) -> None:
        for returned, reason in (
            (NOW - 1, "freshness_deadline_expired"),
            ("bad", "freshness_deadline_invalid"),
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
                self.assertFalse(snapshot["observations"][CHAND]["fresh"])
                self.assertEqual(reason, coordinator.freshness_reason(CHAND))

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
                    "new_state": observed,
                    "last_reported": stamp,
                },
                time_fired=stamp,
            )
        )
        self.hass.states.values["light.chandelier"] = copy.copy(observed)
        self.hass.states.values["light.chandelier"].state = "on"

        snapshot = await coordinator.async_snapshot_source(
            SCENARIO_ID, event(), observation_epoch=1
        )

        self.assertFalse(snapshot["observations"][CHAND]["fresh"])
        self.assertEqual("continuity_broken", coordinator.freshness_reason(CHAND))
