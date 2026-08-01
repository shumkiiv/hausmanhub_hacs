"""Pure tests for the Node-RED retirement readiness contract."""

from __future__ import annotations

from dataclasses import replace
import unittest

from custom_components.hausman_hub.application.climate_cutover import (
    climate_cutover_status,
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


class ClimateCutoverStatusTest(unittest.TestCase):
    def test_all_managed_rooms_with_fresh_shadow_are_ready(self) -> None:
        registry, contours = _setup(ClimateControlScope.MANAGED)

        result = climate_cutover_status(
            registry,
            contours,
            bridge_mode=ClimateControlMode.MANAGED,
            shadow_window=_shadow(),
        )

        self.assertEqual("ready_to_retire", result["phase"])
        self.assertTrue(result["node_red_can_be_disabled"])
        self.assertFalse(result["physical_commands_sent"])
        self.assertEqual([], result["pending_room_ids"])
        self.assertEqual([], result["reasons"])

    def test_disabled_native_control_fails_closed(self) -> None:
        registry, contours = _setup(ClimateControlScope.MANAGED)

        result = climate_cutover_status(
            registry,
            contours,
            bridge_mode=ClimateControlMode.DISABLED,
            shadow_window=_shadow(),
        )

        self.assertFalse(result["node_red_can_be_disabled"])
        self.assertEqual(["native_control_disabled"], result["reasons"])

    def test_canary_and_missing_shadow_are_reported(self) -> None:
        registry, contours = _setup(ClimateControlScope.CANARY)

        result = climate_cutover_status(
            registry,
            contours,
            bridge_mode=ClimateControlMode.MANAGED,
            shadow_window=None,
        )

        self.assertEqual("not_ready", result["phase"])
        self.assertEqual(["living"], result["pending_room_ids"])
        self.assertEqual(
            [
                "rooms_not_fully_managed",
                "shadow_evidence_missing",
                "rooms_not_shadow_ready",
            ],
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
