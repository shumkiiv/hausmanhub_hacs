"""Safe read model for editing the currently saved HausmanHub climate setup."""

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
    registry_to_payload,
)
from custom_components.hausman_hub.application.climate_setup import (
    climate_ir_code_bindings,
    current_climate_contour_setup,
)
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
    contour_registry_to_payload,
    with_applied_climate_schedule_profile,
    with_climate_room_profiles,
    with_climate_schedule,
    with_climate_temporary_temperature,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateControlChannel,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateRegistry,
)
from custom_components.hausman_hub.domain.contours import (
    ClimateProfile,
    ContourRegistry,
)
from tests.test_climate_import import source_payload


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "climate-current-setup.schema.json"
)
FIXTURE = ROOT / "fixtures" / "hausmanhub_climate_current_setup_v1" / "current.json"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def configured_setup() -> tuple[object, object, object]:
    snapshot = import_climate_state(source_payload())
    registry, contours = build_climate_contour_setup(
        snapshot,
        room_ids=["living", "kids"],
        source_ids=[
            "synthetic-ac-source-living",
            "synthetic-humidifier-source-kids",
        ],
        name="Климат дома",
        mode="automatic",
        room_parameters={
            "living": {
                "target_temperature": 25.0,
                "target_humidity": 45,
                "strategy": "normal",
            },
            "kids": {
                "target_temperature": 24.0,
                "target_humidity": 50,
                "strategy": "soft",
            },
        },
    )
    contours = with_climate_room_profiles(
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
            },
            "kids": {
                "profiles": {
                    "day": {
                        "target_temperature": 24.0,
                        "target_humidity": 50,
                        "strategy": "soft",
                    },
                    "night": {
                        "target_temperature": 21.0,
                        "target_humidity": 45,
                        "strategy": "normal",
                    },
                },
                "active_profile": "day",
            },
        },
    )
    contours = with_climate_schedule(
        contours,
        enabled=True,
        day_start="07:00",
        night_start="23:00",
    )
    contours = with_applied_climate_schedule_profile(
        contours,
        ClimateProfile.DAY,
    )
    contours = with_climate_temporary_temperature(
        contours,
        room_id="living",
        target_temperature=23.5,
    )
    return registry, contours, snapshot


class ClimateSetupCurrentTest(unittest.TestCase):
    """The editor sees stored values, not transient engine substitutions."""

    def setUp(self) -> None:
        self.validator = Draft202012Validator(load_json(SCHEMA))

    def test_current_setup_is_exact_complete_and_private_id_free(self) -> None:
        registry, contours, snapshot = configured_setup()
        registry_before = registry_to_payload(registry)  # type: ignore[arg-type]
        contours_before = contour_registry_to_payload(contours)  # type: ignore[arg-type]
        snapshot_before = copy.deepcopy(snapshot)

        result = current_climate_contour_setup(
            registry,  # type: ignore[arg-type]
            contours,  # type: ignore[arg-type]
            snapshot,  # type: ignore[arg-type]
        )

        self.validator.validate(result)
        self.assertEqual(load_json(FIXTURE), result)
        self.assertTrue(result["editing_allowed"])
        self.assertEqual(25.0, result["rooms"][1]["profiles"]["day"]["target_temperature"])
        self.assertEqual(23.5, result["rooms"][1]["temporary_temperature"])
        self.assertEqual(registry_before, registry_to_payload(registry))  # type: ignore[arg-type]
        self.assertEqual(contours_before, contour_registry_to_payload(contours))  # type: ignore[arg-type]
        self.assertEqual(snapshot_before, snapshot)
        serialized = json.dumps(result, ensure_ascii=True, sort_keys=True)
        for private_value in (
            "source_id",
            "entity_id",
            "synthetic-ac-source-living",
            "synthetic-humidifier-source-kids",
        ):
            self.assertNotIn(private_value, serialized)

    def test_missing_contour_has_an_explicit_non_editable_result(self) -> None:
        snapshot = import_climate_state(source_payload())

        result = current_climate_contour_setup(
            ClimateRegistry(),
            ContourRegistry(),
            snapshot,
        )

        self.validator.validate(result)
        self.assertEqual("not_configured", result["status"])
        self.assertFalse(result["editing_allowed"])
        self.assertIsNone(result["name"])
        self.assertEqual([], result["rooms"])
        self.assertEqual("not_configured", result["issues"][0]["code"])

        invalid = copy.deepcopy(result)
        invalid["issues"][0]["message"] = "Неизвестная ошибка"  # type: ignore[index]
        with self.assertRaises(Exception):
            self.validator.validate(invalid)

    def test_1262_v1_fixture_remains_schema_compatible(self) -> None:
        self.validator.validate(load_json(FIXTURE))

    def test_stale_devices_block_editing_but_keep_saved_profiles_visible(self) -> None:
        registry, contours, snapshot = configured_setup()
        ready = current_climate_contour_setup(
            registry,  # type: ignore[arg-type]
            contours,  # type: ignore[arg-type]
            snapshot,  # type: ignore[arg-type]
        )
        stale_source = source_payload()
        stale_source["runtimeHealth"]["status"] = "stale"  # type: ignore[index]

        result = current_climate_contour_setup(
            registry,  # type: ignore[arg-type]
            contours,  # type: ignore[arg-type]
            import_climate_state(stale_source),
        )

        self.validator.validate(result)
        self.assertEqual("attention", result["status"])
        self.assertFalse(result["editing_allowed"])
        self.assertEqual(["data_stale"], [issue["code"] for issue in result["issues"]])
        self.assertEqual(22.0, result["rooms"][1]["profiles"]["night"]["target_temperature"])
        self.assertEqual(ready["setup_revision"], result["setup_revision"])

        changed_contours = with_climate_temporary_temperature(
            contours,  # type: ignore[arg-type]
            room_id="living",
            target_temperature=24.0,
        )
        changed = current_climate_contour_setup(
            registry,  # type: ignore[arg-type]
            changed_contours,
            snapshot,  # type: ignore[arg-type]
        )
        self.assertNotEqual(ready["setup_revision"], changed["setup_revision"])

    def test_universal_ir_bindings_stay_out_of_the_v1_current_setup_payload(self) -> None:
        registry, contours, snapshot = configured_setup()
        original = registry.devices[0]
        remote_device = replace(
            original,
            control_channel=ClimateControlChannel.UNIVERSAL_IR,
            endpoints=(
                ClimateEndpoint(
                    ClimateEndpointRole.CONTROL,
                    "remote.pult_broadlink_gostinnaya",
                ),
            ),
        )
        registry = ClimateRegistry(
            rooms=registry.rooms,
            devices=(remote_device, *registry.devices[1:]),
            home=registry.home,
        )

        result = current_climate_contour_setup(registry, contours, snapshot)
        self.validator.validate(result)
        device = next(
            device
            for room in result["rooms"]
            for device in room["devices"]
            if device["candidate_id"] == "candidate_0001"
        )

        self.assertNotIn("configured_device_id", device)
        self.assertNotIn("remote_entity_id", device)
        self.assertEqual(
            {
                "bindings": [
                    {
                        "candidate_id": "candidate_0001",
                        "configured_device_id": remote_device.device_id,
                        "remote_entity_id": "remote.pult_broadlink_gostinnaya",
                    }
                ]
            },
            climate_ir_code_bindings(registry, contours, snapshot),
        )


if __name__ == "__main__":
    unittest.main()
