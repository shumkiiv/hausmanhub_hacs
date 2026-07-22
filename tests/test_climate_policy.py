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
from custom_components.hausman_hub.domain.climate_demand import ClimateDemandState
from custom_components.hausman_hub.domain.climate_equipment import (
    ClimateDevicePlan,
    ClimateEquipmentAction,
    ClimateEquipmentReason,
    ClimateRoomEquipmentPlan,
    _expected_device_output,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateControlObservation,
    ClimateDataStatus,
    ClimateDelayedIntentState,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateExecutionGuardState,
    ClimateHomeObservation,
    ClimateObservationDeviceKind,
    ClimateOccupancyMode,
    ClimateRoomMode,
    ClimateRoomObservation,
    ClimateSeason,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from custom_components.hausman_hub.domain.climate_policy import (
    CLIMATE_POLICY_PRIORITY,
    ClimateFinalDeviceAction,
    ClimateFinalDeviceReason,
    ClimatePolicyAction,
    ClimatePolicyBlocker,
    ClimatePolicySnapshot,
    ClimatePolicyViolation,
    ClimateRoomPolicy,
    resolve_climate_room_policy,
)
from custom_components.hausman_hub.domain.climate_resolution import (
    ClimateRoomThermalResolution,
    ClimateThermalReason,
    ClimateThermalResolution,
)
from custom_components.hausman_hub.domain.climate_stability import (
    ClimateCycleTiming,
    ClimateCycleTimingReason,
    ClimateRoomStabilityPlan,
    ClimateStableDevicePlan,
    ClimateStabilityAction,
    ClimateStabilityProtection,
    ClimateStabilityReason,
)
from custom_components.hausman_hub.domain.climate_targets import (
    ClimateRoomTarget,
    ClimateTemperatureTargetOrigin,
)
from custom_components.hausman_hub.domain.climate_reference import (
    load_climate_reference_suite,
)
from custom_components.hausman_hub.domain.contours import (
    ClimateProfile,
    ClimateStrategy,
    ContourMode,
)
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

    def test_weather_lockout_blocks_automatic_heating_at_high_threshold(self) -> None:
        result = resolve_climate_room_policy(
            *_weather_inputs(weather_heating_lockout=True),
            observed_at=NOW,
        )

        self.assertIs(result.action, ClimatePolicyAction.OBSERVE)
        self.assertIn(ClimatePolicyBlocker.WEATHER_LOCKOUT, result.blockers)

    def test_weather_lockout_releases_heating_at_low_threshold(self) -> None:
        result = resolve_climate_room_policy(
            *_weather_inputs(weather_heating_lockout=False),
            observed_at=NOW,
        )

        self.assertNotIn(ClimatePolicyBlocker.WEATHER_LOCKOUT, result.blockers)

    def test_weather_lockout_hysteresis_holds_lockout_without_activity(self) -> None:
        result = resolve_climate_room_policy(
            *_weather_inputs(weather_heating_lockout=True),
            observed_at=NOW,
        )

        self.assertIn(ClimatePolicyBlocker.WEATHER_LOCKOUT, result.blockers)

    def test_weather_lockout_hysteresis_keeps_permission_with_active_hydraulic(
        self,
    ) -> None:
        result = resolve_climate_room_policy(
            *_weather_inputs(
                weather_heating_lockout=False,
                radiator_activity=ClimateDeviceActivity.HEATING,
            ),
            observed_at=NOW,
        )

        self.assertNotIn(ClimatePolicyBlocker.WEATHER_LOCKOUT, result.blockers)

    def test_weather_lockout_does_not_affect_cooling(self) -> None:
        result = resolve_climate_room_policy(
            *_weather_inputs(
                weather_heating_lockout=True,
                thermal=ClimateThermalResolution.COOLING,
            ),
            observed_at=NOW,
        )

        self.assertNotIn(ClimatePolicyBlocker.WEATHER_LOCKOUT, result.blockers)

    def test_weather_lockout_bypassed_by_temporary_override(self) -> None:
        result = resolve_climate_room_policy(
            *_weather_inputs(
                weather_heating_lockout=True,
                origin=ClimateTemperatureTargetOrigin.TEMPORARY_OVERRIDE,
            ),
            observed_at=NOW,
        )

        self.assertNotIn(ClimatePolicyBlocker.WEATHER_LOCKOUT, result.blockers)

    def test_weather_lockout_bypassed_by_manual_request(self) -> None:
        result = resolve_climate_room_policy(
            *_weather_inputs(
                weather_heating_lockout=True,
                manual_request=True,
            ),
            observed_at=NOW,
        )

        self.assertIs(result.policy, ClimateRoomPolicy.MANUAL)
        self.assertNotIn(ClimatePolicyBlocker.WEATHER_LOCKOUT, result.blockers)

    def test_weather_lockout_thresholds_are_options(self) -> None:
        result = resolve_climate_room_policy(
            *_weather_inputs(
                weather_heating_lockout=False,
                high=22.0,
                radiator_activity=ClimateDeviceActivity.HEATING,
            ),
            observed_at=NOW,
        )

        self.assertNotIn(ClimatePolicyBlocker.WEATHER_LOCKOUT, result.blockers)

    def test_force_safe_off_bypasses_stability_debounce(self) -> None:
        result = resolve_climate_room_policy(
            *_force_safe_off_inputs(),
            observed_at=NOW,
        )

        self.assertIs(result.policy, ClimateRoomPolicy.SAFETY_LOCKOUT)
        self.assertIs(result.action, ClimatePolicyAction.SAFE_OFF)
        self.assertEqual(("living_ac",), result.safe_stop_device_ids)

    def test_force_safe_off_skips_redundant_turn_off_for_stopped_devices(
        self,
    ) -> None:
        result = resolve_climate_room_policy(
            *_force_safe_off_inputs(ac_activity=ClimateDeviceActivity.STOPPED),
            observed_at=NOW,
        )

        self.assertIs(result.action, ClimatePolicyAction.SAFE_OFF)
        self.assertEqual((), result.safe_stop_device_ids)
        self.assertEqual(
            (ClimateFinalDeviceReason.ALREADY_OFF,),
            tuple(device.reason for device in result.devices),
        )


def _weather_inputs(
    heat_load: float = 20.0,
    *,
    high: float = 18.0,
    low: float = 16.0,
    weather_heating_lockout: bool = True,
    origin: ClimateTemperatureTargetOrigin = ClimateTemperatureTargetOrigin.PROFILE,
    thermal: ClimateThermalResolution = ClimateThermalResolution.HEATING,
    radiator_activity: ClimateDeviceActivity = ClimateDeviceActivity.STOPPED,
    manual_request: bool = False,
):
    room = ClimateRoomObservation(
        room_id="living",
        name="Living",
        data_status=ClimateDataStatus.FRESH,
        temperature=20.0,
        temperature_quality=ClimateTemperatureQuality.NORMAL,
        window=ClimateWindowState.CLOSED,
        mode=ClimateRoomMode.AUTO,
    )
    home = ClimateHomeObservation(
        season=(
            ClimateSeason.WINTER
            if thermal is ClimateThermalResolution.HEATING
            else ClimateSeason.SUMMER
        ),
        heat_load_temperature=heat_load,
        heating_lockout_high=high,
        heating_lockout_low=low,
        central_heating_on=thermal is ClimateThermalResolution.HEATING,
        weather_heating_lockout=weather_heating_lockout,
    )
    control = ClimateControlObservation(
        manual_request=manual_request,
        manual_request_room_id="living" if manual_request else None,
    )
    if thermal is ClimateThermalResolution.HEATING:
        resolution = ClimateRoomThermalResolution(
            room_id="living",
            season=ClimateSeason.WINTER,
            occupancy=ClimateOccupancyMode.HOME,
            central_heating_on=True,
            heating_demand=ClimateDemandState.REQUIRED,
            cooling_demand=ClimateDemandState.NOT_REQUIRED,
            thermal=ClimateThermalResolution.HEATING,
            reason=ClimateThermalReason.HEATING_REQUIRED,
            temperature_origin=origin,
        )
    else:
        resolution = ClimateRoomThermalResolution(
            room_id="living",
            season=ClimateSeason.SUMMER,
            occupancy=ClimateOccupancyMode.HOME,
            central_heating_on=False,
            heating_demand=ClimateDemandState.NOT_REQUIRED,
            cooling_demand=ClimateDemandState.REQUIRED,
            thermal=ClimateThermalResolution.COOLING,
            reason=ClimateThermalReason.COOLING_REQUIRED,
            temperature_origin=origin,
        )
    radiator = ClimateDeviceObservation(
        device_id="living_radiator",
        name="Radiator",
        room_id="living",
        kind=ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
        availability=ClimateDeviceAvailability.AVAILABLE,
        activity=radiator_activity,
    )
    equipment = ClimateRoomEquipmentPlan(
        room_id="living",
        thermal=resolution.thermal,
        devices=(
            ClimateDevicePlan(
                device_id=radiator.device_id,
                room_id=radiator.room_id,
                kind=radiator.kind,
                availability=radiator.availability,
                activity=radiator.activity,
                room_data_status=room.data_status,
                thermal=resolution.thermal,
                season=home.season,
                period=home.period,
                occupancy=home.occupancy,
                central_heating_on=home.central_heating_on,
                central_heating_configured=home.central_heating_configured,
                outdoor_temperature=home.outdoor_temperature,
                heat_load_temperature=home.heat_load_temperature,
                comfort_temperature=22.0,
                strategy=ClimateStrategy.NORMAL,
                observed_at=NOW,
                action=(
                    ClimateEquipmentAction.OBSERVE
                    if home.season is ClimateSeason.WINTER
                    else ClimateEquipmentAction.SAFE_OFF
                ),
                target_temperature=None,
                fan_mode=None,
                quiet=None,
                reason=(
                    ClimateEquipmentReason.PERIOD_UNAVAILABLE
                    if home.season is ClimateSeason.WINTER
                    else ClimateEquipmentReason.CENTRAL_HEATING_OFF
                ),
            ),
        ),
    )
    stability = ClimateRoomStabilityPlan(room_id="living", devices=())
    return room, home, control, resolution, equipment, stability, (radiator,)


def _force_safe_off_inputs(
    *,
    ac_activity: ClimateDeviceActivity = ClimateDeviceActivity.RUNNING,
):
    room = ClimateRoomObservation(
        room_id="living",
        name="Living",
        data_status=ClimateDataStatus.FRESH,
        temperature=30.0,
        temperature_quality=ClimateTemperatureQuality.NORMAL,
        window=ClimateWindowState.OPEN,
        mode=ClimateRoomMode.AUTO,
    )
    home = ClimateHomeObservation(
        season=ClimateSeason.SUMMER,
        central_heating_on=False,
    )
    control = ClimateControlObservation()
    resolution = ClimateRoomThermalResolution(
        room_id="living",
        season=ClimateSeason.SUMMER,
        occupancy=ClimateOccupancyMode.HOME,
        central_heating_on=False,
        heating_demand=ClimateDemandState.NOT_REQUIRED,
        cooling_demand=ClimateDemandState.REQUIRED,
        thermal=ClimateThermalResolution.COOLING,
        reason=ClimateThermalReason.COOLING_REQUIRED,
    )
    ac = ClimateDeviceObservation(
        device_id="living_ac",
        name="AC",
        room_id="living",
        kind=ClimateObservationDeviceKind.AIR_CONDITIONER,
        availability=ClimateDeviceAvailability.AVAILABLE,
        activity=ac_activity,
    )
    action, target, fan, quiet, reason = _expected_device_output(
        kind=ac.kind,
        availability=ac.availability,
        room_data_status=room.data_status,
        thermal=resolution.thermal,
        season=home.season,
        period=home.period,
        occupancy=home.occupancy,
        central_heating_on=home.central_heating_on,
        central_heating_configured=home.central_heating_configured,
        outdoor_temperature=home.outdoor_temperature,
        heat_load_temperature=home.heat_load_temperature,
        comfort_temperature=22.0,
        strategy=ClimateStrategy.NORMAL,
    )
    base = ClimateDevicePlan(
        device_id=ac.device_id,
        room_id=ac.room_id,
        kind=ac.kind,
        availability=ac.availability,
        activity=ac.activity,
        room_data_status=room.data_status,
        thermal=resolution.thermal,
        season=home.season,
        period=home.period,
        occupancy=home.occupancy,
        central_heating_on=home.central_heating_on,
        central_heating_configured=home.central_heating_configured,
        outdoor_temperature=home.outdoor_temperature,
        heat_load_temperature=home.heat_load_temperature,
        comfort_temperature=22.0,
        strategy=ClimateStrategy.NORMAL,
        observed_at=NOW,
        action=action,
        target_temperature=target,
        fan_mode=fan,
        quiet=quiet,
        reason=reason,
    )
    target_object = ClimateRoomTarget(
        room_id="living",
        active_profile=ClimateProfile.DAY,
        profile_temperature=22.0,
        target_temperature=22.0,
        target_humidity=45,
        strategy=ClimateStrategy.NORMAL,
        temperature_origin=ClimateTemperatureTargetOrigin.PROFILE,
        observation_status=room.data_status,
        observation_observed_at=NOW,
    )
    equipment = ClimateRoomEquipmentPlan(
        room_id="living",
        thermal=resolution.thermal,
        devices=(base,),
    )
    stability = ClimateRoomStabilityPlan(
        room_id="living",
        devices=(
            ClimateStableDevicePlan(
                device=ac,
                room=room,
                target=target_object,
                home=home,
                observed_at=NOW,
                base=base,
                cooling_active=True,
                action=ClimateStabilityAction.COOL,
                target_temperature=base.target_temperature,
                fan_mode=base.fan_mode,
                quiet=base.quiet,
                humidity_on_threshold=None,
                humidity_off_threshold=None,
                cycle_timing=ClimateCycleTiming(
                    minimum_run_minutes=8,
                    minimum_off_minutes=6,
                    reason=ClimateCycleTimingReason.DEFAULT,
                ),
                remaining_seconds=None,
                protection=ClimateStabilityProtection.NONE,
                reason=ClimateStabilityReason.COOLING_REQUIRED,
            ),
        ),
    )
    return room, home, control, resolution, equipment, stability, (ac,)


if __name__ == "__main__":
    unittest.main()
