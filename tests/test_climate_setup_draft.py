"""Safe creation of an unsaved HASC climate contour draft."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.climate_import import (
    import_climate_state,
)
from custom_components.hausman_hub.application.climate_registry import (
    registry_from_payload,
    registry_to_payload,
)
from custom_components.hausman_hub.application.climate_setup import (
    ClimateSetupViolation,
    climate_device_candidates,
    climate_setup_options,
    create_climate_contour_draft,
    validate_climate_contour_draft,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FIXTURE = ROOT / "fixtures" / "climate_bridge" / "valid_state.json"
DRAFT_FIXTURES = ROOT / "fixtures" / "hasc_climate_draft_v1"
CONTRACTS = ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def empty_registry() -> object:
    return registry_from_payload({"version": 1, "rooms": [], "devices": []})


class ClimateSetupDraftTest(unittest.TestCase):
    """A short candidate reference can build only one non-persistent draft."""

    def setUp(self) -> None:
        self.snapshot = import_climate_state(load_json(SOURCE_FIXTURE))
        self.registry = empty_registry()
        self.request = load_json(DRAFT_FIXTURES / "request.json")
        self.request_validator = Draft202012Validator(
            load_json(CONTRACTS / "climate-draft-request.schema.json")
        )
        self.draft_validator = Draft202012Validator(
            load_json(CONTRACTS / "climate-draft.schema.json")
        )
        self.options_validator = Draft202012Validator(
            load_json(CONTRACTS / "climate-setup-options.schema.json")
        )
        self.validation_validator = Draft202012Validator(
            load_json(CONTRACTS / "climate-draft-validation.schema.json")
        )

    def test_ready_draft_is_validated_without_mutating_inputs(self) -> None:
        draft = load_json(DRAFT_FIXTURES / "draft.json")
        draft_before = copy.deepcopy(draft)
        registry_before = registry_to_payload(self.registry)  # type: ignore[arg-type]
        snapshot_before = copy.deepcopy(self.snapshot)

        validation = validate_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            draft,
        )

        self.validation_validator.validate(validation)
        self.assertEqual(
            load_json(DRAFT_FIXTURES / "validation.json"),
            validation,
        )
        self.assertTrue(validation["save_allowed"])
        self.assertFalse(validation["command_allowed"])
        self.assertEqual(draft_before, draft)
        self.assertEqual(
            registry_before,
            registry_to_payload(self.registry),  # type: ignore[arg-type]
        )
        self.assertEqual(snapshot_before, self.snapshot)

    def test_sensor_only_room_is_blocked_with_plain_issue(self) -> None:
        source = copy.deepcopy(load_json(SOURCE_FIXTURE))
        source["rooms"] = [  # type: ignore[index]
            room for room in source["rooms"] if room["id"] == "living"  # type: ignore[index]
        ]
        source["devices"] = [  # type: ignore[index]
            {
                "id": "private-temperature-sensor",
                "name": "Датчик температуры",
                "roomId": "living",
                "domain": "sensor",
                "category": "temperature",
                "state": "25.0",
                "unavailable": False,
            }
        ]
        source["capabilities"] = []  # type: ignore[index]
        source["authorityReadiness"]["rooms"] = [  # type: ignore[index]
            room
            for room in source["authorityReadiness"]["rooms"]  # type: ignore[index]
            if room["roomId"] == "living"
        ]
        snapshot = import_climate_state(source)
        options = climate_setup_options(
            self.registry,  # type: ignore[arg-type]
            snapshot,
        )
        draft = create_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            snapshot,
            {
                "snapshot_revision": options["snapshot_revision"],
                "name": "Климат",
                "mode": "automatic",
                "rooms": [
                    {
                        "room_id": "living",
                        "target_temperature": 25.0,
                        "target_humidity": 45,
                        "strategy": "normal",
                        "devices": [
                            {
                                "candidate_id": "candidate_0001",
                                "type": "temperature_sensor",
                            }
                        ],
                    }
                ],
            },
        )

        validation = validate_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            snapshot,
            draft,
        )

        self.validation_validator.validate(validation)
        self.assertEqual("blocked", validation["status"])
        self.assertFalse(validation["save_allowed"])
        self.assertFalse(validation["command_allowed"])
        self.assertEqual(
            [
                {
                    "code": "no_controllable_device",
                    "room_id": "living",
                    "message": (
                        "В комнате нет устройства, которое может управлять климатом."
                    ),
                }
            ],
            validation["issues"],
        )
        self.assertNotIn(
            "private-temperature-sensor",
            json.dumps(validation, ensure_ascii=True, sort_keys=True),
        )

    def test_changed_draft_or_candidate_snapshot_cannot_be_validated(self) -> None:
        changed_draft = copy.deepcopy(load_json(DRAFT_FIXTURES / "draft.json"))
        changed_draft["name"] = "Другой климат"  # type: ignore[index]
        with self.assertRaisesRegex(ClimateSetupViolation, "changed"):
            validate_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                self.snapshot,
                changed_draft,
            )

        stale_revision = copy.deepcopy(load_json(DRAFT_FIXTURES / "draft.json"))
        stale_revision["snapshot_revision"] += 1  # type: ignore[index]
        with self.assertRaises(ClimateSetupViolation) as mismatch:
            validate_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                self.snapshot,
                stale_revision,
            )
        self.assertEqual("snapshot_changed", mismatch.exception.code)

    def test_incomplete_device_capabilities_block_future_save(self) -> None:
        source = copy.deepcopy(load_json(SOURCE_FIXTURE))
        source["rooms"] = [  # type: ignore[index]
            room for room in source["rooms"] if room["id"] == "living"  # type: ignore[index]
        ]
        source["devices"] = [  # type: ignore[index]
            device
            for device in source["devices"]  # type: ignore[index]
            if device["roomId"] == "living"
        ]
        source["capabilities"] = [  # type: ignore[index]
            {
                "deviceId": "synthetic-ac-source-living",
                "commandTypes": ["climate.set_temperature"],
            }
        ]
        source["authorityReadiness"]["rooms"] = [  # type: ignore[index]
            room
            for room in source["authorityReadiness"]["rooms"]  # type: ignore[index]
            if room["roomId"] == "living"
        ]
        snapshot = import_climate_state(source)
        options = climate_setup_options(
            self.registry,  # type: ignore[arg-type]
            snapshot,
        )
        draft = create_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            snapshot,
            {
                "snapshot_revision": options["snapshot_revision"],
                "name": "Климат",
                "mode": "automatic",
                "rooms": [
                    {
                        "room_id": "living",
                        "target_temperature": 25.0,
                        "target_humidity": 45,
                        "strategy": "normal",
                        "devices": [
                            {
                                "candidate_id": "candidate_0001",
                                "type": "air_conditioner",
                            }
                        ],
                    }
                ],
            },
        )

        validation = validate_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            snapshot,
            draft,
        )

        self.validation_validator.validate(validation)
        self.assertEqual("blocked", validation["status"])
        self.assertTrue(validation["checks"]["rooms_have_controllable_devices"])
        self.assertFalse(
            validation["checks"]["device_capabilities_supported"]
        )
        self.assertEqual("unsupported_device_set", validation["issues"][0]["code"])

    def test_validation_schema_rejects_blocked_result_with_passing_checks(self) -> None:
        invalid = copy.deepcopy(load_json(DRAFT_FIXTURES / "validation.json"))
        invalid["status"] = "blocked"  # type: ignore[index]
        invalid["save_allowed"] = False  # type: ignore[index]
        invalid["issues"] = [  # type: ignore[index]
            {
                "code": "no_controllable_device",
                "room_id": "living",
                "message": (
                    "В комнате нет устройства, которое может управлять климатом."
                ),
            }
        ]

        with self.assertRaises(Exception):
            self.validation_validator.validate(invalid)

    def test_setup_options_are_exact_understandable_and_private_id_free(self) -> None:
        options = climate_setup_options(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
        )

        self.options_validator.validate(options)
        self.assertEqual(load_json(DRAFT_FIXTURES / "options.json"), options)
        self.assertTrue(options["draft_creation_allowed"])
        serialized = json.dumps(options, ensure_ascii=True, sort_keys=True)
        for private_value in (
            "source_id",
            "entity_id",
            "synthetic-ac-source-living",
            "synthetic-humidifier-source-kids",
        ):
            self.assertNotIn(private_value, serialized)

        stale_payload = copy.deepcopy(load_json(SOURCE_FIXTURE))
        stale_payload["runtimeHealth"]["status"] = "stale"  # type: ignore[index]
        stale = climate_setup_options(
            self.registry,  # type: ignore[arg-type]
            import_climate_state(stale_payload),
        )
        self.options_validator.validate(stale)
        self.assertFalse(stale["draft_creation_allowed"])
        self.assertFalse(any(device["can_add"] for device in stale["devices"]))

    def test_valid_request_creates_exact_sorted_private_id_free_draft(self) -> None:
        request_before = copy.deepcopy(self.request)
        registry_before = registry_to_payload(self.registry)  # type: ignore[arg-type]
        snapshot_before = copy.deepcopy(self.snapshot)

        draft = create_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            self.request,
        )

        self.request_validator.validate(self.request)
        self.draft_validator.validate(draft)
        self.assertEqual(load_json(DRAFT_FIXTURES / "draft.json"), draft)
        self.assertFalse(draft["save_allowed"])
        self.assertTrue(draft["validation_required"])
        self.assertEqual(request_before, self.request)
        self.assertEqual(
            registry_before,
            registry_to_payload(self.registry),  # type: ignore[arg-type]
        )
        self.assertEqual(snapshot_before, self.snapshot)
        serialized = json.dumps(draft, ensure_ascii=True, sort_keys=True)
        for private_value in (
            "source_id",
            "entity_id",
            "synthetic-ac-source-living",
            "synthetic-humidifier-source-kids",
        ):
            self.assertNotIn(private_value, serialized)

    def test_changed_or_stale_candidate_snapshot_is_rejected_as_conflict(self) -> None:
        changed = copy.deepcopy(self.request)
        changed["snapshot_revision"] += 1  # type: ignore[index]
        with self.assertRaises(ClimateSetupViolation) as mismatch:
            create_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                self.snapshot,
                changed,
            )
        self.assertEqual("snapshot_changed", mismatch.exception.code)

        stale_payload = copy.deepcopy(load_json(SOURCE_FIXTURE))
        stale_payload["runtimeHealth"]["status"] = "stale"  # type: ignore[index]
        with self.assertRaises(ClimateSetupViolation) as stale:
            create_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                import_climate_state(stale_payload),
                self.request,
            )
        self.assertEqual("data_stale", stale.exception.code)

    def test_candidate_cannot_be_reused_or_moved_to_another_room(self) -> None:
        repeated = copy.deepcopy(self.request)
        repeated["rooms"][1]["devices"] = [  # type: ignore[index]
            {"candidate_id": "candidate_0002", "type": "air_conditioner"}
        ]
        with self.assertRaisesRegex(ClimateSetupViolation, "repeated"):
            create_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                self.snapshot,
                repeated,
            )

        moved = copy.deepcopy(self.request)
        moved["rooms"] = [  # type: ignore[index]
            {
                "room_id": "living",
                "target_temperature": 25.0,
                "target_humidity": 45,
                "strategy": "normal",
                "devices": [
                    {"candidate_id": "candidate_0001", "type": "humidifier"}
                ],
            }
        ]
        with self.assertRaisesRegex(ClimateSetupViolation, "room differs"):
            create_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                self.snapshot,
                moved,
            )

    def test_only_detected_device_type_and_valid_comfort_values_are_accepted(self) -> None:
        wrong_type = copy.deepcopy(self.request)
        wrong_type["rooms"][0]["devices"][0]["type"] = "humidifier"  # type: ignore[index]
        with self.assertRaisesRegex(ClimateSetupViolation, "type is invalid"):
            create_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                self.snapshot,
                wrong_type,
            )

        for field, invalid in (
            ("target_temperature", 25.2),
            ("target_humidity", 47),
            ("strategy", "turbo"),
        ):
            with self.subTest(field=field):
                request = copy.deepcopy(self.request)
                request["rooms"][0][field] = invalid  # type: ignore[index]
                with self.assertRaises(ClimateSetupViolation):
                    create_climate_contour_draft(
                        self.registry,  # type: ignore[arg-type]
                        self.snapshot,
                        request,
                    )

    def test_configured_candidate_and_extra_fields_are_not_silently_accepted(self) -> None:
        configured_registry = registry_from_payload(
            load_json(ROOT / "fixtures" / "hasc_climate_v1" / "registry.json")
        )
        configured_request = copy.deepcopy(self.request)
        configured_request["snapshot_revision"] = climate_device_candidates(
            configured_registry,
            self.snapshot,
        )["snapshot_revision"]  # type: ignore[index]
        with self.assertRaisesRegex(ClimateSetupViolation, "unavailable"):
            create_climate_contour_draft(
                configured_registry,
                self.snapshot,
                configured_request,
            )

        extra = copy.deepcopy(self.request)
        extra["confirm"] = True  # type: ignore[index]
        with self.assertRaisesRegex(ClimateSetupViolation, "fields"):
            create_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                self.snapshot,
                extra,
            )


if __name__ == "__main__":
    unittest.main()
