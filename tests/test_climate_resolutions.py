"""Pure tests for seasonal and occupancy-aware thermal resolution."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import unittest

from custom_components.hausman_hub.application.climate_demands import (
    build_climate_demand_snapshot,
)
from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
)
from custom_components.hausman_hub.application.climate_resolutions import (
    build_climate_resolution_snapshot,
    climate_reference_resolution,
)
from custom_components.hausman_hub.application.climate_targets import (
    build_climate_target_snapshot,
)
from custom_components.hausman_hub.domain.climate_demand import (
    ClimateDemandState,
    resolve_climate_room_demand,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
    ClimateHomeObservation,
    ClimateOccupancyMode,
    ClimateRoomObservation,
    ClimateSeason,
    ClimateTemperatureQuality,
)
from custom_components.hausman_hub.domain.climate_reference import (
    load_climate_reference_suite,
)
from custom_components.hausman_hub.domain.climate_resolution import (
    ClimateResolutionSnapshot,
    ClimateResolutionViolation,
    ClimateThermalReason,
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


def demand(
    temperature: float | None,
    *,
    status: ClimateDataStatus = ClimateDataStatus.FRESH,
):
    target = ClimateRoomTarget(
        room_id="living",
        active_profile=ClimateProfile.DAY,
        profile_temperature=25.0,
        target_temperature=25.0,
        target_humidity=45,
        strategy=ClimateStrategy.NORMAL,
        temperature_origin=ClimateTemperatureTargetOrigin.PROFILE,
        observation_status=status,
        observation_observed_at=1_800_000_000_000,
    )
    observed = ClimateRoomObservation(
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
    return resolve_climate_room_demand(target, observed)


def home(
    *,
    season: ClimateSeason = ClimateSeason.UNKNOWN,
    occupancy: ClimateOccupancyMode = ClimateOccupancyMode.HOME,
    central_heating_on: bool | None = None,
) -> ClimateHomeObservation:
    return ClimateHomeObservation(
        season=season,
        occupancy=occupancy,
        central_heating_on=central_heating_on,
    )


class ClimateResolutionTest(unittest.TestCase):
    def test_unknown_and_summer_modes_allow_working_core_cooling(self) -> None:
        unknown = resolve_climate_room_thermal(demand(25.7), home())
        summer = resolve_climate_room_thermal(
            demand(25.7),
            home(season=ClimateSeason.SUMMER),
        )

        for result in (unknown, summer):
            self.assertIs(result.thermal, ClimateThermalResolution.COOLING)
            self.assertIs(result.reason, ClimateThermalReason.COOLING_REQUIRED)

    def test_winter_or_active_heating_blocks_opposing_cooling(self) -> None:
        winter = resolve_climate_room_thermal(
            demand(27.0),
            home(season=ClimateSeason.WINTER),
        )
        active_heating = resolve_climate_room_thermal(
            demand(27.0),
            home(central_heating_on=True),
        )

        for result in (winter, active_heating):
            self.assertIs(result.cooling_demand, ClimateDemandState.REQUIRED)
            self.assertIs(result.thermal, ClimateThermalResolution.OBSERVE)
            self.assertIs(
                result.reason,
                ClimateThermalReason.HEATING_MODE_BLOCKS_COOLING,
            )

    def test_heating_requires_a_known_heating_mode(self) -> None:
        winter = resolve_climate_room_thermal(
            demand(24.4),
            home(season=ClimateSeason.WINTER),
        )
        active_heating = resolve_climate_room_thermal(
            demand(24.4),
            home(central_heating_on=True),
        )
        summer = resolve_climate_room_thermal(
            demand(24.4),
            home(season=ClimateSeason.SUMMER),
        )
        unknown = resolve_climate_room_thermal(demand(24.4), home())

        self.assertIs(winter.thermal, ClimateThermalResolution.HEATING)
        self.assertIs(active_heating.thermal, ClimateThermalResolution.HEATING)
        self.assertIs(summer.thermal, ClimateThermalResolution.HOLD)
        self.assertIs(
            summer.reason,
            ClimateThermalReason.COOLING_MODE_BLOCKS_HEATING,
        )
        self.assertIs(unknown.thermal, ClimateThermalResolution.HOLD)
        self.assertIs(unknown.reason, ClimateThermalReason.THERMAL_MODE_UNKNOWN)

    def test_away_modes_have_priority_even_without_thermal_data(self) -> None:
        safe_off = resolve_climate_room_thermal(
            demand(None),
            home(occupancy=ClimateOccupancyMode.AWAY_SAFE_OFF),
        )
        keep = resolve_climate_room_thermal(
            demand(27.0),
            home(occupancy=ClimateOccupancyMode.AWAY_KEEP),
        )

        self.assertIs(safe_off.thermal, ClimateThermalResolution.SAFE_OFF)
        self.assertIs(safe_off.reason, ClimateThermalReason.AWAY_SAFE_OFF)
        self.assertIs(keep.thermal, ClimateThermalResolution.OBSERVE)
        self.assertIs(keep.reason, ClimateThermalReason.AWAY_KEEP)

    def test_home_mode_holds_comfort_and_reports_unavailable_data(self) -> None:
        comfortable = resolve_climate_room_thermal(demand(25.0), home())
        unavailable = resolve_climate_room_thermal(demand(None), home())

        self.assertIs(comfortable.thermal, ClimateThermalResolution.HOLD)
        self.assertIs(comfortable.reason, ClimateThermalReason.COMFORTABLE)
        self.assertIs(unavailable.thermal, ClimateThermalResolution.UNAVAILABLE)
        self.assertIs(
            unavailable.reason,
            ClimateThermalReason.THERMAL_DATA_UNAVAILABLE,
        )

    def test_snapshot_is_strict_redacted_and_contains_no_command_authority(
        self,
    ) -> None:
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

        self.assertIs(
            resolutions.room("living").thermal,  # type: ignore[union-attr]
            ClimateThermalResolution.COOLING,
        )
        self.assertFalse(resolutions.commands_enabled)
        serialized = json.dumps(asdict(resolutions), ensure_ascii=False)
        for hidden in (
            "source_id",
            "device_id",
            "entity_id",
            "service",
            "endpoint",
            "command",
            "intent",
        ):
            self.assertNotIn(hidden, serialized)

    def test_model_rejects_forged_mutable_and_mixed_resolutions(self) -> None:
        room = resolve_climate_room_thermal(demand(27.0), home())
        snapshot = ClimateResolutionSnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            rooms=(room,),
        )

        with self.assertRaises(ClimateResolutionViolation):
            replace(room, thermal=ClimateThermalResolution.HEATING)
        with self.assertRaises(ClimateResolutionViolation):
            replace(room, reason=ClimateThermalReason.COMFORTABLE)
        with self.assertRaises(ClimateResolutionViolation):
            ClimateResolutionSnapshot(
                contour_id="climate",
                contour_mode=ContourMode.AUTOMATIC,
                rooms=[room],  # type: ignore[arg-type]
            )
        with self.assertRaises(ClimateResolutionViolation):
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
        mixed_room = replace(observed.rooms[0], temperature=26.0)
        mixed_observation = replace(observed, rooms=(mixed_room,))
        with self.assertRaises(ClimateResolutionViolation):
            build_climate_resolution_snapshot(demands, mixed_observation)

    def test_all_reference_cases_have_deterministic_thermal_resolution(self) -> None:
        cases = load_climate_reference_suite()["cases"]

        results = {
            case["id"]: climate_reference_resolution(case["id"])
            for case in cases
        }

        self.assertEqual(30, len(results))
        self.assertIs(
            results["stopped_ac_starts_at_default_gap"].thermal,
            ClimateThermalResolution.COOLING,
        )
        self.assertIs(
            results["away_safe_off_overrides_manual"].thermal,
            ClimateThermalResolution.SAFE_OFF,
        )
        self.assertIs(
            results["away_keep_observes_running_ac"].thermal,
            ClimateThermalResolution.OBSERVE,
        )
        self.assertIs(
            results["winter_trv_uses_cold_weather_target"].thermal,
            ClimateThermalResolution.HOLD,
        )
        self.assertIs(
            results["stale_state_pauses_control"].thermal,
            ClimateThermalResolution.UNAVAILABLE,
        )


if __name__ == "__main__":
    unittest.main()
