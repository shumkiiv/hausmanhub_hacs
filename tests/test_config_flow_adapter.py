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
from types import ModuleType, SimpleNamespace
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

    def __init__(self, key: str, *, default: object = None) -> None:
        self.key = key
        self.default = default


class FakeOptional:
    """Capture an optional schema field and any visible default."""

    def __init__(self, key: str, *, default: object = None) -> None:
        self.key = key
        self.default = default


class FakeSelectSelectorConfig:
    """Capture selector settings supplied by the adapter."""

    def __init__(
        self,
        *,
        options: list[dict[str, str]],
        translation_key: str | None = None,
        multiple: bool = False,
    ) -> None:
        self.options = options
        self.translation_key = translation_key
        self.multiple = multiple


class FakeSelectSelector:
    """Capture a selector without querying an area, device, or entity registry."""

    def __init__(self, config: FakeSelectSelectorConfig) -> None:
        self.config = config


class FakeBooleanSelector:
    """Represent the one local-page setting without a Home Assistant runtime."""


class FakeEntitySelectorConfig:
    """Capture the exact entity domain permitted by the canary selector."""

    def __init__(self, *, domain: str | list[str], multiple: bool) -> None:
        self.domain = domain
        self.multiple = multiple


class FakeEntitySelector:
    """Represent the canary target selector without an entity registry."""

    def __init__(self, config: FakeEntitySelectorConfig) -> None:
        self.config = config


class FakeTextSelectorConfig:
    """Capture multiline private-registry text settings."""

    def __init__(self, *, multiline: bool, type: str) -> None:
        self.multiline = multiline
        self.type = type


class FakeTextSelector:
    """Represent the native multiline text selector."""

    def __init__(self, config: FakeTextSelectorConfig) -> None:
        self.config = config


class FakeTextSelectorType:
    TEXT = "text"


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
        **kwargs: object,
    ) -> dict[str, object]:
        return {
            "type": "form",
            "step_id": step_id,
            "schema": data_schema,
            "errors": errors,
            **kwargs,
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
        **kwargs: object,
    ) -> dict[str, object]:
        return {
            "type": "form",
            "step_id": step_id,
            "schema": data_schema,
            "errors": errors,
            **kwargs,
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
    selector.TextSelector = FakeTextSelector  # type: ignore[attr-defined]
    selector.TextSelectorConfig = FakeTextSelectorConfig  # type: ignore[attr-defined]
    selector.TextSelectorType = FakeTextSelectorType  # type: ignore[attr-defined]

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
        expected_climate_bridge_mode_default: str = "disabled",
        expected_climate_bridge_target_default: str | None = None,
        expected_climate_canary_room_default: str | None = None,
    ) -> FakeSelectSelector:
        """Verify fixed observation, helper-canary, and climate rollout fields."""

        self.assertEqual(9, len(schema.fields))
        fields = list(schema.fields.items())
        mode_field, mode_selector = fields[0]
        page_field, page_selector = fields[1]
        interval_field, interval_selector = fields[2]
        canary_field, canary_selector = fields[3]
        bridge_mode_field, bridge_mode_selector = fields[4]
        target_field, target_selector = fields[5]
        bridge_target_field, bridge_target_selector = fields[6]
        bridge_room_field, bridge_room_selector = fields[7]
        next_step_field, next_step_selector = fields[8]
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
        self.assertIsInstance(bridge_mode_field, FakeRequired)
        self.assertEqual("climate_bridge_mode", bridge_mode_field.key)
        self.assertEqual(expected_climate_bridge_mode_default, bridge_mode_field.default)
        self.assertIsInstance(bridge_mode_selector, FakeSelectSelector)
        self.assertEqual(
            ["disabled", "shadow", "canary"],
            [option["value"] for option in bridge_mode_selector.config.options],
        )
        self.assertIsInstance(target_field, FakeOptional)
        self.assertEqual("canary_control_target", target_field.key)
        self.assertEqual(expected_canary_control_target_default, target_field.default)
        self.assertIsInstance(target_selector, FakeEntitySelector)
        self.assertEqual("input_boolean", target_selector.config.domain)
        self.assertFalse(target_selector.config.multiple)
        self.assertIsInstance(bridge_target_field, FakeOptional)
        self.assertEqual("climate_bridge_target", bridge_target_field.key)
        self.assertEqual(expected_climate_bridge_target_default, bridge_target_field.default)
        self.assertIs(bridge_target_selector, str)
        self.assertIsInstance(bridge_room_field, FakeOptional)
        self.assertEqual("climate_canary_room_id", bridge_room_field.key)
        self.assertEqual(expected_climate_canary_room_default, bridge_room_field.default)
        self.assertIs(bridge_room_selector, str)
        self.assertIsInstance(next_step_field, FakeRequired)
        self.assertEqual("next_step", next_step_field.key)
        self.assertEqual("save_settings", next_step_field.default)
        self.assertIsInstance(next_step_selector, FakeSelectSelector)
        self.assertEqual(
            ["save_settings", "manage_climate_registry"],
            [option["value"] for option in next_step_selector.config.options],
        )
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
                "climate_bridge_mode": "disabled",
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
                "climate_bridge_mode": "disabled",
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
                "climate_bridge_mode": "disabled",
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
                "climate_bridge_mode": "disabled",
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
                "climate_bridge_mode": "disabled",
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
                "climate_bridge_mode": "disabled",
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
                "climate_bridge_mode": "disabled",
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

    async def test_options_configure_shadow_and_one_room_canary(self) -> None:
        """The form persists only a private literal origin and stable room id."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )
        shadow = await options_flow.async_step_init(
            {
                "mode": "shadow",
                "climate_bridge_mode": "shadow",
                "climate_bridge_target": "http://127.0.0.1:1880",
            }
        )
        self.assertEqual("create_entry", shadow["type"])
        self.assertEqual("shadow", shadow["data"]["climate_bridge_mode"])
        self.assertNotIn("climate_canary_room_id", shadow["data"])

        options_flow.config_entry.options = dict(shadow["data"])
        form = await options_flow.async_step_init()
        self.assert_options_fields(
            form["schema"],
            "shadow",
            True,
            "5m",
            expected_climate_bridge_mode_default="shadow",
            expected_climate_bridge_target_default="http://127.0.0.1:1880",
        )

        canary = await options_flow.async_step_init(
            {
                **shadow["data"],
                "climate_bridge_mode": "canary",
                "climate_canary_room_id": "living",
            }
        )
        self.assertEqual("create_entry", canary["type"])
        self.assertEqual("living", canary["data"]["climate_canary_room_id"])

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

    async def test_options_registry_setup_previews_then_requires_atomic_confirmation(self) -> None:
        """The local admin flow must not save a registry from the edit step."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.domain.climate import ClimateRegistry
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from tests.test_climate_import import registry_payload, source_payload

        class Store:
            def __init__(self) -> None:
                self.registry = ClimateRegistry()
                self.saved = []

            async def async_load(self):
                return self.registry

            async def async_save(self, registry):
                self.registry = registry
                self.saved.append(registry)

        class Bridge:
            async def async_fetch_state(self):
                return import_climate_state(source_payload())

            async def async_execute(self, plan):
                raise AssertionError("registry setup must not execute a command")

        store = Store()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.SHADOW,
            ),
            registry_store=store,
            bridge_client=Bridge(),
        )
        await runtime.async_start()
        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {"mode": "shadow"},
        )
        options_flow.hass = SimpleNamespace(  # type: ignore[attr-defined]
            data={"hausman_hub": {"climate_runtime": runtime}}
        )

        editor = await options_flow.async_step_init(
            {"next_step": "manage_climate_registry"}
        )
        room_form = await options_flow.async_step_climate_registry(
            {"climate_registry_action": "add_room"}
        )
        menu_after_room = await options_flow.async_step_climate_registry_room(
            {"climate_room_id": "living", "climate_room_name": "Living room"}
        )
        device_form = await options_flow.async_step_climate_registry(
            {"climate_registry_action": "add_device"}
        )
        menu_after_device = await options_flow.async_step_climate_registry_device(
            {
                "climate_device_id": "living_ac",
                "climate_device_name": "Living AC",
                "climate_device_room": "living",
                "climate_device_kind": "air_conditioner",
                "climate_device_source_id": "synthetic-ac-source-living",
                "climate_device_control_scope": "canary",
                "climate_device_control_owner": "climate_core",
                "climate_device_capabilities": [
                    "power",
                    "target_temperature",
                    "hvac_mode",
                    "fan_mode",
                ],
                "climate_device_control_entity": "climate.synthetic_living_ac",
            }
        )
        preview = await options_flow.async_step_climate_registry(
            {"climate_registry_action": "review_registry"}
        )

        self.assertEqual("climate_registry", editor["step_id"])
        self.assertEqual("climate_registry_room", room_form["step_id"])
        self.assertEqual("climate_registry", menu_after_room["step_id"])
        self.assertEqual("climate_registry_device", device_form["step_id"])
        self.assertEqual("climate_registry", menu_after_device["step_id"])
        self.assertEqual("climate_registry_confirm", preview["step_id"])
        self.assertEqual([], store.saved)
        saved = await options_flow.async_step_climate_registry_confirm(
            {"confirm_registry_save": True}
        )
        self.assertEqual("create_entry", saved["type"])
        self.assertEqual(1, len(store.saved))
        self.assertEqual(["living"], [room.room_id for room in store.saved[0].rooms])
        self.assertEqual(
            ["living_ac"],
            [device.device_id for device in store.saved[0].devices],
        )

    async def test_options_import_candidates_without_private_id_copying(self) -> None:
        """Opaque selector choices populate private bindings only after confirmation."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.domain.climate import ClimateRegistry
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from tests.test_climate_import import source_payload

        class Store:
            def __init__(self) -> None:
                self.registry = ClimateRegistry()
                self.saved = []

            async def async_load(self):
                return self.registry

            async def async_save(self, registry):
                self.registry = registry
                self.saved.append(registry)

        class Bridge:
            def __init__(self) -> None:
                self.executed = []
                self.fetches = 0

            async def async_fetch_state(self):
                self.fetches += 1
                return import_climate_state(source_payload())

            async def async_execute(self, plan):
                self.executed.append(plan)

        store = Store()
        bridge = Bridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.SHADOW,
            ),
            registry_store=store,
            bridge_client=bridge,
        )
        await runtime.async_start()
        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {"mode": "shadow"},
        )
        options_flow.hass = SimpleNamespace(  # type: ignore[attr-defined]
            data={"hausman_hub": {"climate_runtime": runtime}}
        )

        await options_flow.async_step_init({"next_step": "manage_climate_registry"})
        ac_choices = await options_flow.async_step_climate_registry(
            {"climate_registry_action": "import_candidate"}
        )
        choice_fields = list(ac_choices["schema"].fields.items())
        choice_selector = choice_fields[0][1]
        self.assertEqual(
            ["candidate_001", "candidate_002"],
            [option["value"] for option in choice_selector.config.options],
        )
        serialized_choices = str(choice_selector.config.options)
        self.assertNotIn("synthetic-ac-source-living", serialized_choices)
        self.assertNotIn("synthetic-humidifier-source-kids", serialized_choices)

        fetches_before_selection = bridge.fetches
        ac_form = await options_flow.async_step_climate_import_candidate(
            {"climate_import_candidate": "candidate_001"}
        )
        self.assertEqual(fetches_before_selection, bridge.fetches)
        ac_fields = {
            field.key: selector for field, selector in ac_form["schema"].fields.items()
        }
        self.assertNotIn("climate_device_source_id", ac_fields)
        self.assertNotIn("climate_device_room", ac_fields)
        self.assertEqual(
            "climate",
            ac_fields["climate_device_control_entity"].config.domain,
        )
        menu_after_ac = await options_flow.async_step_climate_import_device(
            {
                "climate_device_id": "living_ac",
                "climate_device_name": "Living AC",
                "climate_device_kind": "air_conditioner",
                "climate_device_control_scope": "canary",
                "climate_device_control_owner": "climate_core",
                "climate_device_control_entity": "climate.synthetic_living_ac",
                "climate_device_source_id": "attacker-cannot-override-selection",
            }
        )
        self.assertEqual("climate_registry", menu_after_ac["step_id"])
        self.assertEqual([], store.saved)

        humidifier_choices = await options_flow.async_step_climate_registry(
            {"climate_registry_action": "import_candidate"}
        )
        remaining_selector = next(iter(humidifier_choices["schema"].fields.values()))
        self.assertEqual(
            ["candidate_002"],
            [option["value"] for option in remaining_selector.config.options],
        )
        humidifier_form = await options_flow.async_step_climate_import_candidate(
            {"climate_import_candidate": "candidate_002"}
        )
        humidifier_fields = {
            field.key: selector
            for field, selector in humidifier_form["schema"].fields.items()
        }
        self.assertEqual(
            "humidifier",
            humidifier_fields["climate_device_control_entity"].config.domain,
        )
        await options_flow.async_step_climate_import_device(
            {
                "climate_device_id": "kids_humidifier",
                "climate_device_name": "Kids humidifier",
                "climate_device_kind": "humidifier",
                "climate_device_control_scope": "observed",
                "climate_device_control_owner": "observed",
                "climate_device_control_entity": "humidifier.synthetic_kids",
            }
        )
        preview = await options_flow.async_step_climate_registry(
            {"climate_registry_action": "review_registry"}
        )

        self.assertEqual("climate_registry_confirm", preview["step_id"])
        self.assertEqual([], store.saved)
        saved = await options_flow.async_step_climate_registry_confirm(
            {"confirm_registry_save": True}
        )
        self.assertEqual("create_entry", saved["type"])
        self.assertEqual(1, len(store.saved))
        self.assertEqual(
            ["living", "kids"],
            [room.room_id for room in store.saved[0].rooms],
        )
        self.assertEqual(
            ["synthetic-ac-source-living", "synthetic-humidifier-source-kids"],
            [device.source_id for device in store.saved[0].devices],
        )
        self.assertEqual([], bridge.executed)

    async def test_options_show_redacted_shadow_evidence_without_saving(self) -> None:
        """The guided setup exposes candidate readiness as a read-only screen."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_registry import (
            registry_from_payload,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from tests.test_climate_import import registry_payload, source_payload

        class Store:
            def __init__(self) -> None:
                self.saved = []

            async def async_load(self):
                return registry_from_payload(registry_payload())

            async def async_save(self, registry):
                self.saved.append(registry)

        class Bridge:
            def __init__(self) -> None:
                self.executed = []

            async def async_fetch_state(self):
                return import_climate_state(source_payload())

            async def async_execute(self, plan):
                self.executed.append(plan)

        store = Store()
        bridge = Bridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.SHADOW,
            ),
            registry_store=store,
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {"mode": "shadow"},
        )
        options_flow.hass = SimpleNamespace(  # type: ignore[attr-defined]
            data={"hausman_hub": {"climate_runtime": runtime}}
        )

        await options_flow.async_step_init({"next_step": "manage_climate_registry"})
        candidate_form = await options_flow.async_step_climate_registry(
            {"climate_registry_action": "review_shadow_evidence"}
        )
        evidence_form = await options_flow.async_step_climate_shadow_candidate(
            {"climate_shadow_candidate_room": "living"}
        )

        self.assertEqual("climate_shadow_candidate", candidate_form["step_id"])
        self.assertEqual("climate_shadow_evidence", evidence_form["step_id"])
        self.assertEqual("living", evidence_form["description_placeholders"]["room_id"])
        self.assertEqual("collecting", evidence_form["description_placeholders"]["status"])
        self.assertEqual([], store.saved)
        self.assertEqual([], bridge.executed)
        returned = await options_flow.async_step_climate_shadow_evidence(
            {"close_shadow_evidence": True}
        )
        self.assertEqual("climate_registry", returned["step_id"])


if __name__ == "__main__":
    unittest.main()
