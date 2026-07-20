"""Stable, understandable device candidates for HASC climate setup."""

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
)
from custom_components.hausman_hub.application.climate_setup import (
    climate_device_candidates,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FIXTURE = ROOT / "fixtures" / "climate_bridge" / "valid_state.json"
REGISTRY_FIXTURE = ROOT / "fixtures" / "hasc_climate_v1" / "registry.json"
CANDIDATES_FIXTURE = (
    ROOT
    / "fixtures"
    / "hasc_climate_device_candidates_v1"
    / "candidates.json"
)
CANDIDATES_SCHEMA = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "climate-device-candidates.schema.json"
)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def living_ac_registry() -> object:
    payload = copy.deepcopy(load_json(REGISTRY_FIXTURE))
    payload["rooms"] = [  # type: ignore[index]
        room for room in payload["rooms"] if room["id"] == "living"  # type: ignore[index]
    ]
    payload["devices"] = [  # type: ignore[index]
        device
        for device in payload["devices"]  # type: ignore[index]
        if device["id"] == "living_ac"
    ]
    return registry_from_payload(payload)


def remove_source_device(payload: object, source_id: str) -> None:
    payload["devices"] = [  # type: ignore[index]
        device
        for device in payload["devices"]  # type: ignore[index]
        if device["id"] != source_id
    ]
    payload["capabilities"] = [  # type: ignore[index]
        item
        for item in payload["capabilities"]  # type: ignore[index]
        if item["deviceId"] != source_id
    ]


class ClimateSetupDevicesTest(unittest.TestCase):
    """Keep private discovery details behind stable HASC candidate fields."""

    def setUp(self) -> None:
        schema = load_json(CANDIDATES_SCHEMA)
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)

    def test_candidates_have_russian_types_and_no_private_source_ids(self) -> None:
        result = climate_device_candidates(
            living_ac_registry(),  # type: ignore[arg-type]
            import_climate_state(load_json(SOURCE_FIXTURE)),
        )

        self.validator.validate(result)
        self.assertEqual(load_json(CANDIDATES_FIXTURE), result)
        self.assertEqual(
            ["candidate_0001", "candidate_0002"],
            [candidate["candidate_id"] for candidate in result["candidates"]],
        )
        self.assertEqual(
            "Увлажнитель",
            result["display_names"]["device_types"]["humidifier"],
        )
        serialized = json.dumps(result, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("source_id", serialized)
        self.assertNotIn("synthetic-humidifier-source-kids", serialized)
        self.assertNotIn("entity_id", serialized)

    def test_stale_data_disables_every_candidate(self) -> None:
        stale = copy.deepcopy(load_json(SOURCE_FIXTURE))
        stale["runtimeHealth"]["status"] = "stale"  # type: ignore[index]

        result = climate_device_candidates(
            living_ac_registry(),  # type: ignore[arg-type]
            import_climate_state(stale),
        )

        self.validator.validate(result)
        self.assertEqual("stale", result["data_status"])
        self.assertFalse(result["selection_allowed"])
        self.assertEqual(
            {"data_stale"},
            {candidate["status"] for candidate in result["candidates"]},
        )
        self.assertFalse(
            any(candidate["selectable"] for candidate in result["candidates"])
        )

    def test_missing_configured_device_stays_visible_without_private_id(self) -> None:
        missing = copy.deepcopy(load_json(SOURCE_FIXTURE))
        remove_source_device(missing, "synthetic-ac-source-living")

        result = climate_device_candidates(
            living_ac_registry(),  # type: ignore[arg-type]
            import_climate_state(missing),
        )

        self.validator.validate(result)
        living = next(
            candidate
            for candidate in result["candidates"]
            if candidate["configured_device_id"] == "living_ac"
        )
        self.assertEqual("source_missing", living["status"])
        self.assertEqual([], living["suggested_types"])
        self.assertFalse(living["available"])
        self.assertFalse(living["selectable"])

    def test_unavailable_configured_device_has_an_honest_status(self) -> None:
        unavailable = copy.deepcopy(load_json(SOURCE_FIXTURE))
        living = next(  # type: ignore[call-overload]
            device
            for device in unavailable["devices"]  # type: ignore[index]
            if device["id"] == "synthetic-ac-source-living"
        )
        living["unavailable"] = True
        living["state"] = "unavailable"

        result = climate_device_candidates(
            living_ac_registry(),  # type: ignore[arg-type]
            import_climate_state(unavailable),
        )

        self.validator.validate(result)
        candidate = next(
            value
            for value in result["candidates"]
            if value["configured_device_id"] == "living_ac"
        )
        self.assertTrue(candidate["configured"])
        self.assertFalse(candidate["available"])
        self.assertEqual("unavailable", candidate["status"])
        self.assertFalse(candidate["selectable"])

    def test_snapshot_revision_changes_when_private_binding_changes(self) -> None:
        empty_registry = registry_from_payload(
            {"version": 1, "rooms": [], "devices": []}
        )
        source = load_json(SOURCE_FIXTURE)
        changed = copy.deepcopy(source)
        old_id = "synthetic-humidifier-source-kids"
        new_id = "replacement-private-humidifier"
        for device in changed["devices"]:  # type: ignore[index]
            if device["id"] == old_id:
                device["id"] = new_id
        for capability in changed["capabilities"]:  # type: ignore[index]
            if capability["deviceId"] == old_id:
                capability["deviceId"] = new_id

        before = climate_device_candidates(
            empty_registry,
            import_climate_state(source),
        )
        after = climate_device_candidates(
            empty_registry,
            import_climate_state(changed),
        )

        before_without_revision = dict(before)
        after_without_revision = dict(after)
        before_without_revision.pop("snapshot_revision")
        after_without_revision.pop("snapshot_revision")
        self.assertEqual(before_without_revision, after_without_revision)
        self.assertNotEqual(before["snapshot_revision"], after["snapshot_revision"])

    def test_snapshot_revision_ignores_read_time_when_candidates_are_equal(self) -> None:
        later = copy.deepcopy(load_json(SOURCE_FIXTURE))
        later["generatedAt"] += 60_000  # type: ignore[index]
        registry = living_ac_registry()

        before = climate_device_candidates(
            registry,  # type: ignore[arg-type]
            import_climate_state(load_json(SOURCE_FIXTURE)),
        )
        after = climate_device_candidates(
            registry,  # type: ignore[arg-type]
            import_climate_state(later),
        )

        self.assertNotEqual(before["generated_at"], after["generated_at"])
        self.assertEqual(before["snapshot_revision"], after["snapshot_revision"])

    def test_schema_rejects_a_selectable_candidate_marked_unavailable(self) -> None:
        payload = copy.deepcopy(load_json(CANDIDATES_FIXTURE))
        candidate = payload["candidates"][0]  # type: ignore[index]
        candidate["available"] = False

        with self.assertRaises(Exception):
            self.validator.validate(payload)


if __name__ == "__main__":
    unittest.main()
