"""Pure tests for the command-free shadow-to-canary rollout gate."""

from __future__ import annotations

from dataclasses import replace
import unittest

from custom_components.hausman_hub.application.climate_rollout import (
    climate_rollout_status,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateRegistry,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from tests.test_climate_admin_configuration import configured_setup
from tests.test_climate_runtime import native_application_inputs


def _setup(scope: ClimateControlScope) -> tuple[ClimateRegistry, object]:
    registry, contours = configured_setup()
    native, _ = native_application_inputs(registry)
    return (
        ClimateRegistry(
            rooms=native.rooms,
            devices=tuple(
                replace(
                    device,
                    control_scope=(
                        ClimateControlScope.OBSERVED
                        if device.kind
                        in {
                            ClimateDeviceKind.TEMPERATURE_SENSOR,
                            ClimateDeviceKind.HUMIDITY_SENSOR,
                        }
                        else scope
                    ),
                    control_owner=(
                        ClimateControlOwner.OBSERVED
                        if device.kind
                        in {
                            ClimateDeviceKind.TEMPERATURE_SENSOR,
                            ClimateDeviceKind.HUMIDITY_SENSOR,
                        }
                        else ClimateControlOwner.CLIMATE_CORE
                    ),
                )
                for device in native.devices
            ),
            home=native.home,
        ),
        contours,
    )


def _shadow(*, ready: bool = True, samples: int = 24) -> dict[str, object]:
    return {
        "summary": {"sample_count": samples},
        "rooms": [
            {
                "room_id": "living",
                "verdict": "ready" if ready else "insufficient_data",
                "fresh": True,
            }
        ],
    }


class ClimateRolloutStatusTest(unittest.TestCase):
    def test_one_canary_room_with_ready_shadow_can_be_started(self) -> None:
        registry, contours = _setup(ClimateControlScope.CANARY)

        result = climate_rollout_status(
            registry,
            contours,
            bridge_mode=ClimateControlMode.DISABLED,
            shadow_window=_shadow(),
        )

        self.assertEqual("ready_for_canary", result["phase"])
        self.assertTrue(result["enable_allowed"])
        self.assertFalse(result["commands_enabled"])
        self.assertEqual("living", result["canary_room_id"])
        self.assertEqual([], result["reasons"])

    def test_missing_shadow_fails_closed(self) -> None:
        registry, contours = _setup(ClimateControlScope.CANARY)

        result = climate_rollout_status(
            registry,
            contours,
            bridge_mode=ClimateControlMode.DISABLED,
            shadow_window=None,
        )

        self.assertEqual("shadow", result["phase"])
        self.assertFalse(result["enable_allowed"])
        self.assertEqual(
            ["shadow_evidence_missing", "shadow_evidence_not_ready"],
            result["reasons"],
        )

    def test_managed_scope_cannot_bypass_canary_while_disabled(self) -> None:
        registry, contours = _setup(ClimateControlScope.MANAGED)

        result = climate_rollout_status(
            registry,
            contours,
            bridge_mode=ClimateControlMode.DISABLED,
            shadow_window=_shadow(),
        )

        self.assertFalse(result["enable_allowed"])
        self.assertIn("canary_room_not_selected", result["reasons"])
        self.assertIn("managed_scope_already_present", result["reasons"])

    def test_active_managed_mode_is_reported_but_never_reauthorized(self) -> None:
        registry, contours = _setup(ClimateControlScope.MANAGED)

        result = climate_rollout_status(
            registry,
            contours,
            bridge_mode=ClimateControlMode.MANAGED,
            shadow_window=_shadow(),
        )

        self.assertEqual("managed", result["phase"])
        self.assertFalse(result["enable_allowed"])
        self.assertTrue(result["commands_enabled"])
        self.assertEqual(1, result["managed_room_count"])


if __name__ == "__main__":
    unittest.main()
