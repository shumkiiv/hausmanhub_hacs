"""Compatibility fixtures for the installed HausmanHub climate JSON contracts."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
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
from custom_components.hausman_hub.application.climate_setup import (
    build_climate_contour_draft_setup,
    climate_available_rooms,
    climate_device_candidates,
    climate_draft_save_receipt,
    climate_room_suggestions,
    climate_setup_options,
    create_climate_contour_draft,
    current_climate_contour_setup,
    validate_climate_contour_draft,
)
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
    contour_snapshot,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
from tests.test_climate_canary_preflight import NOW, ready_inputs


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "custom_components" / "hausman_hub" / "contracts"
FIXTURES = ROOT / "fixtures" / "hausmanhub_climate_v1"
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
            "hausmanhub_capabilities_v1/capabilities.json": "v1/api-capabilities.schema.json",
            "hausmanhub_climate_v1/action-request.json": "v1/climate-action-request.schema.json",
            "hausmanhub_climate_v1/operation-query.json": "v1/climate-operation-query.schema.json",
            "hausmanhub_climate_v1/operation-receipt.json": "v1/climate-operation-receipt.schema.json",
            "hausmanhub_climate_v1/registry.json": "v1/climate-registry.schema.json",
            "hausmanhub_climate_v1/readiness.json": "v1/climate-readiness.schema.json",
            "hausmanhub_climate_v1/registry-preview.json": "v1/climate-registry-preview.schema.json",
            "hausmanhub_climate_v1/shadow-candidate-query.json": "v1/climate-shadow-candidate-query.schema.json",
            "hausmanhub_climate_v1/shadow-evidence.json": "v1/climate-shadow-evidence.schema.json",
            "hausmanhub_climate_v1/canary-preflight-query.json": "v1/climate-canary-preflight-query.schema.json",
            "hausmanhub_climate_v1/canary-preflight.json": "v1/climate-canary-preflight.schema.json",
            "hausmanhub_climate_rooms_v1/rooms.json": "v1/climate-rooms.schema.json",
            "hausmanhub_climate_device_candidates_v1/candidates.json": "v1/climate-device-candidates.schema.json",
            "hausmanhub_climate_room_suggestions_v1/suggestions.json": "v1/climate-room-suggestions.schema.json",
            "hausmanhub_climate_draft_v1/request.json": "v1/climate-draft-request.schema.json",
            "hausmanhub_climate_draft_v1/draft.json": "v1/climate-draft.schema.json",
            "hausmanhub_climate_draft_v1/save.json": "v1/climate-draft-save.schema.json",
            "hausmanhub_climate_current_setup_v1/current.json": "v1/climate-current-setup.schema.json",
            "hausmanhub_climate_profiles_v1/request.json": "v1/climate-profile-update-request.schema.json",
            "hausmanhub_climate_profiles_v1/receipt.json": "v1/climate-profile-update.schema.json",
            "hausmanhub_climate_schedule_v1/request.json": "v1/climate-schedule-update-request.schema.json",
            "hausmanhub_climate_schedule_v1/receipt.json": "v1/climate-schedule-update.schema.json",
            "hausmanhub_climate_draft_v1/options.json": "v1/climate-setup-options.schema.json",
            "hausmanhub_climate_draft_v1/validation.json": "v1/climate-draft-validation.schema.json",
            "hausmanhub_climate_v2/home.json": "v2/climate-home.schema.json",
            "hausmanhub_climate_v3/home.json": "v3/climate-home.schema.json",
            "hausmanhub_climate_v4/home.json": "v4/climate-home.schema.json",
            "hausmanhub_climate_v5/home.json": "v5/climate-home.schema.json",
            "hausmanhub_climate_v6/home.json": "v6/climate-home.schema.json",
            "hausmanhub_climate_v7/home.json": "v7/climate-home.schema.json",
            "hausmanhub_climate_v8/home.json": "v8/climate-home.schema.json",
            "hausmanhub_climate_v9/home.json": "v9/climate-home.schema.json",
            "hausmanhub_climate_v10/home.json": "v10/climate-home.schema.json",
            "hausmanhub_climate_v11/home.json": "v11/climate-home.schema.json",
            "hausmanhub_climate_v12/home.json": "v12/climate-home.schema.json",
            "hausmanhub_contours_v1/contours.json": "v1/contours.schema.json",
            "hausmanhub_contours_v2/contours.json": "v2/contours.schema.json",
            "hausmanhub_contours_v3/contours.json": "v3/contours.schema.json",
            "hausmanhub_contours_v4/contours.json": "v4/contours.schema.json",
            "hausmanhub_contours_v5/contours.json": "v5/contours.schema.json",
            "hausmanhub_contours_v6/contours.json": "v6/contours.schema.json",
            "hausmanhub_contours_v7/contours.json": "v7/contours.schema.json",
            "hausmanhub_contour_apply_v1/request.json": "v1/contour-apply-request.schema.json",
            "hausmanhub_contour_apply_v1/preview.json": "v1/contour-apply-preview.schema.json",
            "hausmanhub_contour_apply_v1/receipt.json": "v1/contour-apply-receipt.schema.json",
            "hausmanhub_climate_control_receipt_v1/apply.json": "v1/climate-control-receipt.schema.json",
            "hausmanhub_climate_control_receipt_v1/temporary.json": "v1/climate-control-receipt.schema.json",
            "hausmanhub_climate_control_receipt_v1/return.json": "v1/climate-control-receipt.schema.json",
            "hausmanhub_climate_control_receipt_v1/schedule.json": "v1/climate-control-receipt.schema.json",
            "hausmanhub_temporary_temperature_v1/request.json": "v1/temporary-temperature-request.schema.json",
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
            load_json(ROOT / "fixtures" / "hausmanhub_capabilities_v1" / "capabilities.json"),
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
        rooms = climate_available_rooms(registry, snapshot)
        candidates = climate_device_candidates(registry, snapshot)
        suggestions = climate_room_suggestions(registry, snapshot)
        draft_registry = registry_from_payload(
            {"version": 1, "rooms": [], "devices": []}
        )
        draft_request = load_json(
            ROOT / "fixtures" / "hausmanhub_climate_draft_v1" / "request.json"
        )
        draft = create_climate_contour_draft(
            draft_registry,
            snapshot,
            draft_request,
        )
        setup_options = climate_setup_options(draft_registry, snapshot)
        draft_validation = validate_climate_contour_draft(
            draft_registry,
            snapshot,
            draft,
        )
        saved_registry, saved_contours, save_validation = (
            build_climate_contour_draft_setup(
                draft_registry,
                snapshot,
                draft,
            )
        )
        draft_save = climate_draft_save_receipt(draft, save_validation)
        current_setup = current_climate_contour_setup(
            contour_climate_registry,
            contours,
            snapshot,
        )

        validator("v12/climate-home.schema.json").validate(home)
        validator("v1/climate-admin-import.schema.json").validate(admin)
        validator("v1/climate-rooms.schema.json").validate(rooms)
        validator("v1/climate-device-candidates.schema.json").validate(candidates)
        validator("v1/climate-room-suggestions.schema.json").validate(suggestions)
        validator("v1/climate-draft-request.schema.json").validate(draft_request)
        validator("v1/climate-draft.schema.json").validate(draft)
        validator("v1/climate-setup-options.schema.json").validate(setup_options)
        validator("v1/climate-draft-validation.schema.json").validate(
            draft_validation
        )
        validator("v1/climate-draft-save.schema.json").validate(draft_save)
        validator("v1/climate-current-setup.schema.json").validate(current_setup)
        self.assertEqual(
            load_json(
                ROOT / "fixtures" / "hausmanhub_climate_draft_v1" / "save.json"
            ),
            draft_save,
        )
        self.assertEqual(2, len(saved_registry.rooms))
        self.assertEqual(1, len(saved_contours.contours))
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
        validator("v12/climate-home.schema.json").validate(stale_home)
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
        validator("v12/climate-home.schema.json").validate(missing_home)
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

        home = load_json(ROOT / "fixtures" / "hausmanhub_climate_v2" / "home.json")
        home["rooms"][0]["control"]["enabled"] = True  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v2/climate-home.schema.json").validate(home)

        unknown_reason = load_json(
            ROOT / "fixtures" / "hausmanhub_climate_v2" / "home.json"
        )
        unknown_reason["rooms"][0]["control"]["blocked_reasons"] = [  # type: ignore[index]
            "backend_private_error"
        ]
        with self.assertRaises(Exception):
            validator("v2/climate-home.schema.json").validate(unknown_reason)

        missing_inputs = load_json(
            ROOT / "fixtures" / "hausmanhub_climate_v3" / "home.json"
        )
        missing_inputs["rooms"][0]["control"].pop("action_inputs")  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v3/climate-home.schema.json").validate(missing_inputs)

        weakened_limit = load_json(
            ROOT / "fixtures" / "hausmanhub_climate_v3" / "home.json"
        )
        weakened_limit["rooms"][0]["control"]["action_inputs"][  # type: ignore[index]
            "set_room_target"
        ]["target_temperature"]["maximum"] = 30
        with self.assertRaises(Exception):
            validator("v3/climate-home.schema.json").validate(weakened_limit)

        missing_presentations = load_json(
            ROOT / "fixtures" / "hausmanhub_climate_v4" / "home.json"
        )
        missing_presentations["rooms"][0]["control"].pop(  # type: ignore[index]
            "action_presentations"
        )
        with self.assertRaises(Exception):
            validator("v4/climate-home.schema.json").validate(
                missing_presentations
            )

        wrong_confirmation = load_json(
            ROOT / "fixtures" / "hausmanhub_climate_v4" / "home.json"
        )
        wrong_confirmation["rooms"][0]["control"]["action_presentations"][  # type: ignore[index]
            "turn_room_off"
        ]["confirmation_required"] = False
        with self.assertRaises(Exception):
            validator("v4/climate-home.schema.json").validate(wrong_confirmation)

        orphan_presentation = load_json(
            ROOT / "fixtures" / "hausmanhub_climate_v4" / "home.json"
        )
        orphan_presentation["rooms"][0]["control"]["actions"] = [  # type: ignore[index]
            "turn_room_off"
        ]
        orphan_presentation["rooms"][0]["control"]["action_inputs"] = {}  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v4/climate-home.schema.json").validate(orphan_presentation)

        split_home = load_json(
            ROOT / "fixtures" / "hausmanhub_climate_v5" / "home.json"
        )
        split_home.pop("contours")  # type: ignore[union-attr]
        with self.assertRaises(Exception):
            validator("v5/climate-home.schema.json").validate(split_home)

    def test_v5_examples_describe_reachable_control_states(self) -> None:
        home = load_json(ROOT / "fixtures" / "hausmanhub_climate_v5" / "home.json")
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
            ROOT / "fixtures" / "hausmanhub_contours_v5" / "contours.json"
        )
        managed = contours["contours"][0]  # type: ignore[index]
        self.assertTrue(managed["execution"]["settings_apply"]["available"])
        self.assertTrue(managed["rooms"][0]["temporary_temperature"]["available"])

    def test_v6_public_codes_have_plain_display_names(self) -> None:
        home = load_json(ROOT / "fixtures" / "hausmanhub_climate_v6" / "home.json")
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
        home = load_json(ROOT / "fixtures" / "hausmanhub_climate_v7" / "home.json")
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
        home = load_json(ROOT / "fixtures" / "hausmanhub_climate_v8" / "home.json")
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
        home = load_json(ROOT / "fixtures" / "hausmanhub_climate_v9" / "home.json")
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

    def test_v10_allowed_actions_follow_each_room_gate(self) -> None:
        home = load_json(ROOT / "fixtures" / "hausmanhub_climate_v10" / "home.json")
        control = home["rooms"][0]["control"]  # type: ignore[index]

        self.assertFalse(control["enabled"])
        self.assertEqual(
            ["set_room_target", "turn_room_off"],
            control["actions"],
        )
        self.assertEqual([], control["allowed_actions"])

        ready = copy.deepcopy(home)
        ready_control = ready["rooms"][0]["control"]  # type: ignore[index]
        ready_control["enabled"] = True
        ready_control["allowed_actions"] = [
            "set_room_target",
            "turn_room_off",
        ]
        ready_control["blocked_reasons"] = []
        ready["climate"]["commands_enabled"] = True  # type: ignore[index]
        validator("v10/climate-home.schema.json").validate(ready)

        wrong_aggregate = copy.deepcopy(ready)
        wrong_aggregate["climate"]["commands_enabled"] = False  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v10/climate-home.schema.json").validate(wrong_aggregate)

        no_allowed_room = copy.deepcopy(home)
        no_allowed_room["climate"]["commands_enabled"] = True  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("v10/climate-home.schema.json").validate(no_allowed_room)

        blocked_but_allowed = copy.deepcopy(home)
        blocked_but_allowed["rooms"][0]["control"]["allowed_actions"] = [  # type: ignore[index]
            "turn_room_off"
        ]
        with self.assertRaises(Exception):
            validator("v10/climate-home.schema.json").validate(
                blocked_but_allowed
            )

        missing_allowed_actions = copy.deepcopy(home)
        missing_allowed_actions["rooms"][0]["control"].pop(  # type: ignore[index]
            "allowed_actions"
        )
        with self.assertRaises(Exception):
            validator("v10/climate-home.schema.json").validate(
                missing_allowed_actions
            )

    def test_v11_every_advertised_action_has_exact_blocked_reasons(self) -> None:
        home = load_json(ROOT / "fixtures" / "hausmanhub_climate_v11" / "home.json")
        control = home["rooms"][0]["control"]  # type: ignore[index]

        self.assertEqual(set(control["actions"]), set(control["action_availability"]))
        for action in control["actions"]:
            availability = control["action_availability"][action]
            self.assertFalse(availability["allowed"])
            self.assertEqual(["shadow_only"], availability["blocked_reasons"])
            self.assertEqual(
                "Включена только проверка без команд",
                home["display_names"]["blocked_reasons"]["shadow_only"],  # type: ignore[index]
            )

        ready = copy.deepcopy(home)
        ready_control = ready["rooms"][0]["control"]  # type: ignore[index]
        ready_control["enabled"] = True
        ready_control["allowed_actions"] = list(ready_control["actions"])
        ready_control["blocked_reasons"] = []
        for availability in ready_control["action_availability"].values():
            availability["allowed"] = True
            availability["blocked_reasons"] = []
        ready["climate"]["commands_enabled"] = True  # type: ignore[index]
        validator("v11/climate-home.schema.json").validate(ready)

        missing_action_status = copy.deepcopy(home)
        missing_action_status["rooms"][0]["control"][  # type: ignore[index]
            "action_availability"
        ].pop("set_room_target")
        with self.assertRaises(Exception):
            validator("v11/climate-home.schema.json").validate(
                missing_action_status
            )

        false_permission = copy.deepcopy(home)
        false_permission["rooms"][0]["control"]["action_availability"][  # type: ignore[index]
            "turn_room_off"
        ]["allowed"] = True
        with self.assertRaises(Exception):
            validator("v11/climate-home.schema.json").validate(false_permission)

        missing_reason = copy.deepcopy(home)
        missing_reason["rooms"][0]["control"]["action_availability"][  # type: ignore[index]
            "turn_room_off"
        ]["blocked_reasons"] = []
        with self.assertRaises(Exception):
            validator("v11/climate-home.schema.json").validate(missing_reason)

        unknown_reason = copy.deepcopy(home)
        unknown_reason["rooms"][0]["control"]["action_availability"][  # type: ignore[index]
            "turn_room_off"
        ]["blocked_reasons"] = ["private_backend_error"]
        with self.assertRaises(Exception):
            validator("v11/climate-home.schema.json").validate(unknown_reason)

    def test_v12_state_revision_is_required_and_json_safe(self) -> None:
        home = load_json(ROOT / "fixtures" / "hausmanhub_climate_v12" / "home.json")
        revision = home["state_revision"]  # type: ignore[index]

        self.assertIs(type(revision), int)
        self.assertGreaterEqual(revision, 0)
        self.assertLessEqual(revision, 9_007_199_254_740_991)
        revision_payload = copy.deepcopy(home)
        revision_payload.pop("generated_at")  # type: ignore[union-attr]
        revision_payload.pop("state_revision")  # type: ignore[union-attr]
        encoded = json.dumps(
            revision_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected = int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big") % (
            9_007_199_254_740_991 + 1
        )
        self.assertEqual(expected, revision)
        validator("v12/climate-home.schema.json").validate(home)

        missing_revision = copy.deepcopy(home)
        missing_revision.pop("state_revision")  # type: ignore[union-attr]
        with self.assertRaises(Exception):
            validator("v12/climate-home.schema.json").validate(missing_revision)

        for invalid in (-1, 9_007_199_254_740_992, 1.5, "revision"):
            with self.subTest(invalid=invalid):
                invalid_revision = copy.deepcopy(home)
                invalid_revision["state_revision"] = invalid  # type: ignore[index]
                with self.assertRaises(Exception):
                    validator("v12/climate-home.schema.json").validate(
                        invalid_revision
                    )


if __name__ == "__main__":
    unittest.main()
