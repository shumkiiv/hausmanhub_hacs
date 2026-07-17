"""Pure contract tests for HASC's first opt-in control canary."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.hausman_hub.application.configuration import (  # noqa: E402
    CANARY_CONTROL_ENABLED_FIELD,
    CANARY_CONTROL_TARGET_FIELD,
    ConfigurationViolation,
    create_initial_entry,
    create_options,
    effective_configuration,
)
from custom_components.hausman_hub.application.control import (  # noqa: E402
    CanaryControlViolation,
    canary_control_command,
)
from custom_components.hausman_hub.application.diagnostics import (  # noqa: E402
    diagnostics_snapshot,
)
from custom_components.hausman_hub.domain.observation import HomeSummary  # noqa: E402


class CanaryControlContractTest(unittest.TestCase):
    """Keep the new execution path exact, reversible, and diagnostics-redacted."""

    def test_legacy_configuration_keeps_canary_disarmed(self) -> None:
        configuration = effective_configuration(create_initial_entry("read-only"), {})

        self.assertFalse(configuration.canary_control_enabled)
        self.assertIsNone(configuration.canary_control_target)

    def test_one_input_boolean_target_can_be_armed_and_commanded(self) -> None:
        options = create_options(
            "read-only",
            True,
            "5m",
            True,
            "input_boolean.hasc_canary",
        )
        configuration = effective_configuration(
            create_initial_entry("read-only"),
            options,
        )

        self.assertEqual(
            {
                "canary_control_enabled": True,
                "canary_control_target": "input_boolean.hasc_canary",
                "local_summary_enabled": True,
                "mode": "read-only",
                "summary_update_interval": "5m",
            },
            options,
        )
        command = canary_control_command(
            configuration,
            "input_boolean.hasc_canary",
            True,
        )
        self.assertEqual("input_boolean.hasc_canary", command.target_entity_id)
        self.assertTrue(command.turn_on)

    def test_disarming_drops_a_previously_visible_target(self) -> None:
        options = create_options(
            "shadow",
            False,
            "30m",
            False,
            "input_boolean.previous_canary",
        )

        self.assertFalse(options[CANARY_CONTROL_ENABLED_FIELD])
        self.assertNotIn(CANARY_CONTROL_TARGET_FIELD, options)

    def test_persisted_canary_shape_fails_closed(self) -> None:
        entry_data = create_initial_entry("read-only")
        for label, options in (
            (
                "other domain",
                {
                    CANARY_CONTROL_ENABLED_FIELD: True,
                    CANARY_CONTROL_TARGET_FIELD: "light.kitchen",
                },
            ),
            (
                "missing target",
                {CANARY_CONTROL_ENABLED_FIELD: True},
            ),
            (
                "disabled target retained",
                {
                    CANARY_CONTROL_ENABLED_FIELD: False,
                    CANARY_CONTROL_TARGET_FIELD: "input_boolean.hasc_canary",
                },
            ),
            (
                "multiple-looking target",
                {
                    CANARY_CONTROL_ENABLED_FIELD: True,
                    CANARY_CONTROL_TARGET_FIELD: "input_boolean.one,input_boolean.two",
                },
            ),
            (
                "truth-like arm value",
                {
                    CANARY_CONTROL_ENABLED_FIELD: "true",
                    CANARY_CONTROL_TARGET_FIELD: "input_boolean.hasc_canary",
                },
            ),
        ):
            with self.subTest(label=label):
                with self.assertRaises(ConfigurationViolation):
                    effective_configuration(entry_data, options)

    def test_stale_or_mismatched_command_is_rejected(self) -> None:
        disabled = effective_configuration(create_initial_entry("read-only"), {})
        armed = effective_configuration(
            create_initial_entry("read-only"),
            create_options(
                "read-only",
                True,
                "5m",
                True,
                "input_boolean.hasc_canary",
            ),
        )

        for configuration, target, action in (
            (disabled, "input_boolean.hasc_canary", True),
            (armed, "input_boolean.other", True),
            (armed, "input_boolean.hasc_canary", 1),
        ):
            with self.subTest(target=target, action=action):
                with self.assertRaises(CanaryControlViolation):
                    canary_control_command(configuration, target, action)

    def test_diagnostics_report_scope_but_never_target(self) -> None:
        target = "input_boolean.hasc_canary"
        snapshot = diagnostics_snapshot(
            create_initial_entry("shadow"),
            create_options("shadow", True, "15m", True, target),
            HomeSummary(0, 0, 0, 0, 0, 0, 0, 0, 0),
        )
        serialized = json.dumps(snapshot)

        self.assertTrue(snapshot["entry_summary"]["canary_control_enabled"])
        self.assertEqual(
            "single_input_boolean",
            snapshot["entry_summary"]["canary_control_scope"],
        )
        self.assertNotIn(target, serialized)
        self.assertNotIn(CANARY_CONTROL_TARGET_FIELD, serialized)


if __name__ == "__main__":
    unittest.main()
