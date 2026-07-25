"""Stable available-room contract for the HausmanHub climate setup API."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from tests.climate_bridge_fixture import (
    import_climate_state,
)
from custom_components.hausman_hub.application.climate_registry import (
    registry_from_payload,
)
from custom_components.hausman_hub.application.climate_setup import (
    climate_available_rooms,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FIXTURE = ROOT / "fixtures" / "climate_bridge" / "valid_state.json"
ROOMS_FIXTURE = ROOT / "fixtures" / "hausmanhub_climate_rooms_v1" / "rooms.json"
ROOMS_SCHEMA = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "climate-rooms.schema.json"
)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def one_room_registry() -> object:
    return registry_from_payload(
        {
            "version": 3,
            "home": {
                "outdoor_temperature_entity_id": None,
                "presence_entity_id": None,
                "central_heating_entity_id": None,
            },
            "rooms": [
                {"id": "living", "name": "Гостиная", "window_entity_id": None}
            ],
            "devices": [],
        }
    )


class ClimateSetupRoomsTest(unittest.TestCase):
    """Keep room discovery stable while its source is being replaced."""

    def setUp(self) -> None:
        schema = load_json(ROOMS_SCHEMA)
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)

    def test_fresh_rooms_are_sorted_and_keep_the_configured_hausmanhub_name(self) -> None:
        result = climate_available_rooms(
            one_room_registry(),  # type: ignore[arg-type]
            import_climate_state(load_json(SOURCE_FIXTURE)),
        )

        self.validator.validate(result)
        self.assertEqual(load_json(ROOMS_FIXTURE), result)
        self.assertEqual(["kids", "living"], [room["id"] for room in result["rooms"]])
        serialized = json.dumps(result, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("source_id", serialized)
        self.assertNotIn("synthetic-ac-source", serialized)

    def test_stale_data_keeps_rooms_visible_but_disables_selection(self) -> None:
        stale = copy.deepcopy(load_json(SOURCE_FIXTURE))
        stale["runtimeHealth"]["status"] = "stale"  # type: ignore[index]

        result = climate_available_rooms(
            one_room_registry(),  # type: ignore[arg-type]
            import_climate_state(stale),
        )

        self.validator.validate(result)
        self.assertEqual("stale", result["data_status"])
        self.assertFalse(result["selection_allowed"])
        self.assertTrue(result["rooms"])
        self.assertEqual(
            {"data_stale"},
            {room["status"] for room in result["rooms"]},
        )
        self.assertFalse(any(room["selectable"] for room in result["rooms"]))

    def test_missing_configured_room_stays_visible_and_cannot_be_selected(self) -> None:
        missing = copy.deepcopy(load_json(SOURCE_FIXTURE))
        missing["rooms"] = [  # type: ignore[index]
            room for room in missing["rooms"] if room["id"] != "living"  # type: ignore[index]
        ]
        missing["devices"] = [  # type: ignore[index]
            device
            for device in missing["devices"]  # type: ignore[index]
            if device["roomId"] != "living"
        ]
        missing["capabilities"] = [  # type: ignore[index]
            item
            for item in missing["capabilities"]  # type: ignore[index]
            if item["deviceId"] != "synthetic-ac-source-living"
        ]
        missing["authorityReadiness"]["rooms"] = [  # type: ignore[index]
            room
            for room in missing["authorityReadiness"]["rooms"]  # type: ignore[index]
            if room["roomId"] != "living"
        ]

        result = climate_available_rooms(
            one_room_registry(),  # type: ignore[arg-type]
            import_climate_state(missing),
        )

        self.validator.validate(result)
        living = next(room for room in result["rooms"] if room["id"] == "living")
        self.assertEqual(
            {
                "id": "living",
                "name": "Гостиная",
                "configured": True,
                "selectable": False,
                "status": "source_missing",
            },
            living,
        )

    def test_schema_rejects_selection_claim_without_a_selectable_room(self) -> None:
        payload = copy.deepcopy(load_json(ROOMS_FIXTURE))
        for room in payload["rooms"]:  # type: ignore[index]
            room["selectable"] = False
            room["status"] = "data_stale"

        with self.assertRaises(Exception):
            self.validator.validate(payload)

    def test_schema_rejects_stale_room_inside_a_current_snapshot(self) -> None:
        payload = copy.deepcopy(load_json(ROOMS_FIXTURE))
        payload["rooms"][0]["selectable"] = False  # type: ignore[index]
        payload["rooms"][0]["status"] = "data_stale"  # type: ignore[index]

        with self.assertRaises(Exception):
            self.validator.validate(payload)


if __name__ == "__main__":
    unittest.main()
