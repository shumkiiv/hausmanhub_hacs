"""Native Home Assistant receipts for all contour-backed climate actions."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.climate_ha_observations import ClimateHaEntityState
from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.application.contour_apply import (
    ClimateControlAction,
    ClimateControlContext,
    ContourApplyReceipt,
    ContourApplyStatus,
    ContourApplyViolation,
)
from custom_components.hausman_hub.application.contours import (
    with_applied_climate_schedule_profile,
    with_climate_schedule,
    with_climate_temporary_temperature,
)
from custom_components.hausman_hub.domain.climate import ClimateRegistry
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from custom_components.hausman_hub.domain.contours import ClimateProfile, ClimateStrategy, ContourRegistry
from tests import test_climate_native_runtime as native


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1" / "climate-control-receipt.schema.json"
FIXTURES = ROOT / "fixtures" / "hausmanhub_climate_control_receipt_v1"
COUNT_KEYS = ("room_count", "command_count", "accepted_count", "confirmed_room_count")
PRIVATE_VALUES = (
    "entity_id", "source_id", "climate.living_ac", "synthetic-ac-source-living",
    "service", "set_hvac_mode", "calls", "backend_payload", "127.0.0.1", "http://",
)


def scheduled_contours() -> ContourRegistry:
    contours = native.native_contours()
    contour = contours.contour("climate")
    if contour is None:
        raise AssertionError("native climate contour is unavailable")
    room = contour.rooms[0]
    night = replace(room.night_profile, target_temperature=22.0, strategy=ClimateStrategy.SOFT)
    profiled = replace(
        contours,
        contours=(replace(contour, rooms=(replace(room, night_profile=night),)),),
    )
    scheduled = with_climate_schedule(
        profiled, enabled=True, day_start="07:00", night_start="23:00"
    )
    return with_applied_climate_schedule_profile(scheduled, ClimateProfile.DAY)


def status_runtime(
    states: dict[str, ClimateHaEntityState],
    execution: tuple[bool, int | None, bool] = (True, None, False),
    setup: tuple[ClimateRegistry | None, ContourRegistry | None] = (None, None),
) -> tuple[ClimateRuntime, native.MutableStateView]:
    view = native.MutableStateView(states)
    executor = native.ReflectingStrictExecutor(
        view,
        reflect_on_execute=execution[0],
        completed_count=execution[1],
        break_view_after_execute=execution[2],
    )
    runtime = native.native_application_runtime(
        ClimateControlMode.MANAGED,
        view,
        executor,
        registry=setup[0],
        contours=setup[1],
    )
    return runtime, view


async def apply_native(runtime: ClimateRuntime, request_id: str) -> ContourApplyReceipt:
    await runtime.async_start()
    return await runtime.async_apply_contour(
        {"request_id": request_id, "contour_id": "climate", "confirm": True}
    )


def receipt_summary(
    receipt: ContourApplyReceipt,
) -> tuple[ContourApplyStatus, int, int, int, tuple[str, ...]]:
    return (
        receipt.status,
        receipt.command_count,
        receipt.accepted_count,
        receipt.confirmed_room_count,
        receipt.reasons,
    )


class ClimateControlReceiptTest(unittest.IsolatedAsyncioTestCase):
    """The native application path keeps the public receipt v1 contract."""

    async def test_four_native_actions_match_fixtures_and_stay_redacted(self) -> None:
        scheduled = scheduled_contours()
        overridden = with_climate_temporary_temperature(
            scheduled, room_id="living", target_temperature=23.5
        )
        apply_runtime, _ = status_runtime(native.safe_stop_states(), setup=(None, native.native_contours()))
        schedule_runtime, _ = status_runtime(native.safe_stop_states(), setup=(None, scheduled))
        temporary_runtime, _ = status_runtime(native.safe_stop_states(), setup=(None, scheduled))
        return_runtime, _ = status_runtime(native.safe_stop_states(), setup=(None, overridden))
        for runtime in (apply_runtime, schedule_runtime, temporary_runtime, return_runtime):
            await runtime.async_start()

        applied = await apply_runtime.async_apply_contour(
            {
                "request_id": "android-climate-0001",
                "correlation_id": "corr.android-climate-0001",
                "contour_id": "climate",
                "confirm": True,
            }
        )
        scheduled_receipt = await schedule_runtime.async_run_climate_schedule(datetime(2026, 7, 19, 23, 0))
        if scheduled_receipt is None:
            self.fail("schedule did not produce a receipt")
        temporary = await temporary_runtime.async_temporary_temperature(
            {
                "request_id": "temporary-living-1",
                "correlation_id": "corr.tablet-temp-001",
                "contour_id": "climate",
                "room_id": "living", "action": "set", "target_temperature": 23.5,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        restored = await return_runtime.async_temporary_temperature(
            {
                "request_id": "temporary-living-clear-1", "contour_id": "climate",
                "room_id": "living", "action": "clear", "target_temperature": None,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        payloads = {
            "apply": applied.as_payload(),
            "schedule": scheduled_receipt.as_payload(),
            "temporary": temporary.as_payload(),
            "return": restored.as_payload(),
        }
        expected_changes = {
            "apply": {"temperature": 0, "strategy": 0, "automatic_mode": 0},
            "schedule": {"temperature": 1, "strategy": 1, "automatic_mode": 0},
            "temporary": {"temperature": 1, "strategy": 0, "automatic_mode": 0},
            "return": {"temperature": 1, "strategy": 0, "automatic_mode": 0},
        }
        validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
        for name, payload in payloads.items():
            with self.subTest(action=name):
                validator.validate(payload)
                # These requests deliberately omit the negotiated reliability
                # profile. Their v1 aggregate receipt must stay compatible even
                # while the enhanced fixtures exercise the opt-in surface.
                self.assertNotIn("action_snapshot", payload)
                self.assertNotIn("expected_control_revision", payload)
                self.assertEqual("confirmed", payload["status"])
                self.assertEqual((1, 1, 1, 1), tuple(payload[key] for key in COUNT_KEYS))
                self.assertEqual(expected_changes[name], payload["changes"])
                self.assertEqual([], payload["reasons"])

        serialized = json.dumps(payloads, ensure_ascii=True, sort_keys=True)
        for private_value in PRIVATE_VALUES:
            with self.subTest(private_value=private_value):
                self.assertNotIn(private_value, serialized)

    async def test_native_statuses_reasons_and_counts_are_honest(self) -> None:
        aligned_states = native.safe_stop_states()
        aligned_ac = aligned_states["climate.living_ac"]
        aligned_states[aligned_ac.entity_id] = replace(aligned_ac, state="off")
        aligned_runtime, _ = status_runtime(aligned_states)
        aligned = await apply_native(aligned_runtime, "aligned")
        self.assertEqual(
            (ContourApplyStatus.CONFIRMED, 0, 0, 1, ("already_in_sync",)),
            receipt_summary(aligned),
        )

        pending_runtime, _ = status_runtime(native.safe_stop_states(), (False, None, False))
        await pending_runtime.async_start()
        with patch(
            "custom_components.hausman_hub.application.climate_runtime.asyncio.sleep",
            new=AsyncMock(),
        ):
            pending = await pending_runtime.async_apply_contour(
                {"request_id": "pending", "contour_id": "climate", "confirm": True}
            )
        self.assertEqual(
            (ContourApplyStatus.PENDING, 1, 1, 0, ("state_not_confirmed",)),
            receipt_summary(pending),
        )

        broken_runtime, _ = status_runtime(native.safe_stop_states(), (True, None, True))
        verification_unavailable = await apply_native(broken_runtime, "broken-verification")
        self.assertEqual(
            (ContourApplyStatus.PENDING, 1, 1, 0, ("verification_unavailable",)),
            receipt_summary(verification_unavailable),
        )

        partial_runtime, _ = status_runtime(
            native.two_actuator_states(),
            (True, 1, False),
            (native.two_actuator_registry(), native.two_actuator_contours()),
        )
        partial = await apply_native(partial_runtime, "partial")
        self.assertEqual(
            (ContourApplyStatus.PARTIAL, 2, 1, 0, ("command_result_unavailable",)),
            receipt_summary(partial),
        )

        unavailable_runtime, _ = status_runtime(native.safe_stop_states(), (True, 0, False))
        unavailable = await apply_native(unavailable_runtime, "unavailable")
        self.assertEqual(
            (ContourApplyStatus.UNAVAILABLE, 1, 0, 0, ("command_result_unavailable",)),
            receipt_summary(unavailable),
        )

        denied_runtime, denied_view = status_runtime(native.safe_stop_states())
        await denied_runtime.async_start()
        denied_view.broken = True
        denied = await denied_runtime.async_apply_contour(
            {"request_id": "denied", "contour_id": "climate", "confirm": True}
        )
        self.assertEqual(
            (ContourApplyStatus.UNAVAILABLE, 0, 0, 0, ("engine_rejected",)),
            receipt_summary(denied),
        )
        receipts = (aligned, pending, verification_unavailable, partial, unavailable, denied)
        for receipt in receipts:
            changes = (
                receipt.temperature_changes,
                receipt.strategy_changes,
                receipt.automatic_mode_changes,
            )
            self.assertEqual((0, 0, 0), changes)

    async def test_opt_in_direct_apply_returns_pollable_enhanced_receipt(self) -> None:
        runtime, _ = status_runtime(native.safe_stop_states())
        await runtime.async_start()
        receipt = await runtime.async_apply_contour({
            "request_id": "reliable-apply-1", "contour_id": "climate", "confirm": True,
            "reliability_profile": "climate_reliability_v1", "expected_control_revision": 0,
        })
        payload = receipt.as_payload()
        Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(payload)
        self.assertEqual(1, payload["resulting_control_revision"])
        polled = await runtime.async_control_operation(payload["operation_id"])
        self.assertEqual(payload, polled)

    async def test_enhanced_direct_request_id_cannot_downgrade_to_legacy(self) -> None:
        runtime, _ = status_runtime(native.safe_stop_states())
        await runtime.async_start()
        enhanced = {"request_id": "reliable-downgrade-1", "contour_id": "climate", "confirm": True,
                    "reliability_profile": "climate_reliability_v1", "expected_control_revision": 0}
        await runtime.async_apply_contour(enhanced)
        with self.assertRaisesRegex(ContourApplyViolation, "request id conflicts"):
            await runtime.async_apply_contour({"request_id": "reliable-downgrade-1", "contour_id": "climate", "confirm": True})

    async def test_identical_enhanced_retry_precedes_stale_revision_check(self) -> None:
        runtime, _ = status_runtime(native.safe_stop_states())
        await runtime.async_start()
        request = {"request_id": "reliable-duplicate-1", "contour_id": "climate", "confirm": True,
                   "reliability_profile": "climate_reliability_v1", "expected_control_revision": 0}
        first = await runtime.async_apply_contour(request)
        duplicate = await runtime.async_apply_contour(request)
        self.assertEqual(first.as_payload(), duplicate.as_payload())

    async def test_enhanced_already_in_sync_is_confirmed_with_zero_calls(self) -> None:
        states = native.safe_stop_states()
        ac = states["climate.living_ac"]
        states[ac.entity_id] = replace(ac, state="off")
        runtime, _ = status_runtime(states)
        await runtime.async_start()
        receipt = await runtime.async_apply_contour({
            "request_id": "reliable-in-sync-1", "contour_id": "climate", "confirm": True,
            "reliability_profile": "climate_reliability_v1", "expected_control_revision": 0,
        })
        payload = receipt.as_payload()
        self.assertEqual("confirmed", payload["status"])
        self.assertEqual((0, 0), (payload["command_count"], payload["accepted_count"]))
        self.assertIn("already_in_sync", payload["reasons"])

    async def test_same_target_with_stale_device_proof_is_terminal_without_dispatch(self) -> None:
        class DirectStore:
            records: object | None = None

            async def async_load_direct_control(self):
                return self.records

            async def async_save_direct_control(self, records):
                self.records = records

        states = native.safe_stop_states()
        ac = states["climate.living_ac"]
        states[ac.entity_id] = replace(
            ac,
            attributes={**ac.attributes, "temperature": 24.0},
            last_updated_ms=native.NOW - 10_000_000,
        )
        store = DirectStore()
        view = native.MutableStateView(states)
        executor = native.ReflectingStrictExecutor(view)
        runtime = native.native_application_runtime(
            ClimateControlMode.MANAGED,
            view,
            executor,
            direct_control_store=store,
        )
        await runtime.async_start()
        request = {
            "request_id": "reliable-stale-same-target-1",
            "contour_id": "climate",
            "room_id": "living",
            "action": "set",
            "target_temperature": 24.0,
            "confirm": True,
            "reliability_profile": "climate_reliability_v1",
            "expected_control_revision": 0,
        }
        receipt = await runtime.async_temporary_temperature(
            request, datetime(2026, 7, 19, 12, 0)
        )
        payload = receipt.as_payload()
        leaf = payload["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertEqual("rejected", payload["status"])
        self.assertTrue(payload["final"])
        self.assertEqual("not_attempted", payload["outcomes"]["rooms"]["living"]["status"])
        self.assertEqual("blocked_before_dispatch", leaf["execution_state"])
        self.assertEqual((0, 0), (leaf["command_count"], leaf["accepted_count"]))
        self.assertEqual([], executor.calls)
        Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(payload)

        replay = await runtime.async_temporary_temperature(
            request, datetime(2026, 7, 19, 12, 0)
        )
        self.assertEqual(payload, replay.as_payload())
        self.assertEqual([], executor.calls)

        restarted_view = native.MutableStateView(states)
        restarted_executor = native.ReflectingStrictExecutor(restarted_view)
        restarted = native.native_application_runtime(
            ClimateControlMode.MANAGED,
            restarted_view,
            restarted_executor,
            direct_control_store=store,
        )
        await restarted.async_start()
        self.assertEqual(payload, await restarted.async_control_operation(payload["operation_id"]))
        restored_replay = await restarted.async_temporary_temperature(
            request, datetime(2026, 7, 19, 12, 0)
        )
        self.assertEqual(payload, restored_replay.as_payload())
        self.assertEqual([], restarted_executor.calls)

        fresh = states["climate.living_ac"]
        states[fresh.entity_id] = replace(fresh, last_updated_ms=native.NOW)
        successor = await restarted.async_temporary_temperature(
            {**request, "request_id": "reliable-fresh-same-target-2", "expected_control_revision": 1},
            datetime(2026, 7, 19, 12, 0),
        )
        successor_payload = successor.as_payload()
        self.assertEqual("confirmed", successor_payload["status"])
        self.assertEqual("already_in_sync", successor_payload["outcomes"]["rooms"]["living"]["devices"]["living_ac"]["execution_state"])
        self.assertEqual([], restarted_executor.calls)

    async def test_direct_receipt_survives_restart_and_replay_does_not_dispatch(self) -> None:
        class DirectStore:
            records: object | None = None
            async def async_load_direct_control(self):
                return self.records
            async def async_save_direct_control(self, records):
                self.records = records

        store = DirectStore()
        states = native.safe_stop_states()
        first, _ = status_runtime(states)
        # The native helper has no store argument, therefore construct the
        # restart-aware runtime through the shared fixture directly.
        view = native.MutableStateView(states)
        executor = native.ReflectingStrictExecutor(view)
        first = native.native_application_runtime(ClimateControlMode.MANAGED, view, executor,
            direct_control_store=store)
        await first.async_start()
        request = {"request_id": "restart-direct-1", "contour_id": "climate", "confirm": True,
                   "reliability_profile": "climate_reliability_v1", "expected_control_revision": 0}
        original = (await first.async_apply_contour(request)).as_payload()
        restarted_view = native.MutableStateView(states)
        restarted_executor = native.ReflectingStrictExecutor(restarted_view)
        restarted = native.native_application_runtime(ClimateControlMode.MANAGED, restarted_view,
            restarted_executor, direct_control_store=store)
        await restarted.async_start()
        self.assertEqual(original, await restarted.async_control_operation(original["operation_id"]))
        replay = await restarted.async_apply_contour(request)
        self.assertEqual(original, replay.as_payload())
        self.assertEqual([], restarted_executor.calls)

    async def test_opt_in_temporary_clear_keeps_resulting_schedule_target(self) -> None:
        contours = with_climate_temporary_temperature(
            scheduled_contours(), room_id="living", target_temperature=23.5
        )
        runtime, _ = status_runtime(native.safe_stop_states(), setup=(None, contours))
        await runtime.async_start()
        receipt = await runtime.async_temporary_temperature({
            "request_id": "reliable-clear-1", "contour_id": "climate", "room_id": "living",
            "action": "clear", "target_temperature": None, "confirm": True,
            "reliability_profile": "climate_reliability_v1", "expected_control_revision": 0,
        }, datetime(2026, 7, 19, 12, 0))
        payload = receipt.as_payload()
        Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(payload)
        self.assertIsNone(payload["action"]["target_temperature"])
        self.assertEqual(24.0, payload["action"]["resulting_target_temperature"])

    async def test_shared_revision_allows_exactly_one_concurrent_direct_request(self) -> None:
        class SharedStore:
            def __init__(self) -> None:
                self.records: object | None = None
                self.revision = 0
                self.lock = asyncio.Lock()

            async def async_load_direct_control(self):
                return self.records

            async def async_save_direct_control(self, records):
                self.records = records

            async def async_current_control_revision(self):
                async with self.lock:
                    return self.revision

            async def async_reserve_control_revision(self, expected):
                async with self.lock:
                    if expected != self.revision:
                        raise ValueError("stale")
                    self.revision += 1
                    return self.revision

        store = SharedStore()
        first_view = native.MutableStateView(native.safe_stop_states())
        second_view = native.MutableStateView(native.safe_stop_states())
        first = native.native_application_runtime(
            ClimateControlMode.MANAGED, first_view,
            native.ReflectingStrictExecutor(first_view), direct_control_store=store,
        )
        second = native.native_application_runtime(
            ClimateControlMode.MANAGED, second_view,
            native.ReflectingStrictExecutor(second_view), direct_control_store=store,
        )
        await asyncio.gather(first.async_start(), second.async_start())
        request = {"contour_id": "climate", "confirm": True,
                   "reliability_profile": "climate_reliability_v1",
                   "expected_control_revision": 0}
        outcomes = await asyncio.gather(
            first.async_apply_contour({**request, "request_id": "shared-direct-1"}),
            second.async_apply_contour({**request, "request_id": "shared-direct-2"}),
            return_exceptions=True,
        )
        accepted = [item for item in outcomes if isinstance(item, ContourApplyReceipt)]
        rejected = [item for item in outcomes if isinstance(item, Exception)]
        self.assertEqual(1, len(accepted))
        self.assertEqual(1, len(rejected))
        self.assertEqual(1, store.revision)
        # Any snapshot uses the shared durable source rather than the stale
        # per-runtime cache left in the losing instance.
        await second.async_public_snapshot()
        self.assertEqual(1, second._control_revision)

    def test_action_context_rejects_mixed_or_incomplete_scope(self) -> None:
        with self.assertRaises(ContourApplyViolation):
            ClimateControlContext(action=ClimateControlAction.APPLY_SAVED_SETTINGS, room_id="living")
        with self.assertRaises(ContourApplyViolation):
            ClimateControlContext(action=ClimateControlAction.APPLY_SCHEDULE_PROFILE)
        with self.assertRaises(ContourApplyViolation):
            ClimateControlContext(
                action=ClimateControlAction.SET_TEMPORARY_TEMPERATURE,
                room_id="living",
                target_temperature=None,
            )


if __name__ == "__main__":
    unittest.main()
