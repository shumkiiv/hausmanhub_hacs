"""Production-shaped regressions for saved modes and immutable climate proof."""

import copy
from dataclasses import replace
from itertools import count
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.hausman_hub.application.climate_tablet import (
    ClimateTabletService,
    ClimateTabletUnavailable,
    _StoredOperation,
    _reliable_outcomes,
    _tablet_state_with_checkpoint,
    _with_saved_mode_evidence,
    parse_climate_tablet_action,
)
from custom_components.hausman_hub.application.climate_mode_result import ClimateSavedModeResult
from tests.test_climate_runtime import MemoryStore
from tests.test_climate_tablet import contract_validator, native_home_target_runtime


NOW = 1784280005000


def request(action, parameters, *, revision=0, room_id="living", request_id="mode-proof.1"):
    return {
        "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
        "request_id": request_id, "expected_state_revision": 0,
        "expected_control_revision": revision, "reliability_profile": "climate_reliability_v1",
        "action": action, "room_id": room_id, "parameters": parameters,
    }


class SavedModeProofTests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_confirmation_rejects_older_observation_or_different_mode(self):
        runtime, store, _, _ = native_home_target_runtime(include_humidifier=True)
        runtime._manual_store = MemoryStore(None)
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
        await service.async_load()
        snapshot = await service.async_snapshot()
        device_id = "living_air_conditioner"
        parsed = parse_climate_tablet_action(request("set_device_mode", {"device_id": device_id, "mode": "automatic"}))
        result = ClimateSavedModeResult("living", ((device_id, "automatic"),), NOW)
        scope = {"device_ids": [device_id]}
        metadata = copy.deepcopy(service._last_reliability_metadata)
        metadata[("living", device_id)]["mode_observed_at"] = NOW - 1
        older = _with_saved_mode_evidence(result, parsed, snapshot, scope, metadata)
        self.assertIsNone(older.device_outcomes)
        metadata[("living", device_id)]["mode_observed_at"] = NOW + 1
        next(d for d in snapshot["rooms"][0]["devices"] if d["id"] == device_id)["mode"] = "manual"
        different = _with_saved_mode_evidence(result, parsed, snapshot, scope, metadata)
        self.assertIsNone(different.device_outcomes)

    async def test_background_mode_confirmation_survives_a_moving_runtime_clock(self):
        for device_id in ("living_air_conditioner", "living_radiator", "living_floor", "living_humidifier"):
            for mode in ("manual", "automatic"):
                with self.subTest(device=device_id, mode=mode):
                    runtime, store, _, executor = native_home_target_runtime(include_humidifier=True)
                    runtime._manual_store = MemoryStore(None)
                    clock = count(NOW)
                    runtime._now_ms = lambda: next(clock)
                    await runtime.async_start()
                    if mode == "automatic":
                        await runtime.async_set_device_mode("living", device_id, "manual")
                    service = ClimateTabletService(runtime, store, now_ms=lambda: next(clock))
                    await service.async_load()
                    body = request("set_device_mode", {"device_id": device_id, "mode": mode})
                    accepted = await service.async_submit(body)
                    await service.async_drain()
                    receipt = await service.async_operation(accepted["operation_id"])
                    self.assertEqual("confirmed", receipt["status"], receipt)
                    leaf = receipt["outcomes"]["rooms"]["living"]["devices"][device_id]
                    self.assertEqual("already_in_sync", leaf["execution_state"])
                    self.assertEqual((0, 0), (leaf["command_count"], leaf["accepted_count"]))
                    self.assertEqual([], executor.batches)
                    contract_validator("climate-operation-receipt.schema.json").validate(receipt)
                    restored = ClimateTabletService(runtime, store, now_ms=lambda: next(clock))
                    await restored.async_load()
                    replay = await restored.async_execute(body)
                    self.assertTrue(replay["duplicate"])
                    self.assertEqual("confirmed", replay["status"])

    async def test_native_device_modes_confirm_all_actuator_kinds_without_ha_calls(self):
        for device_id in ("living_air_conditioner", "living_radiator", "living_floor", "living_humidifier"):
            for mode in ("manual", "automatic"):
                with self.subTest(device=device_id, mode=mode):
                    runtime, store, _, executor = native_home_target_runtime(include_humidifier=True)
                    runtime._manual_store = MemoryStore(None)
                    await runtime.async_start()
                    if mode == "automatic":
                        await runtime.async_set_device_mode("living", device_id, "manual")
                    source_times = {key: state.last_updated_ms for key, state in runtime._ha_state_view.states.items()}
                    service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
                    await service.async_load()
                    body = request("set_device_mode", {"device_id": device_id, "mode": mode})
                    receipt = await service.async_execute(body)
                    self.assertEqual("confirmed", receipt["status"], receipt)
                    leaf = receipt["outcomes"]["rooms"]["living"]["devices"][device_id]
                    self.assertEqual("already_in_sync", leaf["execution_state"])
                    self.assertEqual((0, 0), (leaf["command_count"], leaf["accepted_count"]))
                    self.assertEqual(mode, leaf["evidence"]["observed_actual"]["reported_mode"])
                    self.assertEqual([], executor.batches)
                    self.assertEqual(source_times, {key: state.last_updated_ms for key, state in runtime._ha_state_view.states.items()})
                    restored = ClimateTabletService(runtime, store, now_ms=lambda: NOW + 90000)
                    await restored.async_load()
                    replay = await restored.async_execute(body)
                    self.assertTrue(replay["duplicate"])
                    self.assertEqual("confirmed", replay["status"])
                    contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_native_room_mode_confirms_all_owners_with_zero_physical_calls(self):
        runtime, store, _, executor = native_home_target_runtime(include_humidifier=True)
        runtime._manual_store = MemoryStore(None)
        clock = count(NOW)
        runtime._now_ms = lambda: next(clock)
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: next(clock))
        await service.async_load()
        accepted = await service.async_submit(request("set_room_mode", {"mode": "manual"}))
        await service.async_drain()
        receipt = await service.async_operation(accepted["operation_id"])
        self.assertEqual("confirmed", receipt["status"], receipt)
        leaves = receipt["outcomes"]["rooms"]["living"]["devices"]
        self.assertEqual(4, len(leaves))
        self.assertTrue(all(leaf["execution_state"] == "already_in_sync" for leaf in leaves.values()))
        self.assertEqual([], executor.batches)
        restored = ClimateTabletService(runtime, store, now_ms=lambda: next(clock))
        await restored.async_load()
        self.assertEqual("confirmed", (await restored.async_operation(receipt["operation_id"]))["status"])


class FrozenClimateProofTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime, self.store, _, self.executor = native_home_target_runtime(include_humidifier=False)
        self.now = NOW
        original = self.executor.async_execute

        async def one_pending(calls):
            if calls[0].entity_id == "climate.living_radiator":
                self.executor.batches.append(calls)
                return len(calls)
            return await original(calls)

        self.executor.async_execute = one_pending
        await self.runtime.async_start()
        self.service = ClimateTabletService(self.runtime, self.store, now_ms=lambda: self.now)
        await self.service.async_load()
        self.body = request("set_home_targets", {"target_temperature": 25.5}, room_id=None, request_id="frozen-proof.1")
        with patch("custom_components.hausman_hub.application.climate_runtime.asyncio.sleep", new_callable=AsyncMock):
            self.receipt = await self.service.async_execute(self.body)
        self.assertIn(self.receipt["status"], {"pending", "partial"})
        self.assertFalse(self.receipt["final"])

    async def test_confirmed_leaf_keeps_original_evidence_when_live_target_moves(self):
        original = copy.deepcopy(self.receipt["outcomes"]["rooms"]["living"]["devices"]["living_air_conditioner"])
        self.assertEqual("confirmed", original["status"])
        entity = "climate.living_air_conditioner"
        state = self.runtime._ha_state_view.states[entity]
        self.runtime._ha_state_view.states[entity] = replace(state, attributes={**state.attributes, "temperature": 27.0}, last_updated_ms=NOW + 2000)
        self.now += 2000
        refreshed = await self.service.async_operation(self.receipt["operation_id"])
        actual = refreshed["outcomes"]["rooms"]["living"]["devices"]["living_air_conditioner"]
        self.assertEqual(original, actual)
        restored = ClimateTabletService(self.runtime, self.store, now_ms=lambda: self.now)
        await restored.async_load()
        self.assertEqual(3, len(self.executor.batches))

    async def test_authenticated_old_drift_is_downgraded_without_replay(self):
        record = self.service._records_by_request[self.body["request_id"]]
        damaged = copy.deepcopy(record.receipt)
        leaf = damaged["outcomes"]["rooms"]["living"]["devices"]["living_air_conditioner"]
        leaf["evidence"]["reported_target_temperature"] = 27.0
        leaf["evidence"]["observed_actual"]["reported_target_temperature"] = 27.0
        self.service._records_by_request[self.body["request_id"]] = _StoredOperation(record.fingerprint, record.request, damaged, record.dispatch_ledger)
        # Emulate the old server writer, including both authentic checkpoints.
        await self.service._async_save()
        restored = ClimateTabletService(self.runtime, self.store, now_ms=lambda: NOW + 90000)
        await restored.async_load()
        replay = await restored.async_execute(self.body)
        leaves = replay["outcomes"]["rooms"]["living"]["devices"]
        self.assertTrue(replay["duplicate"])
        self.assertTrue(replay["accepted"])
        self.assertFalse(replay["confirmed"])
        self.assertEqual("accepted_timeout", leaves["living_air_conditioner"]["execution_state"])
        self.assertNotIn("evidence", leaves["living_air_conditioner"])
        self.assertEqual("confirmed", leaves["living_floor"]["status"])
        self.assertEqual(3, len(self.executor.batches))
        again = ClimateTabletService(self.runtime, self.store, now_ms=lambda: NOW + 100000)
        await again.async_load()

    async def test_unsigned_drift_still_fails_closed(self):
        leaf = self.store.payload["records"][0]["receipt"]["outcomes"]["rooms"]["living"]["devices"]["living_air_conditioner"]
        leaf["evidence"]["reported_target_temperature"] = 27.0
        leaf["evidence"]["observed_actual"]["reported_target_temperature"] = 27.0
        restored = ClimateTabletService(self.runtime, self.store, now_ms=lambda: NOW + 90000)
        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_state_signature_without_exact_operation_checkpoint_cannot_repair(self):
        leaf = self.store.payload["records"][0]["receipt"]["outcomes"]["rooms"]["living"]["devices"]["living_air_conditioner"]
        leaf["evidence"]["reported_target_temperature"] = 27.0
        leaf["evidence"]["observed_actual"]["reported_target_temperature"] = 27.0
        self.store._scope_bindings["__tablet_state__"] = _tablet_state_with_checkpoint(
            None, self.store.payload, self.service._reliable_scope_integrity_key,
        )
        restored = ClimateTabletService(self.runtime, self.store, now_ms=lambda: NOW + 90000)
        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_native_confirmation_flag_without_matching_readback_stays_pending(self):
        entity = "climate.living_air_conditioner"
        state = self.runtime._ha_state_view.states[entity]
        self.runtime._ha_state_view.states[entity] = replace(state, attributes={**state.attributes, "temperature": 27.0}, last_updated_ms=NOW + 2000)
        snapshot = await self.service._snapshot_unlocked()
        native = {"living_air_conditioner": {
            "status": "confirmed", "execution_state": "applied", "reason": "none",
            "command_count": 1, "accepted_count": 1,
            "evidence": {"native_read_back": True, "fresh": True},
        }}
        outcomes, _ = _reliable_outcomes(
            parse_climate_tablet_action(self.body), snapshot,
            self.receipt["action_snapshot"]["resolved_scope"], "pending", dispatched=True,
            execution_outcomes=native, reliability_metadata=self.service._last_reliability_metadata,
            dispatched_at=NOW,
        )
        leaf = outcomes["living"]["devices"]["living_air_conditioner"]
        self.assertEqual("pending", leaf["status"])
        self.assertEqual("accepted_unverified", leaf["execution_state"])
        self.assertNotIn("evidence", leaf)
