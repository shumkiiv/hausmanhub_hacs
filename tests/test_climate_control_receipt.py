"""Native Home Assistant receipts for all contour-backed climate actions."""

from __future__ import annotations

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
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
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
        ClimateBridgeMode.MANAGED,
        view,
        executor,
        bridge_client=None,
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
            {"request_id": "android-climate-0001", "contour_id": "climate", "confirm": True}
        )
        scheduled_receipt = await schedule_runtime.async_run_climate_schedule(datetime(2026, 7, 19, 23, 0))
        if scheduled_receipt is None:
            self.fail("schedule did not produce a receipt")
        temporary = await temporary_runtime.async_temporary_temperature(
            {
                "request_id": "temporary-living-1", "contour_id": "climate",
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
                fixture = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(fixture, {**payload, "operation_id": fixture["operation_id"]})
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
