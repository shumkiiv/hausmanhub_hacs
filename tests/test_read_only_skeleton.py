from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
import struct
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.hausman_hub.application.configuration import (  # noqa: E402
    ConfigurationViolation,
    DIRECT_EXECUTION_STATUS_FIELD,
    MODE_FIELD,
    create_initial_entry,
    create_options,
    effective_configuration,
)
from custom_components.hausman_hub.application.diagnostics import (  # noqa: E402
    diagnostics_snapshot,
    unavailable_diagnostics_snapshot,
)
from custom_components.hausman_hub.application.observation import create_home_summary  # noqa: E402
from custom_components.hausman_hub.application.local_summary import (  # noqa: E402
    HOME_SUMMARY_COUNT_KEYS,
    home_summary_payload,
    local_summary_snapshot,
)
from custom_components.hausman_hub.application.repairs import (  # noqa: E402
    MANUAL_REPAIR_CATEGORIES,
    manual_guidance_for,
)
from custom_components.hausman_hub.domain.configuration import (  # noqa: E402
    DIRECT_EXECUTION_BLOCKED,
)
from custom_components.hausman_hub.domain.observation import (  # noqa: E402
    HomeSummary,
    RegisteredEntity,
)


INTEGRATION = ROOT / "custom_components" / "hausman_hub"


class ReadOnlySkeletonTest(unittest.TestCase):
    def test_manifest_declares_one_config_entry(self) -> None:
        manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("hausman_hub", manifest["domain"])
        self.assertTrue(manifest["config_flow"])
        self.assertTrue(manifest["single_config_entry"])
        self.assertEqual("0.3.13", manifest["version"])

    def test_current_manifest_version_has_a_plain_change_note(self) -> None:
        manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
        change_history = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertIn(f"## {manifest['version']} —", change_history)

    def test_saved_settings_reload_only_this_hasc_entry(self) -> None:
        """A saved HASC setting must apply without restarting the whole home."""

        integration_source = (INTEGRATION / "__init__.py").read_text(encoding="utf-8")
        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "entry.async_on_unload(entry.add_update_listener(_async_reload_entry))",
            integration_source,
        )
        self.assertIn(
            "await hass.config_entries.async_reload(entry.entry_id)",
            integration_source,
        )
        self.assertLess(
            integration_source.index("register_local_summary_access(hass, entry)"),
            integration_source.index("entry.async_on_unload(entry.add_update_listener"),
        )
        self.assertIn("saving safe options must reload only HASC", core_check_source)

    def test_distribution_documents_mark_the_private_choice_as_history(self) -> None:
        """Keep current manual-HACS instructions separate from the old choice."""

        historical_decision = (ROOT / "docs" / "read-only-skeleton-decision.md").read_text(
            encoding="utf-8"
        )
        packaging_decision = (ROOT / "docs" / "hacs-packaging-decision.md").read_text(
            encoding="utf-8"
        )
        skeleton_guide = (ROOT / "docs" / "read-only-skeleton.md").read_text(encoding="utf-8")
        context = (ROOT / "AI_CONTEXT.md").read_text(encoding="utf-8")

        historical_heading = historical_decision.partition("\n")[0]
        self.assertIn("Historical", historical_heading)
        self.assertNotEqual("# Decision record: private read-only integration skeleton", historical_heading)
        self.assertIn("hacs-packaging-decision.md", historical_decision)

        for current_requirement in (
            "repository is public",
            "added manually in HACS",
            "nine approved diagnostic count sensors",
        ):
            self.assertIn(current_requirement, packaging_decision)

        for document in (skeleton_guide, context):
            self.assertIn("read-only-skeleton-decision.md", document)
            self.assertIn("hacs-packaging-decision.md", document)

        for outdated_instruction in (
            "The skeleton remains private",
            "does not add `hacs.json`",
            "private HACS installation",
        ):
            for document in (packaging_decision, skeleton_guide, context):
                self.assertNotIn(outdated_instruction, document)

    def test_local_access_guide_keeps_the_extra_page_optional(self) -> None:
        """The guide must not make ordinary nine-count viewing wait for a future step."""

        local_access_guide = (ROOT / "docs" / "read-only-local-access.md").read_text(
            encoding="utf-8"
        )
        plain_guide = " ".join(local_access_guide.split())

        self.assertIn("Если дополнительная страница не нужна", plain_guide)
        self.assertIn(
            "Обычные девять строк и диагностика всё равно останутся доступны.",
            plain_guide,
        )
        self.assertNotIn("следующий короткий шаг будет описан", plain_guide)

    def test_brand_icon_is_a_square_transparent_png(self) -> None:
        """Keep the local Home Assistant brand image present and usable."""

        icon = INTEGRATION / "brand" / "icon.png"
        icon_bytes = icon.read_bytes()

        self.assertEqual(b"\x89PNG\r\n\x1a\n", icon_bytes[:8])
        self.assertEqual(b"IHDR", icon_bytes[12:16])
        self.assertEqual((512, 512), struct.unpack(">II", icon_bytes[16:24]))
        self.assertEqual(6, icon_bytes[25], "icon must keep an alpha channel")

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

    def test_empty_options_keep_a_complete_main_setting_safe(self) -> None:
        """Options are optional only after main saved data is complete."""

        configuration = effective_configuration(create_initial_entry("read-only"), {})
        self.assertEqual("read-only", configuration.mode)
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

    def test_persisted_configuration_requires_both_main_fields(self) -> None:
        """A safe option must not fill in a missing saved main field."""

        partial_entries = (
            ({MODE_FIELD: "read-only"}, {}),
            (
                {DIRECT_EXECUTION_STATUS_FIELD: DIRECT_EXECUTION_BLOCKED},
                {MODE_FIELD: "shadow"},
            ),
        )
        for entry_data, options in partial_entries:
            with self.subTest(entry_data=entry_data, options=options):
                with self.assertRaises(ConfigurationViolation):
                    effective_configuration(entry_data, options)

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

    def test_diagnostics_are_allow_listed_and_do_not_copy_sensitive_data(self) -> None:
        data = create_initial_entry("shadow")
        snapshot = diagnostics_snapshot(data, {}, self.home_summary())
        serialized = json.dumps(snapshot, ensure_ascii=False).lower()

        self.assertEqual("shadow", snapshot["entry_summary"]["mode"])
        self.assertEqual("not_granted", snapshot["safety_model"]["device_authority"])
        self.assertEqual(DIRECT_EXECUTION_BLOCKED, snapshot["safety_model"]["direct_execution_status"])
        for forbidden_value in ("token", "entity_id", "device_id", "command", "payload"):
            self.assertNotIn(forbidden_value, serialized)

    def test_diagnostics_shape_is_a_fixed_allow_list(self) -> None:
        """Guard the redacted export against accidental future data expansion."""

        snapshot = diagnostics_snapshot(
            create_initial_entry("read-only"),
            {},
            self.home_summary(),
        )

        self.assertEqual(
            {
                "entry_summary",
                "safety_model",
                "shadow_parity",
                "repairs_summary",
                "home_summary",
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
        self.assertEqual(
            {
                "areas_count",
                "devices_count",
                "entities_count",
                "sensors_count",
                "available_entities_count",
                "unavailable_entities_count",
                "unknown_entities_count",
                "not_reported_entities_count",
                "disabled_entities_count",
            },
            set(snapshot["home_summary"]),
        )
        self.assertEqual({"status", "strategy"}, set(snapshot["redaction_report"]))

    def test_unavailable_diagnostics_never_include_home_data(self) -> None:
        """An inactive HASC setup must return only one static status."""

        self.assertEqual(
            {"diagnostics_status": "unavailable"},
            unavailable_diagnostics_snapshot(),
        )

    def test_diagnostics_adapter_checks_activity_before_reading_the_home(self) -> None:
        """The outer adapter must close before it can collect any home summary."""

        diagnostics_source = (INTEGRATION / "diagnostics.py").read_text(encoding="utf-8")

        self.assertIn("_single_loaded_entry", diagnostics_source)
        self.assertIn("unavailable_diagnostics_snapshot", diagnostics_source)
        self.assertLess(
            diagnostics_source.index("if active_entry is None"),
            diagnostics_source.index("collect_home_summary("),
        )

    def test_local_summary_checks_configuration_before_requesting_the_home(self) -> None:
        """An unsafe saved entry must prevent even the aggregate read."""

        application_source = (INTEGRATION / "application" / "local_summary.py").read_text(
            encoding="utf-8"
        )
        local_adapter_source = (INTEGRATION / "local_summary.py").read_text(
            encoding="utf-8"
        )
        snapshot_source = application_source.split("def local_summary_snapshot", 1)[1].split(
            "def home_summary_payload", 1
        )[0]

        self.assertIn("home_summary_supplier", snapshot_source)
        self.assertLess(
            snapshot_source.index("effective_configuration("),
            snapshot_source.index("home_summary_supplier()"),
        )
        self.assertIn("lambda: collect_home_summary", local_adapter_source)

    def test_local_summary_requires_a_currently_loaded_hasc_entry(self) -> None:
        """A stale local pointer must close before the adapter reads the home."""

        local_adapter_source = (INTEGRATION / "local_summary.py").read_text(
            encoding="utf-8"
        )
        active_entry_source = local_adapter_source.split("def _active_entry", 1)[1].split(
            "def _is_local_read_only_request", 1
        )[0]

        self.assertIn("async_loaded_entries(DOMAIN)", active_entry_source)
        self.assertIn("return configured_entry", active_entry_source)
        self.assertLess(
            active_entry_source.index("async_loaded_entries(DOMAIN)"),
            active_entry_source.index("return configured_entry"),
        )

    def test_home_summary_contains_totals_but_no_names_or_identifiers(self) -> None:
        """The application layer must receive and export counts only."""

        summary = create_home_summary(
            areas_count=2,
            devices_count=3,
            entities=(
                RegisteredEntity(domain="sensor", availability="available"),
                RegisteredEntity(domain="switch", availability="unavailable"),
                RegisteredEntity(domain="sensor", availability="unknown"),
                RegisteredEntity(domain="light", availability="not_reported"),
                RegisteredEntity(domain="switch", availability="disabled"),
            ),
        )
        snapshot = diagnostics_snapshot(create_initial_entry("read-only"), {}, summary)
        serialized = json.dumps(snapshot, ensure_ascii=False)

        self.assertEqual(2, snapshot["home_summary"]["areas_count"])
        self.assertEqual(3, snapshot["home_summary"]["devices_count"])
        self.assertEqual(5, snapshot["home_summary"]["entities_count"])
        self.assertEqual(2, snapshot["home_summary"]["sensors_count"])
        self.assertEqual(1, snapshot["home_summary"]["available_entities_count"])
        self.assertEqual(1, snapshot["home_summary"]["unavailable_entities_count"])
        self.assertEqual(1, snapshot["home_summary"]["unknown_entities_count"])
        self.assertEqual(1, snapshot["home_summary"]["not_reported_entities_count"])
        self.assertEqual(1, snapshot["home_summary"]["disabled_entities_count"])
        for forbidden_value in ("living_room", "sensor.temperature", "192.168.1.20", "21.5"):
            self.assertNotIn(forbidden_value, serialized)

    def test_local_summary_reuses_the_same_nine_count_boundary(self) -> None:
        """The local view use case must not expand the diagnostics shape."""

        summary = self.home_summary()
        payload = local_summary_snapshot(
            create_initial_entry("read-only"),
            {},
            lambda: summary,
        )

        self.assertEqual(
            {
                "areas_count",
                "devices_count",
                "entities_count",
                "sensors_count",
                "available_entities_count",
                "unavailable_entities_count",
                "unknown_entities_count",
                "not_reported_entities_count",
                "disabled_entities_count",
            },
            set(payload),
        )
        home_read_attempted = False

        def fail_if_home_is_read() -> object:
            nonlocal home_read_attempted
            home_read_attempted = True
            raise AssertionError("an unsafe local summary must not read the home")

        with self.assertRaises(ConfigurationViolation):
            local_summary_snapshot(
                {
                    MODE_FIELD: "read-only",
                    DIRECT_EXECUTION_STATUS_FIELD: "not_blocked",
                },
                {},
                fail_if_home_is_read,
            )
        self.assertFalse(home_read_attempted)

    def test_home_summary_display_has_exactly_the_same_nine_numbers(self) -> None:
        """The visible display cannot add data beyond the approved summary."""

        expected_keys = (
            "areas_count",
            "devices_count",
            "entities_count",
            "sensors_count",
            "available_entities_count",
            "unavailable_entities_count",
            "unknown_entities_count",
            "not_reported_entities_count",
            "disabled_entities_count",
        )
        payload = home_summary_payload(self.home_summary())

        self.assertEqual(expected_keys, HOME_SUMMARY_COUNT_KEYS)
        self.assertEqual(expected_keys, tuple(payload))
        self.assertEqual({key: 0 for key in expected_keys}, payload)

    def test_new_summary_sensors_use_the_hasc_name_prefix(self) -> None:
        """New installations must not receive generic sensor names."""

        sensor_source = (INTEGRATION / "sensor.py").read_text(encoding="utf-8")

        self.assertIn(
            'SENSOR_ENTITY_ID_PREFIX: Final = f"sensor.{DOMAIN}_hasc"',
            sensor_source,
        )
        self.assertIn(
            'self.entity_id = f"{SENSOR_ENTITY_ID_PREFIX}_{summary_key}"',
            sensor_source,
        )

    def test_core_smoke_check_requires_no_hasc_devices(self) -> None:
        """Keep the real-Core check aligned with the no-device promise."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("device_registry.async_entries_for_config_entry", core_check_source)
        self.assertIn("the integration must not create devices", core_check_source)
        self.assertIn("entry.device_id", core_check_source)

    def test_core_smoke_check_rejects_a_second_hasc_setup(self) -> None:
        """Keep the one-setup promise covered by the real-Core check."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("async_assert_second_entry_is_rejected", core_check_source)
        self.assertIn('"single_instance_allowed"', core_check_source)
        self.assertIn("the integration must retain exactly one setup", core_check_source)

    def test_core_smoke_check_keeps_an_external_collision_entry_after_removal(self) -> None:
        """HASC cleanup must leave an unrelated similar name untouched."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("ReservedCollisionEntry", core_check_source)
        self.assertIn("assert_reserved_collision_entry_is_unchanged", core_check_source)
        self.assertIn("HASC removal must keep the external collision fixture", core_check_source)
        self.assertIn("HASC removal must not change the external collision fixture", core_check_source)

    def test_core_smoke_check_can_reinstall_after_collision_cleanup(self) -> None:
        """A clean removal must not block the next safe HASC setup."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("reinstalled_entry = await async_create_safe_entry(", core_check_source)
        self.assertIn('"read-only",', core_check_source)
        self.assertIn("reinstalled_entry.entry_id", core_check_source)
        self.assertIn(
            "disabled_reinstall_entry_id = reinstalled_entry.entry_id",
            core_check_source,
        )
        self.assertIn(
            "disabled_reinstall_entry.entry_id",
            core_check_source,
        )

    def test_core_smoke_check_closes_the_local_summary_after_removal(self) -> None:
        """The retained route must not serve counts without an active HASC entry."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "async_assert_local_summary_is_unavailable",
            core_check_source,
        )
        self.assertIn(
            '"HASC removal",',
            core_check_source,
        )
        self.assertIn(
            "local summary must become unavailable after {unavailable_after}",
            core_check_source,
        )
        self.assertGreaterEqual(
            core_check_source.count(
                "await async_assert_local_summary_is_unavailable("
            ),
            4,
        )

    def test_core_smoke_check_deactivates_hasc_without_leaving_counts_available(
        self,
    ) -> None:
        """The normal deactivation control must close and then safely restore HASC."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        self.assertIn("async_disable_safe_entry", core_check_source)
        self.assertIn("ConfigEntryDisabler.USER", core_check_source)
        self.assertIn("async_enable_safe_entry", core_check_source)
        self.assertIn("assert_entry_has_disabled_summary_sensors", core_check_source)
        self.assertIn('"HASC deactivation",', lifecycle_source)
        self.assertIn(
            "a deactivated HASC summary sensor must be disabled by its setup",
            core_check_source,
        )
        self.assertIn(
            "a deactivated HASC summary sensor must not keep a state",
            core_check_source,
        )
        self.assertLess(
            lifecycle_source.index("await async_disable_safe_entry(hass, read_only_entry)"),
            lifecycle_source.index("await async_enable_safe_entry(hass, read_only_entry)"),
        )

    def test_core_smoke_check_unloads_and_starts_hasc_without_user_deactivation(
        self,
    ) -> None:
        """Ordinary unload must close values before the same entry starts again."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        for requirement in (
            "async_unload_safe_entry",
            "async_setup_safe_entry",
            "assert_entry_has_unloaded_summary_sensors",
            "hass.config_entries.async_unload(entry.entry_id)",
            "hass.config_entries.async_setup(entry.entry_id)",
            "ordinary unload must not user-deactivate HASC",
            "an unloaded HASC summary sensor must remain enabled",
            "an unloaded HASC summary sensor must not keep a state",
            '"HASC ordinary unload",',
            '"HASC ordinary setup",',
        ):
            self.assertIn(requirement, core_check_source)

        unload_call = "await async_unload_safe_entry(hass, read_only_entry)"
        setup_call = "await async_setup_safe_entry(hass, read_only_entry)"
        disable_call = "await async_disable_safe_entry(hass, read_only_entry)"
        self.assertLess(lifecycle_source.index(unload_call), lifecycle_source.index(setup_call))
        self.assertLess(
            lifecycle_source.index("assert_entry_has_unloaded_summary_sensors("),
            lifecycle_source.index(setup_call),
        )
        self.assertLess(lifecycle_source.index(setup_call), lifecycle_source.index(disable_call))

    def test_core_smoke_check_recovers_ordinary_unloaded_hasc_after_restart(
        self,
    ) -> None:
        """An enabled HASC setup must return by itself after an ordinary stop."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        for requirement in (
            "async_assert_ordinary_unloaded_entry_recovers_after_restart",
            "ordinary unload restart must keep HASC user-enabled",
            "ordinary unload restart must auto-load HASC",
            "ordinary unload restart must preserve safe entry data",
            "ordinary unload restart must preserve safe entry options",
            '"HASC ordinary unload before restart",',
            '"HASC ordinary-unload restart temporary",',
        ):
            self.assertIn(requirement, core_check_source)

        unload_call = "await async_unload_safe_entry(restarted_hass, restored_entry)"
        stop_call = "await restarted_hass.async_stop()"
        restart_call = (
            "ordinary_unload_restarted_hass = await async_start_empty_home_assistant("
        )
        recovery_call = "await async_assert_ordinary_unloaded_entry_recovers_after_restart("
        self.assertLess(lifecycle_source.index(unload_call), lifecycle_source.index(stop_call))
        self.assertLess(lifecycle_source.index(stop_call), lifecycle_source.index(restart_call))
        self.assertLess(lifecycle_source.index(restart_call), lifecycle_source.index(recovery_call))

    def test_core_smoke_check_removes_ordinarily_stopped_hasc(self) -> None:
        """An ordinary stopped, still-enabled HASC setup must remove cleanly."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]
        helper_source = core_check_source.split(
            "async def async_assert_ordinary_unloaded_entry_can_be_removed(", 1
        )[1].split("\n\ndef assert_hasc_stays_removed_after_restart", 1)[0]

        for requirement in (
            "ordinary stopped, still-enabled HASC entry to remove cleanly",
            "ordinary unload before removal must preserve safe entry data",
            "ordinary unload before removal must preserve safe entry options",
            '"HASC ordinary unload before removal",',
            '"HASC removal after ordinary unload",',
        ):
            self.assertIn(requirement, helper_source)

        unload_call = "await async_unload_safe_entry(hass, entry)"
        removal_call = "await async_remove_safe_entry(hass, entry.entry_id)"
        self.assertLess(helper_source.index(unload_call), helper_source.index(removal_call))
        self.assertIn(
            "await async_assert_ordinary_unloaded_entry_can_be_removed(",
            lifecycle_source,
        )
        recovery_call = "await async_assert_ordinary_unloaded_entry_recovers_after_restart("
        stopped_removal_call = "await async_assert_ordinary_unloaded_entry_can_be_removed("
        self.assertLess(
            lifecycle_source.index(recovery_call),
            lifecycle_source.index(stopped_removal_call),
        )
        reservation_call = "reserved_entry = reserve_summary_sensor_name_for_test("
        preservation_call = "assert_reserved_collision_entry_is_unchanged("
        reservation_start = lifecycle_source.index(reservation_call)
        stopped_removal_start = lifecycle_source.index(stopped_removal_call)
        self.assertLess(reservation_start, stopped_removal_start)
        self.assertLess(
            stopped_removal_start,
            lifecycle_source.index(preservation_call, stopped_removal_start),
        )

    def test_core_smoke_check_deactivates_ordinarily_stopped_hasc(self) -> None:
        """An ordinary stopped HASC setup must still deactivate cleanly."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        for requirement in (
            "ordinary unload before deactivation must preserve safe entry data",
            "ordinary unload before deactivation must preserve safe entry options",
            '"HASC ordinary unload before deactivation",',
            '"HASC deactivation after ordinary unload",',
        ):
            self.assertIn(requirement, lifecycle_source)

        reinstall_call = "reinstalled_entry = await async_create_safe_entry("
        unload_call = "await async_unload_safe_entry("
        deactivation_call = "await async_disable_safe_entry("
        reinstall_start = lifecycle_source.index(reinstall_call)
        unload_start = lifecycle_source.index(unload_call, reinstall_start)
        deactivation_start = lifecycle_source.index(deactivation_call, reinstall_start)
        self.assertLess(reinstall_start, unload_start)
        self.assertLess(unload_start, deactivation_start)
        self.assertLess(
            lifecycle_source.index("assert_entry_has_unloaded_summary_sensors(", unload_start),
            deactivation_start,
        )
        self.assertLess(
            deactivation_start,
            lifecycle_source.index("assert_entry_has_disabled_summary_sensors(", deactivation_start),
        )

    def test_core_smoke_check_reactivates_ordinarily_stopped_hasc(self) -> None:
        """An ordinarily stopped HASC setup must reactivate before restart."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        for requirement in (
            "ordinary-unload reactivation must preserve safe entry data",
            "ordinary-unload reactivation must preserve safe entry options",
            '"HASC reactivation after ordinary unload",',
            '"HASC second deactivation after ordinary unload",',
        ):
            self.assertIn(requirement, lifecycle_source)

        reinstall_call = "reinstalled_entry = await async_create_safe_entry("
        unload_call = "await async_unload_safe_entry("
        deactivation_call = "await async_disable_safe_entry("
        reactivation_call = "await async_enable_safe_entry("
        reinstall_start = lifecycle_source.index(reinstall_call)
        unload_start = lifecycle_source.index(unload_call, reinstall_start)
        first_deactivation_start = lifecycle_source.index(deactivation_call, reinstall_start)
        reactivation_start = lifecycle_source.index(
            reactivation_call,
            first_deactivation_start,
        )
        second_deactivation_start = lifecycle_source.index(
            deactivation_call,
            reactivation_start,
        )
        active_sensor_check_start = lifecycle_source.index(
            "assert_entry_has_only_summary_sensors(",
            reactivation_start,
        )
        safe_diagnostics_start = lifecycle_source.index(
            "await async_assert_safe_diagnostics(",
            reactivation_start,
        )
        local_page_start = lifecycle_source.index(
            "assert_local_summary_view(ordinary_unload_restarted_hass, domain)",
            reactivation_start,
        )

        self.assertLess(reinstall_start, unload_start)
        self.assertLess(unload_start, first_deactivation_start)
        self.assertLess(
            lifecycle_source.index("assert_entry_has_unloaded_summary_sensors(", unload_start),
            first_deactivation_start,
        )
        self.assertLess(first_deactivation_start, reactivation_start)
        self.assertLess(reactivation_start, active_sensor_check_start)
        self.assertLess(active_sensor_check_start, safe_diagnostics_start)
        self.assertLess(safe_diagnostics_start, local_page_start)
        self.assertLess(local_page_start, second_deactivation_start)

    def test_core_smoke_check_rejects_a_second_setup_while_hasc_is_stopped(
        self,
    ) -> None:
        """An ordinary stop must not permit a second saved HASC setup."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        for requirement in (
            "expected_entry_state: config_entries.ConfigEntryState",
            "expected_disabled_by: ConfigEntryDisabler | None",
            "a rejected second setup must preserve HASC deactivation state",
            "a rejected second setup must keep the existing HASC state",
            "ConfigEntryState.NOT_LOADED",
        ):
            self.assertIn(requirement, core_check_source)

        unload_call = "await async_unload_safe_entry(restarted_hass, restored_entry)"
        rejection_call = "await async_assert_second_entry_is_rejected("
        stopped_state_marker = "expected_entry_state=config_entries.ConfigEntryState.NOT_LOADED"
        rejection_start = lifecycle_source.index(
            rejection_call,
            lifecycle_source.index(unload_call),
        )
        unavailable_marker = '"HASC ordinary unload before restart",'
        self.assertLess(lifecycle_source.index(unload_call), rejection_start)
        self.assertLess(
            rejection_start,
            lifecycle_source.index(stopped_state_marker, rejection_start),
        )
        self.assertLess(
            lifecycle_source.index(stopped_state_marker, rejection_start),
            lifecycle_source.index(unavailable_marker),
        )

    def test_core_smoke_check_rejects_second_setup_while_hasc_is_disabled_after_restart(
        self,
    ) -> None:
        """A disabled saved HASC setup must still prevent a second setup."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        for requirement in (
            "expected_disabled_by: ConfigEntryDisabler | None",
            "a rejected second setup must preserve HASC deactivation state",
        ):
            self.assertIn(requirement, core_check_source)

        restart_call = "restarted_hass = await async_start_empty_home_assistant"
        inactive_call = "assert_deactivated_entry_stays_inactive_after_restart("
        rejection_call = "await async_assert_second_entry_is_rejected("
        disabled_marker = "ConfigEntryDisabler.USER"
        enable_call = "await async_enable_safe_entry(restarted_hass, restored_entry)"

        restart_position = lifecycle_source.index(restart_call)
        first_inactive_position = lifecycle_source.index(inactive_call, restart_position)
        rejection_position = lifecycle_source.index(
            rejection_call,
            first_inactive_position,
        )
        disabled_marker_position = lifecycle_source.index(
            disabled_marker,
            rejection_position,
        )
        second_inactive_position = lifecycle_source.index(
            inactive_call,
            rejection_position,
        )
        enable_position = lifecycle_source.index(enable_call, second_inactive_position)

        self.assertLess(restart_position, first_inactive_position)
        self.assertLess(first_inactive_position, rejection_position)
        self.assertLess(rejection_position, disabled_marker_position)
        self.assertLess(disabled_marker_position, second_inactive_position)
        self.assertLess(second_inactive_position, enable_position)

    def test_core_smoke_check_removes_a_disabled_hasc_setup_after_restart(self) -> None:
        """A saved disabled HASC setup must be removable after a restart."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        for requirement in (
            "disabled_reinstall_entry_id",
            "disabled_reinstall_entity_ids",
            "disabled_removal_hass = await async_start_empty_home_assistant",
            "disabled HASC setup must persist until its removal",
        ):
            self.assertIn(requirement, lifecycle_source)

        reinstallation_start = lifecycle_source.index(
            "reinstalled_entry = await async_create_safe_entry("
        )
        deactivation_start = lifecycle_source.index(
            "await async_disable_safe_entry(",
            reinstallation_start,
        )
        disabled_restart_start = lifecycle_source.index(
            "disabled_removal_hass = await async_start_empty_home_assistant",
            deactivation_start,
        )
        persisted_entry_start = lifecycle_source.index(
            "disabled_reinstall_entry = disabled_removal_hass.config_entries.async_get_entry(",
            disabled_restart_start,
        )
        inactive_start = lifecycle_source.index(
            "assert_deactivated_entry_stays_inactive_after_restart(",
            persisted_entry_start,
        )
        removal_start = lifecycle_source.index(
            "await async_remove_safe_entry(",
            inactive_start,
        )
        following_restart_start = lifecycle_source.index(
            "post_removal_hass = await async_start_empty_home_assistant",
            removal_start,
        )

        self.assertLess(reinstallation_start, deactivation_start)
        self.assertLess(deactivation_start, disabled_restart_start)
        self.assertLess(disabled_restart_start, persisted_entry_start)
        self.assertLess(persisted_entry_start, inactive_start)
        self.assertLess(inactive_start, removal_start)
        self.assertLess(removal_start, following_restart_start)
        self.assertIn(
            "removed_entries.append(",
            lifecycle_source[inactive_start:following_restart_start],
        )

    def test_core_smoke_check_keeps_user_deactivation_after_restart(self) -> None:
        """A restart or safe update must not silently reactivate HASC."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        self.assertIn(
            "assert_deactivated_entry_stays_inactive_after_restart",
            core_check_source,
        )
        self.assertIn(
            "a deactivated HASC must not restore its local page after restart",
            core_check_source,
        )
        self.assertIn(
            "a deactivated HASC must not restore state values after restart",
            core_check_source,
        )
        self.assertIn('"HASC deactivation before restart",', lifecycle_source)
        self.assertIn('"HASC disabled-restart temporary",', lifecycle_source)
        self.assertLess(
            lifecycle_source.rindex("await async_disable_safe_entry(hass, read_only_entry)"),
            lifecycle_source.index("restarted_hass = await async_start_empty_home_assistant"),
        )
        self.assertLess(
            lifecycle_source.index("assert_deactivated_entry_stays_inactive_after_restart("),
            lifecycle_source.index("await async_enable_safe_entry(restarted_hass, restored_entry)"),
        )

    def test_core_smoke_check_closes_multiple_saved_hasc_entries(self) -> None:
        """A corrupted saved pair must show nothing until one entry is removed."""

        integration_source = (INTEGRATION / "__init__.py").read_text(encoding="utf-8")
        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        self.assertIn("async_entries(entry.domain)", integration_source)
        self.assertLess(
            integration_source.index("async_entries(entry.domain)"),
            integration_source.index("async_forward_entry_setups"),
        )
        self.assertIn("_close_running_duplicate_hasc_entries", integration_source)
        self.assertIn("async_loaded_entries(domain)", integration_source)
        self.assertIn("_clear_restored_hasc_records", integration_source)
        self.assertIn("async_add_disposable_persisted_duplicate_entry", core_check_source)
        self.assertIn("assert_persisted_duplicate_entries_stay_closed", core_check_source)
        self.assertIn("async_assert_persisted_duplicate_entry_lifecycle", core_check_source)
        self.assertIn(
            "a duplicate saved HASC entry must not load",
            core_check_source,
        )
        self.assertIn(
            "duplicate saved HASC entries must not restore count records",
            core_check_source,
        )
        self.assertIn(
            "removing the duplicate must retain only the original HASC entry",
            core_check_source,
        )
        self.assertEqual(
            2,
            lifecycle_source.count("async_assert_persisted_duplicate_entry_lifecycle("),
        )
        self.assertIn("first_entry_is_user_disabled=True", lifecycle_source)
        self.assertIn("first_entry_is_user_disabled=False", lifecycle_source)
        self.assertIn(
            "removing a duplicate must not automatically load the remaining HASC",
            core_check_source,
        )
        self.assertIn(
            "the remaining enabled HASC entry must reload after duplicate removal",
            core_check_source,
        )
        self.assertIn("expect_retained_local_summary_route=True", core_check_source)
        self.assertIn("adding a duplicate saved HASC entry", core_check_source)

    def test_core_smoke_check_keeps_one_local_summary_route(self) -> None:
        """The temporary lifecycle must never accumulate duplicate local pages."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("def find_local_summary_routes", core_check_source)
        self.assertIn(
            "local summary must register exactly one GET route",
            core_check_source,
        )
        self.assertIn(
            "the retained local summary route must remain unique",
            core_check_source,
        )
        self.assertIn(
            "if find_local_summary_routes(hass):",
            core_check_source,
        )

    def test_core_smoke_check_closes_invalid_saved_configuration(self) -> None:
        """Every unsafe saved main setting must close through a temporary restart."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        self.assertIn("assert_persisted_unsafe_entry_stays_closed", core_check_source)
        self.assertIn(
            "an invalid saved HASC entry must not load after restart",
            core_check_source,
        )
        self.assertIn(
            "an invalid saved HASC entry must not restore count states",
            core_check_source,
        )
        self.assertIn(
            "an invalid saved HASC entry must not restore entity registry records",
            core_check_source,
        )
        self.assertIn("async_assert_unsafe_saved_update_closes_hasc", core_check_source)
        self.assertIn(
            "async_save_unsafe_hasc_setting_without_reading_home",
            core_check_source,
        )
        self.assertIn("must close HASC automatically", core_check_source)
        self.assertIn("must clear entity registry records automatically", core_check_source)
        self.assertIn("must clear count states automatically", core_check_source)
        self.assertIn("async_block_home_summary_reads", core_check_source)
        self.assertIn('f"{scenario_name} automatic closure",', core_check_source)
        self.assertIn("async_assert_invalid_saved_data_lifecycle", core_check_source)
        self.assertIn("UNSAFE_PROXY_DATA", core_check_source)
        self.assertIn("UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA", core_check_source)
        self.assertIn("UNSAFE_MISSING_DIRECT_EXECUTION_DATA", core_check_source)
        self.assertIn("UNSAFE_MISSING_MODE_DATA", core_check_source)
        self.assertIn("UNSAFE_EXTRA_FIELD_DATA", core_check_source)
        self.assertIn("safe_options_mode: str | None = None", core_check_source)
        self.assertIn('"direct_execution_status": "allowed",', core_check_source)
        self.assertEqual(
            5,
            lifecycle_source.count("async_assert_invalid_saved_data_lifecycle("),
        )
        self.assertIn('"invalid-mode data",', lifecycle_source)
        self.assertIn('"unblocked-execution data",', lifecycle_source)
        self.assertIn('"missing-execution-block data",', lifecycle_source)
        self.assertIn('"missing-mode data",', lifecycle_source)
        self.assertIn('safe_options_mode="shadow",', lifecycle_source)
        self.assertIn('"extra-field data",', lifecycle_source)
        self.assertIn(
            'f"{scenario_name} saved main settings",',
            core_check_source,
        )
        self.assertIn(
            'f"{scenario_name} saved options",',
            core_check_source,
        )
        self.assertIn(
            "async_assert_broken_options_form_defaults_to_read_only",
            core_check_source,
        )
        self.assertIn(
            "options form must default to read-only",
            core_check_source,
        )
        self.assertIn(
            "options form must still open for manual repair",
            core_check_source,
        )
        self.assertIn(
            "opening {scenario_name} options must not repair saved data",
            core_check_source,
        )
        self.assertIn(
            "opening {scenario_name} options must not repair saved options",
            core_check_source,
        )
        self.assertIn(
            "await async_assert_closed_diagnostics(hass, domain, entry, scenario_name)",
            core_check_source,
        )
        self.assertIn("await async_assert_local_summary_is_unavailable(", core_check_source)
        self.assertIn("corrected HASC data removal", core_check_source)
        self.assertIn("(*previous_removed_entries, removed_entry)", core_check_source)
        self.assertLess(
            lifecycle_source.index("UNSAFE_PROXY_DATA"),
            lifecycle_source.index("UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA"),
        )
        self.assertLess(
            lifecycle_source.index("UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA"),
            lifecycle_source.index("UNSAFE_MISSING_DIRECT_EXECUTION_DATA"),
        )
        self.assertLess(
            lifecycle_source.index("UNSAFE_MISSING_DIRECT_EXECUTION_DATA"),
            lifecycle_source.index("UNSAFE_MISSING_MODE_DATA"),
        )
        self.assertLess(
            lifecycle_source.index("UNSAFE_MISSING_MODE_DATA"),
            lifecycle_source.index("UNSAFE_EXTRA_FIELD_DATA"),
        )
        self.assertLess(
            core_check_source.index("invalid_data_hass = await async_start_empty_home_assistant"),
            core_check_source.index("recovered_data_hass = await async_start_empty_home_assistant"),
        )

    def test_core_smoke_check_rejects_unsafe_manual_activation(self) -> None:
        """A disabled HASC must not start from either kind of unsafe saved setting."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        self.assertIn(
            "async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle",
            core_check_source,
        )
        self.assertIn("async_enable_unsafe_entry_without_reading_home", core_check_source)
        self.assertIn("async_block_home_summary_reads", core_check_source)
        self.assertIn("must reject unsafe activation", core_check_source)
        self.assertIn("must attempt exactly one HASC reload", core_check_source)
        self.assertIn("must leave unsafe HASC closed with a setup error", core_check_source)
        self.assertIn("must not register services", core_check_source)
        self.assertIn("unsafe_data: dict[str, str] | None = None", core_check_source)
        self.assertIn("unsafe_options: dict[str, str] | None = None", core_check_source)
        self.assertIn("restart_before_activation: bool = False", core_check_source)
        self.assertIn(
            "repair_after_rejected_activation: bool = False",
            core_check_source,
        )
        self.assertIn("reclose_after_recovery: bool = False", core_check_source)
        self.assertIn(
            "repair_after_repeat_closure: bool = False",
            core_check_source,
        )
        self.assertIn(
            "restart_after_repeat_repair: bool = False",
            core_check_source,
        )
        self.assertIn(
            "reclose_after_repeat_repair_restart: bool = False",
            core_check_source,
        )
        self.assertIn(
            "expect_retained_local_summary_route: bool = True",
            core_check_source,
        )
        self.assertIn(
            "assert_deactivated_entry_stays_inactive_after_restart",
            core_check_source,
        )
        self.assertIn("must not restore HASC runtime data", core_check_source)
        self.assertIn("must not restore the local summary route", core_check_source)
        self.assertIn(
            "async_repair_unsafe_entry_after_rejected_activation",
            core_check_source,
        )
        self.assertIn(
            "must reload HASC exactly once after manual repair",
            core_check_source,
        )
        self.assertIn(
            "must restore the direct execution block",
            core_check_source,
        )
        self.assertIn(
            "repeat closure requires a completed safe recovery",
            core_check_source,
        )
        self.assertIn(
            "repeat repair requires a completed repeat closure",
            core_check_source,
        )
        self.assertIn(
            "repeat repair restart requires a completed repeat repair",
            core_check_source,
        )
        self.assertIn(
            "restart repeat closure requires a completed repeat repair restart",
            core_check_source,
        )
        self.assertIn(
            "repeated unsafe data must remain available for manual repair",
            core_check_source,
        )
        self.assertEqual(
            10,
            lifecycle_source.count(
                "async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle("
            ),
        )
        self.assertIn("unsafe_options=UNSAFE_PROXY_OPTIONS", lifecycle_source)
        self.assertIn('scenario_name="unsafe proxy option repair"', lifecycle_source)
        self.assertIn('scenario_name="unsafe proxy data repair"', lifecycle_source)
        self.assertIn(
            'scenario_name="unsafe extra-field option repair"',
            lifecycle_source,
        )
        self.assertIn(
            'scenario_name="unsafe missing execution-block repair"',
            lifecycle_source,
        )
        self.assertIn(
            'scenario_name="unsafe missing mode repair"',
            lifecycle_source,
        )
        self.assertIn(
            'scenario_name="unsafe extra-field data repair"',
            lifecycle_source,
        )
        self.assertIn("unsafe_data=UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA", lifecycle_source)
        self.assertIn('scenario_name="unsafe direct-execution block"', lifecycle_source)
        self.assertIn(
            'scenario_name="unsafe direct-execution block after restart"',
            lifecycle_source,
        )
        self.assertIn("restart_before_activation=True", lifecycle_source)
        self.assertIn(
            'scenario_name="unsafe direct-execution repair after restart"',
            lifecycle_source,
        )
        self.assertIn("repair_after_rejected_activation=True", lifecycle_source)
        self.assertIn("reclose_after_recovery=True", lifecycle_source)
        self.assertIn("repair_after_repeat_closure=True", lifecycle_source)
        self.assertIn("restart_after_repeat_repair=True", lifecycle_source)
        self.assertIn("reclose_after_repeat_repair_restart=True", lifecycle_source)

    def test_core_smoke_check_recovers_corrected_saved_configuration(self) -> None:
        """A repaired temporary entry must recover only the approved surface."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        self.assertIn("recovered_entry_data", core_check_source)
        self.assertIn("invalid_entry_entity_ids", core_check_source)
        self.assertIn("recovered_entry_options", core_check_source)
        self.assertIn("async_assert_corrected_entry_stays_safe_after_restart", core_check_source)
        self.assertIn(
            "a manually corrected HASC data entry must reload successfully",
            core_check_source,
        )
        self.assertIn(
            "manual data correction must restore approved entry data",
            core_check_source,
        )
        self.assertIn("HASC corrected {scenario_name} temporary", core_check_source)
        self.assertIn("HASC corrected-settings restart temporary", core_check_source)
        self.assertIn(
            "restart must preserve the manually corrected safe entry data",
            core_check_source,
        )
        self.assertIn("corrected HASC data removal", core_check_source)
        self.assertLess(
            core_check_source.index("assert_persisted_unsafe_entry_stays_closed("),
            core_check_source.index("reloaded_recovered_entry ="),
        )
        self.assertLess(
            core_check_source.index("reloaded_recovered_entry ="),
            core_check_source.index("recovered_data_restart_hass = await async_start_empty_home_assistant"),
        )
        self.assertLess(
            core_check_source.index("recovered_data_restart_hass = await async_start_empty_home_assistant"),
            core_check_source.index("recovered_data_removal_hass ="),
        )

    def test_core_smoke_check_closes_and_recovers_invalid_saved_options(self) -> None:
        """Every bad saved mode choice must close, then recover only when corrected."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        self.assertIn("async_assert_invalid_saved_options_lifecycle", core_check_source)
        self.assertIn('UNSAFE_PROXY_OPTIONS = {"mode": "proxy"}', core_check_source)
        self.assertIn("UNSAFE_EXTRA_FIELD_OPTIONS", core_check_source)
        self.assertIn('"mode": "shadow",', core_check_source)
        self.assertIn('"unmodelled": "outside_contract",', core_check_source)
        self.assertEqual(
            2,
            lifecycle_source.count("async_assert_invalid_saved_options_lifecycle("),
        )
        self.assertIn("UNSAFE_PROXY_OPTIONS", lifecycle_source)
        self.assertIn("UNSAFE_EXTRA_FIELD_OPTIONS", lifecycle_source)
        self.assertIn('"invalid-mode options",', lifecycle_source)
        self.assertIn('"extra-field options",', lifecycle_source)
        self.assertIn("invalid_options_safe_options", core_check_source)
        self.assertIn("invalid_options_entity_ids", core_check_source)
        self.assertIn(
            "manually corrected HASC options must reload successfully",
            core_check_source,
        )
        self.assertIn(
            "restart must preserve the temporary invalid entry options",
            core_check_source,
        )
        self.assertIn("HASC corrected {scenario_name} temporary", core_check_source)
        self.assertIn("corrected HASC options removal", core_check_source)
        self.assertIn("(*previous_removed_entries, removed_entry)", core_check_source)
        self.assertLess(
            lifecycle_source.index("UNSAFE_PROXY_OPTIONS"),
            lifecycle_source.index("UNSAFE_EXTRA_FIELD_OPTIONS"),
        )
        self.assertLess(
            core_check_source.index("invalid_options_hass = await async_start_empty_home_assistant"),
            core_check_source.index("invalid_options_restarted_hass ="),
        )

    def test_core_smoke_check_removes_state_values_after_removal(self) -> None:
        """A removed HASC entry must not leave count values in the state machine."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("owned_entity_ids", core_check_source)
        self.assertIn("hass.states.get(entity_id) is not None", core_check_source)
        self.assertIn(
            "removed entry must not leave state values behind",
            core_check_source,
        )

    def test_core_smoke_check_keeps_hasc_removed_after_restart(self) -> None:
        """A final empty restart must not silently restore a removed HASC."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("RemovedHascEntry", core_check_source)
        self.assertIn("assert_hasc_stays_removed_after_restart", core_check_source)
        self.assertIn(
            "post_removal_hass = await async_start_empty_home_assistant",
            core_check_source,
        )
        self.assertIn(
            "removed HASC must not restore config entries after restart",
            core_check_source,
        )
        self.assertIn(
            "removed HASC must not restore local summary route after restart",
            core_check_source,
        )

    def test_core_smoke_check_can_reinstall_after_a_clean_restart(self) -> None:
        """A fresh safe setup must follow, rather than replace, the absence check."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        self.assertLess(
            lifecycle_source.index("assert_hasc_stays_removed_after_restart("),
            lifecycle_source.index("fresh_entry = await async_create_safe_entry("),
        )
        self.assertIn(
            "fresh HASC setup must use a new entry identifier",
            lifecycle_source,
        )
        self.assertIn(
            '"HASC post-restart temporary",',
            lifecycle_source,
        )
        self.assertIn(
            "assert_reserved_name_does_not_block_hasc(",
            lifecycle_source,
        )

    def test_core_smoke_check_closes_the_fresh_reinstall_cycle(self) -> None:
        """The fresh setup must also be removed before the final empty restart."""

        core_check_source = (ROOT / "tools" / "check_home_assistant_core.py").read_text(
            encoding="utf-8"
        )
        lifecycle_source = core_check_source.split("async def async_run_check()", 1)[1]

        self.assertLess(
            lifecycle_source.index("fresh_entry = await async_create_safe_entry("),
            lifecycle_source.index("fresh_removal_reader_token"),
        )
        self.assertIn(
            "await async_remove_safe_entry(post_removal_hass, fresh_entry.entry_id)",
            lifecycle_source,
        )
        self.assertIn(
            "await async_assert_invalid_saved_data_lifecycle(",
            lifecycle_source,
        )
        self.assertIn(
            "recovered_data_removal_hass = await async_start_empty_home_assistant",
            core_check_source,
        )
        self.assertGreaterEqual(
            lifecycle_source.count("assert_hasc_stays_removed_after_restart("),
            1,
        )

    def test_home_summary_rejects_impossible_totals(self) -> None:
        """Bad aggregate data cannot reach diagnostics silently."""

        with self.assertRaisesRegex(ValueError, "sensor count"):
            HomeSummary(
                areas_count=0,
                devices_count=0,
                entities_count=1,
                sensors_count=2,
                available_entities_count=1,
                unavailable_entities_count=0,
                unknown_entities_count=0,
                not_reported_entities_count=0,
                disabled_entities_count=0,
            )
        with self.assertRaisesRegex(ValueError, "availability counts"):
            HomeSummary(
                areas_count=0,
                devices_count=0,
                entities_count=1,
                sensors_count=1,
                available_entities_count=0,
                unavailable_entities_count=0,
                unknown_entities_count=0,
                not_reported_entities_count=0,
                disabled_entities_count=0,
            )
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            HomeSummary(
                areas_count=True,
                devices_count=0,
                entities_count=0,
                sensors_count=0,
                available_entities_count=0,
                unavailable_entities_count=0,
                unknown_entities_count=0,
                not_reported_entities_count=0,
                disabled_entities_count=0,
            )
        with self.assertRaisesRegex(ValueError, "approved category"):
            RegisteredEntity(domain="sensor", availability="unexpected")  # type: ignore[arg-type]

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

    def test_outer_adapter_has_no_execution_surface_and_only_summary_sensors(self) -> None:
        forbidden_fragments = (
            "hass.services",
            "async_call(",
            "async_set(",
            "async_fire(",
            "async_create_issue",
            "async_register_entity_service",
            "services.yaml",
            "node-red",
            "aiohttp",
            "requests",
            "websocket",
        )
        source = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in INTEGRATION.rglob("*.py")
        )
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, source)
        self.assertTrue((INTEGRATION / "sensor.py").is_file())
        for absent_module in ("services.yaml", "switch.py", "climate.py"):
            self.assertFalse((INTEGRATION / absent_module).exists())

        sensor_source = (INTEGRATION / "sensor.py").read_text(encoding="utf-8")
        self.assertIn("HOME_SUMMARY_COUNT_KEYS", sensor_source)
        self.assertIn("timedelta(minutes=5)", sensor_source)
        self.assertIn("EntityCategory.DIAGNOSTIC", sensor_source)

        local_view_source = (INTEGRATION / "local_summary.py").read_text(encoding="utf-8")
        self.assertIn("requires_auth = True", local_view_source)
        self.assertIn("cors_allowed = False", local_view_source)
        self.assertIn("async def get", local_view_source)
        for blocked_method in ("async def post", "async def put", "async def patch", "async def delete"):
            self.assertNotIn(blocked_method, local_view_source)

    def test_translations_are_present_for_the_only_selector(self) -> None:
        for language in ("en", "ru"):
            content = json.loads(
                (INTEGRATION / "translations" / f"{language}.json").read_text(encoding="utf-8")
            )
            self.assertIn("mode", content["selector"])
            self.assertIn("unsafe_mode", content["config"]["error"])

    def test_sensor_translations_have_only_the_approved_nine_counts(self) -> None:
        """Every visible label must map to an already-approved aggregate count."""

        expected_keys = set(HOME_SUMMARY_COUNT_KEYS)
        for language in ("en", "ru"):
            with self.subTest(language=language):
                content = json.loads(
                    (INTEGRATION / "translations" / f"{language}.json").read_text(
                        encoding="utf-8"
                    )
                )
                labels = content["entity"]["sensor"]
                self.assertEqual(expected_keys, set(labels))
                self.assertTrue(all(set(label) == {"name"} for label in labels.values()))

    def test_translations_describe_the_public_non_controlling_shell(self) -> None:
        """Keep installation language honest about the integration's safety."""

        english = json.loads(
            (INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8")
        )
        russian = json.loads(
            (INTEGRATION / "translations" / "ru.json").read_text(encoding="utf-8")
        )

        expected_labels = {
            "en": {
                "read-only": "Read-only",
                "shadow": "Comparison without changes",
            },
            "ru": {
                "read-only": "Только чтение",
                "shadow": "Проверка без изменений",
            },
        }

        for language, content in (("en", english), ("ru", russian)):
            with self.subTest(language=language):
                user_step = content["config"]["step"]["user"]
                options_step = content["options"]["step"]["init"]
                rendered_text = "\n".join(
                    (
                        user_step["description"],
                        user_step["data_description"]["mode"],
                        content["config"]["error"]["unsafe_mode"],
                        options_step["description"],
                        options_step["data_description"]["mode"],
                        content["options"]["error"]["unsafe_mode"],
                        *content["selector"]["mode"]["options"].values(),
                    )
                ).lower()
                self.assertNotIn("private", rendered_text)
                self.assertNotIn("shadow", rendered_text)
                self.assertEqual(
                    expected_labels[language],
                    content["selector"]["mode"]["options"],
                )

        self.assertIn("does not change the home", english["config"]["step"]["user"]["description"])
        self.assertIn("without home control", english["options"]["step"]["init"]["description"])
        self.assertIn("не меняет", russian["config"]["step"]["user"]["description"])
        self.assertIn("без управления домом", russian["options"]["step"]["init"]["description"])

    @staticmethod
    def home_summary() -> HomeSummary:
        """Return one synthetic aggregate used by diagnostics-only tests."""

        return HomeSummary(
            areas_count=0,
            devices_count=0,
            entities_count=0,
            sensors_count=0,
            available_entities_count=0,
            unavailable_entities_count=0,
            unknown_entities_count=0,
            not_reported_entities_count=0,
            disabled_entities_count=0,
        )


if __name__ == "__main__":
    unittest.main()
