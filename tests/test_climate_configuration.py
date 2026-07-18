"""Pure tests for disabled/shadow/canary climate bridge configuration."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.configuration import (
    ConfigurationViolation,
    create_options,
    effective_configuration,
)
from custom_components.hausman_hub.domain.climate_bridge import (
    ClimateBridgeMode,
    UnsafeClimateBridgeTarget,
    climate_bridge_target,
)
from custom_components.hausman_hub.domain.native_climate import NativeClimateMode


ENTRY = {
    "mode": "read-only",
    "direct_execution_status": "direct_execution_blocked",
}


class ClimateConfigurationTest(unittest.TestCase):
    def test_target_accepts_only_normalized_private_literal_origins(self) -> None:
        self.assertEqual(
            "http://192.168.1.10:1880",
            climate_bridge_target("http://192.168.1.10:1880/").origin,
        )
        self.assertEqual(
            "http://[::1]:1880",
            climate_bridge_target("http://[::1]:1880").origin,
        )
        for value in (
            "http://example.com:1880",
            "http://8.8.8.8:1880",
            "http://192.168.1.10:1880/path",
            "http://user:pass@192.168.1.10:1880",
            "file:///tmp/api",
        ):
            with self.subTest(value=value), self.assertRaises(UnsafeClimateBridgeTarget):
                climate_bridge_target(value)

    def test_disabled_is_complete_rollback_and_drops_private_values(self) -> None:
        result = create_options(
            "read-only",
            climate_bridge_mode_value="disabled",
            climate_bridge_target_value="http://192.168.1.10:1880",
            climate_canary_room_id_value="living",
        )

        self.assertEqual("disabled", result["climate_bridge_mode"])
        self.assertNotIn("climate_bridge_target", result)
        self.assertNotIn("climate_canary_room_id", result)

    def test_shadow_requires_target_and_never_retains_canary_room(self) -> None:
        result = create_options(
            "shadow",
            climate_bridge_mode_value="shadow",
            climate_bridge_target_value="http://127.0.0.1:1880",
            climate_canary_room_id_value="living",
        )

        configuration = effective_configuration(ENTRY, result)
        self.assertIs(configuration.climate_bridge_mode, ClimateBridgeMode.SHADOW)
        self.assertIsNone(configuration.climate_canary_room_id)
        self.assertNotIn("climate_canary_room_id", result)

    def test_canary_requires_private_target_and_stable_room(self) -> None:
        result = create_options(
            "shadow",
            climate_bridge_mode_value="canary",
            climate_bridge_target_value="http://10.0.0.2:1880",
            climate_canary_room_id_value="living",
        )
        configuration = effective_configuration(ENTRY, result)

        self.assertIs(configuration.climate_bridge_mode, ClimateBridgeMode.CANARY)
        self.assertEqual("living", configuration.climate_canary_room_id)

        for target, room in (
            ("http://example.com:1880", "living"),
            ("http://10.0.0.2:1880", "Living room"),
            (None, "living"),
            ("http://10.0.0.2:1880", None),
        ):
            with self.subTest(target=target, room=room), self.assertRaises(
                ConfigurationViolation
            ):
                create_options(
                    "shadow",
                    climate_bridge_mode_value="canary",
                    climate_bridge_target_value=target,
                    climate_canary_room_id_value=room,
                )

    def test_persisted_hidden_bridge_fields_fail_closed(self) -> None:
        with self.assertRaisesRegex(ConfigurationViolation, "disabled"):
            effective_configuration(
                ENTRY,
                {
                    "climate_bridge_mode": "disabled",
                    "climate_bridge_target": "http://127.0.0.1:1880",
                },
            )
        with self.assertRaisesRegex(ConfigurationViolation, "unsupported fields"):
            effective_configuration(ENTRY, {"climate_bridge_token": "secret"})

    def test_native_preview_persists_one_room_targets_without_authority(self) -> None:
        result = create_options(
            "shadow",
            native_climate_mode_value="preview",
            native_climate_room_id_value="living",
            native_target_temperature_value="22.5",
            native_target_humidity_value=45,
        )

        configuration = effective_configuration(ENTRY, result)
        policy = configuration.native_climate_policy
        self.assertIs(policy.mode, NativeClimateMode.PREVIEW)
        self.assertEqual("living", policy.room_id)
        self.assertEqual(22.5, policy.target_temperature)
        self.assertEqual(45, policy.target_humidity)
        self.assertNotIn("commands_enabled", result)

    def test_disabling_native_preview_drops_room_and_targets(self) -> None:
        result = create_options(
            "read-only",
            native_climate_mode_value="disabled",
            native_climate_room_id_value="living",
            native_target_temperature_value=22.0,
            native_target_humidity_value=45,
        )

        self.assertNotIn("native_climate_mode", result)
        self.assertNotIn("native_climate_room_id", result)
        self.assertNotIn("native_target_temperature", result)
        self.assertNotIn("native_target_humidity", result)
        self.assertIs(
            effective_configuration(ENTRY, result).native_climate_policy.mode,
            NativeClimateMode.DISABLED,
        )

    def test_persisted_incomplete_native_preview_fails_closed(self) -> None:
        for options in (
            {"native_climate_mode": "preview"},
            {
                "native_climate_mode": "preview",
                "native_climate_room_id": "living",
                "native_target_temperature": 22.0,
            },
            {
                "native_climate_mode": "disabled",
                "native_climate_room_id": "living",
            },
        ):
            with self.subTest(options=options), self.assertRaises(
                ConfigurationViolation
            ):
                effective_configuration(ENTRY, options)


if __name__ == "__main__":
    unittest.main()
