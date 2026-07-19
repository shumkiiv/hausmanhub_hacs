"""Pure tests for the public tablet and private administrator projections."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from custom_components.hausman_hub.application.android_climate import (
    admin_climate_import_snapshot,
    android_climate_snapshot,
)
from custom_components.hausman_hub.application.climate_import import import_climate_state
from custom_components.hausman_hub.application.climate_registry import registry_from_payload
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
from tests.test_climate_import import registry_payload, source_payload


class AndroidClimateTest(unittest.TestCase):
    """Keep the normal Android contract stable and private-id free."""

    def test_tablet_snapshot_contains_registered_devices_and_live_room_state(self) -> None:
        result = android_climate_snapshot(
            registry_from_payload(registry_payload()),
            import_climate_state(source_payload()),
            bridge_mode=ClimateBridgeMode.SHADOW,
        )

        self.assertEqual("hausman-hasc-home", result["contract"]["name"])
        self.assertEqual(6, result["contract"]["version"])
        self.assertEqual(
            "Автоматически",
            result["display_names"]["room_modes"]["automatic"],
        )
        self.assertEqual("automatic", result["rooms"][0]["mode"])
        self.assertEqual("working", result["rooms"][0]["devices"][0]["state"])
        self.assertEqual([], result["contours"])
        self.assertEqual("living_ac", result["rooms"][0]["devices"][0]["id"])
        self.assertEqual(25.8, result["rooms"][0]["temperature"])
        self.assertFalse(result["climate"]["commands_enabled"])
        self.assertEqual(
            ["set_room_target", "turn_room_off"],
            result["rooms"][0]["control"]["actions"],
        )
        self.assertEqual(
            {
                "set_room_target": {
                    "target_temperature": {
                        "type": "number",
                        "required": True,
                        "minimum": 18.0,
                        "maximum": 28.0,
                        "step": 0.5,
                        "unit": "°C",
                    }
                }
            },
            result["rooms"][0]["control"]["action_inputs"],
        )
        self.assertEqual(
            {
                "set_room_target": {
                    "title": "Установить температуру",
                    "description": "Изменить желаемую температуру в комнате.",
                    "confirmation_required": False,
                    "fields": {
                        "target_temperature": {
                            "title": "Желаемая температура",
                            "description": (
                                "Значение, которое должен поддерживать "
                                "климатический контур."
                            ),
                        }
                    },
                },
                "turn_room_off": {
                    "title": "Выключить климат",
                    "description": "Остановить поддержание климата в комнате.",
                    "confirmation_required": True,
                    "fields": {},
                },
            },
            result["rooms"][0]["control"]["action_presentations"],
        )
        self.assertEqual(
            ["shadow_only"],
            result["rooms"][0]["control"]["blocked_reasons"],
        )
        self.assertEqual(1, result["reconciliation"]["unregistered_device_count"])

    def test_tablet_snapshot_never_exposes_private_source_or_entity_ids(self) -> None:
        result = android_climate_snapshot(
            registry_from_payload(registry_payload()),
            import_climate_state(source_payload()),
            bridge_mode=ClimateBridgeMode.CANARY,
            canary_room_id="living",
            candidate_ready=True,
        )
        encoded = json.dumps(result, sort_keys=True)

        self.assertTrue(result["climate"]["commands_enabled"])
        self.assertTrue(result["rooms"][0]["control"]["enabled"])
        self.assertEqual([], result["rooms"][0]["control"]["blocked_reasons"])
        self.assertNotIn("synthetic-ac-source-living", encoded)
        self.assertNotIn("climate.synthetic_living_ac", encoded)
        self.assertNotIn("source_id", encoded)
        self.assertNotIn("entity_id", encoded)

    def test_tablet_snapshot_never_echoes_private_engine_state(self) -> None:
        source = source_payload()
        source["devices"][0]["state"] = "vendor-private-cooling-stage-7"  # type: ignore[index]

        result = android_climate_snapshot(
            registry_from_payload(registry_payload()),
            import_climate_state(source),
            bridge_mode=ClimateBridgeMode.SHADOW,
        )
        encoded = json.dumps(result, sort_keys=True)

        self.assertEqual("unknown", result["rooms"][0]["devices"][0]["state"])
        self.assertNotIn("vendor-private-cooling-stage-7", encoded)

    def test_action_metadata_exists_only_for_advertised_actions(self) -> None:
        source = source_payload()
        source["capabilities"][0]["commandTypes"].remove(  # type: ignore[index]
            "climate.set_temperature"
        )

        result = android_climate_snapshot(
            registry_from_payload(registry_payload()),
            import_climate_state(source),
            bridge_mode=ClimateBridgeMode.CANARY,
            canary_room_id="living",
            candidate_ready=True,
        )
        control = result["rooms"][0]["control"]

        self.assertEqual(["turn_room_off"], control["actions"])
        self.assertEqual({}, control["action_inputs"])
        self.assertEqual(
            {
                "turn_room_off": {
                    "title": "Выключить климат",
                    "description": "Остановить поддержание климата в комнате.",
                    "confirmation_required": True,
                    "fields": {},
                }
            },
            control["action_presentations"],
        )
        self.assertIn("actions_unsupported", control["blocked_reasons"])

        source["capabilities"][0]["commandTypes"].remove(  # type: ignore[index]
            "climate.turn_off"
        )
        without_actions = android_climate_snapshot(
            registry_from_payload(registry_payload()),
            import_climate_state(source),
            bridge_mode=ClimateBridgeMode.CANARY,
            canary_room_id="living",
            candidate_ready=True,
        )
        empty_control = without_actions["rooms"][0]["control"]
        self.assertEqual([], empty_control["actions"])
        self.assertEqual({}, empty_control["action_inputs"])
        self.assertEqual({}, empty_control["action_presentations"])

    def test_room_control_fails_closed_for_unavailable_device_and_pending_operation(
        self,
    ) -> None:
        source = source_payload()
        source["devices"][0]["unavailable"] = True  # type: ignore[index]
        source["devices"][0]["state"] = "unavailable"  # type: ignore[index]

        unavailable = android_climate_snapshot(
            registry_from_payload(registry_payload()),
            import_climate_state(source),
            bridge_mode=ClimateBridgeMode.CANARY,
            canary_room_id="living",
            candidate_ready=True,
        )
        pending = android_climate_snapshot(
            registry_from_payload(registry_payload()),
            import_climate_state(source_payload()),
            bridge_mode=ClimateBridgeMode.CANARY,
            canary_room_id="living",
            candidate_ready=True,
            pending_room_ids=("living",),
        )

        self.assertFalse(unavailable["rooms"][0]["control"]["enabled"])
        self.assertIn(
            "device_unavailable",
            unavailable["rooms"][0]["control"]["blocked_reasons"],
        )
        self.assertFalse(pending["rooms"][0]["control"]["enabled"])
        self.assertEqual(
            ["operation_pending"],
            pending["rooms"][0]["control"]["blocked_reasons"],
        )

    def test_each_public_room_control_gate_has_a_stable_blocked_reason(self) -> None:
        cases: list[tuple[str, dict[str, object], dict[str, object], ClimateBridgeMode, str]] = []

        disabled_source = source_payload()
        cases.append(
            (
                "bridge_disabled",
                registry_payload(),
                disabled_source,
                ClimateBridgeMode.DISABLED,
                "living",
            )
        )
        cases.append(
            (
                "room_not_selected",
                registry_payload(),
                source_payload(),
                ClimateBridgeMode.CANARY,
                "kids",
            )
        )
        stale_source = source_payload()
        stale_source["runtimeHealth"]["status"] = "stale"  # type: ignore[index]
        cases.append(
            (
                "state_stale",
                registry_payload(),
                stale_source,
                ClimateBridgeMode.CANARY,
                "living",
            )
        )
        authority_source = source_payload()
        authority_source["authorityReadiness"]["rooms"][0]["eligible"] = False  # type: ignore[index]
        cases.append(
            (
                "authority_not_ready",
                registry_payload(),
                authority_source,
                ClimateBridgeMode.CANARY,
                "living",
            )
        )
        mismatched_registry = copy.deepcopy(registry_payload())
        mismatched_registry["devices"][0]["source_id"] = "missing-source"  # type: ignore[index]
        cases.append(
            (
                "registry_mismatch",
                mismatched_registry,
                source_payload(),
                ClimateBridgeMode.CANARY,
                "living",
            )
        )
        unsupported_source = source_payload()
        unsupported_source["capabilities"][0]["commandTypes"].remove(  # type: ignore[index]
            "climate.turn_off"
        )
        cases.append(
            (
                "actions_unsupported",
                registry_payload(),
                unsupported_source,
                ClimateBridgeMode.CANARY,
                "living",
            )
        )

        for reason, registry, source, mode, canary_room_id in cases:
            with self.subTest(reason=reason):
                result = android_climate_snapshot(
                    registry_from_payload(registry),
                    import_climate_state(source),
                    bridge_mode=mode,
                    canary_room_id=canary_room_id,
                    candidate_ready=True,
                )
                control = result["rooms"][0]["control"]
                self.assertFalse(control["enabled"])
                self.assertIn(reason, control["blocked_reasons"])
                self.assertFalse(result["climate"]["commands_enabled"])

    def test_admin_import_explicitly_contains_candidates_and_private_bindings(self) -> None:
        result = admin_climate_import_snapshot(
            registry_from_payload(registry_payload()),
            import_climate_state(source_payload()),
        )

        self.assertEqual(
            "synthetic-humidifier-source-kids",
            result["candidates"][1]["source_id"],
        )
        self.assertEqual(
            ["humidifier"],
            result["candidates"][1]["suggested_kinds"],
        )


if __name__ == "__main__":
    unittest.main()
