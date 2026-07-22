"""Pure tests for command-free air-conditioner and heating-device plans."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import unittest

from custom_components.hausman_hub.application.climate_demands import (
    build_climate_demand_snapshot,
)
from custom_components.hausman_hub.application.climate_equipment import (
    build_climate_equipment_snapshot,
    climate_reference_equipment,
)
from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
)
from custom_components.hausman_hub.application.climate_resolutions import (
    build_climate_resolution_snapshot,
)
from custom_components.hausman_hub.application.climate_targets import (
    build_climate_target_snapshot,
)
from custom_components.hausman_hub.domain.climate_demand import (
    resolve_climate_room_demand,
)
from custom_components.hausman_hub.domain.climate_equipment import (
    ClimateDevicePlan,
    ClimateEquipmentAction,
    ClimateEquipmentReason,
    ClimateEquipmentSnapshot,
    ClimateEquipmentViolation,
    ClimateRoomEquipmentPlan,
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
    ClimateOccupancyMode,
    ClimateRoomObservation,
    ClimateSeason,
    ClimateTemperatureQuality,
)
from custom_components.hausman_hub.domain.climate_reference import (
    load_climate_reference_suite,
)
from custom_components.hausman_hub.domain.climate_resolution import (
    ClimateThermalResolution,
    resolve_climate_room_thermal,
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


def target(
    *,
    temperature: float = 25.0,
    strategy: ClimateStrategy = ClimateStrategy.NORMAL,
    status: ClimateDataStatus = ClimateDataStatus.FRESH,
) -> ClimateRoomTarget:
    return ClimateRoomTarget(
        room_id="living",
        active_profile=ClimateProfile.DAY,
        profile_temperature=temperature,
        target_temperature=temperature,
        target_humidity=45,
        strategy=strategy,
        temperature_origin=ClimateTemperatureTargetOrigin.PROFILE,
        observation_status=status,
        observation_observed_at=1_800_000_000_000,
    )


def room_observation(
    temperature: float | None,
    *,
    status: ClimateDataStatus = ClimateDataStatus.FRESH,
) -> ClimateRoomObservation:
    return ClimateRoomObservation(
        room_id="living",
        name="Гостиная",
        data_status=status,
        temperature=temperature,
        humidity=45.0,
        temperature_quality=(
            ClimateTemperatureQuality.NORMAL
            if temperature is not None
            else ClimateTemperatureQuality.UNKNOWN
        ),
    )


def home(
    *,
    season: ClimateSeason = ClimateSeason.UNKNOWN,
    period: ClimateDayPeriod = ClimateDayPeriod.DAY,
    occupancy: ClimateOccupancyMode = ClimateOccupancyMode.HOME,
    central_heating_on: bool | None = None,
    central_heating_configured: bool = True,
    outdoor_temperature: float | None = None,
    heat_load_temperature: float | None = None,
) -> ClimateHomeObservation:
    return ClimateHomeObservation(
        season=season,
        period=period,
        occupancy=occupancy,
        central_heating_on=central_heating_on,
        central_heating_configured=central_heating_configured,
        outdoor_temperature=outdoor_temperature,
        heat_load_temperature=heat_load_temperature,
    )


def resolution(
    temperature: float | None,
    observed_home: ClimateHomeObservation,
    *,
    room_target: ClimateRoomTarget | None = None,
):
    selected_target = room_target or target()
    demand = resolve_climate_room_demand(
        selected_target,
        room_observation(
            temperature,
            status=selected_target.observation_status,
        ),
    )
    return resolve_climate_room_thermal(demand, observed_home)


def device(
    kind: ClimateObservationDeviceKind,
    *,
    availability: ClimateDeviceAvailability = ClimateDeviceAvailability.AVAILABLE,
) -> ClimateDeviceObservation:
    return ClimateDeviceObservation(
        device_id=f"living_{kind.value}",
        name="Тепловое устройство",
        room_id="living",
        kind=kind,
        availability=availability,
        activity=(
            ClimateDeviceActivity.IDLE
            if availability is ClimateDeviceAvailability.AVAILABLE
            else ClimateDeviceActivity.UNKNOWN
        ),
    )


class ClimateEquipmentTest(unittest.TestCase):
    def test_air_conditioner_uses_generic_working_profiles_by_strategy(self) -> None:
        observed_home = home()
        expected = {
            ClimateStrategy.SOFT: (26.0, ClimateFanMode.LOW, True),
            ClimateStrategy.NORMAL: (26.0, ClimateFanMode.LOW, False),
            ClimateStrategy.AGGRESSIVE: (25.0, ClimateFanMode.MEDIUM, False),
        }

        for strategy, profile in expected.items():
            with self.subTest(strategy=strategy):
                selected_target = target(strategy=strategy)
                result = resolve_climate_device_plan(
                    device(ClimateObservationDeviceKind.AIR_CONDITIONER),
                    selected_target,
                    resolution(
                        25.7,
                        observed_home,
                        room_target=selected_target,
                    ),
                    observed_home,
                )

                self.assertIs(result.action, ClimateEquipmentAction.COOL)
                self.assertEqual(profile[0], result.target_temperature)
                self.assertIs(profile[1], result.fan_mode)
                self.assertIs(profile[2], result.quiet)
                self.assertIs(
                    result.reason,
                    ClimateEquipmentReason.COOLING_REQUIRED,
                )

    def test_radiator_uses_day_night_cold_and_warm_load_rules(self) -> None:
        radiator = device(ClimateObservationDeviceKind.RADIATOR_THERMOSTAT)
        cases = (
            (home(season=ClimateSeason.WINTER, central_heating_on=True), 19.0),
            (
                home(
                    season=ClimateSeason.WINTER,
                    central_heating_on=True,
                    outdoor_temperature=-12,
                ),
                19.5,
            ),
            (
                home(
                    season=ClimateSeason.WINTER,
                    period=ClimateDayPeriod.NIGHT,
                    central_heating_on=True,
                ),
                17.0,
            ),
            (
                home(
                    season=ClimateSeason.WINTER,
                    central_heating_on=True,
                    heat_load_temperature=20,
                ),
                18.5,
            ),
            (
                home(
                    season=ClimateSeason.WINTER,
                    central_heating_on=True,
                    outdoor_temperature=-10,
                    heat_load_temperature=18,
                ),
                19.0,
            ),
            (
                home(
                    season=ClimateSeason.WINTER,
                    period=ClimateDayPeriod.NIGHT,
                    central_heating_on=True,
                    heat_load_temperature=20,
                ),
                17.0,
            ),
        )

        for observed_home, expected_target in cases:
            with self.subTest(home=observed_home):
                result = resolve_climate_device_plan(
                    radiator,
                    target(),
                    resolution(25.0, observed_home),
                    observed_home,
                )

                self.assertIs(
                    result.action,
                    ClimateEquipmentAction.SET_TEMPERATURE,
                )
                self.assertEqual(expected_target, result.target_temperature)
                self.assertIs(
                    result.reason,
                    ClimateEquipmentReason.HEATING_SCHEDULE,
                )

    def test_radiator_observes_without_heat_period_or_safe_home_mode(self) -> None:
        radiator = device(ClimateObservationDeviceKind.RADIATOR_THERMOSTAT)
        cases = (
            home(season=ClimateSeason.SUMMER, central_heating_on=True),
            home(
                season=ClimateSeason.WINTER,
                period=ClimateDayPeriod.UNKNOWN,
                central_heating_on=True,
            ),
            home(occupancy=ClimateOccupancyMode.AWAY_KEEP),
            home(occupancy=ClimateOccupancyMode.AWAY_SAFE_OFF),
        )

        for observed_home in cases:
            with self.subTest(home=observed_home):
                result = resolve_climate_device_plan(
                    radiator,
                    target(),
                    resolution(25.0, observed_home),
                    observed_home,
                )
                self.assertIs(result.action, ClimateEquipmentAction.OBSERVE)
                self.assertIsNone(result.target_temperature)

    def test_radiator_safe_offs_when_configured_central_heating_is_not_on(self) -> None:
        radiator = device(ClimateObservationDeviceKind.RADIATOR_THERMOSTAT)
        for central_on in (False, None):
            observed_home = home(
                season=ClimateSeason.WINTER,
                central_heating_on=central_on,
            )
            with self.subTest(central_heating_on=central_on):
                result = resolve_climate_device_plan(
                    radiator,
                    target(),
                    resolution(25.0, observed_home),
                    observed_home,
                )
                self.assertIs(result.action, ClimateEquipmentAction.SAFE_OFF)
                self.assertIs(result.reason, ClimateEquipmentReason.CENTRAL_HEATING_OFF)

    def test_radiator_ignores_unconfigured_central_heating(self) -> None:
        radiator = device(ClimateObservationDeviceKind.RADIATOR_THERMOSTAT)
        observed_home = home(
            season=ClimateSeason.WINTER,
            central_heating_configured=False,
        )
        result = resolve_climate_device_plan(
            radiator,
            target(),
            resolution(25.0, observed_home),
            observed_home,
        )
        self.assertIs(result.action, ClimateEquipmentAction.SET_TEMPERATURE)
        self.assertIs(result.reason, ClimateEquipmentReason.HEATING_SCHEDULE)

    def test_stale_room_never_creates_a_thermal_device_setting(self) -> None:
        observed_home = home(
            season=ClimateSeason.WINTER,
            central_heating_on=True,
        )
        stale_target = target(status=ClimateDataStatus.STALE)
        stale_resolution = resolution(
            27.0,
            observed_home,
            room_target=stale_target,
        )

        for kind in (
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
            ClimateObservationDeviceKind.FLOOR_HEATING,
        ):
            with self.subTest(kind=kind):
                result = resolve_climate_device_plan(
                    device(kind),
                    stale_target,
                    stale_resolution,
                    observed_home,
                )
                self.assertIs(result.action, ClimateEquipmentAction.OBSERVE)
                self.assertIsNone(result.target_temperature)

    def test_floor_heating_uses_room_target_only_for_heating_direction(self) -> None:
        observed_home = home(
            season=ClimateSeason.WINTER,
            central_heating_on=True,
        )
        floor = device(ClimateObservationDeviceKind.FLOOR_HEATING)
        heats = resolve_climate_device_plan(
            floor,
            target(),
            resolution(24.4, observed_home),
            observed_home,
        )
        holds = resolve_climate_device_plan(
            floor,
            target(),
            resolution(25.0, observed_home),
            observed_home,
        )

        self.assertIs(heats.action, ClimateEquipmentAction.SET_TEMPERATURE)
        self.assertEqual(25.0, heats.target_temperature)
        self.assertIs(heats.reason, ClimateEquipmentReason.HEATING_REQUIRED)
        self.assertIs(holds.action, ClimateEquipmentAction.HOLD)

    def test_safe_off_is_abstract_for_ac_and_floor_but_radiator_observes(self) -> None:
        away = home(occupancy=ClimateOccupancyMode.AWAY_SAFE_OFF)
        selected_resolution = resolution(None, away)

        air_conditioner = resolve_climate_device_plan(
            device(ClimateObservationDeviceKind.AIR_CONDITIONER),
            target(),
            selected_resolution,
            away,
        )
        floor = resolve_climate_device_plan(
            device(ClimateObservationDeviceKind.FLOOR_HEATING),
            target(),
            selected_resolution,
            away,
        )
        radiator = resolve_climate_device_plan(
            device(ClimateObservationDeviceKind.RADIATOR_THERMOSTAT),
            target(),
            selected_resolution,
            away,
        )

        self.assertIs(air_conditioner.action, ClimateEquipmentAction.SAFE_OFF)
        self.assertIs(floor.action, ClimateEquipmentAction.SAFE_OFF)
        self.assertIs(radiator.action, ClimateEquipmentAction.OBSERVE)

    def test_unavailable_device_never_retains_a_requested_setting(self) -> None:
        observed_home = home()
        result = resolve_climate_device_plan(
            device(
                ClimateObservationDeviceKind.AIR_CONDITIONER,
                availability=ClimateDeviceAvailability.MISSING,
            ),
            target(),
            resolution(27.0, observed_home),
            observed_home,
        )

        self.assertIs(result.action, ClimateEquipmentAction.UNAVAILABLE)
        self.assertIs(result.reason, ClimateEquipmentReason.DEVICE_UNAVAILABLE)
        self.assertIsNone(result.target_temperature)
        self.assertIsNone(result.fan_mode)
        self.assertIsNone(result.quiet)

    def test_snapshot_is_redacted_and_uses_only_selected_thermal_devices(self) -> None:
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
                device(ClimateObservationDeviceKind.FLOOR_HEATING),
            ),
        )
        targets = build_climate_target_snapshot(
            contours.contour("climate"),  # type: ignore[arg-type]
            observed,
        )
        demands = build_climate_demand_snapshot(targets, observed)
        resolutions = build_climate_resolution_snapshot(demands, observed)

        equipment = build_climate_equipment_snapshot(
            contours.contour("climate"),  # type: ignore[arg-type]
            targets,
            resolutions,
            observed,
        )

        room = equipment.room("living")
        self.assertEqual(1, len(room.devices))  # type: ignore[union-attr]
        self.assertIs(
            room.device("living_air_conditioner").action,  # type: ignore[union-attr]
            ClimateEquipmentAction.COOL,
        )
        self.assertFalse(equipment.commands_enabled)
        serialized = json.dumps(asdict(equipment), ensure_ascii=False)
        for hidden in (
            "source_id",
            "entity_id",
            "service",
            "endpoint",
            "command",
            "intent",
        ):
            self.assertNotIn(hidden, serialized)

    def test_model_rejects_forged_mutable_and_mixed_equipment_plans(self) -> None:
        observed_home = home()
        valid = resolve_climate_device_plan(
            device(ClimateObservationDeviceKind.AIR_CONDITIONER),
            target(),
            resolution(27.0, observed_home),
            observed_home,
        )
        room = ClimateRoomEquipmentPlan(
            room_id="living",
            thermal=ClimateThermalResolution.COOLING,
            devices=(valid,),
        )
        snapshot = ClimateEquipmentSnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            rooms=(room,),
        )

        with self.assertRaises(ClimateEquipmentViolation):
            replace(valid, target_temperature=27.0)
        with self.assertRaises(ClimateEquipmentViolation):
            replace(valid, action=ClimateEquipmentAction.HOLD)
        with self.assertRaises(ClimateEquipmentViolation):
            ClimateRoomEquipmentPlan(
                room_id="living",
                thermal=ClimateThermalResolution.COOLING,
                devices=[valid],  # type: ignore[arg-type]
            )
        with self.assertRaises(ClimateEquipmentViolation):
            replace(snapshot, rooms=(room, room))

        registry, contours = setup()
        observed = build_climate_observation_snapshot(
            registry,
            source_snapshot(),
            observed_at=1_784_280_005_000,
        )
        targets = build_climate_target_snapshot(
            contours.contour("climate"),  # type: ignore[arg-type]
            observed,
        )
        demands = build_climate_demand_snapshot(targets, observed)
        resolutions = build_climate_resolution_snapshot(demands, observed)
        mixed_targets = replace(
            targets,
            rooms=(replace(targets.rooms[0], observation_status=ClimateDataStatus.STALE),),
        )
        with self.assertRaises(ClimateEquipmentViolation):
            build_climate_equipment_snapshot(
                contours.contour("climate"),  # type: ignore[arg-type]
                mixed_targets,
                resolutions,
                observed,
            )
        contour = contours.contour("climate")
        missing_device_contour = replace(
            contour,
            rooms=(
                replace(
                    contour.rooms[0],  # type: ignore[union-attr]
                    device_ids=("missing_device",),
                ),
            ),
        )
        with self.assertRaises(ClimateEquipmentViolation):
            build_climate_equipment_snapshot(
                missing_device_contour,
                targets,
                resolutions,
                observed,
            )

    def test_all_reference_cases_have_deterministic_equipment_plans(self) -> None:
        cases = load_climate_reference_suite()["cases"]

        results = {
            case["id"]: climate_reference_equipment(case["id"])
            for case in cases
        }

        self.assertEqual(30, len(results))
        starts = results["stopped_ac_starts_at_default_gap"].devices[0]
        self.assertIs(starts.action, ClimateEquipmentAction.COOL)
        self.assertEqual(26.0, starts.target_temperature)
        self.assertIs(starts.fan_mode, ClimateFanMode.LOW)
        winter_trv = results["winter_trv_uses_cold_weather_target"].devices[0]
        self.assertIs(
            winter_trv.action,
            ClimateEquipmentAction.SET_TEMPERATURE,
        )
        self.assertEqual(19.5, winter_trv.target_temperature)
        heating_off = results["heating_off_leaves_trv_untouched"].devices[0]
        self.assertIs(heating_off.action, ClimateEquipmentAction.SAFE_OFF)
        self.assertIs(heating_off.reason, ClimateEquipmentReason.CENTRAL_HEATING_OFF)
        self.assertEqual(
            (),
            results["unavailable_ac_keeps_decision_without_plan"].devices,
        )


if __name__ == "__main__":
    unittest.main()
