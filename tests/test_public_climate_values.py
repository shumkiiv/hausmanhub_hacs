"""Stable HASC climate codes and their plain Russian names."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.public_climate_values import (
    PUBLIC_CLIMATE_DISPLAY_NAMES,
    public_climate_display_names,
    public_device_state,
    public_room_data_status,
    public_room_mode,
    public_strategy,
)
from custom_components.hausman_hub.domain.climate import ClimateCapability


class PublicClimateValuesTest(unittest.TestCase):
    """Never expose arbitrary engine text through the tablet contracts."""

    def test_every_public_code_has_one_nonempty_russian_name(self) -> None:
        snapshot = public_climate_display_names()

        self.assertEqual(PUBLIC_CLIMATE_DISPLAY_NAMES, snapshot)
        self.assertTrue(all(snapshot.values()))
        for names in snapshot.values():
            self.assertTrue(all(code and name for code, name in names.items()))

        snapshot["device_states"]["working"] = "изменено"
        self.assertEqual(
            "Работает",
            PUBLIC_CLIMATE_DISPLAY_NAMES["device_states"]["working"],
        )
        self.assertNotIn(
            "data_statuses",
            public_climate_display_names(include_room_data_statuses=False),
        )

    def test_private_engine_modes_are_normalized_to_hasc_codes(self) -> None:
        self.assertEqual("automatic", public_room_mode("auto"))
        self.assertEqual("automatic", public_room_mode("forced_auto_only"))
        self.assertEqual("manual", public_room_mode("manual"))
        self.assertEqual("unknown", public_room_mode("vendor_private_mode"))

    def test_every_device_capability_has_a_russian_name(self) -> None:
        self.assertEqual(
            {capability.value for capability in ClimateCapability},
            set(PUBLIC_CLIMATE_DISPLAY_NAMES["device_capabilities"]),
        )

    def test_room_data_status_distinguishes_current_stale_and_missing(self) -> None:
        self.assertEqual(
            "current",
            public_room_data_status(present=True, fresh=True),
        )
        self.assertEqual(
            "stale",
            public_room_data_status(present=True, fresh=False),
        )
        self.assertEqual(
            "unavailable",
            public_room_data_status(present=False, fresh=True),
        )

    def test_arbitrary_device_state_is_never_echoed(self) -> None:
        self.assertEqual("working", public_device_state("cool", available=True))
        self.assertEqual("idle", public_device_state("standby", available=True))
        self.assertEqual("off", public_device_state("off", available=True))
        self.assertEqual(
            "unavailable",
            public_device_state("vendor_private_state", available=False),
        )
        self.assertEqual(
            "unknown",
            public_device_state("vendor_private_state", available=True),
        )

    def test_unknown_strategy_is_not_forwarded(self) -> None:
        self.assertEqual("normal", public_strategy("normal"))
        self.assertEqual("unknown", public_strategy("vendor_private_strategy"))


if __name__ == "__main__":
    unittest.main()
