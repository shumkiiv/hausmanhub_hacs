"""Durable acknowledgement is independent of a slow physical device."""

import asyncio
import copy
import unittest

from custom_components.hausman_hub.application.climate_tablet import (
    ClimateTabletService,
    ClimateTabletUnavailable,
    ClimateTabletViolation,
    _with_reliability_projection,
)
from tests.test_climate_tablet import (
    AuthenticatedLedgerMemoryStore,
    FakeRuntime,
    action_request,
    contract_validator,
    managed_home,
    native_home_target_runtime,
)


class SlowRuntime(FakeRuntime):
    def __init__(self):
        super().__init__(managed_home())
        self.home["_home_target_available"] = True
        self._mark_observed()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def async_home_climate_targets(self, payload):
        self.started.set()
        await self.release.wait()
        return await super().async_home_climate_targets(payload)


class BackgroundClimateActionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = SlowRuntime()
        self.store = AuthenticatedLedgerMemoryStore()
        self.service = ClimateTabletService(self.runtime, self.store)
        await self.service.async_load()
        snapshot = await self.service.async_snapshot()
        self.request = action_request(snapshot["state_revision"])
        self.request.update(
            action="set_home_targets", room_id=None,
            reliability_profile="climate_reliability_v1", expected_control_revision=0,
        )

    async def asyncTearDown(self):
        self.runtime.release.set()
        close = getattr(self.service, "async_close", None)
        if close is not None:
            await close()

    async def test_ack_poll_and_snapshot_do_not_wait_for_device(self):
        receipt = await asyncio.wait_for(self.service.async_submit(self.request), 0.3)
        await asyncio.wait_for(self.runtime.started.wait(), 0.3)
        self.assertFalse(receipt["final"])
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)
        self.assertTrue(all(
            leaf.get("accepted_count", 0) == 0
            for room in receipt["outcomes"]["rooms"].values()
            for leaf in room["devices"].values()
        ))
        self.assertEqual(self.request["request_id"], self.store.payload["records"][0]["request"]["request_id"])
        polled = await asyncio.wait_for(self.service.async_operation(receipt["operation_id"]), 0.3)
        self.assertEqual(receipt, polled)
        snapshot = await asyncio.wait_for(self.service.async_snapshot(), 0.3)
        self.assertEqual(1, snapshot["control_revision"])
        self.assertEqual(23.5, snapshot["rooms"][0]["desired_target_temperature"])
        self.assertEqual(receipt["operation_id"], snapshot["active_operations"][0]["operation_id"])
        duplicate = await asyncio.wait_for(self.service.async_submit(self.request), 0.3)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(receipt["operation_id"], duplicate["operation_id"])
        changed = copy.deepcopy(self.request)
        changed["parameters"]["target_temperature"] = 24.0
        with self.assertRaises(ClimateTabletViolation):
            await self.service.async_submit(changed)
        self.runtime.release.set()
        await self.service.async_drain()
        self.assertEqual(1, len(self.runtime.commands))

    async def test_disconnect_before_ack_does_not_cancel_reserved_work(self):
        entered = asyncio.Event()
        release_save = asyncio.Event()
        original_save = self.store.async_save

        async def slow_save(payload):
            entered.set()
            await release_save.wait()
            await original_save(payload)

        self.store.async_save = slow_save
        caller = asyncio.create_task(self.service.async_submit(self.request))
        await asyncio.wait_for(entered.wait(), 0.3)
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        release_save.set()
        await asyncio.wait_for(self.runtime.started.wait(), 0.3)
        self.runtime.release.set()
        await self.service.async_drain()
        self.assertEqual(1, len(self.runtime.commands))

    async def test_failed_save_cannot_acknowledge_or_dispatch(self):
        async def fail_save(payload):
            raise OSError("test storage failure")

        self.store.async_save = fail_save
        with self.assertRaises(ClimateTabletUnavailable):
            await self.service.async_submit(self.request)
        self.assertFalse(self.runtime.started.is_set())

    async def test_close_cancels_work_and_rejects_new_submission(self):
        await self.service.async_submit(self.request)
        await asyncio.wait_for(self.runtime.started.wait(), 0.3)
        await self.service.async_close()
        self.assertEqual([], self.runtime.commands)
        with self.assertRaises(ClimateTabletUnavailable):
            await self.service.async_submit(self.request)

    async def test_dashboard_reads_do_not_wait_for_physical_runtime_lock(self):
        runtime, _store, _contours, _executor = native_home_target_runtime(include_humidifier=True)
        await runtime.async_start()
        async with runtime._lock:
            targets = await asyncio.wait_for(runtime.async_dashboard_climate_targets(), 0.3)
            ownership = await asyncio.wait_for(runtime.async_dashboard_climate_ownership(), 0.3)
            await asyncio.wait_for(runtime.async_dashboard_outdoor_temperature_entity_ids(), 0.3)
        self.assertIn("living", targets)
        self.assertEqual("automatic", ownership["rooms"]["living"])

    def test_participation_does_not_wait_for_sensor_or_unrelated_target_axis(self):
        snapshot = {"generated_at": 1, "rooms": [{
            "id": "living", "mode": "automatic", "target_temperature": 25.5,
            "target_humidity": 45, "devices": [
                {"id": "ac", "kind": "air_conditioner", "control_scope": "managed",
                 "available": True, "mode": "automatic", "target_temperature": 25.5},
                {"id": "sensor", "kind": "temperature_sensor", "control_scope": "observe_only",
                 "available": True, "mode": "automatic"},
                {"id": "humidifier", "kind": "humidifier", "control_scope": "managed",
                 "available": True, "mode": "automatic", "target_humidity": 45},
            ],
        }]}
        result = _with_reliability_projection(snapshot, {}, 1)
        self.assertEqual(0, result["participation_summary"]["pending_sync_count"])
        self.assertEqual(2, result["participation_summary"]["automatic_count"])
        sensor = result["rooms"][0]["devices"][1]
        self.assertIsNone(sensor["desired_target_temperature"])
        self.assertIsNone(sensor["desired_target_humidity"])

    async def test_one_rejected_device_does_not_erase_healthy_results_on_restart(self):
        runtime, store, _contours, executor = native_home_target_runtime(include_humidifier=False)
        await runtime.async_start()
        original_execute = executor.async_execute

        async def fail_one(calls):
            if calls[0].entity_id == "climate.living_air_conditioner":
                raise RuntimeError("test unavailable AC")
            return await original_execute(calls)

        executor.async_execute = fail_one
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {**self.request, "parameters": {"target_temperature": 25.5}}
        acknowledged = await service.async_submit(request)
        await service.async_drain()
        receipt = await service.async_operation(acknowledged["operation_id"])
        self.assertEqual("partial", receipt["status"])
        self.assertTrue(receipt["accepted"])
        self.assertEqual(2, len(executor.batches))
        native = store._direct_control_records[-1]["receipt"]
        self.assertEqual(2, native["accepted_count"])
        self.assertEqual("partial", native["status"])
