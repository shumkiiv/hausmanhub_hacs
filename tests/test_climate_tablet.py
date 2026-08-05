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
    payload["rooms"][0]["devices"][0]["control_scope"] = "managed"
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

    async def async_public_snapshot(self) -> dict[str, object]:
        return copy.deepcopy(self.home)

    async def async_temporary_temperature(
        self, payload: object, now: object
    ) -> object:
        del now
        self.commands.append(copy.deepcopy(payload))
        return SimpleNamespace(
            status=ContourApplyStatus.CONFIRMED,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_home_climate_targets(self, payload: object) -> object:
        self.commands.append(copy.deepcopy(payload))
        return SimpleNamespace(
            status=ContourApplyStatus.CONFIRMED,
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
        self.assertEqual("working", room["devices"][0]["state"])
        self.assertIsNone(room["devices"][0]["cooldown"])
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_stale_projection_remains_visible_and_disables_every_action(self) -> None:
        home = managed_home()
        home["climate"]["fresh"] = False

        payload = climate_tablet_snapshot(home, climate_mode="managed")

        self.assertFalse(payload["commands_enabled"])
        self.assertEqual(["state_stale"], payload["blocked_reasons"])
        self.assertEqual([], payload["home_control"]["allowed_actions"])
        self.assertEqual([], payload["rooms"][0]["control"]["allowed_actions"])
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
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

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
