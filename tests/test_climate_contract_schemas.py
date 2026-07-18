"""Compatibility fixtures for the installed HASC climate JSON contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

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
            "hasc_contours_v1/contours.json": "v1/contours.schema.json",
        }
        for fixture_name, schema_name in pairs.items():
            with self.subTest(fixture=fixture_name):
                validator(schema_name).validate(load_json(ROOT / "fixtures" / fixture_name))

        for schema_path in SCHEMAS.rglob("*.schema.json"):
            with self.subTest(schema=schema_path.name):
                Draft202012Validator.check_schema(load_json(schema_path))

    def test_generated_home_and_admin_import_match_their_contracts(self) -> None:
        registry = registry_from_payload(load_json(FIXTURES / "registry.json"))
        snapshot = import_climate_state(load_json(SOURCE_FIXTURE))

        home = android_climate_snapshot(
            registry,
            snapshot,
            bridge_mode=ClimateBridgeMode.SHADOW,
        )
        admin = admin_climate_import_snapshot(registry, snapshot)

        validator("v4/climate-home.schema.json").validate(home)
        validator("v1/climate-admin-import.schema.json").validate(admin)
        serialized_home = json.dumps(home, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("source_id", serialized_home)
        self.assertNotIn("entity_id", serialized_home)

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
        generated_contours = contour_snapshot(
            contours,
            contour_climate_registry,
            snapshot,
        )
        validator("v1/contours.schema.json").validate(generated_contours)
        contour_json = json.dumps(generated_contours, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("synthetic-ac-source-living", contour_json)
        self.assertNotIn("entity_id", contour_json)

    def test_action_and_registry_schemas_reject_extra_or_missing_boundary_fields(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
