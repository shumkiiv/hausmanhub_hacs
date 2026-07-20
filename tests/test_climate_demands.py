"""Pure tests for HausmanHub heating, cooling, and humidifying demand."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import unittest

from custom_components.hausman_hub.application.climate_demands import (
    build_climate_demand_snapshot,
    climate_reference_demand,
)
from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
)
from custom_components.hausman_hub.application.climate_targets import (
    build_climate_target_snapshot,
)
from custom_components.hausman_hub.domain.climate_demand import (
    CLIMATE_COOLING_START_GAP,
    CLIMATE_HEATING_COMFORT_GAP,
    ClimateDemandSnapshot,
    ClimateDemandState,
    ClimateDemandViolation,
    resolve_climate_room_demand,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
    ClimateRoomObservation,
    ClimateTemperatureQuality,
)
from custom_components.hausman_hub.domain.climate_reference import (
    load_climate_reference_suite,
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
    status: ClimateDataStatus = ClimateDataStatus.FRESH,
    temperature: float = 25.0,
    humidity: int = 45,
) -> ClimateRoomTarget:
    return ClimateRoomTarget(
        room_id="living",
        active_profile=ClimateProfile.DAY,
        profile_temperature=temperature,
        target_temperature=temperature,
        target_humidity=humidity,
        strategy=ClimateStrategy.NORMAL,
        temperature_origin=ClimateTemperatureTargetOrigin.PROFILE,
        observation_status=status,
        observation_observed_at=1_800_000_000_000,
    )


def observation(
    *,
    status: ClimateDataStatus = ClimateDataStatus.FRESH,
    temperature: float | None = 25.0,
    humidity: float | None = 45.0,
    quality: ClimateTemperatureQuality = ClimateTemperatureQuality.NORMAL,
) -> ClimateRoomObservation:
    return ClimateRoomObservation(
        room_id="living",
        name="Гостиная",
        data_status=status,
        temperature=temperature,
        humidity=humidity,
        temperature_quality=quality,
    )


class ClimateDemandTest(unittest.TestCase):
    def test_cooling_uses_the_working_core_start_gap_exactly(self) -> None:
        starts = resolve_climate_room_demand(
            target(),
            observation(temperature=25.7),
        )
        waits = resolve_climate_room_demand(
            target(),
            observation(temperature=25.6),
        )

        self.assertEqual(0.7, CLIMATE_COOLING_START_GAP)
        self.assertEqual(0.7, starts.cooling_gap)
        self.assertIs(starts.cooling, ClimateDemandState.REQUIRED)
        self.assertIs(starts.heating, ClimateDemandState.NOT_REQUIRED)
        self.assertEqual(0.6, waits.cooling_gap)
        self.assertIs(waits.cooling, ClimateDemandState.NOT_REQUIRED)

    def test_heating_and_humidifying_have_independent_comfort_gaps(self) -> None:
        required = resolve_climate_room_demand(
            target(),
            observation(temperature=24.4, humidity=39.0),
        )
        comfortable = resolve_climate_room_demand(
            target(),
            observation(temperature=24.5, humidity=40.0),
        )

        self.assertEqual(0.5, CLIMATE_HEATING_COMFORT_GAP)
        self.assertIs(required.heating, ClimateDemandState.REQUIRED)
        self.assertIs(required.cooling, ClimateDemandState.NOT_REQUIRED)
        self.assertIs(required.humidifying, ClimateDemandState.REQUIRED)
        self.assertIs(comfortable.heating, ClimateDemandState.NOT_REQUIRED)
        self.assertIs(comfortable.humidifying, ClimateDemandState.NOT_REQUIRED)

    def test_missing_stale_or_suspect_signals_never_create_required_demand(
        self,
    ) -> None:
        stale = resolve_climate_room_demand(
            target(status=ClimateDataStatus.STALE),
            observation(status=ClimateDataStatus.STALE, temperature=30.0),
        )
        suspect = resolve_climate_room_demand(
            target(),
            observation(
                temperature=30.0,
                quality=ClimateTemperatureQuality.SUSPECT,
            ),
        )
        missing = resolve_climate_room_demand(
            target(),
            observation(temperature=None, humidity=None),
        )

        self.assertEqual(30.0, stale.current_temperature)
        self.assertIsNone(stale.cooling_gap)
        self.assertIs(stale.cooling, ClimateDemandState.UNAVAILABLE)
        self.assertIs(suspect.heating, ClimateDemandState.UNAVAILABLE)
        self.assertIs(suspect.cooling, ClimateDemandState.UNAVAILABLE)
        self.assertIs(missing.heating, ClimateDemandState.UNAVAILABLE)
        self.assertIs(missing.humidifying, ClimateDemandState.UNAVAILABLE)

    def test_snapshot_builds_from_one_observation_and_contains_no_control_data(
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

        room = demands.room("living")
        self.assertIs(room.cooling, ClimateDemandState.REQUIRED)  # type: ignore[union-attr]
        self.assertFalse(demands.commands_enabled)
        serialized = json.dumps(asdict(demands), ensure_ascii=False)
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

    def test_model_rejects_mixed_snapshots_mutability_and_forged_demands(self) -> None:
        room = resolve_climate_room_demand(target(), observation())
        snapshot = ClimateDemandSnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            rooms=(room,),
        )

        with self.assertRaises(ClimateDemandViolation):
            resolve_climate_room_demand(
                target(status=ClimateDataStatus.STALE),
                observation(status=ClimateDataStatus.FRESH),
            )
        with self.assertRaises(ClimateDemandViolation):
            ClimateDemandSnapshot(
                contour_id="climate",
                contour_mode=ContourMode.AUTOMATIC,
                rooms=[room],  # type: ignore[arg-type]
            )
        with self.assertRaises(ClimateDemandViolation):
            replace(snapshot, rooms=(room, room))
        with self.assertRaises(ClimateDemandViolation):
            replace(room, cooling=ClimateDemandState.REQUIRED)
        with self.assertRaises(ClimateDemandViolation):
            replace(room, observation_status=ClimateDataStatus.STALE)

    def test_all_reference_cases_have_deterministic_raw_demand(self) -> None:
        cases = load_climate_reference_suite()["cases"]

        results = {
            case["id"]: climate_reference_demand(case["id"])
            for case in cases
        }

        self.assertEqual(30, len(results))
        self.assertIs(
            results["stopped_ac_starts_at_default_gap"].cooling,
            ClimateDemandState.REQUIRED,
        )
        self.assertIs(
            results["stopped_ac_waits_below_default_gap"].cooling,
            ClimateDemandState.NOT_REQUIRED,
        )
        self.assertIs(
            results["dry_closed_room_starts_humidifier"].humidifying,
            ClimateDemandState.REQUIRED,
        )
        for case_id in (
            "missing_temperature_blocks_control",
            "stale_state_pauses_control",
            "temperature_jump_pauses_control",
        ):
            self.assertIs(
                results[case_id].cooling,
                ClimateDemandState.UNAVAILABLE,
            )
        self.assertTrue(all(not result.room_id.startswith("entity.") for result in results.values()))


if __name__ == "__main__":
    unittest.main()
