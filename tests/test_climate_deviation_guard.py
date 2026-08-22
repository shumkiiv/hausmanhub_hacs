"""Tests for bounded restart-safe climate deviation protection."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from custom_components.hausman_hub.application.climate_deviation_guard import (
    ClimateDeviationGuardService,
    ClimateDeviationGuardViolation,
)
from custom_components.hausman_hub.domain.climate import ClimateDeviceKind
from custom_components.hausman_hub.domain.climate_ha_calls import (
    ClimateHaCallPlanSnapshot,
    ClimateHaDeviceCallPlan,
    ClimateHaHvacMode,
    ClimateHaRoomCallPlan,
    ClimateHaService,
    ClimateHaServiceCall,
)
from custom_components.hausman_hub.domain.climate_policy import (
    ClimateFinalDeviceAction,
)
from custom_components.hausman_hub.domain.contours import ContourMode


NOW = 1_800_000_000_000


class MemoryStore:
    def __init__(self) -> None:
        self.payload: object | None = None

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload


class MemoryJournal:
    def __init__(self) -> None:
        self.receipts: list[dict[str, object]] = []

    async def async_append(self, receipt) -> None:
        self.receipts.append(dict(receipt))


def off_plan(observed_at: int) -> ClimateHaCallPlanSnapshot:
    return ClimateHaCallPlanSnapshot(
        contour_id="climate",
        contour_mode=ContourMode.AUTOMATIC,
        observed_at=observed_at,
        rooms=(
            ClimateHaRoomCallPlan(
                room_id="children",
                devices=(
                    ClimateHaDeviceCallPlan(
                        device_id="children_ac",
                        room_id="children",
                        kind=ClimateDeviceKind.AIR_CONDITIONER,
                        action=ClimateFinalDeviceAction.OFF,
                        calls=(
                            ClimateHaServiceCall(
                                ClimateHaService.CLIMATE_SET_HVAC_MODE,
                                "climate.children",
                                hvac_mode=ClimateHaHvacMode.OFF,
                            ),
                        ),
                        limits=(),
                    ),
                ),
            ),
        ),
    )


class ClimateDeviationGuardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = MemoryStore()
        self.journal = MemoryJournal()
        self.service = ClimateDeviationGuardService(
            self.store,
            operation_journal=self.journal,
            now_ms=lambda: NOW,
        )
        await self.service.async_load(("children_ac",))
        self.states = {
            "climate.children": SimpleNamespace(state="off", attributes={})
        }

    async def configure(self, *, mode: str = "enforce", max_retries: int = 2) -> None:
        await self.service.async_replace(
            0,
            {
                "devices": [
                    {
                        "deviceId": "children_ac",
                        "mode": mode,
                        "graceSeconds": 120,
                        "retryCooldownSeconds": 300,
                        "maxRetries": max_retries,
                    }
                ]
            },
            allowed_device_ids=("children_ac",),
        )

    async def evaluate(self, now: int):
        return await self.service.async_evaluate(
            off_plan(now),
            managed_room_ids=("children",),
            state_lookup=self.states.get,
            now_ms=now,
        )

    async def test_first_snapshot_never_arms_or_commands(self) -> None:
        await self.configure()
        self.states["climate.children"] = SimpleNamespace(
            state="cool", attributes={}
        )

        retries = await self.evaluate(NOW + 500_000)

        self.assertEqual((), retries)
        self.assertIsNone(
            self.service.device_status("children_ac", "working")
        )
        self.assertEqual([], self.journal.receipts)

    async def test_confirmed_off_arms_then_grace_and_cooldown_bound_retries(self) -> None:
        await self.configure(max_retries=2)
        await self.service.async_note_off_commands(
            ("children_ac",), commanded_at=NOW
        )
        self.assertEqual((), await self.evaluate(NOW + 1_000))
        self.assertEqual(
            "armed",
            self.service.device_status("children_ac", "off")["status"],
        )

        self.states["climate.children"] = SimpleNamespace(
            state="cool", attributes={}
        )
        self.assertEqual((), await self.evaluate(NOW + 20_000))
        self.assertEqual(
            "grace",
            self.service.device_status("children_ac", "working")["status"],
        )
        first = await self.evaluate(NOW + 141_000)
        self.assertEqual(1, len(first))
        await self.service.async_record_retry(
            "children_ac", attempted_at=NOW + 141_000, accepted=True
        )
        self.assertEqual((), await self.evaluate(NOW + 200_000))
        self.assertEqual(
            "cooldown",
            self.service.device_status("children_ac", "working")["status"],
        )
        second = await self.evaluate(NOW + 442_000)
        self.assertEqual(1, len(second))
        await self.service.async_record_retry(
            "children_ac", attempted_at=NOW + 442_000, accepted=True
        )
        self.assertEqual((), await self.evaluate(NOW + 443_000))
        status = self.service.device_status("children_ac", "working")
        self.assertEqual("escalated", status["status"])
        self.assertEqual(2, status["retry_count"])
        self.assertEqual(
            1,
            sum(
                item.get("error_code") == "climate_deviation_escalated"
                for item in self.journal.receipts
            ),
        )

    async def test_explicit_readback_confirmation_arms_before_next_tick(self) -> None:
        await self.configure()
        await self.service.async_note_off_commands(
            ("children_ac",), commanded_at=NOW
        )

        await self.service.async_confirm_off_commands(
            ("children_ac",), confirmed_at=NOW + 500
        )

        self.assertEqual(
            "armed",
            self.service.device_status("children_ac", "off")["status"],
        )

    async def test_monitor_mode_reports_without_physical_retry(self) -> None:
        await self.configure(mode="monitor")
        await self.service.async_note_off_commands(
            ("children_ac",), commanded_at=NOW
        )
        await self.evaluate(NOW + 1_000)
        self.states["climate.children"] = SimpleNamespace(
            state="cool", attributes={}
        )

        retries = await self.evaluate(NOW + 500_000)

        self.assertEqual((), retries)
        self.assertEqual(
            "observed",
            self.service.device_status("children_ac", "working")["status"],
        )

    async def test_unavailable_feedback_never_retries(self) -> None:
        await self.configure()
        await self.service.async_note_off_commands(
            ("children_ac",), commanded_at=NOW
        )
        await self.evaluate(NOW + 1_000)
        self.states["climate.children"] = SimpleNamespace(
            state="unavailable", attributes={}
        )

        self.assertEqual((), await self.evaluate(NOW + 500_000))
        self.assertEqual(
            "unavailable",
            self.service.device_status("children_ac", "unavailable")[
                "observed_state"
            ],
        )

    async def test_armed_state_survives_restart(self) -> None:
        await self.configure()
        await self.service.async_note_off_commands(
            ("children_ac",), commanded_at=NOW
        )
        await self.evaluate(NOW + 1_000)
        restarted = ClimateDeviationGuardService(
            self.store,
            now_ms=lambda: NOW + 2_000,
        )

        await restarted.async_load(("children_ac",))

        self.assertEqual(
            "armed",
            restarted.device_status("children_ac", "off")["status"],
        )

    async def test_future_dated_restart_state_is_disarmed(self) -> None:
        await self.configure()
        await self.service.async_note_off_commands(
            ("children_ac",), commanded_at=NOW
        )
        await self.service.async_confirm_off_commands(
            ("children_ac",), confirmed_at=NOW
        )
        self.store.payload["updated_at"] = NOW + 60_000
        restarted = ClimateDeviationGuardService(
            self.store,
            now_ms=lambda: NOW,
        )

        await restarted.async_load(("children_ac",))

        self.assertIsNone(restarted.device_status("children_ac", "off"))

    async def test_invalid_duplicate_and_unknown_device_settings_fail_closed(self) -> None:
        policy = {
            "deviceId": "children_ac",
            "mode": "monitor",
            "graceSeconds": 120,
            "retryCooldownSeconds": 300,
            "maxRetries": 3,
        }
        with self.assertRaises(ClimateDeviationGuardViolation):
            await self.service.async_replace(
                0,
                {"devices": [policy, policy]},
                allowed_device_ids=("children_ac",),
            )
        with self.assertRaises(ClimateDeviationGuardViolation):
            await self.service.async_replace(
                0,
                {"devices": [{**policy, "deviceId": "unknown_ac"}]},
                allowed_device_ids=("children_ac",),
            )
