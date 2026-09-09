"""A whole-home goal spans rooms with different physical target owners."""

import copy
from dataclasses import replace
import unittest
from unittest.mock import patch

from custom_components.hausman_hub.application import climate_application

from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.application.climate_ha_observations import ClimateHaEntityState
from custom_components.hausman_hub.application.contours import with_home_climate_targets
from custom_components.hausman_hub.application.climate_tablet import (
    ClimateTabletService,
    ClimateTabletUnavailable,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDeviceKind,
)
from tests.test_climate_tablet import contract_validator, native_home_target_runtime
from tests.test_climate_manual import MemoryManualStore


NOW = 1784280005000
REQUEST = {
    "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
    "request_id": "tablet.climate.mixed-home",
    "correlation_id": "corr.mixed-home",
    "expected_state_revision": 0,
    "expected_control_revision": 0,
    "reliability_profile": "climate_reliability_v1",
    "action": "set_home_targets",
    "room_id": None,
    "parameters": {"target_temperature": 25.5, "target_humidity": 55},
}


def mixed_home_runtime(*, humidity_only_office=False):
    """Five rooms, two humidifiers, seven temperature owners, real planning."""
    runtime, store, contours, executor = native_home_target_runtime(include_humidifier=True)
    runtime._manual_store = MemoryManualStore()
    registry = runtime._registry_store.registry
    contour = contours.registry.contour("climate")
    rooms = list(registry.rooms)
    devices = list(registry.devices)
    assignments = list(contour.rooms)
    states = executor._state_view.states
    for sensor in registry.devices:
        if sensor.kind is ClimateDeviceKind.HUMIDITY_SENSOR:
            entity_id = sensor.endpoints[0].entity_id
            states[entity_id] = ClimateHaEntityState(entity_id, "45", {}, NOW)
    for room_id, has_humidifier in (
        ("nursery", True), ("office", False), ("alice", False), ("kitchen", False),
    ):
        humidity_only = humidity_only_office and room_id == "office"
        has_humidifier = has_humidifier or humidity_only
        source_room = registry.rooms[0]
        window_id = source_room.window_entity_id.replace("living", room_id)
        rooms.append(replace(source_room, room_id=room_id, name=room_id, window_entity_id=window_id))
        states[window_id] = replace(states[source_room.window_entity_id], entity_id=window_id)
        selected = []
        for source in registry.devices:
            if source.kind in {ClimateDeviceKind.RADIATOR_THERMOSTAT, ClimateDeviceKind.FLOOR_HEATING}:
                continue
            if humidity_only and source.kind is ClimateDeviceKind.AIR_CONDITIONER:
                continue
            if source.kind is ClimateDeviceKind.HUMIDIFIER and not has_humidifier:
                continue
            endpoints = tuple(replace(endpoint, entity_id=endpoint.entity_id.replace("living", room_id))
                              for endpoint in source.endpoints)
            device = replace(source, device_id=source.device_id.replace("living", room_id),
                             room_id=room_id, source_id=source.source_id.replace("living", room_id),
                             endpoints=endpoints)
            devices.append(device)
            if source.device_id in contour.rooms[0].device_ids:
                selected.append(device.device_id)
            for old, new in zip(source.endpoints, endpoints, strict=True):
                if old.entity_id in states:
                    states[new.entity_id] = replace(states[old.entity_id], entity_id=new.entity_id)
        assignments.append(replace(contour.rooms[0], room_id=room_id, device_ids=tuple(selected)))
    runtime._registry_store.registry = replace(registry, rooms=tuple(rooms), devices=tuple(devices))
    contours.registry = replace(contours.registry, contours=(replace(contour, rooms=tuple(assignments)),))
    return runtime, store, contours, executor


class MixedHomeTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_combined_goal_saves_all_rooms_and_dispatches_only_existing_axes(self):
        """Absent room humidifiers must not reject valid temperature owners."""
        runtime, store, contours, executor = mixed_home_runtime()
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
        await service.async_load()
        try:
            accepted = await service.async_submit(copy.deepcopy(REQUEST))
            await service.async_drain()
            receipt = await service.async_operation(accepted["operation_id"])
            contract_validator("climate-operation-receipt.schema.json").validate(receipt)
            self.assertEqual("confirmed", receipt["status"], receipt)
            self.assertEqual({"living", "nursery", "office", "alice", "kitchen"},
                             set(receipt["outcomes"]["rooms"]))
            self.assertEqual([(25.5, 55)] * 5,
                             [(room.target_temperature, room.target_humidity)
                              for room in contours.registry.contour("climate").rooms])
            calls = [call for batch in executor.batches for call in batch]
            self.assertEqual({"living_air_conditioner", "living_radiator", "living_floor",
                              "nursery_air_conditioner", "office_air_conditioner",
                              "alice_air_conditioner", "kitchen_air_conditioner"},
                             {call.owner_device_id for call in calls if call.temperature == 25.5})
            self.assertEqual({"living_humidifier", "nursery_humidifier"},
                             {call.owner_device_id for call in calls if call.humidity == 55})
            self.assertEqual(9, len(calls))
            duplicate = await service.async_submit(copy.deepcopy(REQUEST))
            restarted = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
            await restarted.async_load()
            recovered = await restarted.async_operation(receipt["operation_id"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(receipt, recovered)
            # Reload the real native runtime too, not only its tablet adapter.
            restored_runtime = ClimateRuntime(
                entry_id="entry", configuration=runtime.configuration,
                registry_store=runtime._registry_store, contour_store=contours,
                strict_ha_call_executor=executor, ha_state_view=runtime._ha_state_view,
                direct_control_store=store, now_ms=lambda: NOW,
                manual_store=runtime._manual_store,
            )
            await restored_runtime.async_start()
            restored_service = ClimateTabletService(restored_runtime, store, now_ms=lambda: NOW)
            await restored_service.async_load()
            self.assertEqual(receipt, await restored_service.async_operation(receipt["operation_id"]))
            restored_snapshot = await restored_service.async_snapshot()
            self.assertEqual({(25.5, 55)}, {
                (room["desired_target_temperature"], room["desired_target_humidity"])
                for room in restored_snapshot["rooms"]
            })
            self.assertEqual(9, sum(len(batch) for batch in executor.batches))
        finally:
            await service.async_close()

    async def test_combined_goal_accepts_a_humidity_only_room(self):
        """A room without temperature equipment is not a broken humidifier."""
        runtime, store, contours, executor = mixed_home_runtime(humidity_only_office=True)
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
        await service.async_load()
        receipt = await service.async_execute(copy.deepcopy(REQUEST))
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)
        self.assertEqual("confirmed", receipt["status"], receipt)
        office = receipt["outcomes"]["rooms"]["office"]["devices"]
        self.assertEqual({"office_humidifier"}, set(office))
        calls = [call for batch in executor.batches for call in batch]
        self.assertEqual(6, sum(call.temperature == 25.5 for call in calls))
        self.assertEqual(3, sum(call.humidity == 55 for call in calls))
        self.assertEqual([(25.5, 55)] * 5, [
            (room.target_temperature, room.target_humidity)
            for room in contours.registry.contour("climate").rooms
        ])

    async def test_combined_goal_keeps_both_manual_humidifiers_off(self):
        """Saving humidity does not re-enable a user-excluded actuator."""
        runtime, store, contours, executor = mixed_home_runtime()
        await runtime.async_start()
        for room_id in ("living", "nursery"):
            await runtime.async_set_device_mode(room_id, f"{room_id}_humidifier", "manual")
        service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
        await service.async_load()
        receipt = await service.async_execute(copy.deepcopy(REQUEST))
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)
        self.assertEqual("partial", receipt["status"], receipt)
        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["final"])
        self.assertEqual("saved_for_manual_device", receipt["intent"]["status"])
        for room_id in ("living", "nursery"):
            leaf = receipt["outcomes"]["rooms"][room_id]["devices"][f"{room_id}_humidifier"]
            self.assertEqual(("manual", 0, 0), (leaf["status"], leaf["command_count"], leaf["accepted_count"]))
            room = next(room for room in (await service.async_snapshot())["rooms"]
                        if room["id"] == room_id)
            device = next(device for device in room["devices"]
                          if device["id"] == f"{room_id}_humidifier")
            self.assertEqual("manual", device["mode"])
        calls = [call for batch in executor.batches for call in batch]
        self.assertEqual(7, len(calls))
        self.assertTrue(all(call.temperature == 25.5 and call.humidity is None for call in calls))
        self.assertEqual([55] * 5, [room.target_humidity for room in contours.registry.contour("climate").rooms])

    async def test_combined_goal_does_not_hide_a_broken_present_owner(self):
        """Only an absent kind may be omitted, never a damaged real binding."""
        for mutation in (
            {"endpoints": ()},
            {"control_scope": ClimateControlScope.OBSERVED,
             "control_owner": ClimateControlOwner.OBSERVED},
            {"control_owner": ClimateControlOwner.MANUAL},
        ):
            with self.subTest(mutation=mutation):
                runtime, store, contours, executor = mixed_home_runtime()
                registry = runtime._registry_store.registry
                runtime._registry_store.registry = replace(registry, devices=tuple(
                    replace(device, **mutation) if device.device_id == "nursery_humidifier" else device
                    for device in registry.devices
                ))
                await runtime.async_start()
                service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
                await service.async_load()
                with self.assertRaises(ClimateTabletUnavailable):
                    await service.async_submit(copy.deepcopy(REQUEST))
                self.assertEqual([], contours.saved)
                self.assertEqual([], executor.batches)
                self.assertEqual(0, store._control_revision)
                self.assertIsNone(store.payload)

    async def test_humidity_only_goal_uses_only_rooms_with_humidifiers(self):
        """An unrelated temperature room does not enter a humidity receipt."""
        runtime, store, contours, executor = mixed_home_runtime()
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
        await service.async_load()
        request = {**copy.deepcopy(REQUEST), "parameters": {"target_humidity": 55}}
        receipt = await service.async_execute(request)
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)
        self.assertEqual("confirmed", receipt["status"], receipt)
        self.assertEqual({"living", "nursery"}, set(receipt["outcomes"]["rooms"]))
        calls = [call for batch in executor.batches for call in batch]
        self.assertEqual({"living_humidifier", "nursery_humidifier"}, {call.owner_device_id for call in calls})
        self.assertEqual(2, len(calls))
        self.assertTrue(all(call.temperature is None and call.humidity == 55 for call in calls))
        self.assertEqual([(25.0, 55)] * 5, [
            (room.target_temperature, room.target_humidity)
            for room in contours.registry.contour("climate").rooms
        ])

    async def test_owner_goal_25_5_and_45_preserves_manual_modes_after_restart(self):
        """The reported home request saves both values without manual calls."""
        runtime, store, contours, executor = mixed_home_runtime()
        contours.registry = with_home_climate_targets(
            contours.registry, target_temperature=None, target_humidity=53,
        )
        await runtime.async_start()
        manual_ids = {"living_humidifier", "nursery_humidifier",
                      "nursery_air_conditioner", "alice_air_conditioner"}
        for device_id in manual_ids:
            await runtime.async_set_device_mode(device_id.split("_")[0], device_id, "manual")
        service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
        await service.async_load()
        request = {**copy.deepcopy(REQUEST), "parameters": {
            "target_temperature": 25.5, "target_humidity": 45,
        }}
        accepted = await service.async_submit(request)
        await service.async_drain()
        receipt = await service.async_operation(accepted["operation_id"])
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)
        self.assertEqual(("partial", "saved_for_manual_device"),
                         (receipt["status"], receipt["intent"]["status"]))
        calls = [call for batch in executor.batches for call in batch]
        self.assertEqual(5, len(calls))
        self.assertFalse(manual_ids & {call.owner_device_id for call in calls})
        self.assertEqual([(25.5, 45)] * 5, [
            (room.target_temperature, room.target_humidity)
            for room in contours.registry.contour("climate").rooms
        ])
        restored_runtime = ClimateRuntime(
            entry_id="entry", configuration=runtime.configuration,
            registry_store=runtime._registry_store, contour_store=contours,
            strict_ha_call_executor=executor, ha_state_view=runtime._ha_state_view,
            direct_control_store=store, now_ms=lambda: NOW,
            manual_store=runtime._manual_store,
        )
        await restored_runtime.async_start()
        restored = ClimateTabletService(restored_runtime, store, now_ms=lambda: NOW)
        await restored.async_load()
        self.assertEqual(receipt, await restored.async_operation(receipt["operation_id"]))
        self.assertTrue((await restored.async_submit(request))["duplicate"])
        snapshot = await restored.async_snapshot()
        self.assertEqual(manual_ids, {
            device["id"] for room in snapshot["rooms"] for device in room["devices"]
            if device["mode"] == "manual"
        })
        self.assertEqual(5, sum(len(batch) for batch in executor.batches))
        await service.async_close()
        await restored.async_close()

    async def test_dangling_owner_is_not_treated_as_an_absent_room_axis(self):
        runtime, store, contours, executor = mixed_home_runtime()
        registry = runtime._registry_store.registry
        runtime._registry_store.registry = replace(registry, devices=tuple(
            device for device in registry.devices if device.device_id != "nursery_humidifier"
        ))
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
        await service.async_load()
        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_submit(copy.deepcopy(REQUEST))
        self.assertEqual([], contours.saved)
        self.assertEqual([], executor.batches)
        self.assertEqual(0, store._control_revision)

    async def test_stale_unneeded_humidity_does_not_block_a_temperature_only_room(self):
        """Kitchen temperature remains usable while its unused humidity is old."""
        runtime, store, contours, executor = mixed_home_runtime()
        sensor = next(device for device in runtime._registry_store.registry.devices
                      if device.room_id == "kitchen" and device.kind is ClimateDeviceKind.HUMIDITY_SENSOR)
        entity_id = sensor.endpoints[0].entity_id
        states = executor._state_view.states
        states[entity_id] = replace(states[entity_id], last_updated_ms=NOW - 4 * 60 * 60 * 1000)
        air_conditioner = next(device for device in runtime._registry_store.registry.devices
                               if device.device_id == "kitchen_air_conditioner")
        control_id = air_conditioner.endpoints[0].entity_id
        states[control_id] = replace(states[control_id], state="off", attributes={})
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
        await service.async_load()
        accepted = await service.async_submit(copy.deepcopy(REQUEST))
        await service.async_drain()
        receipt = await service.async_operation(accepted["operation_id"])
        expected_rooms = {"living", "nursery", "office", "alice", "kitchen"}
        for result in (accepted, receipt):
            self.assertEqual(expected_rooms, set(result["intent"]["resolved_scope"]["room_ids"]))
            self.assertEqual(expected_rooms, set(result["outcomes"]["rooms"]))
            self.assertEqual(result["action_snapshot"]["resolved_scope"],
                             result["intent"]["resolved_scope"])
        self.assertEqual("partial", receipt["status"], receipt)
        self.assertTrue(receipt["final"])
        self.assertEqual(8, sum(len(batch) for batch in executor.batches))

    async def test_stale_required_axis_still_rejects_before_saving(self):
        for room_id, kind in (("kitchen", ClimateDeviceKind.TEMPERATURE_SENSOR),
                              ("nursery", ClimateDeviceKind.HUMIDITY_SENSOR)):
            with self.subTest(room=room_id, kind=kind):
                runtime, store, contours, executor = mixed_home_runtime()
                sensor = next(device for device in runtime._registry_store.registry.devices
                              if device.room_id == room_id and device.kind is kind)
                entity_id = sensor.endpoints[0].entity_id
                states = executor._state_view.states
                states[entity_id] = replace(states[entity_id], last_updated_ms=NOW - 4 * 60 * 60 * 1000)
                await runtime.async_start()
                service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
                await service.async_load()
                with self.assertRaises(ClimateTabletUnavailable):
                    await service.async_submit(copy.deepcopy(REQUEST))
                self.assertEqual([], contours.saved)
                self.assertEqual([], executor.batches)
                self.assertEqual(0, store._control_revision)

    async def test_explicit_scope_does_not_come_from_the_legacy_automatic_projection(self):
        """A missing legacy projection cannot erase a proven explicit owner."""
        runtime, store, contours, executor = mixed_home_runtime()
        original = climate_application.build_climate_ha_call_plan

        def without_legacy_kitchen(*args, **kwargs):
            result = original(*args, **kwargs)
            return replace(result, rooms=tuple(room for room in result.rooms if room.room_id != "kitchen"))

        with patch.object(climate_application, "build_climate_ha_call_plan", side_effect=without_legacy_kitchen):
            await runtime.async_start()
            service = ClimateTabletService(runtime, store, now_ms=lambda: NOW)
            await service.async_load()
            accepted = await service.async_submit(copy.deepcopy(REQUEST))
            await service.async_drain()
            receipt = await service.async_operation(accepted["operation_id"])
        expected_rooms = {"living", "nursery", "office", "alice", "kitchen"}
        for result in (accepted, receipt):
            self.assertEqual(expected_rooms, set(result["intent"]["resolved_scope"]["room_ids"]))
            self.assertEqual(result["action_snapshot"]["resolved_scope"], result["intent"]["resolved_scope"])
        self.assertEqual("confirmed", receipt["status"], receipt)
        self.assertEqual(9, sum(len(batch) for batch in executor.batches))
