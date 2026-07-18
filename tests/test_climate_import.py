"""Pure read-only tests for importing the existing Climate API contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from custom_components.hausman_hub.application.climate_import import (
    ClimateImportViolation,
    import_climate_state,
)
from custom_components.hausman_hub.application.climate_registry import (
    ClimateRegistryViolation,
    reconcile_climate_registry,
    registry_from_payload,
    registry_to_payload,
)
from custom_components.hausman_hub.domain.climate import ClimateDeviceKind


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "climate_bridge" / "valid_state.json"


def source_payload() -> dict[str, object]:
    """Return a fresh copy of the sanitized Climate API fixture."""

    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def registry_payload() -> dict[str, object]:
    """Return an exact configured registry with private synthetic bindings."""

    return {
        "version": 1,
        "rooms": [
            {"id": "living", "name": "Living room"},
            {"id": "kids", "name": "Kids"},
        ],
        "devices": [
            {
                "id": "living_ac",
                "name": "Living AC",
                "room_id": "living",
                "kind": "air_conditioner",
                "source_id": "synthetic-ac-source-living",
                "control_scope": "canary",
                "control_owner": "climate_core",
                "capabilities": [
                    "power",
                    "target_temperature",
                    "hvac_mode",
                    "fan_mode",
                ],
                "endpoints": [
                    {
                        "role": "control",
                        "entity_id": "climate.synthetic_living_ac",
                    }
                ],
            }
        ],
    }


def complete_registry_payload() -> dict[str, object]:
    """Return exact bindings for every device in the synthetic source fixture."""

    payload = registry_payload()
    payload["devices"].append(  # type: ignore[union-attr]
        {
            "id": "kids_humidifier",
            "name": "Kids humidifier",
            "room_id": "kids",
            "kind": "humidifier",
            "source_id": "synthetic-humidifier-source-kids",
            "control_scope": "observed",
            "control_owner": "observed",
            "capabilities": ["power", "target_humidity"],
            "endpoints": [
                {
                    "role": "control",
                    "entity_id": "humidifier.synthetic_kids",
                }
            ],
        }
    )
    return payload


class ClimateImportTest(unittest.TestCase):
    """Require explicit binding and fail-closed state/capability validation."""

    def test_valid_contract_imports_rooms_devices_capabilities_and_authority(self) -> None:
        snapshot = import_climate_state(
            source_payload(),
            now_ms=1784280005000,
        )

        self.assertTrue(snapshot.runtime_fresh)
        self.assertEqual(2, len(snapshot.rooms))
        self.assertEqual(2, len(snapshot.devices))
        self.assertTrue(snapshot.room("living").authority_eligible)
        self.assertFalse(snapshot.room("kids").authority_eligible)
        self.assertEqual(
            (ClimateDeviceKind.AIR_CONDITIONER,),
            snapshot.device("synthetic-ac-source-living").suggested_kinds,
        )
        self.assertEqual(
            (ClimateDeviceKind.HUMIDIFIER,),
            snapshot.device("synthetic-humidifier-source-kids").suggested_kinds,
        )

    def test_stale_runtime_remains_visible_but_is_not_command_fresh(self) -> None:
        snapshot = import_climate_state(
            source_payload(),
            now_ms=1784280000000 + 5 * 60 * 1000 + 1,
        )

        self.assertFalse(snapshot.runtime_fresh)
        self.assertEqual(2, len(snapshot.rooms))

    def test_import_rejects_wrong_contract_duplicates_and_unknown_commands(self) -> None:
        wrong_contract = source_payload()
        wrong_contract["contract"]["name"] = "not-climate"  # type: ignore[index]
        with self.assertRaisesRegex(ClimateImportViolation, "contract name"):
            import_climate_state(wrong_contract)

        duplicate = source_payload()
        duplicate["devices"].append(copy.deepcopy(duplicate["devices"][0]))  # type: ignore[union-attr,index]
        with self.assertRaisesRegex(ClimateImportViolation, "device ids"):
            import_climate_state(duplicate)

        unknown_command = source_payload()
        unknown_command["capabilities"][0]["commandTypes"].append("homeassistant.restart")  # type: ignore[index,union-attr]
        with self.assertRaisesRegex(ClimateImportViolation, "unsupported command"):
            import_climate_state(unknown_command)

    def test_registry_round_trip_preserves_only_fixed_fields(self) -> None:
        registry = registry_from_payload(registry_payload())

        self.assertEqual(registry_payload(), registry_to_payload(registry))

        extra = registry_payload()
        extra["devices"][0]["service"] = "climate.set_temperature"  # type: ignore[index]
        with self.assertRaisesRegex(ClimateRegistryViolation, "fixed fields"):
            registry_from_payload(extra)

    def test_reconciliation_never_auto_imports_or_hides_drift(self) -> None:
        registry = registry_from_payload(registry_payload())
        snapshot = import_climate_state(source_payload())

        reconciliation = reconcile_climate_registry(registry, snapshot)

        self.assertFalse(reconciliation.matches)
        self.assertEqual(("living_ac",), reconciliation.matched_device_ids)
        self.assertEqual(
            ("synthetic-humidifier-source-kids",),
            reconciliation.unregistered_source_ids,
        )
        self.assertEqual(1, len(registry.devices))

    def test_reconciliation_reports_missing_and_room_mismatch_separately(self) -> None:
        payload = registry_payload()
        payload["devices"].append(  # type: ignore[union-attr]
            {
                "id": "missing_humidifier",
                "name": "Missing humidifier",
                "room_id": "kids",
                "kind": "humidifier",
                "source_id": "missing-source",
                "control_scope": "observed",
                "control_owner": "observed",
                "capabilities": ["power", "target_humidity"],
                "endpoints": [
                    {
                        "role": "control",
                        "entity_id": "humidifier.synthetic_missing",
                    }
                ],
            }
        )
        registry = registry_from_payload(payload)
        source = source_payload()
        source["devices"][0]["roomId"] = "kids"  # type: ignore[index]
        snapshot = import_climate_state(source)

        reconciliation = reconcile_climate_registry(registry, snapshot)

        self.assertEqual(("missing_humidifier",), reconciliation.missing_device_ids)
        self.assertEqual(("living_ac",), reconciliation.room_mismatch_device_ids)


if __name__ == "__main__":
    unittest.main()
