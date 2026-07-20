"""Compatibility fixtures for the installed HASC climate JSON contracts."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.api_capabilities import (
    api_capabilities_snapshot,
)
from custom_components.hausman_hub.application.android_climate import (
    admin_climate_import_snapshot,
    android_climate_snapshot,
)
from custom_components.hausman_hub.application.climate_canary_preflight import (
    climate_canary_preflight,
)
from custom_components.hausman_hub.application.climate_import import import_climate_state
from custom_components.hausman_hub.application.climate_registry import registry_from_payload
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
    contour_snapshot,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
from tests.test_climate_canary_preflight import NOW, ready_inputs


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "custom_components" / "hausman_hub" / "contracts"
FIXTURES = ROOT / "fixtures" / "hasc_climate_v1"
SOURCE_FIXTURE = ROOT / "fixtures" / "climate_bridge" / "valid_state.json"
LOCAL_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone(timedelta(hours=3)))


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def validator(name: str) -> Draft202012Validator:
    schema = load_json(SCHEMAS / name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


class ClimateContractSchemasTest(unittest.TestCase):
    """Keep examples and generated projections compatible with schema v1."""

    def test_every_packaged_schema_is_valid_and_each_fixture_matches(self) -> None:
        pairs = {
            "hasc_capabilities_v1/capabilities.json": "v1/api-capabilities.schema.json",
            "hasc_climate_v1/action-request.json": "v1/climate-action-request.schema.json",
            "hasc_climate_v1/operation-query.json": "v1/climate-operation-query.schema.json",
            "hasc_climate_v1/operation-receipt.json": "v1/climate-operation-receipt.schema.json",
            "hasc_climate_v1/registry.json": "v1/climate-registry.schema.json",
            "hasc_climate_v1/readiness.json": "v1/climate-readiness.schema.json",
            "hasc_climate_v1/registry-preview.json": "v1/climate-registry-preview.schema.json",
            "hasc_climate_v1/shadow-candidate-query.json": "v1/climate-shadow-candidate-query.schema.json",
            "hasc_climate_v1/shadow-evidence.json": "v1/climate-shadow-evidence.schema.json",
            "hasc_climate_v1/canary-preflight-query.json": "v1/climate-canary-preflight-query.schema.json",
            "hasc_climate_v1/canary-preflight.json": "v1/climate-canary-preflight.schema.json",
            "hasc_climate_v2/home.json": "v2/climate-home.schema.json",
            "hasc_climate_v3/home.json": "v3/climate-home.schema.json",
            "hasc_climate_v4/home.json": "v4/climate-home.schema.json",
            "hasc_climate_v5/home.json": "v5/climate-home.schema.json",
            "hasc_climate_v6/home.json": "v6/climate-home.schema.json",
            "hasc_climate_v7/home.json": "v7/climate-home.schema.json",
            "hasc_climate_v8/home.json": "v8/climate-home.schema.json",
            "hasc_climate_v9/home.json": "v9/climate-home.schema.json",
            "hasc_contours_v1/contours.json": "v1/contours.schema.json",
            "hasc_contours_v2/contours.json": "v2/contours.schema.json",
            "hasc_contours_v3/contours.json": "v3/contours.schema.json",
            "hasc_contours_v4/contours.json": "v4/contours.schema.json",
            "hasc_contours_v5/contours.json": "v5/contours.schema.json",
            "hasc_contours_v6/contours.json": "v6/contours.schema.json",
            "hasc_contours_v7/contours.json": "v7/contours.schema.json",
            "hasc_contour_apply_v1/request.json": "v1/contour-apply-request.schema.json",
            "hasc_contour_apply_v1/preview.json": "v1/contour-apply-preview.schema.json",
            "hasc_contour_apply_v1/receipt.json": "v1/contour-apply-receipt.schema.json",
            "hasc_temporary_temperature_v1/request.json": "v1/temporary-temperature-request.schema.json",
        }
        for fixture_name, schema_name in pairs.items():
            with self.subTest(fixture=fixture_name):
                validator(schema_name).validate(load_json(ROOT / "fixtures" / fixture_name))

        for schema_path in SCHEMAS.rglob("*.schema.json"):
            with self.subTest(schema=schema_path.name):
                Draft202012Validator.check_schema(load_json(schema_path))

    def test_generated_home_and_admin_import_match_their_contracts(self) -> None:
        capabilities = api_capabilities_snapshot()
        validator("v1/api-capabilities.schema.json").validate(capabilities)
        self.assertEqual(
            load_json(ROOT / "fixtures" / "hasc_capabilities_v1" / "capabilities.json"),
            capabilities,
        )

        registry = registry_from_payload(load_json(FIXTURES / "registry.json"))
        snapshot = import_climate_state(load_json(SOURCE_FIXTURE))

        contour_climate_registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        home = android_climate_snapshot(
            contour_climate_registry,
            snapshot,
            contours=contours,
            bridge_mode=ClimateBridgeMode.SHADOW,
            local_now=LOCAL_NOW,
        )
        admin = admin_climate_import_snapshot(registry, snapshot)

        validator("v9/climate-home.schema.json").validate(home)
        validator("v1/climate-admin-import.schema.json").validate(admin)
        serialized_home = json.dumps(home, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("source_id", serialized_home)
        self.assertNotIn("entity_id", serialized_home)

        stale_source = load_json(SOURCE_FIXTURE)
        stale_source["runtimeHealth"]["status"] = "stale"  # type: ignore[index]
        stale_home = android_climate_snapshot(
            contour_climate_registry,
            import_climate_state(stale_source),
            contours=contours,
            bridge_mode=ClimateBridgeMode.SHADOW,
            local_now=LOCAL_NOW,
        )
        validator("v9/climate-home.schema.json").validate(stale_home)
        self.assertEqual(
            "stale",
            stale_home["rooms"][0]["actual"]["data_status"],  # type: ignore[index]
        )

        missing_source = load_json(SOURCE_FIXTURE)
        missing_source["rooms"] = []  # type: ignore[index]
        missing_source["devices"] = []  # type: ignore[index]
        missing_source["capabilities"] = []  # type: ignore[index]
        missing_source["authorityReadiness"]["rooms"] = []  # type: ignore[index]
        missing_home = android_climate_snapshot(
            contour_climate_registry,
            import_climate_state(missing_source),
            contours=contours,
            bridge_mode=ClimateBridgeMode.SHADOW,
            local_now=LOCAL_NOW,
        )
        validator("v9/climate-home.schema.json").validate(missing_home)
        self.assertEqual(
            {
                "data_status": "unavailable",
                "temperature": None,
                "humidity": None,
                "mode": "unknown",
            },
            missing_home["rooms"][0]["actual"],  # type: ignore[index]
        )

        preflight_registry, preflight_snapshot, evidence = ready_inputs()
        preflight = climate_canary_preflight(
            preflight_registry,
            preflight_snapshot,
            evidence,
            bridge_mode=ClimateBridgeMode.SHADOW,
            room_id="living",
            pending_operation=False,
            checked_at=NOW,
        )
        validator("v1/climate-canary-preflight.schema.json").validate(preflight)

        disabled_evidence = copy.deepcopy(evidence)
        disabled_evidence["candidate"]["status"] = "blocked"  # type: ignore[index]
        disabled_evidence["candidate"]["ready"] = False  # type: ignore[index]
        disabled_evidence["candidate"]["reasons"] = [  # type: ignore[index]
            "bridge_disabled"
        ]
        disabled_preflight = climate_canary_preflight(
            preflight_registry,
            None,
            disabled_evidence,
            bridge_mode=ClimateBridgeMode.DISABLED,
            room_id="living",
            pending_operation=False,
            checked_at=NOW,
        )
        validator("v1/climate-canary-preflight.schema.json").validate(
            disabled_preflight
        )

        generated_contours = contour_snapshot(
            contours,
            contour_climate_registry,
            snapshot,
            local_now=LOCAL_NOW,
        )
        validator("v7/contours.schema.json").validate(generated_contours)
        contour_json = json.dumps(generated_contours, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("synthetic-ac-source-living", contour_json)
        self.assertNotIn("entity_id", contour_json)
        self.assertEqual(generated_contours["contours"], home["contours"])

    def test_action_and_registry_schemas_reject_extra_or_missing_boundary_fields(self) -> None:
        capabilities = api_capabilities_snapshot()
        capabilities["private_origin"] = "http://192.168.1.10:1880"
        with self.assertRaises(Exception):
            validator("v1/api-capabilities.schema.json").validate(capabilities)

        action = load_json(FIXTURES / "action-request.json")
        missing_request = copy.deepcopy(action)
        missing_request.pop("request_id")  # type: ignore[union-attr]
        with self.assertRaises(Exception):
            validator("v1/climate-action-request.schema.json").validate(missing_request)

        registry = load_json(FIXTURES / "registry.json")
        registry["devices"][0]["service"] = "climate.set_temperature"  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v1/climate-registry.schema.json").validate(registry)

        preflight = load_json(FIXTURES / "canary-preflight.json")
        preflight["activation"]["allowed"] = True  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v1/climate-canary-preflight.schema.json").validate(preflight)

        home = load_json(ROOT / "fixtures" / "hasc_climate_v2" / "home.json")
        home["rooms"][0]["control"]["enabled"] = True  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v2/climate-home.schema.json").validate(home)

        unknown_reason = load_json(
            ROOT / "fixtures" / "hasc_climate_v2" / "home.json"
        )
        unknown_reason["rooms"][0]["control"]["blocked_reasons"] = [  # type: ignore[index]
            "backend_private_error"
        ]
        with self.assertRaises(Exception):
            validator("v2/climate-home.schema.json").validate(unknown_reason)

        missing_inputs = load_json(
            ROOT / "fixtures" / "hasc_climate_v3" / "home.json"
        )
        missing_inputs["rooms"][0]["control"].pop("action_inputs")  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v3/climate-home.schema.json").validate(missing_inputs)

        weakened_limit = load_json(
            ROOT / "fixtures" / "hasc_climate_v3" / "home.json"
        )
        weakened_limit["rooms"][0]["control"]["action_inputs"][  # type: ignore[index]
            "set_room_target"
        ]["target_temperature"]["maximum"] = 30
        with self.assertRaises(Exception):
            validator("v3/climate-home.schema.json").validate(weakened_limit)

        missing_presentations = load_json(
            ROOT / "fixtures" / "hasc_climate_v4" / "home.json"
        )
        missing_presentations["rooms"][0]["control"].pop(  # type: ignore[index]
            "action_presentations"
        )
        with self.assertRaises(Exception):
            validator("v4/climate-home.schema.json").validate(
                missing_presentations
            )

        wrong_confirmation = load_json(
            ROOT / "fixtures" / "hasc_climate_v4" / "home.json"
        )
        wrong_confirmation["rooms"][0]["control"]["action_presentations"][  # type: ignore[index]
            "turn_room_off"
        ]["confirmation_required"] = False
        with self.assertRaises(Exception):
            validator("v4/climate-home.schema.json").validate(wrong_confirmation)

        orphan_presentation = load_json(
            ROOT / "fixtures" / "hasc_climate_v4" / "home.json"
        )
        orphan_presentation["rooms"][0]["control"]["actions"] = [  # type: ignore[index]
            "turn_room_off"
        ]
        orphan_presentation["rooms"][0]["control"]["action_inputs"] = {}  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v4/climate-home.schema.json").validate(orphan_presentation)

        split_home = load_json(
            ROOT / "fixtures" / "hasc_climate_v5" / "home.json"
        )
        split_home.pop("contours")  # type: ignore[union-attr]
        with self.assertRaises(Exception):
            validator("v5/climate-home.schema.json").validate(split_home)

    def test_v5_examples_describe_reachable_control_states(self) -> None:
        home = load_json(ROOT / "fixtures" / "hasc_climate_v5" / "home.json")
        room = home["rooms"][0]  # type: ignore[index]
        contour = home["contours"][0]  # type: ignore[index]
        contour_room = contour["rooms"][0]

        self.assertEqual(room["id"], contour_room["id"])
        self.assertEqual(
            room["target_temperature"],
            contour_room["targets"]["temperature"],
        )
        self.assertTrue(contour_room["targets_in_sync"])
        self.assertFalse(contour["execution"]["settings_apply"]["available"])
        self.assertFalse(contour_room["temporary_temperature"]["available"])
        self.assertFalse(
            contour["execution"]["temporary_temperature"]["available"]
        )

        contours = load_json(
            ROOT / "fixtures" / "hasc_contours_v5" / "contours.json"
        )
        managed = contours["contours"][0]  # type: ignore[index]
        self.assertTrue(managed["execution"]["settings_apply"]["available"])
        self.assertTrue(managed["rooms"][0]["temporary_temperature"]["available"])

    def test_v6_public_codes_have_plain_display_names(self) -> None:
        home = load_json(ROOT / "fixtures" / "hasc_climate_v6" / "home.json")
        home_schema = load_json(
            ROOT
            / "custom_components"
            / "hausman_hub"
            / "contracts"
            / "v6"
            / "climate-home.schema.json"
        )
        contour_schema = load_json(
            ROOT
            / "custom_components"
            / "hausman_hub"
            / "contracts"
            / "v6"
            / "contours.schema.json"
        )
        names = home["display_names"]  # type: ignore[index]
        room = home["rooms"][0]  # type: ignore[index]
        device = room["devices"][0]
        contour = home["contours"][0]  # type: ignore[index]
        contour_room = contour["rooms"][0]

        checks = (
            ("room_modes", room["mode"]),
            ("device_kinds", device["kind"]),
            ("control_scopes", device["control_scope"]),
            ("device_states", device["state"]),
            ("contour_kinds", contour["kind"]),
            ("contour_modes", contour["mode"]),
            ("contour_statuses", contour["status"]),
            ("room_statuses", contour_room["status"]),
            ("strategies", contour_room["targets"]["strategy"]),
            ("profiles", contour_room["comfort_profiles"]["active"]),
            ("room_modes", contour_room["engine_mode"]),
            ("strategies", contour_room["engine_strategy"]),
        )
        checks += tuple(
            ("device_capabilities", capability)
            for capability in device["capabilities"]
        )
        for category, code in checks:
            with self.subTest(category=category, code=code):
                self.assertTrue(names[category][code])

        home_definitions = home_schema["$defs"]  # type: ignore[index]
        capability_codes = set(
            home_definitions["device"]["properties"]["capabilities"]["items"][
                "enum"
            ]
        )
        blocked_reason_codes = set(home_definitions["blocked_reason"]["enum"])
        contour_reason_codes = set(home_definitions["contour_reason"]["enum"])
        self.assertEqual(capability_codes, set(names["device_capabilities"]))
        self.assertEqual(blocked_reason_codes, set(names["blocked_reasons"]))
        self.assertEqual(contour_reason_codes, set(names["contour_reasons"]))
        self.assertEqual(
            set(contour_schema["$defs"]["reason"]["enum"]),  # type: ignore[index]
            set(names["contour_reasons"]),
        )

    def test_v7_room_actual_state_is_explicit_and_strict(self) -> None:
        home = load_json(ROOT / "fixtures" / "hasc_climate_v7" / "home.json")
        actual = home["rooms"][0]["actual"]  # type: ignore[index]

        self.assertEqual(
            {
                "data_status": "current",
                "temperature": 25.8,
                "humidity": 44,
                "mode": "automatic",
            },
            actual,
        )
        self.assertEqual(
            "Данные актуальны",
            home["display_names"]["data_statuses"]["current"],  # type: ignore[index]
        )

        unknown_status = copy.deepcopy(home)
        unknown_status["rooms"][0]["actual"]["data_status"] = "vendor_fresh"  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v7/climate-home.schema.json").validate(unknown_status)

    def test_v8_active_target_and_saved_profiles_cannot_be_confused(self) -> None:
        home = load_json(ROOT / "fixtures" / "hasc_climate_v8" / "home.json")
        room = home["rooms"][0]  # type: ignore[index]

        self.assertEqual(24, room["active_target"]["temperature"])
        self.assertEqual(23, room["saved_profiles"]["night"]["temperature"])
        self.assertEqual("day", room["saved_profiles"]["active"])

        partial_profiles = copy.deepcopy(home)
        partial_profiles["rooms"][0]["saved_profiles"]["active"] = None  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v8/climate-home.schema.json").validate(partial_profiles)

        private_strategy = copy.deepcopy(home)
        private_strategy["rooms"][0]["active_target"]["strategy"] = "vendor"  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v8/climate-home.schema.json").validate(private_strategy)

    def test_v9_next_schedule_change_is_exact_and_strict(self) -> None:
        home = load_json(ROOT / "fixtures" / "hasc_climate_v9" / "home.json")
        schedule = home["contours"][0]["schedule"]  # type: ignore[index]

        self.assertEqual("night", schedule["next_profile"])
        self.assertEqual(
            "2026-07-17T23:00:00+03:00",
            schedule["next_change_at"],
        )

        missing_offset = copy.deepcopy(home)
        missing_offset["contours"][0]["schedule"]["next_change_at"] = (  # type: ignore[index]
            "2026-07-17T23:00:00"
        )
        with self.assertRaises(Exception):
            validator("v9/climate-home.schema.json").validate(missing_offset)

        disabled_with_transition = copy.deepcopy(home)
        disabled_with_transition["contours"][0]["schedule"]["enabled"] = False  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v9/climate-home.schema.json").validate(
                disabled_with_transition
            )


if __name__ == "__main__":
    unittest.main()
