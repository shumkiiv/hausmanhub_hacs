"""Tests for manual mode, policy priority, and safe climate stop."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import unittest

from custom_components.hausman_hub.application.climate_demands import (
    build_climate_demand_snapshot,
)
from custom_components.hausman_hub.application.climate_equipment import (
    build_climate_equipment_snapshot,
)
from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
    climate_reference_observation,
)
from custom_components.hausman_hub.application.climate_policy import (
    build_climate_policy_snapshot,
    climate_reference_policy,
)
from custom_components.hausman_hub.application.climate_resolutions import (
    build_climate_resolution_snapshot,
)
from custom_components.hausman_hub.application.climate_stability import (
    build_climate_stability_snapshot,
)
from custom_components.hausman_hub.application.climate_targets import (
    build_climate_target_snapshot,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateControlObservation,
    ClimateDelayedIntentState,
    ClimateExecutionGuardState,
    ClimateRoomMode,
)
from custom_components.hausman_hub.domain.climate_policy import (
    CLIMATE_POLICY_PRIORITY,
    ClimateFinalDeviceAction,
    ClimatePolicyAction,
    ClimatePolicyBlocker,
    ClimatePolicySnapshot,
    ClimatePolicyViolation,
    ClimateRoomPolicy,
    resolve_climate_room_policy,
)
from custom_components.hausman_hub.domain.climate_reference import (
    load_climate_reference_suite,
)
from custom_components.hausman_hub.domain.contours import ContourMode
from tests.test_contours import setup, source_snapshot


NOW = 1_800_000_000_000


def _with_room(plan, room):
    stability = replace(
        plan.stability,
        devices=tuple(
            replace(device, room=room) for device in plan.stability.devices
        ),
    )
    return resolve_climate_room_policy(
        room,
        plan.home,
        plan.control,
        plan.resolution,
        plan.equipment,
        stability,
        plan.selected_devices,
        observed_at=plan.observed_at,
    )


class ClimatePolicyTest(unittest.TestCase):
    def test_priority_ladder_matches_the_working_climate_core(self) -> None:
        self.assertEqual(
            (
                "away",
                "safety_lockout",
                "freshness_guard",
                "forced_auto_only",
                "manual",
                "auto",
                "direct_device_command",
            ),
            CLIMATE_POLICY_PRIORITY,
        )

    def test_all_reference_cases_match_policy_action_and_blockers_exactly(self) -> None:
        cases = load_climate_reference_suite()["cases"]
        results = {
            case["id"]: climate_reference_policy(case["id"])
            for case in cases
        }

        self.assertEqual(30, len(results))
        self.assertEqual(
            results,
            {
                case["id"]: climate_reference_policy(case["id"])
                for case in cases
            },
        )
        for case in cases:
            with self.subTest(case_id=case["id"]):
                actual = results[case["id"]]
                expected = case["expected"]
                self.assertEqual(expected["policy"], actual.policy.value)
                self.assertEqual(expected["action"], actual.action.value)
                self.assertEqual(
                    expected["blockers"],
                    [blocker.value for blocker in actual.blockers],
                )

    def test_manual_mode_and_manual_request_never_retain_an_automatic_plan(self) -> None:
        saved_manual = climate_reference_policy("manual_mode_observes")
        automatic = climate_reference_policy("stopped_ac_starts_at_default_gap")
        request = ClimateControlObservation(
            manual_request=True,
            manual_request_room_id=automatic.room_id,
        )

        requested_manual = resolve_climate_room_policy(
            automatic.room,
            automatic.home,
            request,
            automatic.resolution,
            automatic.equipment,
            automatic.stability,
            automatic.selected_devices,
            observed_at=automatic.observed_at,
        )

        for result in (saved_manual, requested_manual):
            self.assertIs(result.policy, ClimateRoomPolicy.MANUAL)
            self.assertIs(result.action, ClimatePolicyAction.OBSERVE)
            self.assertEqual(
                (ClimatePolicyBlocker.MANUAL_NO_AUTOMATIC_PLAN,),
                result.blockers,
            )
            self.assertEqual((), result.devices)
            self.assertEqual((), result.safe_stop_device_ids)

    def test_forced_automation_rejects_manual_request_but_keeps_plan(self) -> None:
        result = climate_reference_policy("forced_auto_rejects_manual_request")

        self.assertIs(result.policy, ClimateRoomPolicy.FORCED_AUTO_ONLY)
        self.assertIs(result.action, ClimatePolicyAction.COOL)
        self.assertEqual((ClimatePolicyBlocker.FORCED_AUTO_ONLY,), result.blockers)
        self.assertEqual(1, len(result.devices))
        self.assertIs(result.devices[0].action, ClimateFinalDeviceAction.COOL)

    def test_away_has_priority_over_manual_and_can_either_stop_or_keep(self) -> None:
        stop = climate_reference_policy("away_safe_off_overrides_manual")
        keep = climate_reference_policy("away_keep_observes_running_ac")

        self.assertIs(stop.policy, ClimateRoomPolicy.AWAY)
        self.assertIs(stop.action, ClimatePolicyAction.SAFE_OFF)
        self.assertEqual(("reference_air_conditioner",), stop.safe_stop_device_ids)
        self.assertIs(keep.policy, ClimateRoomPolicy.AWAY)
        self.assertIs(keep.action, ClimatePolicyAction.OBSERVE)
        self.assertEqual((), keep.devices)

    def test_safe_stop_targets_running_devices_and_suppresses_redundant_off(self) -> None:
        running_ac = climate_reference_policy("open_window_forces_safe_off")
        stopped_ac = climate_reference_policy("unknown_window_beats_stale_state")
        humidifier = climate_reference_policy("open_window_stops_humidifier")

        self.assertEqual(
            ("reference_air_conditioner",),
            running_ac.safe_stop_device_ids,
        )
        self.assertEqual((), stopped_ac.safe_stop_device_ids)
        self.assertIs(stopped_ac.devices[0].action, ClimateFinalDeviceAction.OFF)
        self.assertEqual(
            ("reference_humidifier",),
            humidifier.safe_stop_device_ids,
        )

    def test_safety_lockout_beats_stale_data_and_keeps_fixed_blocker_order(self) -> None:
        unknown_window = climate_reference_policy("unknown_window_beats_stale_state")
        base = climate_reference_policy("stopped_ac_starts_at_default_gap")
        blocked_room = replace(
            base.room,
            cooling_allowed=False,
            heating_allowed=False,
        )
        permissions = _with_room(base, blocked_room)

        self.assertIs(unknown_window.policy, ClimateRoomPolicy.SAFETY_LOCKOUT)
        self.assertEqual((ClimatePolicyBlocker.WINDOW,), unknown_window.blockers)
        self.assertEqual(
            (
                ClimatePolicyBlocker.COOLING_BLOCKED,
                ClimatePolicyBlocker.HEATING_BLOCKED,
            ),
            permissions.blockers,
        )

    def test_freshness_guard_drops_delayed_work_and_has_no_device_plan(self) -> None:
        for case_id, blocker in (
            ("stale_state_pauses_control", ClimatePolicyBlocker.STALE_STATE),
            (
                "temperature_jump_pauses_control",
                ClimatePolicyBlocker.TEMPERATURE_JUMP,
            ),
            (
                "stale_delayed_intent_is_dropped",
                ClimatePolicyBlocker.STALE_DELAYED_COMMAND,
            ),
        ):
            with self.subTest(case_id=case_id):
                result = climate_reference_policy(case_id)
                self.assertIs(result.policy, ClimateRoomPolicy.FRESHNESS_GUARD)
                self.assertEqual((blocker,), result.blockers)
                self.assertEqual((), result.devices)

    def test_control_observation_requires_exact_room_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "manual request and its room"):
            ClimateControlObservation(manual_request=True)
        with self.assertRaisesRegex(ValueError, "delayed intent and its room"):
            ClimateControlObservation(
                delayed_intent=ClimateDelayedIntentState.STALE_AFTER_CONTROL_CHANGE,
            )
        with self.assertRaisesRegex(ValueError, "execution guard and its room"):
            ClimateControlObservation(
                execution_guard=ClimateExecutionGuardState.DUPLICATE,
            )
        observation = climate_reference_observation(
            "stopped_ac_starts_at_default_gap"
        )
        with self.assertRaisesRegex(ValueError, "observed room"):
            replace(
                observation,
                control=ClimateControlObservation(
                    manual_request=True,
                    manual_request_room_id="other_room",
                ),
            )

    def test_builder_uses_only_selected_devices_and_one_observation(self) -> None:
        registry, contours = setup()
        contour = contours.contour("climate")
        observation = build_climate_observation_snapshot(
            registry,
            source_snapshot(),
            observed_at=NOW,
        )
        targets = build_climate_target_snapshot(contour, observation)  # type: ignore[arg-type]
        demands = build_climate_demand_snapshot(targets, observation)
        resolutions = build_climate_resolution_snapshot(demands, observation)
        equipment = build_climate_equipment_snapshot(
            contour,  # type: ignore[arg-type]
            targets,
            resolutions,
            observation,
        )
        stability = build_climate_stability_snapshot(
            contour,  # type: ignore[arg-type]
            targets,
            equipment,
            observation,
        )

        result = build_climate_policy_snapshot(
            contour,  # type: ignore[arg-type]
            resolutions,
            equipment,
            stability,
            observation,
        )

        self.assertEqual(NOW, result.observed_at)
        self.assertEqual(1, len(result.rooms))
        self.assertFalse(result.commands_enabled)
        serialized = json.dumps(asdict(result), ensure_ascii=False)
        for hidden in (
            "source_id",
            "entity_id",
            "service",
            "endpoint",
            "command",
            "backend_payload",
        ):
            self.assertNotIn(hidden, serialized)

    def test_model_rejects_forged_mutable_and_mixed_policy_results(self) -> None:
        valid = climate_reference_policy("stopped_ac_starts_at_default_gap")
        snapshot = ClimatePolicySnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            observed_at=valid.observed_at,
            rooms=(valid,),
        )

        with self.assertRaises(ClimatePolicyViolation):
            replace(valid, action=ClimatePolicyAction.OFF)
        with self.assertRaises(ClimatePolicyViolation):
            replace(valid, blockers=[ClimatePolicyBlocker.WINDOW])  # type: ignore[arg-type]
        with self.assertRaises(ClimatePolicyViolation):
            replace(valid, devices=())
        with self.assertRaises(ClimatePolicyViolation):
            replace(snapshot, rooms=(valid, valid))
        with self.assertRaises(ClimatePolicyViolation):
            replace(snapshot, observed_at=valid.observed_at + 1)
        with self.assertRaises(ClimatePolicyViolation):
            replace(snapshot, contour_mode=ContourMode.OBSERVE)


if __name__ == "__main__":
    unittest.main()
