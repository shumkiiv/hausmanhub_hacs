"""Pure tests for climate hysteresis and short-cycle protection."""

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
)
from custom_components.hausman_hub.application.climate_resolutions import (
    build_climate_resolution_snapshot,
)
from custom_components.hausman_hub.application.climate_stability import (
    build_climate_stability_snapshot,
    climate_reference_stability,
)
from custom_components.hausman_hub.application.climate_targets import (
    build_climate_target_snapshot,
)
from custom_components.hausman_hub.domain.climate_demand import (
    resolve_climate_room_demand,
)
from custom_components.hausman_hub.domain.climate_equipment import (
    resolve_climate_device_plan,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
    ClimateDayPeriod,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateFanMode,
    ClimateHomeObservation,
    ClimateObservationDeviceKind,
    ClimatePhysicalFeedback,
    ClimateRoomObservation,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from custom_components.hausman_hub.domain.climate_reference import (
    load_climate_reference_suite,
)
from custom_components.hausman_hub.domain.climate_resolution import (
    resolve_climate_room_thermal,
)
from custom_components.hausman_hub.domain.climate_stability import (
    ClimateCycleTimingReason,
    ClimateRoomStabilityPlan,
    ClimateStabilityAction,
    ClimateStabilityProtection,
    ClimateStabilityReason,
    ClimateStabilitySnapshot,
    ClimateStabilityViolation,
    climate_cycle_timing,
    resolve_climate_stability_plan,
)
from custom_components.hausman_hub.domain.climate_targets import (
    ClimateRoomTarget,
    ClimateTemperatureTargetOrigin,
)
from custom_components.hausman_hub.domain.contours import (
    ClimateProfile,
    ClimateStrategy,
    ContourMode,
)
from tests.test_contours import setup, source_snapshot


NOW = 1_800_000_000_000


def target(
    *,
    temperature: float = 25.0,
    humidity: int = 45,
    strategy: ClimateStrategy = ClimateStrategy.NORMAL,
) -> ClimateRoomTarget:
    return ClimateRoomTarget(
        room_id="living",
        active_profile=ClimateProfile.DAY,
        profile_temperature=temperature,
        target_temperature=temperature,
        target_humidity=humidity,
        strategy=strategy,
        temperature_origin=ClimateTemperatureTargetOrigin.PROFILE,
        observation_status=ClimateDataStatus.FRESH,
        observation_observed_at=NOW,
    )


def room(
    *,
    temperature: float = 25.0,
    humidity: float | None = 45.0,
    window: ClimateWindowState = ClimateWindowState.CLOSED,
    hard_off_temperature: float | None = None,
) -> ClimateRoomObservation:
    return ClimateRoomObservation(
        room_id="living",
        name="Гостиная",
        data_status=ClimateDataStatus.FRESH,
        temperature=temperature,
        humidity=humidity,
        temperature_quality=ClimateTemperatureQuality.NORMAL,
        window=window,
        hard_off_temperature=hard_off_temperature,
    )


def device(
    kind: ClimateObservationDeviceKind,
    *,
    activity: ClimateDeviceActivity,
    last_started_at: int | None = None,
    last_stopped_at: int | None = None,
    cooling_evidence_confirmed: bool = False,
    cooling_rate_per_hour: float | None = None,
    confirmed_short_cycle_count: int | None = None,
    current_target_temperature: float | None = None,
    fan_mode: ClimateFanMode | None = None,
    physical_feedback: ClimatePhysicalFeedback = ClimatePhysicalFeedback.UNKNOWN,
) -> ClimateDeviceObservation:
    return ClimateDeviceObservation(
        device_id=f"living_{kind.value}",
        name="Климатическое устройство",
        room_id="living",
        kind=kind,
        availability=ClimateDeviceAvailability.AVAILABLE,
        activity=activity,
        current_target_temperature=current_target_temperature,
        fan_mode=fan_mode,
        physical_feedback=physical_feedback,
        last_started_at=last_started_at,
        last_stopped_at=last_stopped_at,
        cooling_evidence_confirmed=cooling_evidence_confirmed,
        cooling_rate_per_hour=cooling_rate_per_hour,
        confirmed_short_cycle_count=confirmed_short_cycle_count,
    )


def stable(
    observed_device: ClimateDeviceObservation,
    observed_room: ClimateRoomObservation,
    *,
    selected_target: ClimateRoomTarget | None = None,
    home: ClimateHomeObservation | None = None,
    cooling_active: bool = False,
):
    selected_target = selected_target or target()
    observed_home = home or ClimateHomeObservation()
    base = None
    if observed_device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER:
        demand = resolve_climate_room_demand(selected_target, observed_room)
        resolution = resolve_climate_room_thermal(demand, observed_home)
        base = resolve_climate_device_plan(
            observed_device,
            selected_target,
            resolution,
            observed_home,
        )
    return resolve_climate_stability_plan(
        observed_device,
        observed_room,
        selected_target,
        observed_home,
        observed_at=NOW,
        base=base,
        cooling_active=cooling_active,
    )


class ClimateStabilityTest(unittest.TestCase):
    def test_stopped_air_conditioner_uses_exact_start_boundary(self) -> None:
        stopped = device(
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            activity=ClimateDeviceActivity.STOPPED,
        )

        starts = stable(stopped, room(temperature=25.7))
        waits = stable(stopped, room(temperature=25.699))

        self.assertIs(starts.action, ClimateStabilityAction.COOL)
        self.assertEqual(26.0, starts.target_temperature)
        self.assertIs(starts.fan_mode, ClimateFanMode.LOW)
        self.assertIs(waits.action, ClimateStabilityAction.OFF)

    def test_running_air_conditioner_maintains_and_softens(self) -> None:
        running = device(
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            activity=ClimateDeviceActivity.RUNNING,
        )

        maintains = stable(
            running,
            room(temperature=25.2),
            selected_target=target(temperature=25.0),
        )
        softens = stable(
            running,
            room(temperature=24.8),
            selected_target=target(temperature=25.0),
        )

        self.assertIs(maintains.action, ClimateStabilityAction.MAINTAIN)
        self.assertEqual(26.0, maintains.target_temperature)
        self.assertIs(
            maintains.protection,
            ClimateStabilityProtection.COOLING_HYSTERESIS,
        )
        self.assertIs(softens.action, ClimateStabilityAction.MAINTAIN)
        self.assertEqual(27.0, softens.target_temperature)
        self.assertIs(
            softens.reason,
            ClimateStabilityReason.SOFTEN_BEFORE_STOP,
        )

    def test_minimum_run_and_off_windows_end_on_exact_boundary(self) -> None:
        slow_running = device(
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            activity=ClimateDeviceActivity.RUNNING,
            last_started_at=NOW - 9 * 60_000,
            cooling_evidence_confirmed=True,
            cooling_rate_per_hour=0.3,
        )
        held = stable(
            slow_running,
            room(temperature=24.8),
            selected_target=target(temperature=25.5),
        )
        released = stable(
            replace(slow_running, last_started_at=NOW - 10 * 60_000),
            room(temperature=24.8),
            selected_target=target(temperature=25.5),
        )
        stopped = device(
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            activity=ClimateDeviceActivity.STOPPED,
            last_stopped_at=NOW - 5 * 60_000,
        )
        paused = stable(stopped, room(temperature=26.0))
        restarted = stable(
            replace(stopped, last_stopped_at=NOW - 6 * 60_000),
            room(temperature=26.0),
        )

        self.assertIs(held.reason, ClimateStabilityReason.MINIMUM_RUN_HOLD)
        self.assertEqual(60, held.remaining_seconds)
        self.assertIs(released.reason, ClimateStabilityReason.SOFTEN_BEFORE_STOP)
        self.assertIs(paused.reason, ClimateStabilityReason.MINIMUM_OFF_PAUSE)
        self.assertEqual(60, paused.remaining_seconds)
        self.assertIs(restarted.action, ClimateStabilityAction.COOL)

    def test_cycle_timing_adapts_to_rate_strategy_and_short_cycles(self) -> None:
        cases = (
            (
                device(
                    ClimateObservationDeviceKind.AIR_CONDITIONER,
                    activity=ClimateDeviceActivity.RUNNING,
                ),
                target(),
                (8, 6, ClimateCycleTimingReason.DEFAULT),
            ),
            (
                device(
                    ClimateObservationDeviceKind.AIR_CONDITIONER,
                    activity=ClimateDeviceActivity.RUNNING,
                    cooling_evidence_confirmed=True,
                    cooling_rate_per_hour=1.2,
                ),
                target(),
                (5, 8, ClimateCycleTimingReason.CONFIRMED_FAST_COOLING),
            ),
            (
                device(
                    ClimateObservationDeviceKind.AIR_CONDITIONER,
                    activity=ClimateDeviceActivity.RUNNING,
                    cooling_evidence_confirmed=True,
                    cooling_rate_per_hour=0.35,
                ),
                target(),
                (10, 5, ClimateCycleTimingReason.CONFIRMED_SLOW_COOLING),
            ),
            (
                device(
                    ClimateObservationDeviceKind.AIR_CONDITIONER,
                    activity=ClimateDeviceActivity.RUNNING,
                    confirmed_short_cycle_count=2,
                ),
                target(),
                (8, 8, ClimateCycleTimingReason.CONFIRMED_SHORT_CYCLES),
            ),
            (
                device(
                    ClimateObservationDeviceKind.AIR_CONDITIONER,
                    activity=ClimateDeviceActivity.RUNNING,
                    cooling_evidence_confirmed=True,
                    cooling_rate_per_hour=0.2,
                ),
                target(strategy=ClimateStrategy.AGGRESSIVE),
                (8, 6, ClimateCycleTimingReason.AGGRESSIVE_DEFAULT),
            ),
            (
                device(
                    ClimateObservationDeviceKind.AIR_CONDITIONER,
                    activity=ClimateDeviceActivity.RUNNING,
                    confirmed_short_cycle_count=1,
                ),
                target(strategy=ClimateStrategy.AGGRESSIVE),
                (
                    8,
                    7,
                    ClimateCycleTimingReason.AGGRESSIVE_DEFAULT_AND_SHORT_CYCLES,
                ),
            ),
        )

        for observed_device, selected_target, expected in cases:
            with self.subTest(expected=expected):
                timing = climate_cycle_timing(observed_device, selected_target)
                self.assertEqual(expected[0], timing.minimum_run_minutes)
                self.assertEqual(expected[1], timing.minimum_off_minutes)
                self.assertIs(expected[2], timing.reason)

    def test_hard_off_overrides_minimum_run(self) -> None:
        running = device(
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            activity=ClimateDeviceActivity.RUNNING,
            last_started_at=NOW - 60_000,
        )

        result = stable(
            running,
            room(temperature=25.4, hard_off_temperature=25.4),
            selected_target=target(temperature=25.5),
        )

        self.assertIs(result.action, ClimateStabilityAction.OFF)
        self.assertIs(result.reason, ClimateStabilityReason.HARD_OFF_THRESHOLD)
        self.assertIsNone(result.remaining_seconds)

    def test_confirmed_weak_cooling_escalates_in_two_stable_steps(self) -> None:
        common = device(
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            activity=ClimateDeviceActivity.RUNNING,
            last_started_at=NOW - 11 * 60_000,
            cooling_evidence_confirmed=True,
            cooling_rate_per_hour=0.2,
            current_target_temperature=26,
            fan_mode=ClimateFanMode.LOW,
            physical_feedback=ClimatePhysicalFeedback.CONFIRMED,
        )

        raises_fan = stable(common, room(temperature=25.8))
        lowers_target = stable(
            replace(common, fan_mode=ClimateFanMode.MEDIUM),
            room(temperature=25.8),
        )
        stale_feedback = stable(
            replace(common, physical_feedback=ClimatePhysicalFeedback.STALE),
            room(temperature=25.8),
        )

        self.assertIs(raises_fan.action, ClimateStabilityAction.MAINTAIN)
        self.assertEqual(26.0, raises_fan.target_temperature)
        self.assertIs(raises_fan.fan_mode, ClimateFanMode.MEDIUM)
        self.assertIs(
            raises_fan.reason,
            ClimateStabilityReason.WEAK_COOLING_RAISE_FAN,
        )
        self.assertEqual(25.0, lowers_target.target_temperature)
        self.assertIs(
            lowers_target.reason,
            ClimateStabilityReason.WEAK_COOLING_LOWER_TARGET,
        )
        self.assertIs(stale_feedback.action, ClimateStabilityAction.COOL)
        self.assertIs(stale_feedback.fan_mode, ClimateFanMode.LOW)

    def test_night_weak_cooling_dwell_releases_at_exact_boundary(self) -> None:
        common = device(
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            activity=ClimateDeviceActivity.RUNNING,
            cooling_evidence_confirmed=True,
            cooling_rate_per_hour=0.2,
            current_target_temperature=26,
            fan_mode=ClimateFanMode.LOW,
            physical_feedback=ClimatePhysicalFeedback.CONFIRMED,
        )
        observed_home = ClimateHomeObservation(period=ClimateDayPeriod.NIGHT)

        waits = stable(
            replace(common, last_started_at=NOW - 18 * 60_000 + 1),
            room(temperature=26.0),
            home=observed_home,
        )
        escalates = stable(
            replace(common, last_started_at=NOW - 18 * 60_000),
            room(temperature=26.0),
            home=observed_home,
        )

        self.assertIs(waits.action, ClimateStabilityAction.COOL)
        self.assertIs(waits.fan_mode, ClimateFanMode.LOW)
        self.assertIs(escalates.action, ClimateStabilityAction.MAINTAIN)
        self.assertIs(escalates.fan_mode, ClimateFanMode.MEDIUM)

    def test_night_maintenance_is_quiet(self) -> None:
        result = stable(
            device(
                ClimateObservationDeviceKind.AIR_CONDITIONER,
                activity=ClimateDeviceActivity.RUNNING,
            ),
            room(temperature=25.7),
            selected_target=target(temperature=25.5),
            home=ClimateHomeObservation(period=ClimateDayPeriod.NIGHT),
        )

        self.assertIs(result.action, ClimateStabilityAction.MAINTAIN)
        self.assertTrue(result.quiet)

    def test_humidifier_uses_exact_normal_and_raised_hysteresis(self) -> None:
        stopped = device(
            ClimateObservationDeviceKind.HUMIDIFIER,
            activity=ClimateDeviceActivity.STOPPED,
        )
        running = replace(stopped, activity=ClimateDeviceActivity.HUMIDIFYING)

        starts = stable(stopped, room(humidity=39))
        holds = stable(running, room(humidity=43))
        stops = stable(running, room(humidity=44))
        raised_starts = stable(
            stopped,
            room(humidity=40),
            cooling_active=True,
        )
        raised_stops = stable(
            running,
            room(humidity=45),
            home=ClimateHomeObservation(heat_load_temperature=26),
        )

        self.assertIs(starts.action, ClimateStabilityAction.HUMIDIFY)
        self.assertEqual(
            (39, 44),
            (starts.humidity_on_threshold, starts.humidity_off_threshold),
        )
        self.assertIs(holds.action, ClimateStabilityAction.HOLD)
        self.assertIs(
            holds.protection,
            ClimateStabilityProtection.HUMIDITY_HYSTERESIS,
        )
        self.assertIs(stops.action, ClimateStabilityAction.OFF)
        self.assertIs(raised_starts.action, ClimateStabilityAction.HUMIDIFY)
        self.assertEqual(
            (40, 45),
            (
                raised_starts.humidity_on_threshold,
                raised_starts.humidity_off_threshold,
            ),
        )
        self.assertIs(raised_stops.action, ClimateStabilityAction.OFF)

    def test_humidifier_thresholds_follow_configured_room_target(self) -> None:
        result = stable(
            device(
                ClimateObservationDeviceKind.HUMIDIFIER,
                activity=ClimateDeviceActivity.STOPPED,
            ),
            room(humidity=44),
            selected_target=target(humidity=50),
        )

        self.assertIs(result.action, ClimateStabilityAction.HUMIDIFY)
        self.assertEqual(
            (44, 49),
            (result.humidity_on_threshold, result.humidity_off_threshold),
        )

        with self.assertRaises(ClimateStabilityViolation):
            replace(result, humidity_on_threshold=43)

    def test_humidifier_heat_load_boundary_and_unknown_windows_are_exact(self) -> None:
        stopped = device(
            ClimateObservationDeviceKind.HUMIDIFIER,
            activity=ClimateDeviceActivity.STOPPED,
        )
        below = stable(
            stopped,
            room(humidity=40),
            home=ClimateHomeObservation(heat_load_temperature=25.999),
        )
        boundary = stable(
            stopped,
            room(humidity=40),
            home=ClimateHomeObservation(heat_load_temperature=26),
        )

        self.assertEqual(
            (39, 44),
            (below.humidity_on_threshold, below.humidity_off_threshold),
        )
        self.assertIs(boundary.action, ClimateStabilityAction.HUMIDIFY)
        self.assertEqual(
            (40, 45),
            (boundary.humidity_on_threshold, boundary.humidity_off_threshold),
        )
        for window in (
            ClimateWindowState.UNKNOWN,
            ClimateWindowState.NOT_CONFIGURED,
        ):
            with self.subTest(window=window):
                result = stable(stopped, room(humidity=30, window=window))
                self.assertIs(result.action, ClimateStabilityAction.OFF)
                self.assertIs(
                    result.protection,
                    ClimateStabilityProtection.WINDOW,
                )

    def test_unconfirmed_window_stops_humidifier_before_missing_humidity(self) -> None:
        humidifier = device(
            ClimateObservationDeviceKind.HUMIDIFIER,
            activity=ClimateDeviceActivity.HUMIDIFYING,
        )

        result = stable(
            humidifier,
            room(humidity=None, window=ClimateWindowState.OPEN),
        )

        self.assertIs(result.action, ClimateStabilityAction.OFF)
        self.assertIs(result.protection, ClimateStabilityProtection.WINDOW)

    def test_snapshot_uses_only_selected_devices_and_contains_no_command(self) -> None:
        registry, contours = setup()
        observed = build_climate_observation_snapshot(
            registry,
            source_snapshot(),
            observed_at=1_784_280_005_000,
        )
        observed = replace(
            observed,
            devices=(
                *observed.devices,
                device(
                    ClimateObservationDeviceKind.HUMIDIFIER,
                    activity=ClimateDeviceActivity.STOPPED,
                ),
            ),
        )
        contour = contours.contour("climate")
        targets = build_climate_target_snapshot(contour, observed)  # type: ignore[arg-type]
        demands = build_climate_demand_snapshot(targets, observed)
        resolutions = build_climate_resolution_snapshot(demands, observed)
        equipment = build_climate_equipment_snapshot(
            contour,  # type: ignore[arg-type]
            targets,
            resolutions,
            observed,
        )

        result = build_climate_stability_snapshot(
            contour,  # type: ignore[arg-type]
            targets,
            equipment,
            observed,
        )

        self.assertEqual(1, len(result.room("living").devices))  # type: ignore[union-attr]
        self.assertFalse(result.commands_enabled)
        serialized = json.dumps(asdict(result), ensure_ascii=False)
        for hidden in (
            "source_id",
            "entity_id",
            "service",
            "endpoint",
            "command",
            "intent",
        ):
            self.assertNotIn(hidden, serialized)

    def test_model_rejects_forged_mutable_and_mixed_plans(self) -> None:
        valid = stable(
            device(
                ClimateObservationDeviceKind.AIR_CONDITIONER,
                activity=ClimateDeviceActivity.RUNNING,
            ),
            room(temperature=25.2),
        )
        room_plan = ClimateRoomStabilityPlan("living", (valid,))
        snapshot = ClimateStabilitySnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            observed_at=NOW,
            rooms=(room_plan,),
        )

        with self.assertRaises(ClimateStabilityViolation):
            replace(valid, action=ClimateStabilityAction.OFF)
        with self.assertRaises(ClimateStabilityViolation):
            replace(valid, remaining_seconds=1)
        with self.assertRaises(ClimateStabilityViolation):
            ClimateRoomStabilityPlan("living", [valid])  # type: ignore[arg-type]
        with self.assertRaises(ClimateStabilityViolation):
            replace(snapshot, rooms=(room_plan, room_plan))
        with self.assertRaises(ClimateStabilityViolation):
            replace(snapshot, observed_at=NOW + 1)
        with self.assertRaises(ClimateStabilityViolation):
            stable(
                valid.device,
                valid.room,
                selected_target=replace(
                    valid.target,
                    observation_observed_at=NOW - 1,
                ),
            )

    def test_all_reference_cases_are_deterministic_with_exact_anchors(self) -> None:
        cases = load_climate_reference_suite()["cases"]
        cases_by_id = {case["id"]: case for case in cases}
        results = {
            case["id"]: climate_reference_stability(case["id"])
            for case in cases
        }

        self.assertEqual(30, len(results))
        self.assertEqual(
            results,
            {
                case["id"]: climate_reference_stability(case["id"])
                for case in cases
            },
        )
        starts = results["stopped_ac_starts_at_default_gap"].devices[0]
        waits = results["stopped_ac_waits_below_default_gap"].devices[0]
        maintains = results["running_ac_maintains_near_target"].devices[0]
        softens = results["running_ac_softens_before_stop"].devices[0]
        raises_fan = results["weak_cooling_raises_fan_first"].devices[0]
        lowers_target = results["weak_cooling_lowers_setpoint_second"].devices[0]
        pause = results["minimum_off_pause_blocks_restart"].devices[0]
        minimum_run = results["minimum_run_holds_slow_cycle"].devices[0]
        humidify = results["dry_closed_room_starts_humidifier"].devices[0]
        window = results["open_window_stops_humidifier"].devices[0]
        stale_feedback = results[
            "stale_physical_feedback_blocks_escalation"
        ].devices[0]

        self.assertIs(starts.action, ClimateStabilityAction.COOL)
        self.assertIs(waits.action, ClimateStabilityAction.OFF)
        self.assertIs(maintains.action, ClimateStabilityAction.MAINTAIN)
        self.assertEqual(27.0, softens.target_temperature)
        self.assertIs(raises_fan.fan_mode, ClimateFanMode.MEDIUM)
        self.assertEqual(25.0, lowers_target.target_temperature)
        self.assertIs(pause.protection, ClimateStabilityProtection.MINIMUM_OFF)
        self.assertEqual(60, pause.remaining_seconds)
        self.assertIs(
            minimum_run.protection,
            ClimateStabilityProtection.MINIMUM_RUN,
        )
        self.assertEqual(60, minimum_run.remaining_seconds)
        self.assertIs(humidify.action, ClimateStabilityAction.HUMIDIFY)
        self.assertIs(window.action, ClimateStabilityAction.OFF)
        self.assertIs(stale_feedback.action, ClimateStabilityAction.COOL)
        self.assertIs(stale_feedback.fan_mode, ClimateFanMode.LOW)

        exact_air_conditioner_cases = (
            "stopped_ac_starts_at_default_gap",
            "stopped_ac_waits_below_default_gap",
            "running_ac_maintains_near_target",
            "hard_off_threshold_stops_running_ac",
            "running_ac_softens_before_stop",
            "weak_cooling_raises_fan_first",
            "weak_cooling_lowers_setpoint_second",
            "minimum_off_pause_blocks_restart",
            "minimum_run_holds_slow_cycle",
            "night_profile_is_quiet",
            "stale_physical_feedback_blocks_escalation",
        )
        for case_id in exact_air_conditioner_cases:
            with self.subTest(case_id=case_id):
                expected = cases_by_id[case_id]["expected"]
                actual = results[case_id].devices[0]
                self.assertEqual(expected["action"], actual.action.value)
                self.assertEqual(expected["setpoint"], actual.target_temperature)
                self.assertEqual(
                    expected["fan_mode"],
                    None if actual.fan_mode is None else actual.fan_mode.value,
                )
                self.assertEqual(expected["quiet"], actual.quiet)
        self.assertEqual(
            "humidifier_on",
            cases_by_id["dry_closed_room_starts_humidifier"]["expected"][
                "auxiliary_action"
            ],
        )
        self.assertIs(humidify.action, ClimateStabilityAction.HUMIDIFY)
        self.assertEqual(
            "humidifier_off",
            cases_by_id["open_window_stops_humidifier"]["expected"][
                "auxiliary_action"
            ],
        )
        self.assertIs(window.action, ClimateStabilityAction.OFF)


if __name__ == "__main__":
    unittest.main()
