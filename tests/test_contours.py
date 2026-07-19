"""Pure tests for the universal HASC contour model and climate adapter."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from custom_components.hausman_hub.application.climate_import import (
    import_climate_state,
)
from custom_components.hausman_hub.application.contours import (
    ContourRegistryViolation,
    build_climate_contour_setup,
    contour_registry_from_payload,
    contour_registry_to_payload,
    contour_snapshot,
    migrate_contour_registry_payload,
    validate_contour_bindings,
    with_active_climate_profile,
    with_applied_climate_schedule_profile,
    with_climate_contour_mode,
    with_climate_room_profiles,
    with_climate_schedule,
    with_climate_temporary_temperature,
    without_climate_temporary_temperature,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateControlOwner,
    ClimateControlScope,
)
from custom_components.hausman_hub.domain.contours import ClimateProfile, ContourMode


ROOT = Path(__file__).resolve().parents[1]


def source_payload() -> dict[str, object]:
    return json.loads(
        (ROOT / "fixtures" / "climate_bridge" / "valid_state.json").read_text(
            encoding="utf-8"
        )
    )


def source_snapshot():
    payload = source_payload()
    return import_climate_state(
        payload,
        now_ms=payload["generatedAt"] + 1_000,  # type: ignore[operator]
    )


def setup():
    return build_climate_contour_setup(
        source_snapshot(),
        room_ids=["living"],
        source_ids=["synthetic-ac-source-living"],
        name="Климат",
        mode="automatic",
        target_temperature=25.0,
        target_humidity=45,
        strategy="normal",
    )


class ContoursTest(unittest.TestCase):
    def test_existing_engine_selection_builds_public_contour_and_private_registry(
        self,
    ) -> None:
        climate_registry, contours = setup()

        contour = contours.contour("climate")
        device = climate_registry.devices[0]
        self.assertIsNotNone(contour)
        self.assertIs(contour.mode, ContourMode.AUTOMATIC)  # type: ignore[union-attr]
        self.assertEqual(("living_air_conditioner",), contour.rooms[0].device_ids)  # type: ignore[union-attr]
        self.assertIs(device.control_scope, ClimateControlScope.MANAGED)
        self.assertIs(device.control_owner, ClimateControlOwner.CLIMATE_CORE)
        self.assertEqual((), device.endpoints)

    def test_contour_payload_round_trip_is_exact_and_rejects_hidden_fields(self) -> None:
        _, contours = setup()
        payload = contour_registry_to_payload(contours)

        self.assertEqual(contours, contour_registry_from_payload(payload))
        hidden = copy.deepcopy(payload)
        hidden["contours"][0]["private_source"] = "must-not-pass"  # type: ignore[index]
        with self.assertRaises(ContourRegistryViolation):
            contour_registry_from_payload(hidden)

    def test_public_status_uses_existing_algorithm_without_private_ids(self) -> None:
        climate_registry, contours = setup()

        result = contour_snapshot(contours, climate_registry, source_snapshot())

        contour = result["contours"][0]  # type: ignore[index]
        self.assertEqual(6, result["contract"]["version"])  # type: ignore[index]
        self.assertEqual("hausman-climate", contour["engine"]["name"])
        self.assertTrue(contour["execution"]["automatic_active"])
        self.assertFalse(contour["execution"]["hasc_direct_commands"])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("synthetic-ac-source-living", serialized)
        self.assertNotIn("entity_id", serialized)

    def test_profiles_switch_active_targets_without_commands_or_bindings(self) -> None:
        climate_registry, contours = setup()
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
        selected = with_active_climate_profile(configured, "night")

        room = selected.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertEqual("night", room.active_profile.value)
        self.assertEqual(22.0, room.target_temperature)
        self.assertEqual("soft", room.strategy.value)
        self.assertEqual(
            contours.contour("climate").rooms[0].device_ids,  # type: ignore[union-attr]
            room.device_ids,
        )
        public = contour_snapshot(selected, climate_registry, source_snapshot())
        public_room = public["contours"][0]["rooms"][0]  # type: ignore[index]
        self.assertEqual("night", public_room["comfort_profiles"]["active"])
        self.assertEqual(25.0, public_room["comfort_profiles"]["day"]["temperature"])
        self.assertEqual(22.0, public_room["targets"]["temperature"])

    def test_legacy_contour_migration_duplicates_values_into_safe_profiles(self) -> None:
        legacy = {
            "version": 1,
            "contours": [
                {
                    "id": "climate",
                    "name": "Климат",
                    "kind": "climate",
                    "mode": "automatic",
                    "engine": "existing_climate_core",
                    "rooms": [
                        {
                            "room_id": "living",
                            "device_ids": ["living_air_conditioner"],
                            "target_temperature": 25.0,
                            "target_humidity": 45,
                            "strategy": "normal",
                        }
                    ],
                }
            ],
        }

        migrated = migrate_contour_registry_payload(1, legacy)
        room = migrated["contours"][0]["rooms"][0]  # type: ignore[index]
        self.assertEqual(4, migrated["version"])
        self.assertEqual("day", room["active_profile"])
        self.assertEqual(room["profiles"]["day"], room["profiles"]["night"])
        self.assertIsNone(room["temporary_override"])
        self.assertEqual(
            contour_registry_from_payload(migrated),
            contour_registry_from_payload(
                contour_registry_to_payload(contour_registry_from_payload(migrated))
            ),
        )

        hidden = copy.deepcopy(legacy)
        hidden["contours"][0]["rooms"][0]["hidden"] = True  # type: ignore[index]
        with self.assertRaises(ContourRegistryViolation):
            migrate_contour_registry_payload(1, hidden)

    def test_profile_registry_migration_adds_disabled_schedule(self) -> None:
        _, contours = setup()
        profile_payload = contour_registry_to_payload(contours)
        profile_payload["version"] = 2
        profile_payload["contours"][0].pop("schedule")  # type: ignore[index]
        profile_payload["contours"][0]["rooms"][0].pop(  # type: ignore[index]
            "temporary_override"
        )

        migrated = migrate_contour_registry_payload(2, profile_payload)

        schedule = migrated["contours"][0]["schedule"]  # type: ignore[index]
        self.assertEqual(
            {
                "enabled": False,
                "day_start": "07:00",
                "night_start": "23:00",
                "last_applied_profile": None,
            },
            schedule,
        )
        self.assertEqual(4, migrated["version"])
        self.assertIsNone(
            migrated["contours"][0]["rooms"][0]["temporary_override"]  # type: ignore[index]
        )

    def test_schedule_registry_migration_preserves_schedule_and_adds_override(
        self,
    ) -> None:
        _, contours = setup()
        scheduled = with_climate_schedule(
            contours,
            enabled=True,
            day_start="06:30",
            night_start="22:45",
        )
        payload = contour_registry_to_payload(scheduled)
        payload["version"] = 3
        payload["contours"][0]["rooms"][0].pop(  # type: ignore[index]
            "temporary_override"
        )

        migrated = migrate_contour_registry_payload(3, payload)

        self.assertEqual(4, migrated["version"])
        self.assertEqual(
            {
                "enabled": True,
                "day_start": "06:30",
                "night_start": "22:45",
                "last_applied_profile": None,
            },
            migrated["contours"][0]["schedule"],  # type: ignore[index]
        )
        self.assertIsNone(
            migrated["contours"][0]["rooms"][0]["temporary_override"]  # type: ignore[index]
        )

    def test_schedule_selects_day_and_night_across_midnight(self) -> None:
        climate_registry, contours = setup()
        scheduled = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        schedule = scheduled.contour("climate").schedule  # type: ignore[union-attr]

        self.assertEqual("night", schedule.profile_at(hour=6, minute=59).value)
        self.assertEqual("day", schedule.profile_at(hour=7, minute=0).value)
        self.assertEqual("night", schedule.profile_at(hour=23, minute=0).value)
        public = contour_snapshot(scheduled, climate_registry, source_snapshot())
        self.assertEqual(
            {"enabled": True, "day_start": "07:00", "night_start": "23:00"},
            public["contours"][0]["schedule"],  # type: ignore[index]
        )

    def test_schedule_rejects_equal_or_malformed_times(self) -> None:
        _, contours = setup()
        for day_start, night_start in (
            ("07:00", "07:00"),
            ("7:00", "23:00"),
            ("07:00", "24:00"),
        ):
            with self.subTest(day=day_start, night=night_start), self.assertRaises(
                ContourRegistryViolation
            ):
                with_climate_schedule(
                    contours,
                    enabled=True,
                    day_start=day_start,
                    night_start=night_start,
                )

    def test_temporary_temperature_changes_only_effective_target(self) -> None:
        climate_registry, contours = setup()
        scheduled = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        scheduled = with_active_climate_profile(scheduled, "day")
        scheduled_contour = scheduled.contour("climate")
        # Mark the current period as applied, just as the runtime scheduler does.
        scheduled = with_applied_climate_schedule_profile(
            scheduled,
            scheduled_contour.rooms[0].active_profile,  # type: ignore[union-attr]
        )
        changed = with_climate_temporary_temperature(
            scheduled,
            room_id="living",
            target_temperature=23.5,
        )

        room = changed.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertEqual(23.5, room.target_temperature)
        self.assertEqual(25.0, room.profile_settings.target_temperature)
        public = contour_snapshot(
            changed,
            climate_registry,
            source_snapshot(),
            settings_apply_enabled=True,
        )
        public_room = public["contours"][0]["rooms"][0]  # type: ignore[index]
        self.assertEqual(23.5, public_room["targets"]["temperature"])
        self.assertEqual(25.0, public_room["comfort_profiles"]["day"]["temperature"])
        self.assertEqual(
            {
                "active": True,
                "temperature": 23.5,
                "ends": "next_schedule_change",
                "available": True,
            },
            public_room["temporary_temperature"],
        )

        reconfigured = with_climate_room_profiles(
            changed,
            {
                "living": {
                    "profiles": {
                        "day": {
                            "target_temperature": 24.0,
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
        self.assertEqual(
            23.5,
            reconfigured.contour("climate").rooms[0].target_temperature,  # type: ignore[union-attr]
        )
        rearmed = with_active_climate_profile(
            reconfigured,
            "day",
            clear_temporary=False,
        )
        rearmed = with_applied_climate_schedule_profile(
            rearmed,
            ClimateProfile.DAY,
        )
        self.assertEqual(
            23.5,
            rearmed.contour("climate").rooms[0].target_temperature,  # type: ignore[union-attr]
        )

        restored = without_climate_temporary_temperature(
            rearmed,
            room_id="living",
        )
        restored_room = restored.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertEqual(24.0, restored_room.target_temperature)
        self.assertIsNone(restored_room.temporary_override)

    def test_schedule_profile_change_clears_temporary_temperature(self) -> None:
        _, contours = setup()
        scheduled = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        changed = with_climate_temporary_temperature(
            scheduled,
            room_id="living",
            target_temperature=23.5,
        )

        switched = with_active_climate_profile(changed, "night")

        room = switched.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertIsNone(room.temporary_override)
        self.assertEqual(room.night_profile.target_temperature, room.target_temperature)

    def test_contour_edit_updates_active_profile_and_keeps_inactive_profile(self) -> None:
        _, contours = setup()
        profiles = {
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
                "active_profile": "night",
            }
        }

        _, edited = build_climate_contour_setup(
            source_snapshot(),
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            room_parameters={
                "living": {
                    "target_temperature": 21.5,
                    "target_humidity": 35,
                    "strategy": "aggressive",
                }
            },
            room_profiles=profiles,
        )

        room = edited.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertEqual("night", room.active_profile.value)
        self.assertEqual(21.5, room.night_profile.target_temperature)
        self.assertEqual(25.0, room.day_profile.target_temperature)
        self.assertEqual(45, room.day_profile.target_humidity)

        invalid = copy.deepcopy(profiles)
        invalid["living"]["profiles"]["day"]["hidden"] = True  # type: ignore[index]
        with self.assertRaises(ContourRegistryViolation):
            with_climate_room_profiles(contours, invalid)

    def test_unavailable_or_manual_engine_never_looks_automatic(self) -> None:
        climate_registry, contours = setup()
        payload = source_payload()
        payload["rooms"][0]["mode"] = "manual"  # type: ignore[index]
        manual = import_climate_state(
            payload,
            now_ms=payload["generatedAt"] + 1_000,  # type: ignore[operator]
        )

        missing = contour_snapshot(contours, climate_registry, None)
        result = contour_snapshot(contours, climate_registry, manual)

        self.assertEqual("unavailable", missing["contours"][0]["status"])  # type: ignore[index]
        self.assertFalse(result["contours"][0]["execution"]["automatic_active"])  # type: ignore[index]

    def test_different_engine_target_never_looks_automatic(self) -> None:
        climate_registry, contours = setup()
        payload = source_payload()
        payload["rooms"][0]["targets"]["temperature"] = 26.0  # type: ignore[index]
        different = import_climate_state(
            payload,
            now_ms=payload["generatedAt"] + 1_000,  # type: ignore[operator]
        )

        result = contour_snapshot(contours, climate_registry, different)

        contour = result["contours"][0]  # type: ignore[index]
        self.assertEqual("attention", contour["status"])
        self.assertFalse(contour["execution"]["automatic_active"])
        self.assertFalse(contour["rooms"][0]["targets_in_sync"])

    def test_contour_bindings_reject_missing_and_cross_room_devices(self) -> None:
        climate_registry, contours = setup()
        payload = contour_registry_to_payload(contours)
        payload["contours"][0]["rooms"][0]["device_ids"] = ["missing"]  # type: ignore[index]
        invalid = contour_registry_from_payload(payload)

        with self.assertRaisesRegex(ContourRegistryViolation, "missing"):
            validate_contour_bindings(invalid, climate_registry)

    def test_mode_change_preserves_assignments_and_parameters(self) -> None:
        _, contours = setup()
        disabled = with_climate_contour_mode(contours, "disabled")

        self.assertIs(
            disabled.contour("climate").mode,  # type: ignore[union-attr]
            ContourMode.DISABLED,
        )
        self.assertEqual(
            contours.contour("climate").rooms,  # type: ignore[union-attr]
            disabled.contour("climate").rooms,  # type: ignore[union-attr]
        )

    def test_setup_requires_selected_rooms_devices_and_valid_parameters(self) -> None:
        snapshot = source_snapshot()
        for rooms, devices, temperature, humidity, strategy in (
            ([], ["synthetic-ac-source-living"], 25, 45, "normal"),
            (["living"], [], 25, 45, "normal"),
            (["living"], ["synthetic-ac-source-living"], 17, 45, "normal"),
            (["living"], ["synthetic-ac-source-living"], 25, 44, "normal"),
            (["living"], ["synthetic-ac-source-living"], 25, 45, "unknown"),
        ):
            with self.subTest(
                rooms=rooms,
                devices=devices,
                temperature=temperature,
                humidity=humidity,
                strategy=strategy,
            ), self.assertRaises(ContourRegistryViolation):
                build_climate_contour_setup(
                    snapshot,
                    room_ids=rooms,
                    source_ids=devices,
                    name="Климат",
                    mode="observe",
                    target_temperature=temperature,
                    target_humidity=humidity,
                    strategy=strategy,
                )

    def test_setup_keeps_distinct_parameters_for_each_selected_room(self) -> None:
        registry, contours = build_climate_contour_setup(
            source_snapshot(),
            room_ids=["living", "kids"],
            source_ids=[
                "synthetic-ac-source-living",
                "synthetic-humidifier-source-kids",
            ],
            name="Климат",
            mode="automatic",
            room_parameters={
                "living": {
                    "target_temperature": 25.0,
                    "target_humidity": 45,
                    "strategy": "normal",
                },
                "kids": {
                    "target_temperature": 23.5,
                    "target_humidity": 50,
                    "strategy": "soft",
                },
            },
        )

        contour = contours.contour("climate")
        self.assertEqual(2, len(registry.rooms))
        self.assertEqual(2, len(contour.rooms))  # type: ignore[union-attr]
        by_room = {room.room_id: room for room in contour.rooms}  # type: ignore[union-attr]
        self.assertEqual(25.0, by_room["living"].target_temperature)
        self.assertEqual(45, by_room["living"].target_humidity)
        self.assertEqual("normal", by_room["living"].strategy.value)
        self.assertEqual(23.5, by_room["kids"].target_temperature)
        self.assertEqual(50, by_room["kids"].target_humidity)
        self.assertEqual("soft", by_room["kids"].strategy.value)

    def test_per_room_parameters_must_exactly_match_selected_rooms(self) -> None:
        common = {
            "snapshot": source_snapshot(),
            "room_ids": ["living", "kids"],
            "source_ids": [
                "synthetic-ac-source-living",
                "synthetic-humidifier-source-kids",
            ],
            "name": "Климат",
            "mode": "automatic",
        }
        living = {
            "target_temperature": 25.0,
            "target_humidity": 45,
            "strategy": "normal",
        }
        for parameters in (
            {"living": living},
            {"living": living, "kids": living, "other": living},
            {
                "living": living,
                "kids": {**living, "hidden": "must-not-pass"},
            },
        ):
            with self.subTest(parameters=parameters), self.assertRaises(
                ContourRegistryViolation
            ):
                build_climate_contour_setup(
                    **common,
                    room_parameters=parameters,
                )

        with self.assertRaisesRegex(ContourRegistryViolation, "cannot be mixed"):
            build_climate_contour_setup(
                **common,
                target_temperature=25.0,
                room_parameters={"living": living, "kids": living},
            )

    def test_automatic_device_ids_stay_within_public_id_limit(self) -> None:
        payload = source_payload()
        long_room_id = "r" + "oom" * 21
        payload["rooms"][0]["id"] = long_room_id  # type: ignore[index]
        payload["devices"][0]["roomId"] = long_room_id  # type: ignore[index]
        snapshot = import_climate_state(
            payload,
            now_ms=payload["generatedAt"] + 1_000,  # type: ignore[operator]
        )

        registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=[long_room_id],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="observe",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )

        self.assertLessEqual(len(registry.devices[0].device_id), 64)
        self.assertEqual(
            registry.devices[0].device_id,
            contours.contours[0].rooms[0].device_ids[0],
        )


if __name__ == "__main__":
    unittest.main()
