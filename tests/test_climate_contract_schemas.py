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
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
from tests.test_climate_canary_preflight import NOW, ready_inputs


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1"
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
            "action-request.json": "climate-action-request.schema.json",
            "operation-query.json": "climate-operation-query.schema.json",
            "operation-receipt.json": "climate-operation-receipt.schema.json",
            "registry.json": "climate-registry.schema.json",
            "readiness.json": "climate-readiness.schema.json",
            "registry-preview.json": "climate-registry-preview.schema.json",
            "shadow-candidate-query.json": "climate-shadow-candidate-query.schema.json",
            "shadow-evidence.json": "climate-shadow-evidence.schema.json",
            "canary-preflight-query.json": "climate-canary-preflight-query.schema.json",
            "canary-preflight.json": "climate-canary-preflight.schema.json",
        }
        for fixture_name, schema_name in pairs.items():
            with self.subTest(fixture=fixture_name):
                validator(schema_name).validate(load_json(FIXTURES / fixture_name))

        for schema_path in SCHEMAS.glob("*.schema.json"):
            with self.subTest(schema=schema_path.name):
                Draft202012Validator.check_schema(load_json(schema_path))

    def test_generated_home_and_admin_import_match_their_contracts(self) -> None:
        registry = registry_from_payload(load_json(FIXTURES / "registry.json"))
        snapshot = import_climate_state(load_json(SOURCE_FIXTURE))

        home = android_climate_snapshot(registry, snapshot, commands_enabled=False)
        admin = admin_climate_import_snapshot(registry, snapshot)

        validator("climate-home.schema.json").validate(home)
        validator("climate-admin-import.schema.json").validate(admin)
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
        validator("climate-canary-preflight.schema.json").validate(preflight)

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
        validator("climate-canary-preflight.schema.json").validate(
            disabled_preflight
        )

    def test_action_and_registry_schemas_reject_extra_or_missing_boundary_fields(self) -> None:
        action = load_json(FIXTURES / "action-request.json")
        missing_request = copy.deepcopy(action)
        missing_request.pop("request_id")  # type: ignore[union-attr]
        with self.assertRaises(Exception):
            validator("climate-action-request.schema.json").validate(missing_request)

        registry = load_json(FIXTURES / "registry.json")
        registry["devices"][0]["service"] = "climate.set_temperature"  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("climate-registry.schema.json").validate(registry)

        preflight = load_json(FIXTURES / "canary-preflight.json")
        preflight["activation"]["allowed"] = True  # type: ignore[index]
        with self.assertRaises(Exception):
            validator("climate-canary-preflight.schema.json").validate(preflight)


if __name__ == "__main__":
    unittest.main()
