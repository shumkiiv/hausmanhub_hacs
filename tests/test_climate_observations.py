"""Tests for HausmanHub's private-id-free climate observation boundary."""

from __future__ import annotations

from dataclasses import asdict, replace
import math
import unittest

from custom_components.hausman_hub.application.climate_import import (
    import_climate_state,
)
from custom_components.hausman_hub.application.climate_observations import (
    REFERENCE_ROOM_ID,
    build_climate_observation_snapshot,
    climate_reference_observation,
    unavailable_climate_observation_snapshot,
)
from custom_components.hausman_hub.application.climate_registry import (
    registry_from_payload,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateControlObservation,
    ClimateDataStatus,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateHomeObservation,
    ClimateObservationDeviceKind,
    ClimateObservationSnapshot,
    ClimateObservationViolation,
    ClimateRoomObservation,
)
from custom_components.hausman_hub.domain.climate_reference import (
    load_climate_reference_suite,
)
from tests.test_climate_import import registry_payload, source_payload


class ClimateObservationTest(unittest.TestCase):
    def test_import_is_reduced_to_configured_stable_ids_and_bounded_facts(self) -> None:
        registry = registry_from_payload(registry_payload())
        observation = build_climate_observation_snapshot(
            registry,
            import_climate_state(source_payload(), now_ms=1784280005000),
            observed_at=1784280005000,
        )

        self.assertTrue(observation.runtime_fresh)
        self.assertEqual(("living", "kids"), tuple(room.room_id for room in observation.rooms))
        self.assertEqual(("living_ac",), tuple(device.device_id for device in observation.devices))
        self.assertEqual(25.8, observation.room("living").temperature)
        self.assertEqual(44.0, observation.room("living").humidity)
        self.assertTrue(observation.room("living").authority_eligible)
        self.assertIs(
            observation.device("living_ac").activity,
            ClimateDeviceActivity.COOLING,
        )
        self.assertTrue(observation.device("living_ac").available)

        payload = asdict(observation)
        self.assertNotIn("synthetic-ac-source-living", repr(payload))
        self.assertFalse(_has_private_or_command_key(payload))

    def test_stale_state_is_visible_but_loses_authority(self) -> None:
        registry = registry_from_payload(registry_payload())
        observation = build_climate_observation_snapshot(
            registry,
            import_climate_state(source_payload(), now_ms=1784280300001),
            observed_at=1784280300001,
        )

        self.assertIs(observation.data_status, ClimateDataStatus.STALE)
        self.assertIs(observation.room("living").data_status, ClimateDataStatus.STALE)
        self.assertFalse(observation.room("living").authority_eligible)

    def test_missing_mismatched_and_unregistered_devices_are_fail_closed(self) -> None:
        payload = registry_payload()
        payload["rooms"].append({"id": "office", "name": "Office"})  # type: ignore[union-attr]
        payload["devices"][0]["source_id"] = "missing-private-source"  # type: ignore[index]
        registry = registry_from_payload(payload)
        observation = build_climate_observation_snapshot(
            registry,
            import_climate_state(source_payload()),
        )

        self.assertIs(
            observation.room("office").data_status,
            ClimateDataStatus.UNAVAILABLE,
        )
        self.assertIs(
            observation.device("living_ac").availability,
            ClimateDeviceAvailability.MISSING,
        )
        self.assertEqual(("living_ac",), tuple(item.device_id for item in observation.devices))
        self.assertNotIn("synthetic-humidifier-source-kids", repr(asdict(observation)))

        mismatch_source = source_payload()
        mismatch_source["devices"][0]["roomId"] = "kids"  # type: ignore[index]
        mismatch = build_climate_observation_snapshot(
            registry_from_payload(registry_payload()),
            import_climate_state(mismatch_source),
        )
        self.assertIs(
            mismatch.device("living_ac").availability,
            ClimateDeviceAvailability.MISSING,
        )

    def test_unavailable_source_never_invents_room_or_device_values(self) -> None:
        registry = registry_from_payload(registry_payload())
        observation = unavailable_climate_observation_snapshot(
            registry,
            observed_at=1784280005000,
        )

        self.assertFalse(observation.runtime_fresh)
        self.assertIsNone(observation.source_generated_at)
        self.assertTrue(
            all(room.data_status is ClimateDataStatus.UNAVAILABLE for room in observation.rooms)
        )
        self.assertTrue(
            all(
                device.availability is ClimateDeviceAvailability.MISSING
                for device in observation.devices
            )
        )

    def test_every_frozen_case_fits_the_internal_model_without_commands(self) -> None:
        suite = load_climate_reference_suite()
        cases = suite["cases"]
        self.assertEqual(30, len(cases))  # type: ignore[arg-type]

        for case in cases:  # type: ignore[union-attr]
            with self.subTest(case_id=case["id"]):
                observation = climate_reference_observation(case["id"])
                values = case["input"]
                room = observation.room(REFERENCE_ROOM_ID)
                self.assertIsNotNone(room)
                self.assertEqual(values["target_temperature"], room.observed_target_temperature)
                self.assertEqual(values["target_humidity"], room.observed_target_humidity)
                self.assertEqual(values["observation"]["temperature"], room.temperature)
                self.assertEqual(values["observation"]["humidity"], room.humidity)
                self.assertEqual(
                    set(values["available_devices"]),
                    {device.kind.value for device in observation.devices},
                )
                self.assertEqual(
                    values["observation"]["state_fresh"],
                    observation.runtime_fresh,
                )
                self.assertFalse(_has_private_or_command_key(asdict(observation)))

    def test_model_rejects_unsafe_or_contradictory_values(self) -> None:
        with self.assertRaises(ClimateObservationViolation):
            ClimateRoomObservation(
                room_id="living",
                name="Living",
                data_status=ClimateDataStatus.FRESH,
                temperature=True,  # type: ignore[arg-type]
            )
        with self.assertRaises(ClimateObservationViolation):
            ClimateRoomObservation(
                room_id="living",
                name="Living",
                data_status=ClimateDataStatus.FRESH,
                temperature=math.nan,
            )
        with self.assertRaises(ClimateObservationViolation):
            ClimateRoomObservation(
                room_id="living",
                name="Living",
                data_status=ClimateDataStatus.UNAVAILABLE,
                temperature=22,
            )
        with self.assertRaises(ClimateObservationViolation):
            ClimateDeviceObservation(
                device_id="living_ac",
                name="AC",
                room_id="living",
                kind=ClimateObservationDeviceKind.AIR_CONDITIONER,
                availability=ClimateDeviceAvailability.MISSING,
                activity=ClimateDeviceActivity.RUNNING,
            )
        with self.assertRaises(ClimateObservationViolation):
            ClimateHomeObservation(heat_load_temperature=101)
        with self.assertRaises(ClimateObservationViolation):
            ClimateHomeObservation(
                heat_load_temperature=True,  # type: ignore[arg-type]
            )

    def test_snapshot_rejects_mutable_duplicates_and_unknown_room_links(self) -> None:
        registry = registry_from_payload(registry_payload())
        valid = build_climate_observation_snapshot(
            registry,
            import_climate_state(source_payload()),
        )

        with self.assertRaises(ClimateObservationViolation):
            replace(valid, rooms=list(valid.rooms))  # type: ignore[arg-type]
        with self.assertRaises(ClimateObservationViolation):
            replace(valid, rooms=(valid.rooms[0], valid.rooms[0]))
        with self.assertRaises(ClimateObservationViolation):
            replace(
                valid,
                devices=(replace(valid.devices[0], room_id="unknown_room"),),
            )

    def test_snapshot_requires_complete_typed_top_level_observations(self) -> None:
        room = ClimateRoomObservation(
            room_id="living",
            name="Living",
            data_status=ClimateDataStatus.FRESH,
        )
        with self.assertRaises(ClimateObservationViolation):
            ClimateObservationSnapshot(
                observed_at=1,
                source_generated_at=1,
                data_status=ClimateDataStatus.FRESH,
                home=ClimateHomeObservation(),
                control=ClimateControlObservation(),
                rooms=(room,),
                devices=(
                    ClimateDeviceObservation(
                        device_id="ac",
                        name="AC",
                        room_id="other",
                        kind=ClimateObservationDeviceKind.AIR_CONDITIONER,
                        availability=ClimateDeviceAvailability.MISSING,
                    ),
                ),
            )


def _has_private_or_command_key(value: object) -> bool:
    forbidden = {
        "source_id",
        "entity_id",
        "service",
        "command",
        "commands",
        "command_types",
        "endpoint",
        "endpoints",
    }
    if isinstance(value, dict):
        return any(
            key in forbidden or _has_private_or_command_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_private_or_command_key(item) for item in value)
    return False


if __name__ == "__main__":
    unittest.main()
