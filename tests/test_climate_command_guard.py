"""Tests for device-level climate diffing and restart-safe suppression."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from custom_components.hausman_hub.application.climate_command_guard import (
    climate_command_guard_from_payload,
    climate_command_guard_to_payload,
    clear_aligned_climate_commands,
    empty_climate_command_guard,
    guard_diverged_climate_calls,
    reserve_guarded_commands,
    reserve_scheduled_synchronization,
)
from custom_components.hausman_hub.domain.climate import ClimateDeviceKind
from custom_components.hausman_hub.domain.climate_command_guard import (
    ClimateCommandGuardViolation,
)
from custom_components.hausman_hub.domain.climate_comparison import (
    ClimateComparisonReason,
    ClimateComparisonSnapshot,
    ClimateComparisonStatus,
    ClimateDeviceComparison,
    ClimateRoomComparison,
)
from custom_components.hausman_hub.domain.climate_ha_calls import (
    ClimateHaCallPlanSnapshot,
    ClimateHaDeviceCallPlan,
    ClimateHaHvacMode,
    ClimateHaRoomCallPlan,
    ClimateHaService,
    ClimateHaServiceCall,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDeviceActivity,
    ClimateFanMode,
    ClimateObservationDeviceKind,
    ClimateRoomMode,
)
from custom_components.hausman_hub.domain.climate_policy import (
    ClimateFinalDeviceAction,
)
from custom_components.hausman_hub.domain.contours import ContourMode


NOW = 1_800_000_000_000


def device_comparison(
    device_id: str,
    *,
    kind: ClimateObservationDeviceKind,
    status: ClimateComparisonStatus,
    action: ClimateFinalDeviceAction,
    activity: ClimateDeviceActivity,
) -> ClimateDeviceComparison:
    return ClimateDeviceComparison(
        device_id=device_id,
        room_id="living",
        kind=kind,
        status=status,
        reasons=(
            ()
            if status is ClimateComparisonStatus.ALIGNED
            else (ClimateComparisonReason.DEVICE_ACTIVITY_MISMATCH,)
        ),
        planned_action=action,
        observed_activity=activity,
    )


class ClimateCommandGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.states = {
            "climate.living": SimpleNamespace(
                state="cool",
                attributes={"temperature": 26, "fan_mode": "low"},
            ),
            "humidifier.living": SimpleNamespace(state="off", attributes={}),
        }
        self.ac_calls = (
            ClimateHaServiceCall(
                ClimateHaService.CLIMATE_SET_HVAC_MODE,
                "climate.living",
                hvac_mode=ClimateHaHvacMode.COOL,
            ),
            ClimateHaServiceCall(
                ClimateHaService.CLIMATE_SET_TEMPERATURE,
                "climate.living",
                temperature=26,
            ),
            ClimateHaServiceCall(
                ClimateHaService.CLIMATE_SET_FAN_MODE,
                "climate.living",
                fan_mode=ClimateFanMode.LOW,
            ),
        )
        self.humidifier_call = ClimateHaServiceCall(
            ClimateHaService.HUMIDIFIER_TURN_ON,
            "humidifier.living",
        )
        self.call_plan = ClimateHaCallPlanSnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            observed_at=NOW,
            rooms=(
                ClimateHaRoomCallPlan(
                    room_id="living",
                    devices=(
                        ClimateHaDeviceCallPlan(
                            device_id="living_ac",
                            room_id="living",
                            kind=ClimateDeviceKind.AIR_CONDITIONER,
                            action=ClimateFinalDeviceAction.COOL,
                            calls=self.ac_calls,
                            limits=(),
                        ),
                        ClimateHaDeviceCallPlan(
                            device_id="living_humidifier",
                            room_id="living",
                            kind=ClimateDeviceKind.HUMIDIFIER,
                            action=ClimateFinalDeviceAction.HUMIDIFY,
                            calls=(self.humidifier_call,),
                            limits=(),
                        ),
                    ),
                ),
            ),
        )

    def comparison(
        self,
        *,
        ac_status: ClimateComparisonStatus = ClimateComparisonStatus.ALIGNED,
    ) -> ClimateComparisonSnapshot:
        return ClimateComparisonSnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            observed_at=NOW,
            rooms=(
                ClimateRoomComparison(
                    room_id="living",
                    status=ClimateComparisonStatus.DIVERGED,
                    reasons=(ClimateComparisonReason.DEVICE_ACTIVITY_MISMATCH,),
                    planned_policy=None,
                    planned_action=None,
                    observed_mode=ClimateRoomMode.AUTO,
                    devices=(
                        device_comparison(
                            "living_ac",
                            kind=ClimateObservationDeviceKind.AIR_CONDITIONER,
                            status=ac_status,
                            action=ClimateFinalDeviceAction.COOL,
                            activity=ClimateDeviceActivity.COOLING,
                        ),
                        device_comparison(
                            "living_humidifier",
                            kind=ClimateObservationDeviceKind.HUMIDIFIER,
                            status=ClimateComparisonStatus.DIVERGED,
                            action=ClimateFinalDeviceAction.HUMIDIFY,
                            activity=ClimateDeviceActivity.STOPPED,
                        ),
                    ),
                ),
            ),
        )

    def test_diverged_humidifier_never_recommands_aligned_air_conditioner(self) -> None:
        guarded = guard_diverged_climate_calls(
            self.call_plan,
            self.comparison(),
            state_lookup=self.states.get,
            memory=empty_climate_command_guard(updated_at=NOW),
        )

        room = guarded.call_plan.room("living")
        self.assertEqual(
            ["living_humidifier"],
            [device.device_id for device in room.devices],  # type: ignore[union-attr]
        )
        self.assertEqual((self.humidifier_call,), guarded.devices[0].calls)

    def test_identical_failed_intent_is_attempted_only_once(self) -> None:
        memory = empty_climate_command_guard(updated_at=NOW)
        first = guard_diverged_climate_calls(
            self.call_plan,
            self.comparison(),
            state_lookup=self.states.get,
            memory=memory,
        )
        memory = reserve_guarded_commands(
            memory,
            first.devices,
            attempted_at=NOW,
        )

        repeated = guard_diverged_climate_calls(
            self.call_plan,
            self.comparison(),
            state_lookup=self.states.get,
            memory=memory,
        )

        self.assertEqual((), repeated.devices)
        self.assertEqual((), repeated.call_plan.room("living").devices)  # type: ignore[union-attr]

    def test_unstable_feedback_cannot_rearm_the_same_desired_state(self) -> None:
        memory = empty_climate_command_guard(updated_at=NOW)
        first = guard_diverged_climate_calls(
            self.call_plan,
            self.comparison(),
            state_lookup=self.states.get,
            memory=memory,
        )
        memory = reserve_guarded_commands(memory, first.devices, attempted_at=NOW)
        self.states["humidifier.living"] = SimpleNamespace(
            state="unavailable",
            attributes={"humidity": 10},
        )

        repeated = guard_diverged_climate_calls(
            self.call_plan,
            self.comparison(),
            state_lookup=self.states.get,
            memory=memory,
        )

        self.assertEqual((), repeated.devices)

    def test_field_diff_omits_temperature_and_fan_that_already_match(self) -> None:
        self.states["climate.living"] = SimpleNamespace(
            state="off",
            attributes={"temperature": 26, "fan_mode": "low"},
        )
        comparison = self.comparison(ac_status=ClimateComparisonStatus.DIVERGED)

        guarded = guard_diverged_climate_calls(
            self.call_plan,
            comparison,
            state_lookup=self.states.get,
            memory=empty_climate_command_guard(updated_at=NOW),
        )

        ac = next(device for device in guarded.devices if device.device_id == "living_ac")
        self.assertEqual(
            (ClimateHaService.CLIMATE_SET_HVAC_MODE,),
            tuple(call.service for call in ac.calls),
        )

    def test_aligned_readback_rearms_and_schedule_slot_latches_once(self) -> None:
        memory = empty_climate_command_guard(updated_at=NOW)
        first = guard_diverged_climate_calls(
            self.call_plan,
            self.comparison(),
            state_lookup=self.states.get,
            memory=memory,
        )
        memory = reserve_guarded_commands(memory, first.devices, attempted_at=NOW)
        aligned_room = ClimateComparisonSnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            observed_at=NOW,
            rooms=(
                ClimateRoomComparison(
                    room_id="living",
                    status=ClimateComparisonStatus.ALIGNED,
                    reasons=(),
                    planned_policy=None,
                    planned_action=None,
                    observed_mode=ClimateRoomMode.AUTO,
                    devices=(
                        device_comparison(
                            "living_humidifier",
                            kind=ClimateObservationDeviceKind.HUMIDIFIER,
                            status=ClimateComparisonStatus.ALIGNED,
                            action=ClimateFinalDeviceAction.HUMIDIFY,
                            activity=ClimateDeviceActivity.HUMIDIFYING,
                        ),
                    ),
                ),
            ),
        )
        memory, changed = clear_aligned_climate_commands(
            memory,
            aligned_room,
            now_ms=NOW + 1,
        )
        self.assertTrue(changed)
        self.assertIsNone(memory.command("living_humidifier"))

        memory, due = reserve_scheduled_synchronization(
            memory,
            slot="2026-08-12T22:00",
            reserved_at=NOW + 2,
        )
        repeated, due_again = reserve_scheduled_synchronization(
            memory,
            slot="2026-08-12T22:00",
            reserved_at=NOW + 3,
        )
        self.assertTrue(due)
        self.assertFalse(due_again)
        self.assertIs(repeated, memory)

    def test_restart_payload_round_trips_and_rejects_extra_fields(self) -> None:
        memory = empty_climate_command_guard(updated_at=NOW)
        guarded = guard_diverged_climate_calls(
            self.call_plan,
            self.comparison(),
            state_lookup=self.states.get,
            memory=memory,
        )
        memory = reserve_guarded_commands(memory, guarded.devices, attempted_at=NOW)
        memory, _ = reserve_scheduled_synchronization(
            memory,
            slot="2026-08-12T10:00",
            reserved_at=NOW,
        )
        payload = climate_command_guard_to_payload(memory)

        self.assertEqual(memory, climate_command_guard_from_payload(payload))

        malformed = dict(payload)
        malformed["entity_id"] = "climate.living"
        with self.assertRaises(ClimateCommandGuardViolation):
            climate_command_guard_from_payload(malformed)


if __name__ == "__main__":
    unittest.main()
