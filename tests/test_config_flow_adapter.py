"""Isolated contract tests for the Home Assistant form adapter.

These tests use a deliberately tiny in-memory stand-in for the Home Assistant
form API. They test only the adapter behavior authored in this repository; they
do not start Home Assistant, load integrations, discover devices, or contact a
home. A real Core 2026.6 runtime check remains a separate Python 3.14 task.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest


ROOT = Path(__file__).resolve().parents[1]

CONFIG_FLOW_MODULE = "custom_components.hausman_hub.config_flow"
FAKE_MODULE_NAMES = (
    "voluptuous",
    "homeassistant",
    "homeassistant.config_entries",
    "homeassistant.core",
    "homeassistant.data_entry_flow",
    "homeassistant.helpers",
    "homeassistant.helpers.selector",
)


class FakeSchema:
    """Store the fields passed to a form schema without performing UI work."""

    def __init__(self, fields: dict[str, object]) -> None:
        self.fields = fields


class FakeRequired:
    """Capture a required schema field and its declared default."""

    def __init__(self, key: str, *, default: object) -> None:
        self.key = key
        self.default = default


class FakeOptional:
    """Capture an optional schema field and any visible default."""

    def __init__(self, key: str, *, default: object = None) -> None:
        self.key = key
        self.default = default


class FakeSelectSelectorConfig:
    """Capture selector settings supplied by the adapter."""

    def __init__(self, *, options: list[dict[str, str]], translation_key: str) -> None:
        self.options = options
        self.translation_key = translation_key


class FakeSelectSelector:
    """Capture a selector without querying an area, device, or entity registry."""

    def __init__(self, config: FakeSelectSelectorConfig) -> None:
        self.config = config


class FakeBooleanSelector:
    """Represent the one local-page setting without a Home Assistant runtime."""


class FakeEntitySelectorConfig:
    """Capture the exact entity domain permitted by the canary selector."""

    def __init__(self, *, domain: str, multiple: bool) -> None:
        self.domain = domain
        self.multiple = multiple


class FakeEntitySelector:
    """Represent the canary target selector without an entity registry."""

    def __init__(self, config: FakeEntitySelectorConfig) -> None:
        self.config = config


class FakeConfigEntry:
    """The two config-entry mappings used by the adapter under test."""

    def __init__(self, data: dict[str, object], options: dict[str, object]) -> None:
        self.data = data
        self.options = options


class FakeConfigFlow:
    """Minimal behavior used by HausmanHubConfigFlow."""

    domain: str | None = None

    def __init_subclass__(cls, *, domain: str | None = None, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls.domain = domain

    def __init__(self) -> None:
        self.unique_id: str | None = None
        self.unique_id_checked = False

    async def async_set_unique_id(self, unique_id: str) -> None:
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self) -> None:
        self.unique_id_checked = True

    def async_create_entry(self, *, title: str, data: dict[str, object]) -> dict[str, object]:
        return {"type": "create_entry", "title": title, "data": data}

    def async_show_form(
        self,
        *,
        step_id: str,
        data_schema: FakeSchema,
        errors: dict[str, str],
    ) -> dict[str, object]:
        return {
            "type": "form",
            "step_id": step_id,
            "schema": data_schema,
            "errors": errors,
        }


class FakeOptionsFlow:
    """Minimal options-flow base with the config-entry property used by HASC."""

    config_entry: FakeConfigEntry

    def async_create_entry(self, *, title: str, data: dict[str, object]) -> dict[str, object]:
        return {"type": "create_entry", "title": title, "data": data}

    def async_show_form(
        self,
        *,
        step_id: str,
        data_schema: FakeSchema,
        errors: dict[str, str],
    ) -> dict[str, object]:
        return {
            "type": "form",
            "step_id": step_id,
            "schema": data_schema,
            "errors": errors,
        }


def fake_home_assistant_modules() -> dict[str, ModuleType]:
    """Build only the imports required by config_flow.py, entirely in memory."""

    voluptuous = ModuleType("voluptuous")
    voluptuous.Schema = FakeSchema  # type: ignore[attr-defined]
    voluptuous.Required = FakeRequired  # type: ignore[attr-defined]
    voluptuous.Optional = FakeOptional  # type: ignore[attr-defined]

    homeassistant = ModuleType("homeassistant")
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = FakeConfigEntry  # type: ignore[attr-defined]
    config_entries.ConfigFlow = FakeConfigFlow  # type: ignore[attr-defined]
    config_entries.OptionsFlow = FakeOptionsFlow  # type: ignore[attr-defined]

    core = ModuleType("homeassistant.core")
    core.callback = lambda function: function  # type: ignore[attr-defined]

    data_entry_flow = ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict  # type: ignore[attr-defined]

    helpers = ModuleType("homeassistant.helpers")
    selector = ModuleType("homeassistant.helpers.selector")
    selector.SelectOptionDict = lambda *, value, label: {  # type: ignore[attr-defined]
        "value": value,
        "label": label,
    }
    selector.BooleanSelector = FakeBooleanSelector  # type: ignore[attr-defined]
    selector.EntitySelector = FakeEntitySelector  # type: ignore[attr-defined]
    selector.EntitySelectorConfig = FakeEntitySelectorConfig  # type: ignore[attr-defined]
    selector.SelectSelector = FakeSelectSelector  # type: ignore[attr-defined]
    selector.SelectSelectorConfig = FakeSelectSelectorConfig  # type: ignore[attr-defined]

    homeassistant.config_entries = config_entries  # type: ignore[attr-defined]
    homeassistant.core = core  # type: ignore[attr-defined]
    homeassistant.helpers = helpers  # type: ignore[attr-defined]
    helpers.selector = selector  # type: ignore[attr-defined]

    return {
        "voluptuous": voluptuous,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.data_entry_flow": data_entry_flow,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.selector": selector,
    }


class ConfigFlowAdapterTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the adapter through the same flow hooks Home Assistant calls."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.original_sys_path = sys.path[:]
        sys.path.insert(0, str(ROOT))
        cls.previous_modules = {
            name: sys.modules.get(name) for name in (*FAKE_MODULE_NAMES, CONFIG_FLOW_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, CONFIG_FLOW_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        cls.config_flow = importlib.import_module(CONFIG_FLOW_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, CONFIG_FLOW_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in cls.previous_modules.items() if module is not None}
        )
        sys.path[:] = cls.original_sys_path

    def assert_mode_field(self, schema: FakeSchema, expected_default: str) -> FakeSelectSelector:
        """Return the only mode selector and verify its safe default value."""

        self.assertEqual(1, len(schema.fields))
        field, selector = next(iter(schema.fields.items()))
        self.assertIsInstance(field, FakeRequired)
        self.assertEqual("mode", field.key)
        self.assertEqual(expected_default, field.default)
        self.assertIsInstance(selector, FakeSelectSelector)
        return selector

    def assert_options_fields(
        self,
        schema: FakeSchema,
        expected_mode_default: str,
        expected_local_page_default: bool,
        expected_summary_update_interval_default: str,
        expected_canary_control_enabled_default: bool = False,
        expected_canary_control_target_default: str | None = None,
    ) -> FakeSelectSelector:
        """Verify the fixed observation fields and narrow canary selector."""

        self.assertEqual(5, len(schema.fields))
        fields = list(schema.fields.items())
        mode_field, mode_selector = fields[0]
        page_field, page_selector = fields[1]
        interval_field, interval_selector = fields[2]
        canary_field, canary_selector = fields[3]
        target_field, target_selector = fields[4]
        self.assertIsInstance(mode_field, FakeRequired)
        self.assertEqual("mode", mode_field.key)
        self.assertEqual(expected_mode_default, mode_field.default)
        self.assertIsInstance(mode_selector, FakeSelectSelector)
        self.assertIsInstance(page_field, FakeRequired)
        self.assertEqual("local_summary_enabled", page_field.key)
        self.assertEqual(expected_local_page_default, page_field.default)
        self.assertIsInstance(page_selector, FakeBooleanSelector)
        self.assertEqual("boolean", page_selector.selector_type)
        self.assertIsInstance(interval_field, FakeRequired)
        self.assertEqual("summary_update_interval", interval_field.key)
        self.assertEqual(
            expected_summary_update_interval_default,
            interval_field.default,
        )
        self.assertIsInstance(interval_selector, FakeSelectSelector)
        self.assertEqual(
            ["5m", "15m", "30m"],
            [option["value"] for option in interval_selector.config.options],
        )
        self.assertEqual(
            "summary_update_interval",
            interval_selector.config.translation_key,
        )
        self.assertIsInstance(canary_field, FakeRequired)
        self.assertEqual("canary_control_enabled", canary_field.key)
        self.assertEqual(expected_canary_control_enabled_default, canary_field.default)
        self.assertIsInstance(canary_selector, FakeBooleanSelector)
        self.assertEqual("boolean", canary_selector.selector_type)
        self.assertIsInstance(target_field, FakeOptional)
        self.assertEqual("canary_control_target", target_field.key)
        self.assertEqual(expected_canary_control_target_default, target_field.default)
        self.assertIsInstance(target_selector, FakeEntitySelector)
        self.assertEqual("input_boolean", target_selector.config.domain)
        self.assertFalse(target_selector.config.multiple)
        return mode_selector

    async def test_user_form_exposes_only_the_two_approved_modes(self) -> None:
        flow = self.config_flow.HausmanHubConfigFlow()

        result = await flow.async_step_user()

        self.assertEqual("form", result["type"])
        self.assertEqual("user", result["step_id"])
        schema = result["schema"]
        self.assertIsInstance(schema, FakeSchema)
        selector = self.assert_mode_field(schema, "read-only")
        self.assertEqual(
            ["read-only", "shadow"],
            [option["value"] for option in selector.config.options],
        )

    async def test_user_flow_creates_only_a_safe_shadow_entry(self) -> None:
        flow = self.config_flow.HausmanHubConfigFlow()

        result = await flow.async_step_user({"mode": "shadow"})

        self.assertEqual("create_entry", result["type"])
        self.assertEqual("shadow", result["data"]["mode"])
        self.assertEqual(
            "direct_execution_blocked",
            result["data"]["direct_execution_status"],
        )
        self.assertEqual("hausman_hub_read_only_skeleton", flow.unique_id)
        self.assertTrue(flow.unique_id_checked)

    async def test_forms_discard_extra_user_input(self) -> None:
        """Only the approved mode may cross either HASC form boundary."""

        extra_input = {
            "mode": "shadow",
            "direct_execution_status": "allowed",
            "unmodelled": "outside_contract",
        }
        user_flow = self.config_flow.HausmanHubConfigFlow()
        user_result = await user_flow.async_step_user(extra_input)

        self.assertEqual("create_entry", user_result["type"])
        self.assertEqual(
            {
                "mode": "shadow",
                "direct_execution_status": "direct_execution_blocked",
            },
            user_result["data"],
        )

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )
        options_result = await options_flow.async_step_init(extra_input)

        self.assertEqual("create_entry", options_result["type"])
        self.assertEqual(
            {
                "mode": "shadow",
                "local_summary_enabled": True,
                "summary_update_interval": "5m",
                "canary_control_enabled": False,
            },
            options_result["data"],
        )

    async def test_user_flow_rejects_proxy_mode(self) -> None:
        flow = self.config_flow.HausmanHubConfigFlow()

        result = await flow.async_step_user({"mode": "proxy"})

        self.assertEqual("form", result["type"])
        self.assertEqual({"mode": "unsafe_mode"}, result["errors"])

    async def test_options_flow_keeps_the_same_safety_boundary(self) -> None:
        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )

        initial_form = await options_flow.async_step_init()
        shadow_result = await options_flow.async_step_init({"mode": "shadow"})
        proxy_result = await options_flow.async_step_init({"mode": "proxy"})

        self.assertEqual("form", initial_form["type"])
        self.assert_options_fields(initial_form["schema"], "read-only", True, "5m")
        self.assertEqual("create_entry", shadow_result["type"])
        self.assertEqual(
            {
                "mode": "shadow",
                "local_summary_enabled": True,
                "summary_update_interval": "5m",
                "canary_control_enabled": False,
            },
            shadow_result["data"],
        )
        self.assertEqual("form", proxy_result["type"])
        self.assertEqual({"mode": "unsafe_mode"}, proxy_result["errors"])

    async def test_options_form_hides_a_broken_saved_configuration(self) -> None:
        """A damaged saved configuration cannot make shadow look selected."""

        blocked = {"direct_execution_status": "direct_execution_blocked"}
        for label, data, options in (
            (
                "unsafe option",
                {"mode": "read-only", **blocked},
                {"mode": "proxy"},
            ),
            (
                "unknown initial mode",
                {"mode": "outside_contract", **blocked},
                {},
            ),
            (
                "missing initial mode",
                blocked,
                {},
            ),
            (
                "direct execution no longer blocked",
                {"mode": "read-only", "direct_execution_status": "allowed"},
                {"mode": "shadow"},
            ),
            (
                "missing direct execution block",
                {"mode": "read-only"},
                {"mode": "shadow"},
            ),
            (
                "extra saved setting",
                {
                    "mode": "read-only",
                    **blocked,
                    "unmodelled": "outside_contract",
                },
                {"mode": "shadow"},
            ),
            (
                "extra saved option",
                {"mode": "read-only", **blocked},
                {"mode": "shadow", "unmodelled": "outside_contract"},
            ),
        ):
            with self.subTest(label=label):
                options_flow = self.config_flow.HausmanHubOptionsFlow()
                options_flow.config_entry = FakeConfigEntry(dict(data), dict(options))
                original_data = dict(options_flow.config_entry.data)
                original_options = dict(options_flow.config_entry.options)

                result = await options_flow.async_step_init()

                self.assertEqual("form", result["type"])
                self.assert_options_fields(result["schema"], "read-only", True, "5m")
                self.assertEqual(original_data, options_flow.config_entry.data)
                self.assertEqual(original_options, options_flow.config_entry.options)

    async def test_options_form_keeps_a_safe_shadow_default(self) -> None:
        """A valid saved shadow choice must remain the selected form default."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {"mode": "shadow"},
        )
        original_data = dict(options_flow.config_entry.data)
        original_options = dict(options_flow.config_entry.options)

        result = await options_flow.async_step_init()

        self.assertEqual("form", result["type"])
        self.assert_options_fields(result["schema"], "shadow", True, "5m")
        self.assertEqual(original_data, options_flow.config_entry.data)
        self.assertEqual(original_options, options_flow.config_entry.options)

    async def test_options_can_close_only_the_optional_local_page(self) -> None:
        """The new setting changes no mode, device, or home-facing input."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )

        result = await options_flow.async_step_init(
            {"mode": "read-only", "local_summary_enabled": False}
        )

        self.assertEqual("create_entry", result["type"])
        self.assertEqual(
            {
                "mode": "read-only",
                "local_summary_enabled": False,
                "summary_update_interval": "5m",
                "canary_control_enabled": False,
            },
            result["data"],
        )

    async def test_options_can_slow_only_the_same_nine_count_refresh(self) -> None:
        """The new choice changes timing, not data or runtime authority."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {"mode": "shadow", "local_summary_enabled": False},
        )

        initial_form = await options_flow.async_step_init()
        result = await options_flow.async_step_init(
            {
                "mode": "shadow",
                "local_summary_enabled": False,
                "summary_update_interval": "30m",
                "canary_control_enabled": False,
            }
        )

        self.assert_options_fields(initial_form["schema"], "shadow", False, "5m")
        self.assertEqual("create_entry", result["type"])
        self.assertEqual(
            {
                "mode": "shadow",
                "local_summary_enabled": False,
                "summary_update_interval": "30m",
                "canary_control_enabled": False,
            },
            result["data"],
        )

    async def test_options_can_arm_and_disarm_one_input_boolean_canary(self) -> None:
        """The form exposes one exact helper target and a reversible arm switch."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )

        armed = await options_flow.async_step_init(
            {
                "mode": "read-only",
                "local_summary_enabled": True,
                "summary_update_interval": "5m",
                "canary_control_enabled": True,
                "canary_control_target": "input_boolean.hasc_canary",
            }
        )

        self.assertEqual("create_entry", armed["type"])
        self.assertEqual(
            {
                "mode": "read-only",
                "local_summary_enabled": True,
                "summary_update_interval": "5m",
                "canary_control_enabled": True,
                "canary_control_target": "input_boolean.hasc_canary",
            },
            armed["data"],
        )

        options_flow.config_entry.options = dict(armed["data"])
        armed_form = await options_flow.async_step_init()
        self.assert_options_fields(
            armed_form["schema"],
            "read-only",
            True,
            "5m",
            True,
            "input_boolean.hasc_canary",
        )

        disarmed = await options_flow.async_step_init(
            {
                **armed["data"],
                "canary_control_enabled": False,
            }
        )
        self.assertEqual("create_entry", disarmed["type"])
        self.assertFalse(disarmed["data"]["canary_control_enabled"])
        self.assertNotIn("canary_control_target", disarmed["data"])

    async def test_options_reject_unsafe_canary_values(self) -> None:
        """No missing target, other entity domain, or truth-like arm value passes."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )
        base = {
            "mode": "read-only",
            "local_summary_enabled": True,
            "summary_update_interval": "5m",
        }

        for invalid_input, expected_error in (
            (
                {**base, "canary_control_enabled": "true"},
                {"canary_control_enabled": "unsafe_canary_control_setting"},
            ),
            (
                {**base, "canary_control_enabled": True},
                {"canary_control_target": "unsafe_canary_control_target"},
            ),
            (
                {
                    **base,
                    "canary_control_enabled": True,
                    "canary_control_target": "light.kitchen",
                },
                {"canary_control_target": "unsafe_canary_control_target"},
            ),
        ):
            with self.subTest(invalid_input=invalid_input):
                result = await options_flow.async_step_init(invalid_input)
                self.assertEqual("form", result["type"])
                self.assertEqual(expected_error, result["errors"])

    async def test_options_reject_a_non_boolean_local_page_setting(self) -> None:
        """Truth-like strings and numbers cannot silently open the local page."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )

        for invalid_value in ("true", 1, None):
            with self.subTest(invalid_value=invalid_value):
                result = await options_flow.async_step_init(
                    {"mode": "read-only", "local_summary_enabled": invalid_value}
                )

                self.assertEqual("form", result["type"])
                self.assertEqual(
                    {"local_summary_enabled": "unsafe_local_summary_setting"},
                    result["errors"],
                )

    async def test_options_reject_an_unapproved_summary_update_interval(self) -> None:
        """Strings, numbers, and faster choices cannot change the read cadence."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )

        for invalid_value in ("1m", "60m", 5, None):
            with self.subTest(invalid_value=invalid_value):
                result = await options_flow.async_step_init(
                    {
                        "mode": "read-only",
                        "local_summary_enabled": True,
                        "summary_update_interval": invalid_value,
                    }
                )

                self.assertEqual("form", result["type"])
                self.assertEqual(
                    {"summary_update_interval": "unsafe_summary_update_interval"},
                    result["errors"],
                )


if __name__ == "__main__":
    unittest.main()
