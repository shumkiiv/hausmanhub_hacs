"""Whole-home climate target contract and registry tests."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
    with_home_climate_targets,
    with_room_climate_humidity,
)
from custom_components.hausman_hub.application.home_climate_targets import (
    HomeClimateTargetsViolation,
    parse_home_climate_targets_request,
)
from tests.climate_bridge_fixture import import_climate_state
from tests.test_climate_import import source_payload


class HomeClimateTargetsTest(unittest.TestCase):
    def test_request_requires_confirmation_and_at_least_one_target(self) -> None:
        request = parse_home_climate_targets_request(
            {
                "request_id": "android-home-target-1",
                "contour_id": "climate",
                "target_temperature": 25.5,
                "target_humidity": None,
                "confirm": True,
            }
        )

        self.assertEqual(25.5, request.target_temperature)
        self.assertIsNone(request.target_humidity)
        with self.assertRaises(HomeClimateTargetsViolation):
            parse_home_climate_targets_request(
                {
                    "request_id": "android-home-target-2",
                    "contour_id": "climate",
                    "target_temperature": None,
                    "target_humidity": None,
                    "confirm": True,
                }
            )

    def test_common_target_updates_active_profile_and_clears_overrides(self) -> None:
        registry, contours = build_climate_contour_setup(
            import_climate_state(source_payload()),
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )

        updated = with_home_climate_targets(
            contours,
            target_temperature=24.5,
            target_humidity=50,
        )

        self.assertIsNotNone(registry.room("living"))
        room = updated.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertEqual(24.5, room.target_temperature)
        self.assertEqual(50, room.target_humidity)
        self.assertIsNone(room.temporary_override)

    def test_room_humidity_updates_only_the_selected_active_profile(self) -> None:
        _, contours = build_climate_contour_setup(
            import_climate_state(source_payload()),
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )

        updated = with_room_climate_humidity(
            contours, room_id="living", target_humidity=50
        )

        room = updated.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertEqual(25.0, room.target_temperature)
        self.assertEqual(50, room.target_humidity)


if __name__ == "__main__":
    unittest.main()
