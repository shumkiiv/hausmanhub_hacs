from __future__ import annotations

import asyncio
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
from custom_components.hausman_hub.application.repairs import manual_guidance_for  # noqa: E402
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

    def test_manual_repair_guidance_never_performs_a_repair(self) -> None:
        guidance = manual_guidance_for("redaction_failure")
        self.assertEqual("critical", guidance.severity)
        self.assertIn("вручную", guidance.message)
        with self.assertRaisesRegex(ValueError, "unknown manual repair category"):
            manual_guidance_for("automatic_fix")

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
