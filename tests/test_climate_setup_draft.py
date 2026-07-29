"""Safe creation of an unsaved HausmanHub climate contour draft."""

from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from tests.climate_bridge_fixture import (
    import_climate_state,
)
from custom_components.hausman_hub.application.climate_registry import (
    registry_from_payload,
    registry_to_payload,
)
from custom_components.hausman_hub.application.climate_setup import (
    ClimateSetupViolation,
    build_climate_contour_draft_setup,
    climate_device_candidates,
    climate_draft_save_receipt,
    climate_setup_options,
    current_climate_contour_setup,
    create_climate_contour_draft,
    validate_climate_contour_draft,
)
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
    with_climate_schedule,
)
from tests.test_climate_setup_current import configured_setup


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FIXTURE = ROOT / "fixtures" / "climate_bridge" / "valid_state.json"
DRAFT_FIXTURES = ROOT / "fixtures" / "hausmanhub_climate_draft_v1"
CONTRACTS = ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def empty_registry() -> object:
    return registry_from_payload({"version": 3, "home": {"outdoor_temperature_entity_id": None, "presence_entity_id": None, "central_heating_entity_id": None}, "rooms": [], "devices": []})


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
        self.save_validator = Draft202012Validator(
            load_json(CONTRACTS / "climate-draft-save.schema.json")
        )

    def test_ready_draft_builds_one_exact_setup_and_private_free_receipt(self) -> None:
        draft = load_json(DRAFT_FIXTURES / "draft.json")
        draft_before = copy.deepcopy(draft)
        registry_before = registry_to_payload(self.registry)  # type: ignore[arg-type]
        snapshot_before = copy.deepcopy(self.snapshot)

        registry, contours, validation = build_climate_contour_draft_setup(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            draft,
        )
        receipt = climate_draft_save_receipt(draft, validation)

        self.save_validator.validate(receipt)
        self.assertEqual(load_json(DRAFT_FIXTURES / "save.json"), receipt)
        self.assertEqual(["kids", "living"], [room.room_id for room in registry.rooms])
        self.assertEqual(
            ["kids_humidifier", "living_air_conditioner"],
            [device.device_id for device in registry.devices],
        )
        contour = contours.contours[0]
        self.assertEqual("climate", contour.contour_id)
        self.assertEqual("Климат дома", contour.name)
        self.assertEqual(
            [24.0, 25.0],
            [room.day_profile.target_temperature for room in contour.rooms],
        )
        self.assertEqual(draft_before, draft)
        self.assertEqual(
            registry_before,
            registry_to_payload(self.registry),  # type: ignore[arg-type]
        )
        self.assertEqual(snapshot_before, self.snapshot)
        serialized = json.dumps(receipt, ensure_ascii=True, sort_keys=True)
        for private_value in (
            "source_id",
            "synthetic-ac-source-living",
            "synthetic-humidifier-source-kids",
        ):
            self.assertNotIn(private_value, serialized)

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

    def test_draft_round_trips_room_bounds_and_managed_control_channel(self) -> None:
        request = copy.deepcopy(self.request)
        living = next(room for room in request["rooms"] if room["room_id"] == "living")
        living["min_temperature"] = 22.0
        living["max_temperature"] = 26.0
        living["devices"][0]["control_channel"] = "direct_wifi"

        draft = create_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            request,
        )
        validation = validate_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            draft,
        )
        registry, contours, _ = build_climate_contour_draft_setup(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            draft,
        )
        current = current_climate_contour_setup(registry, contours, self.snapshot)

        self.assertTrue(validation["save_allowed"])
        self.assertEqual(22.0, draft["rooms"][1]["min_temperature"])
        self.assertEqual(26.0, draft["rooms"][1]["max_temperature"])
        self.assertEqual(
            "direct_wifi",
            draft["rooms"][1]["devices"][0]["control_channel"],
        )
        saved_living = next(room for room in contours.contours[0].rooms if room.room_id == "living")
        self.assertEqual(22.0, saved_living.min_temperature)
        self.assertEqual(26.0, saved_living.max_temperature)
        saved_device = next(
            device
            for device in registry.devices
            if device.source_id == "synthetic-ac-source-living"
        )
        self.assertEqual("direct_wifi", saved_device.control_channel.value)
        current_living = next(room for room in current["rooms"] if room["id"] == "living")
        self.assertEqual(22.0, current_living["min_temperature"])
        self.assertEqual(26.0, current_living["max_temperature"])
        self.assertEqual(
            "direct_wifi",
            current_living["devices"][0]["control_channel"],
        )

    def test_legacy_draft_without_new_fields_defaults_to_null(self) -> None:
        draft = create_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            self.request,
        )

        self.assertTrue(
            all(room["min_temperature"] is None for room in draft["rooms"])
        )
        self.assertTrue(
            all(room["max_temperature"] is None for room in draft["rooms"])
        )
        self.assertTrue(
            all(
                device["control_channel"] is None
                for room in draft["rooms"]
                for device in room["devices"]
            )
        )
        legacy_draft = copy.deepcopy(draft)
        for room in legacy_draft["rooms"]:
            room.pop("min_temperature")
            room.pop("max_temperature")
            for device in room["devices"]:
                device.pop("control_channel")

        validation = validate_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            legacy_draft,
        )

        self.assertTrue(validation["save_allowed"])

    def test_draft_validation_blocks_invalid_bounds_and_control_channels(self) -> None:
        invalid_bounds = copy.deepcopy(self.request)
        living = next(
            room for room in invalid_bounds["rooms"] if room["room_id"] == "living"
        )
        living["min_temperature"] = 25.5
        bounds_draft = create_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            invalid_bounds,
        )

        bounds_validation = validate_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            bounds_draft,
        )

        self.validation_validator.validate(bounds_validation)
        self.assertEqual("blocked", bounds_validation["status"])
        self.assertEqual("invalid_temperature_bounds", bounds_validation["issues"][0]["code"])
        self.assertEqual("living", bounds_validation["issues"][0]["room_id"])

        invalid_channel = copy.deepcopy(self.request)
        living = next(
            room for room in invalid_channel["rooms"] if room["room_id"] == "living"
        )
        living["devices"][0]["control_channel"] = "not_a_channel"
        channel_draft = create_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            invalid_channel,
        )

        channel_validation = validate_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            channel_draft,
        )

        self.validation_validator.validate(channel_validation)
        self.assertEqual("blocked", channel_validation["status"])
        self.assertEqual("invalid_control_channel", channel_validation["issues"][0]["code"])
        self.assertEqual("living", channel_validation["issues"][0]["room_id"])
        self.assertEqual(
            living["devices"][0]["candidate_id"],
            channel_validation["issues"][0]["candidate_id"],
        )

        source = copy.deepcopy(load_json(SOURCE_FIXTURE))
        source["devices"].append(  # type: ignore[union-attr]
            {
                "id": "synthetic-living-temperature",
                "name": "Living temperature",
                "roomId": "living",
                "domain": "sensor",
                "category": "temperature",
                "state": "24.0",
                "unavailable": False,
            }
        )
        snapshot = import_climate_state(source)
        options = climate_setup_options(self.registry, snapshot)  # type: ignore[arg-type]
        observed_candidate = next(
            device
            for device in options["devices"]
            if "temperature_sensor" in device["suggested_types"]
        )
        observed_channel = copy.deepcopy(self.request)
        observed_channel["snapshot_revision"] = options["snapshot_revision"]
        living = next(
            room for room in observed_channel["rooms"] if room["room_id"] == "living"
        )
        living["devices"].append(
            {
                "candidate_id": observed_candidate["candidate_id"],
                "type": "temperature_sensor",
                "control_channel": "universal_ir",
            }
        )
        observed_draft = create_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            snapshot,
            observed_channel,
        )

        observed_validation = validate_climate_contour_draft(
            self.registry,  # type: ignore[arg-type]
            snapshot,
            observed_draft,
        )

        self.validation_validator.validate(observed_validation)
        self.assertEqual("blocked", observed_validation["status"])
        self.assertEqual(
            "invalid_control_channel",
            observed_validation["issues"][0]["code"],
        )
        self.assertEqual("living", observed_validation["issues"][0]["room_id"])
        self.assertEqual(
            observed_candidate["candidate_id"],
            observed_validation["issues"][0]["candidate_id"],
        )

    def test_draft_validation_blocks_channel_for_preserved_canary_device(self) -> None:
        registry, contours, snapshot = configured_setup()
        registry_payload = registry_to_payload(registry)
        for device in registry_payload["devices"]:  # type: ignore[union-attr]
            if device["id"] == "living_air_conditioner":
                device["control_scope"] = "canary"
                device["endpoints"] = [
                    {"role": "control", "entity_id": "climate.living_ac"}
                ]
        canary_registry = registry_from_payload(registry_payload)
        current = current_climate_contour_setup(
            canary_registry,
            contours,
            snapshot,
        )
        request = {
            "snapshot_revision": current["snapshot_revision"],
            "setup_revision": current["setup_revision"],
            "name": current["name"],
            "mode": current["mode"],
            "rooms": [
                {
                    "room_id": room["id"],
                    "target_temperature": room["profiles"]["day"]["target_temperature"],
                    "target_humidity": room["profiles"]["day"]["target_humidity"],
                    "strategy": room["profiles"]["day"]["strategy"],
                    "devices": [
                        {
                            "candidate_id": device["candidate_id"],
                            "type": device["type"],
                            "control_channel": (
                                "universal_ir"
                                if room["id"] == "living"
                                else None
                            ),
                        }
                        for device in room["devices"]
                    ],
                }
                for room in current["rooms"]
            ],
        }
        draft = create_climate_contour_draft(
            canary_registry,
            snapshot,
            request,
            contours=contours,
        )

        validation = validate_climate_contour_draft(
            canary_registry,
            snapshot,
            draft,
            contours=contours,
        )

        self.assertEqual("blocked", validation["status"])
        self.assertFalse(validation["save_allowed"])
        self.assertEqual(
            "invalid_control_channel",
            validation["issues"][0]["code"],
        )
        self.assertEqual("living", validation["issues"][0]["room_id"])

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
                    "level": "error",
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
        with self.assertRaises(ClimateSetupViolation) as blocked:
            build_climate_contour_draft_setup(
                self.registry,  # type: ignore[arg-type]
                snapshot,
                draft,
            )
        self.assertEqual("draft_blocked", blocked.exception.code)

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

    def test_setup_options_project_bounded_private_id_free_ir_remotes(self) -> None:
        from custom_components.hausman_hub.application.climate_native_setup import (
            ClimateHaCatalogEntry,
            ClimateHaCatalogRoom,
            ClimateHaEntityCatalog,
        )

        remotes = ClimateHaEntityCatalog(
            rooms=(
                ClimateHaCatalogRoom(room_id="living", name="Гостиная"),
            ),
            entries=(
                ClimateHaCatalogEntry(
                    entity_id="remote.living_broadlink",
                    domain="remote",
                    state="on",
                    device_class=None,
                    supported_features=0,
                    friendly_name="  Пульт   гостиной  ",
                    available=True,
                    last_updated_ms=1,
                    room_id="living",
                ),
                ClimateHaCatalogEntry(
                    entity_id="remote.roomless",
                    domain="remote",
                    state="unavailable",
                    device_class=None,
                    supported_features=0,
                    friendly_name=None,
                    available=False,
                    last_updated_ms=1,
                ),
                ClimateHaCatalogEntry(
                    entity_id="climate.not_a_remote",
                    domain="climate",
                    state="cool",
                    device_class=None,
                    supported_features=0,
                    friendly_name="Фасад",
                    available=True,
                    last_updated_ms=1,
                    room_id="living",
                ),
            ),
        )

        options = climate_setup_options(
            self.registry,  # type: ignore[arg-type]
            self.snapshot,
            remotes,
        )

        self.options_validator.validate(options)
        self.assertEqual(
            [
                {
                    "name": "Пульт гостиной",
                    "room_id": "living",
                    "available": True,
                },
                {"name": "ИК-пульт", "room_id": "", "available": False},
            ],
            options["ir_remotes"],
        )
        serialized = json.dumps(options, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("entity_id", serialized)
        self.assertNotIn("remote.living_broadlink", serialized)

        with self.assertRaises(ClimateSetupViolation):
            climate_setup_options(
                self.registry,  # type: ignore[arg-type]
                self.snapshot,
                object(),  # type: ignore[arg-type]
            )

    def test_setup_options_schema_covers_roomless_candidates_and_ir_remotes(self) -> None:
        options = copy.deepcopy(load_json(DRAFT_FIXTURES / "options.json"))
        options["ir_remotes"] = [
            {"name": "Пульт гостиной", "room_id": "living", "available": True},
            {"name": "ИК-пульт", "room_id": "", "available": False},
        ]
        options["devices"].append(
            {
                "candidate_id": "candidate_0003",
                "candidate_key": "ckey_000000000003",
                "name": "Комнатный фасад SmartIR",
                "room_id": "",
                "suggested_types": ["air_conditioner"],
                "recommended_type": "air_conditioner",
                "status": "available",
                "suggested_room_id": None,
                "suggested_room_name": None,
                "reason": "unassigned_room",
                "can_add": True,
            }
        )

        self.options_validator.validate(options)

        invalid = copy.deepcopy(options)
        invalid["devices"][-1]["reason"] = "detected_room"  # type: ignore[index]
        with self.assertRaises(Exception):
            self.options_validator.validate(invalid)
        invalid = copy.deepcopy(options)
        invalid["ir_remotes"][0]["entity_id"] = "remote.living"  # type: ignore[index]
        with self.assertRaises(Exception):
            self.options_validator.validate(invalid)
        invalid = copy.deepcopy(options)
        invalid["devices"][-1]["room_id"] = "kids"  # type: ignore[index]
        with self.assertRaises(Exception):
            self.options_validator.validate(invalid)

    def test_setup_options_schema_allows_only_official_zigbee2mqtt_images(self) -> None:
        options = copy.deepcopy(load_json(DRAFT_FIXTURES / "options.json"))
        options["devices"][0].update(  # type: ignore[index]
            {
                "device_group_id": "device_0123456789abcdef",
                "device_name": "Климат детская",
                "manufacturer": "KOJIMA",
                "model": "Temperature and humidity sensor",
                "image_url": (
                    "https://www.zigbee2mqtt.io/images/devices/"
                    "KOJIMA-THS-ZG-LCD.png"
                ),
            }
        )

        self.options_validator.validate(options)

        options["devices"][0]["image_url"] = "https://example.com/device.png"  # type: ignore[index]
        with self.assertRaises(Exception):
            self.options_validator.validate(options)

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

        roomless_snapshot = replace(
            self.snapshot,
            devices=tuple(
                replace(device, room_id="")
                if device.room_id == "kids"
                else device
                for device in self.snapshot.devices
            ),
        )
        roomless = copy.deepcopy(self.request)
        roomless["snapshot_revision"] = climate_setup_options(
            self.registry,  # type: ignore[arg-type]
            roomless_snapshot,
        )["snapshot_revision"]
        with self.assertRaisesRegex(ClimateSetupViolation, "room differs"):
            create_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                roomless_snapshot,
                roomless,
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
            ("target_humidity", 75),
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

    def test_configured_candidate_is_accepted_only_unchanged_in_its_room(self) -> None:
        configured_registry = registry_from_payload(
            load_json(ROOT / "fixtures" / "hausmanhub_climate_v1" / "registry.json")
        )
        configured_request = copy.deepcopy(self.request)
        configured_request["snapshot_revision"] = climate_device_candidates(
            configured_registry,
            self.snapshot,
        )["snapshot_revision"]  # type: ignore[index]
        draft = create_climate_contour_draft(
            configured_registry,
            self.snapshot,
            configured_request,
        )
        self.draft_validator.validate(draft)

        retyped = copy.deepcopy(configured_request)
        retyped["rooms"][0]["devices"][0]["type"] = "floor_heating"  # type: ignore[index]
        with self.assertRaisesRegex(ClimateSetupViolation, "unavailable"):
            create_climate_contour_draft(
                configured_registry,
                self.snapshot,
                retyped,
            )

        moved = copy.deepcopy(configured_request)
        moved["rooms"][0]["devices"] = [  # type: ignore[index]
            {"candidate_id": "candidate_0001", "type": "humidifier"}
        ]
        with self.assertRaisesRegex(ClimateSetupViolation, "unavailable"):
            create_climate_contour_draft(
                configured_registry,
                self.snapshot,
                moved,
            )

        extra = copy.deepcopy(self.request)
        extra["confirm"] = True  # type: ignore[index]
        with self.assertRaisesRegex(ClimateSetupViolation, "fields"):
            create_climate_contour_draft(
                self.registry,  # type: ignore[arg-type]
                self.snapshot,
                extra,
            )

    def test_edit_preserves_profiles_schedule_overrides_bindings_and_ids(self) -> None:
        registry, contours, snapshot = configured_setup()
        registry_payload = registry_to_payload(registry)  # type: ignore[arg-type]
        registry_payload["home"]["outdoor_temperature_entity_id"] = (  # type: ignore[index]
            "sensor.outdoor"
        )
        for room_payload in registry_payload["rooms"]:  # type: ignore[union-attr]
            if room_payload["id"] == "living":
                room_payload["window_entity_id"] = "binary_sensor.living_window"
                room_payload["presence_entity_ids"] = [
                    "binary_sensor.living_motion",
                    "binary_sensor.living_occupancy",
                ]
        registry = registry_from_payload(registry_payload)
        current = current_climate_contour_setup(
            registry,
            contours,  # type: ignore[arg-type]
            snapshot,  # type: ignore[arg-type]
        )
        request_rooms = []
        for room in current["rooms"]:  # type: ignore[union-attr]
            active_profile = room["profiles"]["active_profile"]
            active = room["profiles"][active_profile]
            request_rooms.append(
                {
                    "room_id": room["id"],
                    "target_temperature": (
                        26.0 if room["id"] == "living" else active["target_temperature"]
                    ),
                    "target_humidity": active["target_humidity"],
                    "strategy": active["strategy"],
                    "devices": [
                        {
                            "candidate_id": device["candidate_id"],
                            "type": device["type"],
                        }
                        for device in room["devices"]
                    ],
                }
            )
        request = {
            "snapshot_revision": current["snapshot_revision"],
            "setup_revision": current["setup_revision"],
            "name": current["name"],
            "mode": current["mode"],
            "rooms": request_rooms,
        }
        self.request_validator.validate(request)
        missing_revision = copy.deepcopy(request)
        missing_revision.pop("setup_revision")
        with self.assertRaises(ClimateSetupViolation) as missing:
            create_climate_contour_draft(
                registry,
                snapshot,  # type: ignore[arg-type]
                missing_revision,
                contours=contours,  # type: ignore[arg-type]
            )
        self.assertEqual("setup_changed", missing.exception.code)
        draft = create_climate_contour_draft(
            registry,
            snapshot,  # type: ignore[arg-type]
            request,
            contours=contours,  # type: ignore[arg-type]
        )

        updated_registry, updated_contours, validation = (
            build_climate_contour_draft_setup(
                registry,
                snapshot,  # type: ignore[arg-type]
                draft,
                contours=contours,  # type: ignore[arg-type]
            )
        )

        self.assertEqual("ready", validation["status"])
        updated = updated_contours.contour("climate")
        self.assertIsNotNone(updated)
        assert updated is not None
        living = next(room for room in updated.rooms if room.room_id == "living")
        kids = next(room for room in updated.rooms if room.room_id == "kids")
        self.assertEqual(26.0, living.day_profile.target_temperature)
        self.assertEqual(22.0, living.night_profile.target_temperature)
        self.assertEqual(21.0, kids.night_profile.target_temperature)
        self.assertTrue(updated.schedule.enabled)
        self.assertEqual("07:00", updated.schedule.day_start)
        self.assertEqual("day", updated.schedule.last_applied_profile.value)  # type: ignore[union-attr]
        self.assertEqual(23.5, living.temporary_override.target_temperature)  # type: ignore[union-attr]
        self.assertEqual(
            [device.device_id for device in registry.devices],  # type: ignore[union-attr]
            [device.device_id for device in updated_registry.devices],
        )
        self.assertEqual(
            "sensor.outdoor",
            updated_registry.home.outdoor_temperature_entity_id,
        )
        self.assertEqual(
            "binary_sensor.living_window",
            updated_registry.room("living").window_entity_id,  # type: ignore[union-attr]
        )
        self.assertEqual(
            (
                "binary_sensor.living_motion",
                "binary_sensor.living_occupancy",
            ),
            updated_registry.room("living").presence_entity_ids,  # type: ignore[union-attr]
        )

        stale_request = copy.deepcopy(request)
        stale_request["setup_revision"] += 1
        with self.assertRaises(ClimateSetupViolation) as stale:
            create_climate_contour_draft(
                registry,
                snapshot,  # type: ignore[arg-type]
                stale_request,
                contours=contours,  # type: ignore[arg-type]
            )
        self.assertEqual("setup_changed", stale.exception.code)

        changed_contours = with_climate_schedule(
            contours,  # type: ignore[arg-type]
            enabled=True,
            day_start="08:00",
            night_start="23:00",
        )
        with self.assertRaises(ClimateSetupViolation) as changed:
            validate_climate_contour_draft(
                registry,
                snapshot,  # type: ignore[arg-type]
                draft,
                contours=changed_contours,
            )
        self.assertEqual("setup_changed", changed.exception.code)

    def test_adding_an_earlier_sensor_keeps_existing_public_device_id(self) -> None:
        initial_source = copy.deepcopy(load_json(SOURCE_FIXTURE))
        initial_source["devices"].append(  # type: ignore[union-attr]
            {
                "id": "sensor-zulu",
                "name": "Zulu temperature",
                "roomId": "living",
                "domain": "sensor",
                "category": "temperature",
                "state": "22.0",
                "unavailable": False,
            }
        )
        initial_snapshot = import_climate_state(initial_source)
        registry, contours = build_climate_contour_setup(
            initial_snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living", "sensor-zulu"],
            name="Климат",
            mode="automatic",
            target_temperature=22.0,
            target_humidity=45,
            strategy="normal",
        )
        zulu_before = next(
            device for device in registry.devices if device.source_id == "sensor-zulu"
        )

        updated_source = copy.deepcopy(initial_source)
        updated_source["devices"].append(  # type: ignore[union-attr]
            {
                "id": "sensor-alpha",
                "name": "Alpha temperature",
                "roomId": "living",
                "domain": "sensor",
                "category": "temperature",
                "state": "23.0",
                "unavailable": False,
            }
        )
        updated_snapshot = import_climate_state(updated_source)
        options = climate_setup_options(registry, updated_snapshot)
        current = current_climate_contour_setup(
            registry,
            contours,
            updated_snapshot,
        )
        candidate_by_name = {
            device["name"]: device["candidate_id"]
            for device in options["devices"]  # type: ignore[union-attr]
        }
        current_room = current["rooms"][0]  # type: ignore[index]
        devices = [
            {
                "candidate_id": device["candidate_id"],
                "type": device["type"],
            }
            for device in current_room["devices"]
        ]
        devices.append(
            {
                "candidate_id": candidate_by_name["Alpha temperature"],
                "type": "temperature_sensor",
            }
        )
        draft = create_climate_contour_draft(
            registry,
            updated_snapshot,
            {
                "snapshot_revision": options["snapshot_revision"],
                "setup_revision": current["setup_revision"],
                "name": "Климат",
                "mode": "automatic",
                "rooms": [
                    {
                        "room_id": "living",
                        "target_temperature": 22.0,
                        "target_humidity": 45,
                        "strategy": "normal",
                        "devices": devices,
                    }
                ],
            },
            contours=contours,
        )

        updated_registry, updated_contours, _ = build_climate_contour_draft_setup(
            registry,
            updated_snapshot,
            draft,
            contours=contours,
        )

        zulu_after = next(
            device
            for device in updated_registry.devices
            if device.source_id == "sensor-zulu"
        )
        alpha = next(
            device
            for device in updated_registry.devices
            if device.source_id == "sensor-alpha"
        )
        self.assertEqual(zulu_before.device_id, zulu_after.device_id)
        self.assertNotEqual(zulu_after.device_id, alpha.device_id)
        saved = updated_contours.contour("climate")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(
            {device.device_id for device in updated_registry.devices},
            set(saved.rooms[0].device_ids),
        )


if __name__ == "__main__":
    unittest.main()
