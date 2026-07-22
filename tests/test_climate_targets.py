"""Pure tests for command-free HausmanHub room comfort targets."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import unittest

from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
)
from custom_components.hausman_hub.application.climate_targets import (
    build_climate_target_snapshot,
    climate_reference_target,
)
from custom_components.hausman_hub.application.contours import (
    with_active_climate_profile,
    with_applied_climate_schedule_profile,
    with_climate_room_profiles,
    with_climate_schedule,
    with_climate_temporary_temperature,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
    ClimateOccupancyMode,
    ClimateRoomMode,
    ClimateRoomObservation,
)
from custom_components.hausman_hub.domain.climate_reference import (
    load_climate_reference_suite,
)
from custom_components.hausman_hub.domain.climate_targets import (
    ClimateRoomTargetPolicy,
    ClimateTargetSnapshot,
    ClimateTargetViolation,
    ClimateTemperatureTargetOrigin,
    resolve_climate_room_target,
)
from custom_components.hausman_hub.domain.contours import (
    ClimateComfortSettings,
    ClimateProfile,
    ClimateStrategy,
    ClimateTemporaryOverride,
)
from tests.test_contours import setup, source_snapshot


NOW = 1_800_000_000_000


class ClimateTargetsTest(unittest.TestCase):
    def test_day_and_night_profiles_resolve_saved_comfort_values(self) -> None:
        registry, contours = setup()
        configured = with_climate_room_profiles(
            contours,
            {
                "living": {
                    "profiles": {
                        "day": {
                            "target_temperature": 25.0,
                            "target_humidity": 45,
                            "strategy": "normal",
                        },
                        "night": {
                            "target_temperature": 22.0,
                            "target_humidity": 40,
                            "strategy": "soft",
                        },
                    },
                    "active_profile": "day",
                }
            },
        )
        observation = build_climate_observation_snapshot(
            registry,
            source_snapshot(),
            observed_at=1_784_280_005_000,
        )

        day = build_climate_target_snapshot(
            configured.contour("climate"),  # type: ignore[arg-type]
            observation,
        ).room("living")
        night_registry = with_active_climate_profile(configured, "night")
        night = build_climate_target_snapshot(
            night_registry.contour("climate"),  # type: ignore[arg-type]
            observation,
        ).room("living")

        self.assertEqual(
            (ClimateProfile.DAY, 25.0, 45, ClimateStrategy.NORMAL),
            (
                day.active_profile,
                day.target_temperature,
                day.target_humidity,
                day.strategy,
            ),
        )
        self.assertEqual(
            (ClimateProfile.NIGHT, 22.0, 40, ClimateStrategy.SOFT),
            (
                night.active_profile,
                night.target_temperature,
                night.target_humidity,
                night.strategy,
            ),
        )

    def test_temporary_override_changes_only_effective_temperature(self) -> None:
        registry, contours = setup()
        configured = with_climate_room_profiles(
            contours,
            {
                "living": {
                    "profiles": {
                        "day": {
                            "target_temperature": 25.0,
                            "target_humidity": 50,
                            "strategy": "aggressive",
                        },
                        "night": {
                            "target_temperature": 21.5,
                            "target_humidity": 40,
                            "strategy": "soft",
                        },
                    },
                    "active_profile": "day",
                }
            },
        )
        configured = with_climate_schedule(
            configured,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        configured = with_applied_climate_schedule_profile(
            configured,
            ClimateProfile.DAY,
        )
        configured = with_climate_temporary_temperature(
            configured,
            room_id="living",
            target_temperature=23.5,
        )
        observation = build_climate_observation_snapshot(
            registry,
            source_snapshot(),
            observed_at=1_784_280_005_000,
        )

        target = build_climate_target_snapshot(
            configured.contour("climate"),  # type: ignore[arg-type]
            observation,
        ).room("living")

        self.assertEqual(25.0, target.profile_temperature)
        self.assertEqual(23.5, target.target_temperature)
        self.assertEqual(50, target.target_humidity)
        self.assertIs(target.strategy, ClimateStrategy.AGGRESSIVE)
        self.assertIs(
            target.temperature_origin,
            ClimateTemperatureTargetOrigin.TEMPORARY_OVERRIDE,
        )

    def test_unavailable_observation_keeps_configuration_but_not_authority(self) -> None:
        _, contours = setup()
        contour = contours.contour("climate")
        room = contour.rooms[0]  # type: ignore[union-attr]
        target = resolve_climate_room_target(
            ClimateRoomTargetPolicy(
                room_id=room.room_id,
                day_profile=room.day_profile,
                night_profile=room.night_profile,
                active_profile=room.active_profile,
            ),
            None,
            observed_at=1_784_280_005_000,
        )
        snapshot = ClimateTargetSnapshot(
            contour_id=contour.contour_id,  # type: ignore[union-attr]
            contour_mode=contour.mode,  # type: ignore[union-attr]
            rooms=(target,),
        )

        self.assertEqual(25.0, target.target_temperature)
        self.assertIs(target.observation_status, ClimateDataStatus.UNAVAILABLE)
        self.assertFalse(snapshot.commands_enabled)
        serialized = json.dumps(asdict(snapshot), ensure_ascii=False)
        for hidden in ("source_id", "entity_id", "service", "endpoint", "command"):
            self.assertNotIn(hidden, serialized)

    def test_target_model_rejects_mutability_duplicates_and_room_mismatch(self) -> None:
        registry, contours = setup()
        contour = contours.contour("climate")
        observation = build_climate_observation_snapshot(
            registry,
            source_snapshot(),
            observed_at=1_784_280_005_000,
        )
        snapshot = build_climate_target_snapshot(
            contour,  # type: ignore[arg-type]
            observation,
        )
        target = snapshot.rooms[0]
        policy = ClimateRoomTargetPolicy(
            room_id="living",
            day_profile=contour.rooms[0].day_profile,  # type: ignore[union-attr]
            night_profile=contour.rooms[0].night_profile,  # type: ignore[union-attr]
            active_profile=ClimateProfile.DAY,
            temporary_override=ClimateTemporaryOverride(23.0),
        )

        with self.assertRaises(ClimateTargetViolation):
            ClimateTargetSnapshot(
                contour_id="climate",
                contour_mode=contour.mode,  # type: ignore[union-attr]
                rooms=[target],  # type: ignore[arg-type]
            )
        with self.assertRaises(ClimateTargetViolation):
            replace(snapshot, rooms=())
        with self.assertRaises(ClimateTargetViolation):
            replace(snapshot, rooms=(target, target))
        with self.assertRaises(ClimateTargetViolation):
            resolve_climate_room_target(
                policy,
                ClimateRoomObservation(
                    room_id="bedroom",
                    name="Спальня",
                    data_status=ClimateDataStatus.UNAVAILABLE,
                ),
                observed_at=observation.observed_at,
            )

    def test_all_frozen_reference_cases_keep_exact_room_targets(self) -> None:
        cases = load_climate_reference_suite()["cases"]

        for case in cases:
            with self.subTest(case_id=case["id"]):
                target = climate_reference_target(case["id"])
                values = case["input"]
                self.assertEqual(
                    float(values["target_temperature"]),
                    target.target_temperature,
                )
                self.assertEqual(values["target_humidity"], target.target_humidity)
                self.assertEqual(values["period"], target.active_profile.value)
                self.assertIs(
                    target.temperature_origin,
                    ClimateTemperatureTargetOrigin.PROFILE,
                )

    def test_away_setback_lowers_heating_target_by_exactly_two(self) -> None:
        target = resolve_climate_room_target(
            _setback_policy(22.0),
            _setback_observation(19.0),
            observed_at=NOW,
            occupancy=ClimateOccupancyMode.AWAY_SETBACK,
        )

        self.assertEqual(20.0, target.target_temperature)
        self.assertEqual(22.0, target.profile_temperature)
        self.assertIs(
            target.temperature_origin,
            ClimateTemperatureTargetOrigin.AWAY_SETBACK,
        )

    def test_away_setback_raises_cooling_target_by_exactly_two(self) -> None:
        target = resolve_climate_room_target(
            _setback_policy(22.0),
            _setback_observation(26.0),
            observed_at=NOW,
            occupancy=ClimateOccupancyMode.AWAY_SETBACK,
        )

        self.assertEqual(24.0, target.target_temperature)
        self.assertIs(
            target.temperature_origin,
            ClimateTemperatureTargetOrigin.AWAY_SETBACK,
        )

    def test_away_setback_never_inverts_demand(self) -> None:
        heating = resolve_climate_room_target(
            _setback_policy(20.0),
            _setback_observation(19.0),
            observed_at=NOW,
            occupancy=ClimateOccupancyMode.AWAY_SETBACK,
        )
        cooling = resolve_climate_room_target(
            _setback_policy(22.0),
            _setback_observation(23.0),
            observed_at=NOW,
            occupancy=ClimateOccupancyMode.AWAY_SETBACK,
        )

        self.assertEqual(19.0, heating.target_temperature)
        self.assertEqual(23.0, cooling.target_temperature)

    def test_away_setback_respects_temperature_bounds(self) -> None:
        target = resolve_climate_room_target(
            _setback_policy(18.5),
            _setback_observation(17.0),
            observed_at=NOW,
            occupancy=ClimateOccupancyMode.AWAY_SETBACK,
        )

        self.assertEqual(18.0, target.target_temperature)

    def test_away_setback_skips_temporary_override(self) -> None:
        policy = _setback_policy(22.0)
        policy = replace(
            policy,
            temporary_override=ClimateTemporaryOverride(target_temperature=24.0),
        )
        target = resolve_climate_room_target(
            policy,
            _setback_observation(19.0),
            observed_at=NOW,
            occupancy=ClimateOccupancyMode.AWAY_SETBACK,
        )

        self.assertEqual(24.0, target.target_temperature)
        self.assertIs(
            target.temperature_origin,
            ClimateTemperatureTargetOrigin.TEMPORARY_OVERRIDE,
        )

    def test_away_setback_skips_manual_room_mode(self) -> None:
        target = resolve_climate_room_target(
            _setback_policy(22.0),
            _setback_observation(19.0, mode=ClimateRoomMode.MANUAL),
            observed_at=NOW,
            occupancy=ClimateOccupancyMode.AWAY_SETBACK,
        )

        self.assertEqual(22.0, target.target_temperature)
        self.assertIs(
            target.temperature_origin,
            ClimateTemperatureTargetOrigin.PROFILE,
        )

    def test_unknown_occupancy_keeps_target_unchanged(self) -> None:
        target = resolve_climate_room_target(
            _setback_policy(22.0),
            _setback_observation(19.0),
            observed_at=NOW,
            occupancy=ClimateOccupancyMode.UNKNOWN,
        )

        self.assertEqual(22.0, target.target_temperature)
        self.assertIs(
            target.temperature_origin,
            ClimateTemperatureTargetOrigin.PROFILE,
        )


def _setback_policy(target_temperature: float) -> ClimateRoomTargetPolicy:
    settings = ClimateComfortSettings(
        target_temperature=target_temperature,
        target_humidity=45,
        strategy=ClimateStrategy.NORMAL,
    )
    return ClimateRoomTargetPolicy(
        room_id="living",
        day_profile=settings,
        night_profile=settings,
        active_profile=ClimateProfile.DAY,
    )


def _setback_observation(
    temperature: float,
    *,
    mode: ClimateRoomMode = ClimateRoomMode.AUTO,
) -> ClimateRoomObservation:
    return ClimateRoomObservation(
        room_id="living",
        name="Living",
        data_status=ClimateDataStatus.FRESH,
        temperature=temperature,
        mode=mode,
    )


if __name__ == "__main__":
    unittest.main()
