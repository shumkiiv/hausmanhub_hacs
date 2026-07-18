"""Pure tests for HASC's non-executing native climate decision."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.climate_import import import_climate_state
from custom_components.hausman_hub.application.climate_registry import registry_from_payload
from custom_components.hausman_hub.domain.native_climate import (
    HumidityDemand,
    NativeClimateMode,
    NativeClimateViolation,
    TemperatureDemand,
    native_climate_policy,
    preview_native_climate,
)
from tests.test_climate_import import registry_payload, source_payload


class NativeClimateTest(unittest.TestCase):
    def test_disabled_policy_retains_nothing_and_never_enables_commands(self) -> None:
        policy = native_climate_policy("disabled")
        decision = preview_native_climate(
            policy,
            registry_from_payload(registry_payload()),
            import_climate_state(source_payload()),
        )

        self.assertIs(policy.mode, NativeClimateMode.DISABLED)
        self.assertIsNone(policy.room_id)
        self.assertEqual("disabled", decision.status)
        execution = decision.as_payload()["execution"]
        self.assertFalse(execution["commands_enabled"])  # type: ignore[index]

    def test_preview_calculates_cooling_from_fresh_observations(self) -> None:
        policy = native_climate_policy("preview", "living", "22.0", 45)

        decision = preview_native_climate(
            policy,
            registry_from_payload(registry_payload()),
            import_climate_state(source_payload()),
        )

        self.assertEqual("ready", decision.status)
        self.assertIs(decision.temperature_demand, TemperatureDemand.COOLING)
        self.assertIs(decision.humidity_demand, HumidityDemand.HOLD)
        self.assertTrue(decision.temperature_device_ready)
        self.assertFalse(decision.humidity_device_ready)
        self.assertEqual((), decision.reasons)
        payload = decision.as_payload()
        self.assertEqual("preview_only", payload["execution"]["mode"])  # type: ignore[index]
        self.assertFalse(payload["execution"]["commands_enabled"])  # type: ignore[index]

    def test_preview_reports_missing_heating_equipment_without_a_command(self) -> None:
        policy = native_climate_policy("preview", "living", 28.0, 45)

        decision = preview_native_climate(
            policy,
            registry_from_payload(registry_payload()),
            import_climate_state(source_payload()),
        )

        self.assertIs(decision.temperature_demand, TemperatureDemand.HEATING)
        self.assertFalse(decision.temperature_device_ready)
        self.assertIn("temperature_device_unavailable", decision.reasons)
        execution = decision.as_payload()["execution"]
        self.assertFalse(execution["commands_enabled"])  # type: ignore[index]

    def test_missing_or_stale_state_cannot_produce_an_actionable_decision(self) -> None:
        policy = native_climate_policy("preview", "living", 22.0, 45)
        registry = registry_from_payload(registry_payload())

        missing = preview_native_climate(policy, registry, None)
        stale = preview_native_climate(
            policy,
            registry,
            import_climate_state(source_payload(), now_ms=1784280300001),
        )

        self.assertEqual("unavailable", missing.status)
        self.assertIs(missing.temperature_demand, TemperatureDemand.UNAVAILABLE)
        self.assertEqual("stale", stale.status)
        self.assertIs(stale.temperature_demand, TemperatureDemand.UNAVAILABLE)
        self.assertFalse(stale.temperature_device_ready)

    def test_policy_accepts_only_fixed_comfort_bounds_and_steps(self) -> None:
        for temperature, humidity in (
            (17.5, 45),
            (28.5, 45),
            (22.2, 45),
            (22.0, 29),
            (22.0, 71),
            (22.0, 42),
            (True, 45),
            (22.0, True),
        ):
            with self.subTest(temperature=temperature, humidity=humidity):
                with self.assertRaises(NativeClimateViolation):
                    native_climate_policy(
                        "preview",
                        "living",
                        temperature,
                        humidity,
                    )


if __name__ == "__main__":
    unittest.main()
