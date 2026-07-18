"""Pure tests for the strict HASC-to-climate-core command translator."""

from __future__ import annotations

import copy
import unittest

from custom_components.hausman_hub.application.climate_commands import (
    ClimateCommandViolation,
    plan_climate_command,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
from custom_components.hausman_hub.application.climate_import import import_climate_state
from custom_components.hausman_hub.application.climate_registry import registry_from_payload
from tests.test_climate_import import registry_payload, source_payload


def plan(request: object, *, mode: ClimateBridgeMode = ClimateBridgeMode.CANARY):
    """Plan against the sanitized eligible living-room fixture."""

    return plan_climate_command(
        request,
        registry_from_payload(registry_payload()),
        import_climate_state(source_payload()),
        bridge_mode=mode,
        canary_room_id="living",
    )


class ClimateCommandsTest(unittest.TestCase):
    """Require exact public actions, mappings, capabilities, and canary gates."""

    def test_room_target_maps_to_existing_core_contract(self) -> None:
        result = plan(
            {
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )

        self.assertTrue(result.execute)
        self.assertEqual("climate.set_temperature", result.backend_command_type)
        self.assertEqual(
            {
                "command": "set_room_target",
                "roomId": "living",
                "targetTemperature": 24.5,
            },
            result.backend_payload,
        )

    def test_device_actions_resolve_private_id_only_after_validation(self) -> None:
        result = plan(
            {
                "action": "set_device_fan_mode",
                "device_id": "living_ac",
                "fan_mode": "low",
            }
        )

        self.assertEqual("living_ac", result.device_id)
        self.assertEqual(
            {
                "command": "device_action",
                "roomId": "living",
                "deviceId": "synthetic-ac-source-living",
                "payload": {"type": "climate.set_fan_mode", "fan_mode": "low"},
            },
            result.backend_payload,
        )

    def test_shadow_builds_same_plan_but_never_executes(self) -> None:
        result = plan(
            {
                "action": "set_device_power",
                "device_id": "living_ac",
                "on": False,
            },
            mode=ClimateBridgeMode.SHADOW,
        )

        self.assertFalse(result.execute)
        self.assertEqual("climate.turn_off", result.backend_command_type)

    def test_disabled_unknown_extra_and_raw_backend_fields_fail_closed(self) -> None:
        with self.assertRaisesRegex(ClimateCommandViolation, "disabled"):
            plan_climate_command(
                {"action": "set_room_target"},
                registry_from_payload(registry_payload()),
                import_climate_state(source_payload()),
                bridge_mode=ClimateBridgeMode.DISABLED,
            )
        with self.assertRaisesRegex(ClimateCommandViolation, "unsupported"):
            plan({"action": "call_service", "service": "homeassistant.restart"})
        with self.assertRaisesRegex(ClimateCommandViolation, "fixed fields"):
            plan(
                {
                    "action": "set_device_power",
                    "device_id": "living_ac",
                    "on": True,
                    "entity_id": "climate.injected",
                }
            )

    def test_ranges_modes_and_advertised_capabilities_are_enforced(self) -> None:
        with self.assertRaisesRegex(ClimateCommandViolation, "0.5 steps"):
            plan(
                {
                    "action": "set_room_target",
                    "room_id": "living",
                    "target_temperature": 24.2,
                }
            )
        with self.assertRaisesRegex(ClimateCommandViolation, "0.5 steps"):
            plan(
                {
                    "action": "set_room_target",
                    "room_id": "living",
                    "target_temperature": 24.500000000000004,
                }
            )
        with self.assertRaisesRegex(ClimateCommandViolation, "HVAC mode"):
            plan(
                {
                    "action": "set_device_hvac_mode",
                    "device_id": "living_ac",
                    "hvac_mode": "heat",
                }
            )
        source = source_payload()
        source["capabilities"][0]["commandTypes"].remove("climate.set_fan_mode")
        with self.assertRaisesRegex(ClimateCommandViolation, "not advertised"):
            plan_climate_command(
                {
                    "action": "set_device_fan_mode",
                    "device_id": "living_ac",
                    "fan_mode": "high",
                },
                registry_from_payload(registry_payload()),
                import_climate_state(source),
                bridge_mode=ClimateBridgeMode.SHADOW,
            )

        unavailable = source_payload()
        unavailable["devices"][0]["unavailable"] = True
        unavailable["devices"][0]["state"] = "unavailable"
        with self.assertRaisesRegex(ClimateCommandViolation, "unavailable"):
            plan_climate_command(
                {
                    "action": "turn_room_off",
                    "room_id": "living",
                },
                registry_from_payload(registry_payload()),
                import_climate_state(unavailable),
                bridge_mode=ClimateBridgeMode.SHADOW,
            )

    def test_canary_requires_fresh_exact_room_and_authority(self) -> None:
        registry = registry_from_payload(registry_payload())
        snapshot = import_climate_state(source_payload())
        request = {
            "action": "set_device_power",
            "device_id": "living_ac",
            "on": True,
        }
        with self.assertRaisesRegex(ClimateCommandViolation, "canary room"):
            plan_climate_command(
                request,
                registry,
                snapshot,
                bridge_mode=ClimateBridgeMode.CANARY,
                canary_room_id="kids",
            )

        stale = source_payload()
        stale["runtimeHealth"]["status"] = "stale"
        with self.assertRaisesRegex(ClimateCommandViolation, "stale"):
            plan_climate_command(
                request,
                registry,
                import_climate_state(stale),
                bridge_mode=ClimateBridgeMode.CANARY,
                canary_room_id="living",
            )

        blocked = source_payload()
        blocked["authorityReadiness"]["rooms"][0]["eligible"] = False
        with self.assertRaisesRegex(ClimateCommandViolation, "authority"):
            plan_climate_command(
                request,
                registry,
                import_climate_state(blocked),
                bridge_mode=ClimateBridgeMode.CANARY,
                canary_room_id="living",
            )

    def test_trv_humidifier_and_floor_heating_have_explicit_mappings(self) -> None:
        payload = registry_payload()
        payload["devices"] = [
            {
                "id": "kids_humidifier",
                "name": "Kids humidifier",
                "room_id": "kids",
                "kind": "humidifier",
                "source_id": "synthetic-humidifier-source-kids",
                "control_scope": "canary",
                "control_owner": "climate_core",
                "capabilities": ["power", "target_humidity"],
                "endpoints": [
                    {"role": "control", "entity_id": "humidifier.synthetic_kids"}
                ],
            }
        ]
        result = plan_climate_command(
            {
                "action": "set_device_target_humidity",
                "device_id": "kids_humidifier",
                "target_humidity": 50,
            },
            registry_from_payload(payload),
            import_climate_state(source_payload()),
            bridge_mode=ClimateBridgeMode.SHADOW,
        )
        self.assertEqual("humidifier.set_humidity", result.backend_command_type)

        trv_payload = copy.deepcopy(payload)
        trv_payload["devices"][0].update(
            {
                "id": "kids_trv",
                "name": "Kids TRV",
                "kind": "radiator_thermostat",
                "source_id": "synthetic-trv-source-kids",
                "capabilities": ["target_temperature"],
                "endpoints": [{"role": "control", "entity_id": "climate.synthetic_trv"}],
            }
        )
        source = source_payload()
        source["devices"].append(
            {
                "id": "synthetic-trv-source-kids",
                "name": "Kids TRV",
                "roomId": "kids",
                "domain": "climate",
                "category": "heating",
                "state": "heat",
                "unavailable": False,
            }
        )
        source["capabilities"].append(
            {
                "deviceId": "synthetic-trv-source-kids",
                "commandTypes": ["trv.set_temperature"],
            }
        )
        result = plan_climate_command(
            {
                "action": "set_device_target_temperature",
                "device_id": "kids_trv",
                "target_temperature": 22.5,
            },
            registry_from_payload(trv_payload),
            import_climate_state(source),
            bridge_mode=ClimateBridgeMode.SHADOW,
        )
        self.assertEqual("trv.set_temperature", result.backend_command_type)


if __name__ == "__main__":
    unittest.main()
