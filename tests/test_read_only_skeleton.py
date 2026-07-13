from __future__ import annotations

import asyncio
from dataclasses import fields
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.hausman_hub import async_setup_entry  # noqa: E402
from custom_components.hausman_hub.application.configuration import (  # noqa: E402
    ConfigurationViolation,
    DIRECT_EXECUTION_STATUS_FIELD,
    MODE_FIELD,
    create_initial_entry,
    create_options,
    effective_configuration,
)
from custom_components.hausman_hub.application.diagnostics import diagnostics_snapshot  # noqa: E402
from custom_components.hausman_hub.application.repairs import (  # noqa: E402
    MANUAL_REPAIR_CATEGORIES,
    manual_guidance_for,
)
from custom_components.hausman_hub.domain.configuration import (  # noqa: E402
    DIRECT_EXECUTION_BLOCKED,
)


INTEGRATION = ROOT / "custom_components" / "hausman_hub"


class FakeEntry:
    def __init__(self, data: dict[str, object], options: dict[str, object]) -> None:
        self.data = data
        self.options = options


class ReadOnlySkeletonTest(unittest.TestCase):
    def test_manifest_declares_one_private_config_entry_without_hacs_metadata(self) -> None:
        manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("hausman_hub", manifest["domain"])
        self.assertTrue(manifest["config_flow"])
        self.assertTrue(manifest["single_config_entry"])
        self.assertEqual("0.1.0", manifest["version"])
        self.assertFalse((ROOT / "hacs.json").exists())

    def test_initial_entry_only_contains_an_approved_mode_and_direct_block(self) -> None:
        data = create_initial_entry("read-only")
        self.assertEqual(
            {
                MODE_FIELD: "read-only",
                DIRECT_EXECUTION_STATUS_FIELD: DIRECT_EXECUTION_BLOCKED,
            },
            data,
        )

    def test_options_can_select_shadow_without_granting_execution(self) -> None:
        configuration = effective_configuration(
            create_initial_entry("read-only"),
            create_options("shadow"),
        )
        self.assertEqual("shadow", configuration.mode)
        self.assertEqual(DIRECT_EXECUTION_BLOCKED, configuration.direct_execution_status)

    def test_proxy_direct_and_unknown_data_are_rejected(self) -> None:
        for unsafe_mode in ("proxy", "direct", "", None):
            with self.subTest(mode=unsafe_mode):
                with self.assertRaises(ConfigurationViolation):
                    create_initial_entry(unsafe_mode)

        unsafe_entry = create_initial_entry("read-only")
        unsafe_entry[DIRECT_EXECUTION_STATUS_FIELD] = "allowed"
        with self.assertRaises(ConfigurationViolation):
            effective_configuration(unsafe_entry, {})

        with self.assertRaises(ConfigurationViolation):
            effective_configuration(create_initial_entry("read-only"), {"token": "blocked"})

    def test_persisted_configuration_rejects_representative_extra_top_level_fields(
        self,
    ) -> None:
        """Reject representative unmodelled top-level fields, regardless of value."""

        unexpected_entry_fields = {
            "service_path": "outside_contract",
            "entity_reference": "outside_contract",
            "command_payload": {"synthetic": "outside_contract"},
            "token": "outside_contract",
            "unmodelled": {"nested": "value"},
        }
        unexpected_option_fields = {
            "service_path": "outside_contract",
            "entity_reference": "outside_contract",
            "command_payload": {"synthetic": "outside_contract"},
            DIRECT_EXECUTION_STATUS_FIELD: DIRECT_EXECUTION_BLOCKED,
            "unmodelled": {"nested": "value"},
        }

        for field, value in unexpected_entry_fields.items():
            with self.subTest(container="entry_data", field=field):
                entry_data = create_initial_entry("read-only")
                entry_data[field] = value
                with self.assertRaises(ConfigurationViolation):
                    effective_configuration(entry_data, {})

        for field, value in unexpected_option_fields.items():
            with self.subTest(container="options", field=field):
                with self.assertRaises(ConfigurationViolation):
                    effective_configuration(
                        create_initial_entry("read-only"),
                        {field: value},
                    )

        with self.assertRaises(ConfigurationViolation):
            effective_configuration(
                create_initial_entry("read-only"),
                {
                    "entity_reference": "outside_contract",
                    DIRECT_EXECUTION_STATUS_FIELD: DIRECT_EXECUTION_BLOCKED,
                },
            )

    def test_setup_refuses_an_entry_outside_the_safe_contract(self) -> None:
        safe_entry = FakeEntry(create_initial_entry("shadow"), {})
        self.assertTrue(asyncio.run(async_setup_entry(None, safe_entry)))

        unsafe_entry = FakeEntry(
            {
                MODE_FIELD: "shadow",
                DIRECT_EXECUTION_STATUS_FIELD: "not_blocked",
            },
            {},
        )
        self.assertFalse(asyncio.run(async_setup_entry(None, unsafe_entry)))

    def test_diagnostics_are_allow_listed_and_do_not_copy_sensitive_data(self) -> None:
        data = create_initial_entry("shadow")
        snapshot = diagnostics_snapshot(data, {})
        serialized = json.dumps(snapshot, ensure_ascii=False).lower()

        self.assertEqual("shadow", snapshot["entry_summary"]["mode"])
        self.assertEqual("not_granted", snapshot["safety_model"]["device_authority"])
        self.assertEqual(DIRECT_EXECUTION_BLOCKED, snapshot["safety_model"]["direct_execution_status"])
        for forbidden_value in ("token", "entity_id", "device_id", "command", "payload"):
            self.assertNotIn(forbidden_value, serialized)

    def test_diagnostics_shape_is_a_fixed_allow_list(self) -> None:
        """Guard the redacted export against accidental future data expansion."""

        snapshot = diagnostics_snapshot(create_initial_entry("read-only"), {})

        self.assertEqual(
            {
                "entry_summary",
                "safety_model",
                "shadow_parity",
                "repairs_summary",
                "redaction_report",
            },
            set(snapshot),
        )
        self.assertEqual({"mode", "single_config_entry"}, set(snapshot["entry_summary"]))
        self.assertEqual(
            {"device_authority", "direct_execution_status", "proxy_status"},
            set(snapshot["safety_model"]),
        )
        self.assertEqual({"parity_status", "evidence_status"}, set(snapshot["shadow_parity"]))
        self.assertEqual(
            {"automatic_repairs", "manual_guidance_only"},
            set(snapshot["repairs_summary"]),
        )
        self.assertEqual({"status", "strategy"}, set(snapshot["redaction_report"]))

    def test_manual_repair_guidance_never_performs_a_repair(self) -> None:
        guidance = manual_guidance_for("redaction_failure")
        self.assertEqual("critical", guidance.severity)
        self.assertIn("вручную", guidance.message)
        with self.assertRaisesRegex(ValueError, "unknown manual repair category"):
            manual_guidance_for("automatic_fix")

    def test_manual_repair_guidance_has_only_the_approved_fixed_shape(self) -> None:
        """Keep every repair result as static human guidance, never an action."""

        expected_severities = {
            "missing_references": "warning",
            "unsafe_mode": "error",
            "unresolved_owner_contour": "error",
            "stale_parity": "warning",
            "redaction_failure": "critical",
        }
        self.assertEqual(set(expected_severities), MANUAL_REPAIR_CATEGORIES)
        for category, severity in expected_severities.items():
            with self.subTest(category=category):
                guidance = manual_guidance_for(category)
                self.assertEqual(category, guidance.category)
                self.assertEqual(severity, guidance.severity)
                self.assertIsInstance(guidance.message, str)
                self.assertTrue(guidance.message)
                self.assertEqual(
                    {"category", "severity", "message"},
                    {field.name for field in fields(guidance)},
                )

    def test_outer_adapter_contains_no_runtime_execution_surface(self) -> None:
        forbidden_fragments = (
            "hass.services",
            "async_call(",
            "async_create_issue",
            "services.yaml",
            "node-red",
        )
        source = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in INTEGRATION.rglob("*.py")
        )
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, source)
        for absent_module in ("services.yaml", "sensor.py", "switch.py", "climate.py"):
            self.assertFalse((INTEGRATION / absent_module).exists())

    def test_translations_are_present_for_the_only_selector(self) -> None:
        for language in ("en", "ru"):
            content = json.loads(
                (INTEGRATION / "translations" / f"{language}.json").read_text(encoding="utf-8")
            )
            self.assertIn("mode", content["selector"])
            self.assertIn("unsafe_mode", content["config"]["error"])


if __name__ == "__main__":
    unittest.main()
