"""Tests for the canonical tablet climate projection and durable operations."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.climate_tablet import (
    ClimateTabletOperationNotFound,
    ClimateTabletService,
    ClimateTabletUnavailable,
    ClimateTabletViolation,
    climate_tablet_snapshot,
    parse_climate_tablet_action,
)
from custom_components.hausman_hub.application.contour_apply import ContourApplyStatus
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1"


def contract_validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def managed_home() -> dict[str, object]:
    payload = json.loads(
        (ROOT / "fixtures" / "hausmanhub_climate_v12" / "home.json").read_text(
            encoding="utf-8"
        )
    )
    contour = payload["contours"][0]
    contour["execution"]["settings_apply"]["available"] = True
    contour["execution"]["temporary_temperature"]["available"] = True
    contour["rooms"][0]["temporary_temperature"]["available"] = True
    room = payload["rooms"][0]
    room["devices"][0]["control_scope"] = "managed"
    room["control"]["enabled"] = True
    room["control"]["allowed_actions"] = [
        "set_room_target",
        "turn_room_off",
    ]
    room["control"]["blocked_reasons"] = []
    for availability in room["control"]["action_availability"].values():
        availability["allowed"] = True
        availability["blocked_reasons"] = []
    return payload


def action_request(
    revision: int,
    *,
    request_id: str = "tablet.climate.0001",
    target: float = 23.5,
) -> dict[str, object]:
    return {
        "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
        "request_id": request_id,
        "expected_state_revision": revision,
        "action": "set_room_target",
        "room_id": "living",
        "parameters": {"target_temperature": target},
    }


class MemoryOperationStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> object | None:
        return copy.deepcopy(self.payload)

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved.append(copy.deepcopy(payload))


class FakeRuntime:
    def __init__(self, home: dict[str, object]) -> None:
        self.home = copy.deepcopy(home)
        self.configuration = SimpleNamespace(
            mode="shadow",
            climate_bridge_mode=ClimateControlMode.MANAGED,
        )
        self.commands: list[dict[str, object]] = []
        self.result_status = ContourApplyStatus.CONFIRMED

    async def async_public_snapshot(self) -> dict[str, object]:
        return copy.deepcopy(self.home)

    async def async_temporary_temperature(
        self, payload: object, now: object
    ) -> object:
        del now
        self.commands.append(copy.deepcopy(payload))
        return SimpleNamespace(
            status=self.result_status,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_home_climate_targets(self, payload: object) -> object:
        self.commands.append(copy.deepcopy(payload))
        return SimpleNamespace(
            status=self.result_status,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_room_humidity_target(
        self, *, request_id: object, room_id: object, target_humidity: object
    ) -> object:
        self.commands.append(
            {
                "request_id": request_id,
                "room_id": room_id,
                "target_humidity": target_humidity,
            }
        )
        self.home["rooms"][0]["target_humidity"] = target_humidity
        return SimpleNamespace(
            status=self.result_status,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_set_room_mode(self, room_id: object, mode: object) -> object:
        self.commands.append({"room_id": room_id, "mode": mode})
        self.home["rooms"][0]["mode"] = mode
        return SimpleNamespace(
            status=self.result_status,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_set_device_mode(
        self, room_id: object, device_id: object, mode: object
    ) -> object:
        self.commands.append(
            {"room_id": room_id, "device_id": device_id, "mode": mode}
        )
        self.home["rooms"][0]["devices"][0]["mode"] = mode
        return SimpleNamespace(
            status=self.result_status,
            confirmed_room_count=1,
            accepted_count=1,
        )


class ClimateTabletProjectionTest(unittest.TestCase):
    def test_managed_projection_exposes_only_currently_executable_actions(self) -> None:
        payload = climate_tablet_snapshot(managed_home(), climate_mode="managed")

        self.assertEqual("managed", payload["phase"])
        self.assertEqual("hausman_hub", payload["authority"])
        self.assertTrue(payload["commands_enabled"])
        self.assertEqual(
            ["set_home_targets"], payload["home_control"]["allowed_actions"]
        )
        room = payload["rooms"][0]
        self.assertEqual(["set_room_target"], room["control"]["allowed_actions"])
        self.assertEqual("air_conditioner", room["devices"][0]["kind"])
        self.assertEqual("managed", room["devices"][0]["control_scope"])
        self.assertEqual("working", room["devices"][0]["state"])
        self.assertIsNone(room["devices"][0]["cooldown"])
        self.assertEqual(
            {"minimum": 18, "maximum": 28, "step": 0.5},
            room["temperature_range"],
        )
        self.assertEqual("day", room["active_profile"])
        self.assertFalse(room["temporary_override"]["active"])
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_stale_projection_keeps_manual_exclusion_available(self) -> None:
        home = managed_home()
        home["climate"]["fresh"] = False
        home["rooms"][0]["control"]["allowed_actions"].append("set_room_mode")

        payload = climate_tablet_snapshot(home, climate_mode="managed")

        self.assertTrue(payload["commands_enabled"])
        self.assertEqual([], payload["blocked_reasons"])
        self.assertEqual([], payload["home_control"]["allowed_actions"])
        self.assertEqual(
            ["set_room_mode"], payload["rooms"][0]["control"]["allowed_actions"]
        )
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_shadow_projection_keeps_observations_and_disables_every_action(self) -> None:
        payload = climate_tablet_snapshot(managed_home(), climate_mode="shadow")

        self.assertEqual("shadow", payload["phase"])
        self.assertEqual("legacy_climate_core", payload["authority"])
        self.assertFalse(payload["commands_enabled"])
        self.assertEqual("living", payload["rooms"][0]["id"])
        self.assertEqual(
            [], payload["rooms"][0]["control"]["allowed_actions"]
        )
        self.assertIn(
            "shadow_only", payload["rooms"][0]["control"]["blocked_reasons"]
        )
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_disabled_projection_never_reads_or_invents_room_state(self) -> None:
        payload = climate_tablet_snapshot(
            None,
            climate_mode="disabled",
            generated_at=1_785_949_200_000,
        )

        self.assertEqual("disabled", payload["phase"])
        self.assertEqual("none", payload["authority"])
        self.assertEqual([], payload["rooms"])
        self.assertFalse(payload["commands_enabled"])
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_disabled_projection_keeps_durable_active_operation_visible(self) -> None:
        operation = {
            "operation_id": "0123456789abcdef0123456789abcdef",
            "request_id": "tablet.climate.0001",
            "action": "set_room_target",
            "room_id": "living",
            "status": "pending",
            "updated_at": 1_785_949_200_000,
        }

        payload = climate_tablet_snapshot(
            None,
            climate_mode="disabled",
            active_operations=(operation,),
            generated_at=1_785_949_200_000,
        )

        self.assertEqual([operation], payload["active_operations"])
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_room_control_and_range_follow_authoritative_native_runtime(self) -> None:
        home = managed_home()
        control = home["rooms"][0]["control"]
        control["enabled"] = False
        control["allowed_actions"] = []
        control["blocked_reasons"] = ["device_unavailable"]
        target_input = control["action_inputs"]["set_room_target"][
            "target_temperature"
        ]
        target_input.update({"minimum": 19, "maximum": 27, "step": 1})

        payload = climate_tablet_snapshot(home, climate_mode="managed")
        room = payload["rooms"][0]

        self.assertFalse(room["control"]["enabled"])
        self.assertEqual([], room["control"]["allowed_actions"])
        self.assertEqual(
            ["device_unavailable"],
            room["control"]["blocked_reasons"],
        )
        self.assertEqual(19, room["minimum_temperature"])
        self.assertEqual(
            {"minimum": 19, "maximum": 27, "step": 1},
            room["temperature_range"],
        )
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_public_request_rejects_raw_home_assistant_target(self) -> None:
        request = action_request(managed_home()["state_revision"])
        request["entity_id"] = "climate.unsafe"

        with self.assertRaises(ClimateTabletViolation):
            parse_climate_tablet_action(request)

    def test_home_target_rejects_explicit_null_parameter(self) -> None:
        request = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "tablet.climate.home.1",
            "expected_state_revision": managed_home()["state_revision"],
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_temperature": None},
        }

        with self.assertRaises(ClimateTabletViolation):
            parse_climate_tablet_action(request)


class ClimateTabletServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.home = managed_home()
        self.runtime = FakeRuntime(self.home)
        self.store = MemoryOperationStore()
        self.now = 1_785_949_320_000
        self.service = ClimateTabletService(
            self.runtime,
            self.store,
            operation_id_factory=lambda: "0123456789abcdef0123456789abcdef",
            now_ms=lambda: self.now,
            local_now=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
        )

    async def test_reserves_before_execution_and_deduplicates_retry(self) -> None:
        request = action_request(self.home["state_revision"])

        receipt = await self.service.async_execute(request)
        duplicate = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertTrue(receipt["confirmed"])
        self.assertFalse(receipt["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(1, len(self.runtime.commands))
        self.assertEqual("pending", self.store.saved[0]["records"][0]["receipt"]["status"])
        self.assertEqual("confirmed", self.store.saved[1]["records"][0]["receipt"]["status"])
        snapshot = await self.service.async_snapshot()
        self.assertEqual(
            receipt["operation_id"],
            snapshot["rooms"][0]["devices"][0]["last_confirmed_operation"][
                "operation_id"
            ],
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_set_room_mode_dispatches_existing_contract_action(self) -> None:
        self.runtime.home["rooms"][0]["control"]["allowed_actions"].append(
            "set_room_mode"
        )
        request = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "tablet.climate.mode.1",
            "expected_state_revision": self.home["state_revision"],
            "action": "set_room_mode",
            "room_id": "living",
            "parameters": {"mode": "manual"},
        }

        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual(
            {"room_id": "living", "mode": "manual"},
            self.runtime.commands[0],
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_set_room_humidity_target_dispatches_typed_room_intent(self) -> None:
        self.runtime.home["rooms"][0]["control"]["allowed_actions"].append(
            "set_room_humidity_target"
        )
        request = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "tablet.climate.humidity.1",
            "expected_state_revision": self.home["state_revision"],
            "action": "set_room_humidity_target",
            "room_id": "living",
            "parameters": {"target_humidity": 50},
        }

        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual(
            {
                "request_id": "tablet.climate.humidity.1",
                "room_id": "living",
                "target_humidity": 50,
            },
            self.runtime.commands[0],
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_set_device_mode_dispatches_durable_manual_exclusion(self) -> None:
        device = self.runtime.home["rooms"][0]["devices"][0]
        device["mode"] = "automatic"
        device["control"] = {
            "enabled": True,
            "allowed_actions": ["set_device_mode"],
            "actions": ["set_device_mode"],
            "blocked_reasons": [],
        }
        request = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "tablet.climate.device-mode.1",
            "expected_state_revision": self.home["state_revision"],
            "action": "set_device_mode",
            "room_id": "living",
            "parameters": {
                "device_id": device["id"],
                "mode": "manual",
            },
        }

        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual(
            {
                "room_id": "living",
                "device_id": device["id"],
                "mode": "manual",
            },
            self.runtime.commands[0],
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_pending_operation_confirms_from_read_back_without_reexecution(self) -> None:
        self.runtime.result_status = ContourApplyStatus.PENDING
        request = action_request(self.home["state_revision"])
        pending = await self.service.async_execute(request)
        self.runtime.home["state_revision"] += 1
        self.runtime.home["rooms"][0]["target_temperature"] = 23.5
        temporary = self.runtime.home["contours"][0]["rooms"][0][
            "temporary_temperature"
        ]
        temporary.update(
            {
                "active": True,
                "temperature": 23.5,
                "ends": "next_schedule_change",
                "ends_at": "2026-08-05T23:00:00+00:00",
            }
        )

        confirmed = await self.service.async_operation(pending["operation_id"])

        self.assertEqual("confirmed", confirmed["status"])
        self.assertTrue(confirmed["read_back"]["matched"])
        self.assertEqual(1, len(self.runtime.commands))
        contract_validator("climate-operation-receipt.schema.json").validate(
            confirmed
        )

    async def test_restart_restores_final_receipt_without_reexecution(self) -> None:
        request = action_request(self.home["state_revision"])
        original = await self.service.async_execute(request)
        restarted_runtime = FakeRuntime(self.home)
        restarted = ClimateTabletService(restarted_runtime, self.store)

        await restarted.async_load()
        duplicate = await restarted.async_execute(request)

        self.assertEqual(original["operation_id"], duplicate["operation_id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual([], restarted_runtime.commands)

    async def test_pending_reservation_times_out_after_restart_without_reexecution(self) -> None:
        request = action_request(self.home["state_revision"])
        completed = await self.service.async_execute(request)
        pending_store = MemoryOperationStore(self.store.saved[0])
        restarted_runtime = FakeRuntime(self.home)
        now = self.now + 60_000
        restarted = ClimateTabletService(
            restarted_runtime,
            pending_store,
            now_ms=lambda: now,
        )

        await restarted.async_load()
        snapshot = await restarted.async_snapshot()
        receipt = await restarted.async_operation(completed["operation_id"])

        self.assertEqual([], snapshot["active_operations"])
        self.assertEqual("timed_out", receipt["status"])
        self.assertTrue(receipt["final"])
        self.assertEqual([], restarted_runtime.commands)
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_pending_reservation_survives_restart_while_runtime_is_disabled(self) -> None:
        request = action_request(self.home["state_revision"])
        completed = await self.service.async_execute(request)
        pending_store = MemoryOperationStore(self.store.saved[0])
        restarted_runtime = FakeRuntime(self.home)
        restarted_runtime.configuration = SimpleNamespace(
            mode="read-only",
            climate_bridge_mode=ClimateControlMode.DISABLED,
        )
        restarted = ClimateTabletService(
            restarted_runtime,
            pending_store,
            now_ms=lambda: self.now + 30_000,
        )

        await restarted.async_load()
        snapshot = await restarted.async_snapshot()
        duplicate = await restarted.async_execute(request)

        self.assertEqual(
            completed["operation_id"],
            snapshot["active_operations"][0]["operation_id"],
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual("pending", duplicate["status"])
        self.assertEqual([], restarted_runtime.commands)
        contract_validator("climate-runtime.schema.json").validate(snapshot)

    async def test_conflicting_request_id_is_rejected_without_command(self) -> None:
        await self.service.async_execute(action_request(self.home["state_revision"]))

        with self.assertRaisesRegex(ClimateTabletViolation, "already used"):
            await self.service.async_execute(
                action_request(self.home["state_revision"], target=24.0)
            )
        self.assertEqual(1, len(self.runtime.commands))

    async def test_stale_revision_is_rejected_before_reservation(self) -> None:
        with self.assertRaisesRegex(ClimateTabletViolation, "revision changed"):
            await self.service.async_execute(
                action_request(self.home["state_revision"] + 1)
            )

        self.assertEqual([], self.runtime.commands)
        self.assertEqual([], self.store.saved)

    async def test_shadow_runtime_reads_state_without_exposing_commands(self) -> None:
        self.runtime.configuration = SimpleNamespace(
            mode="shadow",
            climate_bridge_mode=ClimateControlMode.DISABLED,
        )

        snapshot = await self.service.async_snapshot()

        self.assertEqual("shadow", snapshot["phase"])
        self.assertEqual("living", snapshot["rooms"][0]["id"])
        self.assertFalse(snapshot["commands_enabled"])
        self.assertEqual([], self.runtime.commands)

        with self.assertRaises(ClimateTabletViolation) as raised:
            await self.service.async_execute(
                action_request(snapshot["state_revision"])
            )
        self.assertEqual("climate_shadow_only", raised.exception.code)
        self.assertEqual([], self.store.saved)
        self.assertEqual([], self.runtime.commands)

    async def test_unknown_operation_is_not_found(self) -> None:
        with self.assertRaises(ClimateTabletOperationNotFound):
            await self.service.async_operation("f" * 32)

    async def test_damaged_store_fails_closed(self) -> None:
        service = ClimateTabletService(
            self.runtime,
            MemoryOperationStore({"version": 1, "records": [{"unsafe": True}]}),
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_load()

    async def test_inconsistent_persisted_receipt_fails_closed(self) -> None:
        await self.service.async_execute(action_request(self.home["state_revision"]))
        damaged = copy.deepcopy(self.store.payload)
        damaged["records"][0]["receipt"]["confirmed"] = False
        service = ClimateTabletService(self.runtime, MemoryOperationStore(damaged))

        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_load()


if __name__ == "__main__":
    unittest.main()
