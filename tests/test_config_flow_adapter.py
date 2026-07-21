"""Isolated contract tests for the Home Assistant form adapter.

These tests use a deliberately tiny in-memory stand-in for the Home Assistant
form API. They test only the adapter behavior authored in this repository; they
do not start Home Assistant, load integrations, discover devices, or contact a
home. A real Core 2026.6 runtime check remains a separate Python 3.14 task.
"""

from __future__ import annotations

import importlib
from datetime import datetime
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
    "homeassistant.util",
    "homeassistant.util.dt",
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
        options: list[dict[str, str]] | list[str],
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


class FakeTimeSelector:
    """Represent a local clock picker without a Home Assistant runtime."""


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
    """Minimal options-flow base with the config-entry property used by HausmanHub."""

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
    selector.TimeSelector = FakeTimeSelector  # type: ignore[attr-defined]

    util = ModuleType("homeassistant.util")
    dt = ModuleType("homeassistant.util.dt")
    dt.now = lambda: datetime(2026, 7, 19, 12, 0)  # type: ignore[attr-defined]

    homeassistant.config_entries = config_entries  # type: ignore[attr-defined]
    homeassistant.core = core  # type: ignore[attr-defined]
    homeassistant.helpers = helpers  # type: ignore[attr-defined]
    homeassistant.util = util  # type: ignore[attr-defined]
    helpers.selector = selector  # type: ignore[attr-defined]
    util.dt = dt  # type: ignore[attr-defined]

    return {
        "voluptuous": voluptuous,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.data_entry_flow": data_entry_flow,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.selector": selector,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt,
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

    def test_operator_result_labels_are_plain_russian(self) -> None:
        """Internal contract codes must not leak into Russian result screens."""

        self.assertEqual("готово", self.config_flow._russian_status("ready"))
        self.assertEqual("нет", self.config_flow._russian_yes_no(False))
        self.assertEqual(
            "данные о климате устарели; ещё не проверены обязательные действия",
            self.config_flow._russian_reasons(
                ["state_stale", "required_shadow_intents_missing"]
            ),
        )
        self.assertEqual(
            "установка температуры, выключение климата",
            self.config_flow._russian_actions(
                ["set_room_target", "turn_room_off"]
            ),
        )
        self.assertEqual(
            "неизвестная причина",
            self.config_flow._russian_reasons(["unexpected_internal_code"]),
        )

    def test_fixed_selectors_defer_visible_labels_to_translations(self) -> None:
        """A raw English label must not override the Russian selector text."""

        selectors = {
            "mode": self.config_flow.MODE_SELECTOR,
            "summary_update_interval": self.config_flow.SUMMARY_UPDATE_INTERVAL_SELECTOR,
            "climate_bridge_mode": self.config_flow.CLIMATE_BRIDGE_MODE_SELECTOR,
            "native_climate_mode": self.config_flow.NATIVE_CLIMATE_MODE_SELECTOR,
            "settings_section": self.config_flow.OPTIONS_SECTION_SELECTOR,
            "advanced_settings_action": (
                self.config_flow.ADVANCED_SETTINGS_ACTION_SELECTOR
            ),
            "contour_action": self.config_flow.CONTOUR_ACTION_SELECTOR,
            "contour_mode": self.config_flow.CONTOUR_MODE_SELECTOR,
            "contour_strategy": self.config_flow.CONTOUR_STRATEGY_SELECTOR,
            "climate_registry_action": self.config_flow.CLIMATE_REGISTRY_ACTION_SELECTOR,
            "climate_device_kind": self.config_flow.CLIMATE_DEVICE_KIND_SELECTOR,
            "climate_device_control_scope": self.config_flow.CLIMATE_DEVICE_SCOPE_SELECTOR,
            "climate_device_control_owner": self.config_flow.CLIMATE_DEVICE_OWNER_SELECTOR,
            "climate_device_capabilities": (
                self.config_flow.CLIMATE_DEVICE_CAPABILITIES_SELECTOR
            ),
        }
        for translation_key, selector in selectors.items():
            with self.subTest(translation_key=translation_key):
                self.assertEqual(translation_key, selector.config.translation_key)
                self.assertTrue(
                    all(isinstance(option, str) for option in selector.config.options)
                )

    def assert_section_form(self, schema: FakeSchema) -> None:
        """Verify the short first screen that only selects one settings area."""

        self.assertEqual(1, len(schema.fields))
        field, selector = next(iter(schema.fields.items()))
        self.assertIsInstance(field, FakeRequired)
        self.assertEqual("settings_section", field.key)
        self.assertEqual("contours", field.default)
        self.assertIsInstance(selector, FakeSelectSelector)
        self.assertEqual(
            [
                "contours",
                "general_settings",
                "advanced_settings",
            ],
            selector.config.options,
        )

    def assert_general_settings_fields(
        self,
        schema: FakeSchema,
        expected_mode_default: str,
        expected_local_page_default: bool,
        expected_summary_update_interval_default: str,
    ) -> FakeSelectSelector:
        """Verify the three fields used only for aggregate information."""

        self.assertEqual(3, len(schema.fields))
        fields = list(schema.fields.items())
        mode_field, mode_selector = fields[0]
        page_field, page_selector = fields[1]
        interval_field, interval_selector = fields[2]
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
            interval_selector.config.options,
        )
        self.assertEqual(
            "summary_update_interval",
            interval_selector.config.translation_key,
        )
        return mode_selector

    def assert_test_switch_fields(
        self,
        schema: FakeSchema,
        expected_enabled_default: bool,
        expected_target_default: str | None,
    ) -> None:
        """Verify the legacy helper test is isolated from climate settings."""

        self.assertEqual(2, len(schema.fields))
        (enabled_field, enabled_selector), (target_field, target_selector) = list(
            schema.fields.items()
        )
        self.assertIsInstance(enabled_field, FakeRequired)
        self.assertEqual("canary_control_enabled", enabled_field.key)
        self.assertEqual(expected_enabled_default, enabled_field.default)
        self.assertIsInstance(enabled_selector, FakeBooleanSelector)
        self.assertEqual("boolean", enabled_selector.selector_type)
        self.assertIsInstance(target_field, FakeOptional)
        self.assertEqual("canary_control_target", target_field.key)
        self.assertEqual(expected_target_default, target_field.default)
        self.assertIsInstance(target_selector, FakeEntitySelector)
        self.assertEqual("input_boolean", target_selector.config.domain)
        self.assertFalse(target_selector.config.multiple)

    def assert_climate_connection_fields(
        self,
        schema: FakeSchema,
        expected_mode_default: str,
    ) -> None:
        """Verify the connection screen asks only for the safe bridge stage."""

        self.assertEqual(1, len(schema.fields))
        bridge_mode_field, bridge_mode_selector = next(iter(schema.fields.items()))
        self.assertIsInstance(bridge_mode_field, FakeRequired)
        self.assertEqual("climate_bridge_mode", bridge_mode_field.key)
        self.assertEqual(expected_mode_default, bridge_mode_field.default)
        self.assertIsInstance(bridge_mode_selector, FakeSelectSelector)
        self.assertEqual(
            ["disabled", "shadow", "canary", "managed"],
            bridge_mode_selector.config.options,
        )

    def assert_climate_endpoint_fields(
        self,
        schema: FakeSchema,
        *,
        target_default: str | None,
        room_default: str | None = None,
        expect_room: bool,
    ) -> None:
        """Verify the room field exists only for one-room trial control."""

        self.assertEqual(2 if expect_room else 1, len(schema.fields))
        fields = list(schema.fields.items())
        target_field, target_validator = fields[0]
        self.assertIsInstance(target_field, FakeRequired)
        self.assertEqual("climate_bridge_target", target_field.key)
        self.assertEqual(target_default, target_field.default)
        self.assertIs(target_validator, str)
        if expect_room:
            room_field, room_validator = fields[1]
            self.assertIsInstance(room_field, FakeRequired)
            self.assertEqual("climate_canary_room_id", room_field.key)
            self.assertEqual(room_default, room_field.default)
            self.assertIs(room_validator, str)

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
            selector.config.options,
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
        """Only the approved mode may cross either HausmanHub form boundary."""

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
        section_result = await options_flow.async_step_init(
            {"settings_section": "general_settings", "unmodelled": "outside_contract"}
        )
        self.assertEqual("general_settings", section_result["step_id"])
        options_result = await options_flow.async_step_general_settings(extra_input)

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
        general_form = await options_flow.async_step_init(
            {"settings_section": "general_settings"}
        )
        shadow_result = await options_flow.async_step_general_settings({"mode": "shadow"})

        proxy_flow = self.config_flow.HausmanHubOptionsFlow()
        proxy_flow.config_entry = options_flow.config_entry
        proxy_result = await proxy_flow.async_step_general_settings({"mode": "proxy"})

        self.assertEqual("form", initial_form["type"])
        self.assert_section_form(initial_form["schema"])
        self.assert_general_settings_fields(
            general_form["schema"], "read-only", True, "5m"
        )
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

    async def test_options_menu_rejects_an_unknown_settings_area(self) -> None:
        """The short menu cannot be used to jump to an unapproved step."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )

        result = await options_flow.async_step_init(
            {"settings_section": "unknown_settings"}
        )

        self.assertEqual("form", result["type"])
        self.assert_section_form(result["schema"])
        self.assertEqual(
            {"settings_section": "unsafe_settings_section"},
            result["errors"],
        )

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
                general = await options_flow.async_step_general_settings()

                self.assertEqual("form", result["type"])
                self.assert_section_form(result["schema"])
                self.assert_general_settings_fields(
                    general["schema"], "read-only", True, "5m"
                )
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

        result = await options_flow.async_step_general_settings()

        self.assertEqual("form", result["type"])
        self.assert_general_settings_fields(result["schema"], "shadow", True, "5m")
        self.assertEqual(original_data, options_flow.config_entry.data)
        self.assertEqual(original_options, options_flow.config_entry.options)

    async def test_options_can_close_only_the_optional_local_page(self) -> None:
        """The new setting changes no mode, device, or home-facing input."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )

        result = await options_flow.async_step_general_settings(
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

        initial_form = await options_flow.async_step_general_settings()
        result = await options_flow.async_step_general_settings(
            {
                "mode": "shadow",
                "local_summary_enabled": False,
                "summary_update_interval": "30m",
                "canary_control_enabled": False,
                "climate_bridge_mode": "disabled",
            }
        )

        self.assert_general_settings_fields(
            initial_form["schema"], "shadow", False, "5m"
        )
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

        armed = await options_flow.async_step_test_switch(
            {
                "canary_control_enabled": True,
                "canary_control_target": "input_boolean.hausmanhub_canary",
            }
        )

        self.assertEqual("create_entry", armed["type"])
        self.assertEqual(
            {
                "mode": "read-only",
                "local_summary_enabled": True,
                "summary_update_interval": "5m",
                "canary_control_enabled": True,
                "canary_control_target": "input_boolean.hausmanhub_canary",
                "climate_bridge_mode": "disabled",
            },
            armed["data"],
        )

        options_flow.config_entry.options = dict(armed["data"])
        armed_form = await options_flow.async_step_test_switch()
        self.assert_test_switch_fields(
            armed_form["schema"],
            True,
            "input_boolean.hausmanhub_canary",
        )

        disarmed = await options_flow.async_step_test_switch(
            {
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
        connection = await options_flow.async_step_climate_connection()
        self.assert_climate_connection_fields(connection["schema"], "disabled")
        endpoint = await options_flow.async_step_climate_connection(
            {"climate_bridge_mode": "shadow"}
        )
        self.assert_climate_endpoint_fields(
            endpoint["schema"], target_default=None, expect_room=False
        )
        shadow = await options_flow.async_step_climate_endpoint(
            {"climate_bridge_target": "http://127.0.0.1:1880"}
        )
        self.assertEqual("create_entry", shadow["type"])
        self.assertEqual("shadow", shadow["data"]["climate_bridge_mode"])
        self.assertNotIn("climate_canary_room_id", shadow["data"])

        options_flow.config_entry.options = dict(shadow["data"])
        form = await options_flow.async_step_climate_connection()
        self.assert_climate_connection_fields(
            form["schema"],
            "shadow",
        )

        canary_endpoint = await options_flow.async_step_climate_connection(
            {"climate_bridge_mode": "canary"}
        )
        self.assert_climate_endpoint_fields(
            canary_endpoint["schema"],
            target_default="http://127.0.0.1:1880",
            expect_room=True,
        )
        canary = await options_flow.async_step_climate_endpoint(
            {
                "climate_bridge_target": "http://127.0.0.1:1880",
                "climate_canary_room_id": "living",
            }
        )
        self.assertEqual("create_entry", canary["type"])
        self.assertEqual("living", canary["data"]["climate_canary_room_id"])

        options_flow.config_entry.options = dict(canary["data"])
        disabled = await options_flow.async_step_climate_connection(
            {"climate_bridge_mode": "disabled"}
        )
        self.assertEqual("create_entry", disabled["type"])
        self.assertEqual("disabled", disabled["data"]["climate_bridge_mode"])
        self.assertNotIn("climate_bridge_target", disabled["data"])
        self.assertNotIn("climate_canary_room_id", disabled["data"])

    async def test_options_preview_one_room_native_climate_without_commands(self) -> None:
        """The built-in HausmanHub controller stores targets only after preview."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_ha_observations import (
            ClimateHaEntityState,
        )
        from custom_components.hausman_hub.application.climate_registry import (
            registry_from_payload,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from tests.test_climate_import import registry_payload, source_payload

        registry = registry_from_payload(registry_payload())

        class Store:
            async def async_load(self):
                return registry

            async def async_save(self, registry):
                raise AssertionError("native policy must not rewrite the registry")

        class Bridge:
            def __init__(self) -> None:
                self.executed = []
                self.fetch_count = 0

            async def async_fetch_state(self):
                self.fetch_count += 1
                return import_climate_state(source_payload())

            async def async_execute(self, plan):
                self.executed.append(plan)

        class StateView:
            def __init__(self) -> None:
                self.reads = 0

            def entity_state(
                self,
                entity_id: str,
            ) -> ClimateHaEntityState | None:
                self.reads += 1
                if entity_id != "climate.synthetic_living_ac":
                    return None
                return ClimateHaEntityState(
                    entity_id=entity_id,
                    state="cool",
                    attributes={"current_temperature": 25.8},
                    last_updated_ms=1784280000000,
                )

        bridge = Bridge()
        state_view = StateView()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.SHADOW,
            ),
            registry_store=Store(),
            bridge_client=bridge,
            ha_state_view=state_view,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_after_start = bridge.fetch_count
        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {
                "mode": "shadow",
                "climate_bridge_mode": "shadow",
                "climate_bridge_target": "http://127.0.0.1:1880",
            },
        )
        options_flow.hass = SimpleNamespace(  # type: ignore[attr-defined]
            data={"hausman_hub": {"climate_runtime": runtime}}
        )

        await options_flow.async_step_init(
            {"settings_section": "advanced_settings"}
        )
        mode_form = await options_flow.async_step_advanced_settings(
            {"advanced_settings_action": "native_climate"}
        )
        policy_form = await options_flow.async_step_native_climate(
            {"native_climate_mode": "preview"}
        )
        preview_form = await options_flow.async_step_native_climate_policy(
            {
                "native_climate_room_id": "living",
                "native_target_temperature": "22.0",
                "native_target_humidity": "45",
            }
        )

        self.assertEqual("native_climate", mode_form["step_id"])
        self.assertEqual("native_climate_policy", policy_form["step_id"])
        self.assertEqual("native_climate_confirm", preview_form["step_id"])
        self.assertEqual(
            "нужно охлаждать",
            preview_form["description_placeholders"]["temperature_decision"],
        )
        self.assertEqual("нет", preview_form["description_placeholders"]["commands"])
        self.assertGreater(state_view.reads, 0)
        self.assertEqual(fetches_after_start, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

        saved = await options_flow.async_step_native_climate_confirm(
            {"confirm_native_climate_preview": True}
        )

        self.assertEqual("create_entry", saved["type"])
        self.assertEqual("preview", saved["data"]["native_climate_mode"])
        self.assertEqual("living", saved["data"]["native_climate_room_id"])
        self.assertEqual(22.0, saved["data"]["native_target_temperature"])
        self.assertEqual(45, saved["data"]["native_target_humidity"])
        self.assertNotIn("commands_enabled", saved["data"])
        self.assertEqual([], bridge.executed)

        options_flow.config_entry.options = dict(saved["data"])
        general = await options_flow.async_step_general_settings(
            {
                "mode": "shadow",
                "local_summary_enabled": True,
                "summary_update_interval": "15m",
            }
        )
        self.assertEqual("preview", general["data"]["native_climate_mode"])
        self.assertEqual("living", general["data"]["native_climate_room_id"])

        options_flow.config_entry.options = dict(general["data"])
        disabled = await options_flow.async_step_native_climate(
            {"native_climate_mode": "disabled"}
        )
        self.assertNotIn("native_climate_mode", disabled["data"])
        self.assertNotIn("native_climate_room_id", disabled["data"])
        self.assertNotIn("native_target_temperature", disabled["data"])
        self.assertNotIn("native_target_humidity", disabled["data"])
        self.assertEqual([], bridge.executed)

    async def test_simple_contour_wizard_selects_existing_engine_devices(self) -> None:
        """The normal path creates one contour without technical registry fields."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.domain.climate import (
            ClimateRegistry,
            ClimateRoom,
        )
        from custom_components.hausman_hub.domain.climate_bridge import (
            ClimateBridgeMode,
            climate_bridge_target,
        )
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from custom_components.hausman_hub.domain.contours import ContourRegistry
        from tests.test_climate_import import source_payload
        from tests.test_climate_runtime import (
            ReflectingStrictExecutor,
            native_application_inputs,
        )

        class ClimateStore:
            def __init__(self) -> None:
                self.registry = ClimateRegistry()

            async def async_load(self):
                return self.registry

            async def async_save(self, registry):
                self.registry = registry

        class ContourStore:
            def __init__(self) -> None:
                self.registry = ContourRegistry()

            async def async_load(self):
                return self.registry

            async def async_save(self, registry):
                self.registry = registry

        class Bridge:
            def __init__(self) -> None:
                self.executed = []
                self.payload = source_payload()

            async def async_fetch_state(self):
                return import_climate_state(self.payload)

            async def async_execute(self, plan):
                self.executed.append(plan)
                room = self.payload["rooms"][0]
                if plan.action == "set_room_target_strategy":
                    room["targets"]["targetStrategy"] = plan.backend_payload[
                        "targetStrategy"
                    ]
                elif plan.action == "set_room_target":
                    room["targets"]["temperature"] = plan.backend_payload[
                        "targetTemperature"
                    ]
                elif plan.action == "set_room_mode":
                    room["mode"] = plan.backend_payload["mode"]

        from tests.test_climate_runtime import SnapshotStateView

        climate_store = ClimateStore()
        contour_store = ContourStore()
        bridge = Bridge()

        class BridgeView:
            def __init__(self, owner: object) -> None:
                self._owner = owner

            @property
            def snapshot(self):
                return import_climate_state(self._owner.payload)

        climate_store.registry = ClimateRegistry(
            rooms=(
                ClimateRoom("living", "Living room"),
                ClimateRoom("kids", "Kids"),
            ),
            devices=(),
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.MANAGED,
                climate_bridge_target=climate_bridge_target(
                    "http://127.0.0.1:1880"
                ),
            ),
            registry_store=climate_store,
            contour_store=contour_store,
            bridge_client=bridge,
            ha_state_view=SnapshotStateView(
                climate_store.registry, BridgeView(bridge)
            ),
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {
                "mode": "shadow",
                "climate_bridge_mode": "managed",
                "climate_bridge_target": "http://127.0.0.1:1880",
            },
        )
        options_flow.hass = SimpleNamespace(  # type: ignore[attr-defined]
            data={"hausman_hub": {"climate_runtime": runtime}}
        )

        contour_menu = await options_flow.async_step_init(
            {"settings_section": "contours"}
        )
        setup_form = await options_flow.async_step_contours(
            {"contour_action": "configure_climate"}
        )

        self.assertEqual("contours", contour_menu["step_id"])
        self.assertEqual("climate_contour_setup", setup_form["step_id"])
        fields = {
            marker.key: selector
            for marker, selector in setup_form["schema"].fields.items()
        }
        self.assertEqual(
            {
                "contour_name",
                "contour_mode",
                "contour_rooms",
                "contour_devices",
            },
            set(fields),
        )
        self.assertTrue(fields["contour_rooms"].config.multiple)
        self.assertTrue(fields["contour_devices"].config.multiple)
        self.assertNotIn(
            "synthetic-ac-source-living",
            str(fields["contour_devices"].config.options),
        )

        room_form = await options_flow.async_step_climate_contour_setup(
            {
                "contour_name": "Климат",
                "contour_mode": "automatic",
                "contour_rooms": ["living"],
                "contour_devices": ["device_001"],
            }
        )

        self.assertEqual("climate_contour_room", room_form["step_id"])
        self.assertEqual(
            "Living room",
            room_form["description_placeholders"]["room_name"],
        )
        self.assertEqual("1", room_form["description_placeholders"]["room_number"])
        self.assertEqual("1", room_form["description_placeholders"]["room_count"])
        # Unassigned native candidates are assigned to the room on this step.
        self.assertEqual(
            {
                "contour_target_temperature",
                "contour_target_humidity",
                "contour_strategy",
                "contour_room_devices",
            },
            {marker.key for marker in room_form["schema"].fields},
        )
        preview = await options_flow.async_step_climate_contour_room(
            {
                "contour_target_temperature": "25.0",
                "contour_target_humidity": "45",
                "contour_strategy": "normal",
                "contour_room_devices": ["climate.synthetic_ac_source_living"],
            }
        )

        self.assertEqual("climate_contour_confirm", preview["step_id"])
        # The honest native preview: HausmanHub's own automatic control is
        # not active until the contour is saved and HA state confirms it.
        self.assertEqual("нет", preview["description_placeholders"]["automatic"])
        self.assertEqual("День", preview["description_placeholders"]["active_profile"])
        self.assertIn(
            "Living room: 25 °C, 45 %, обычно",
            preview["description_placeholders"]["room_settings"],
        )
        self.assertEqual([], bridge.executed)
        saved = await options_flow.async_step_climate_contour_confirm(
            {"confirm_contour_save": True}
        )

        self.assertEqual("create_entry", saved["type"])
        self.assertEqual("managed", saved["data"]["climate_bridge_mode"])
        self.assertEqual(1, len(contour_store.registry.contours))
        self.assertEqual(1, len(climate_store.registry.rooms))
        self.assertEqual(1, len(climate_store.registry.devices))
        self.assertEqual([], bridge.executed)
        native_registry, state_view = native_application_inputs(
            climate_store.registry
        )
        climate_store.registry = native_registry
        runtime._registry = native_registry
        runtime._ha_state_view = state_view
        executor = ReflectingStrictExecutor(state_view)
        runtime._strict_ha_call_executor = executor

        options_flow.config_entry.options = dict(saved["data"])
        bridge.payload["rooms"][0]["mode"] = "manual"
        bridge.payload["rooms"][0]["targets"]["temperature"] = 26
        bridge.payload["rooms"][0]["targets"]["targetStrategy"] = "soft"
        edit_flow = self.config_flow.HausmanHubOptionsFlow()
        edit_flow.config_entry = FakeConfigEntry(
            options_flow.config_entry.data,
            dict(saved["data"]),
        )
        edit_flow.hass = options_flow.hass
        edit_setup = await edit_flow.async_step_contours(
            {"contour_action": "configure_climate"}
        )
        edit_defaults = {
            marker.key: marker.default
            for marker in edit_setup["schema"].fields
        }
        self.assertEqual(["living"], edit_defaults["contour_rooms"])
        self.assertEqual(["device_001"], edit_defaults["contour_devices"])
        edit_room = await edit_flow.async_step_climate_contour_setup(
            edit_defaults
        )
        room_defaults = {
            marker.key: marker.default
            for marker in edit_room["schema"].fields
        }
        self.assertEqual("25.0", room_defaults["contour_target_temperature"])
        self.assertEqual("45", room_defaults["contour_target_humidity"])
        self.assertEqual("normal", room_defaults["contour_strategy"])

        profile_flow = self.config_flow.HausmanHubOptionsFlow()
        profile_flow.config_entry = FakeConfigEntry(
            options_flow.config_entry.data,
            dict(saved["data"]),
        )
        profile_flow.hass = options_flow.hass
        day_profile = await profile_flow.async_step_contours(
            {"contour_action": "configure_profiles"}
        )
        self.assertEqual("climate_profiles_room", day_profile["step_id"])
        self.assertEqual("День", day_profile["description_placeholders"]["profile_name"])
        night_profile = await profile_flow.async_step_climate_profiles_room(
            {
                "contour_target_temperature": "24.5",
                "contour_target_humidity": "45",
                "contour_strategy": "normal",
            }
        )
        self.assertEqual("climate_profiles_room", night_profile["step_id"])
        self.assertEqual(
            "Ночь",
            night_profile["description_placeholders"]["profile_name"],
        )
        profiles_review = await profile_flow.async_step_climate_profiles_room(
            {
                "contour_target_temperature": "22.0",
                "contour_target_humidity": "40",
                "contour_strategy": "soft",
            }
        )
        self.assertEqual("climate_profiles_confirm", profiles_review["step_id"])
        self.assertIn(
            "день 24.5 °C, 45 %, обычно; ночь 22 °C, 40 %, мягко и тихо",
            profiles_review["description_placeholders"]["profile_settings"],
        )
        profiles_saved = await profile_flow.async_step_climate_profiles_confirm(
            {"confirm_profile_save": True}
        )
        self.assertEqual("create_entry", profiles_saved["type"])
        self.assertEqual([], bridge.executed)

        select_flow = self.config_flow.HausmanHubOptionsFlow()
        select_flow.config_entry = FakeConfigEntry(
            options_flow.config_entry.data,
            dict(saved["data"]),
        )
        select_flow.hass = options_flow.hass
        select_profile = await select_flow.async_step_contours(
            {"contour_action": "select_profile"}
        )
        self.assertEqual("climate_profile_select", select_profile["step_id"])
        select_review = await select_flow.async_step_climate_profile_select(
            {"contour_profile": "night"}
        )
        self.assertEqual(
            "climate_profile_select_confirm",
            select_review["step_id"],
        )
        self.assertEqual(
            "Ночь",
            select_review["description_placeholders"]["active_profile"],
        )
        selected = await select_flow.async_step_climate_profile_select_confirm(
            {"confirm_profile_select": True}
        )
        self.assertEqual("create_entry", selected["type"])
        room_policy = contour_store.registry.contours[0].rooms[0]
        self.assertEqual("night", room_policy.active_profile.value)
        self.assertEqual(22.0, room_policy.target_temperature)
        self.assertEqual([], bridge.executed)

        apply_form = await options_flow.async_step_contours(
            {"contour_action": "apply_climate"}
        )
        self.assertEqual("climate_contour_apply_confirm", apply_form["step_id"])
        self.assertEqual(
            "Ночь",
            apply_form["description_placeholders"]["active_profile"],
        )
        # Native strict HA plan call count, formerly the bridge command count.
        self.assertEqual("1", apply_form["description_placeholders"]["command_count"])
        refused = await options_flow.async_step_climate_contour_apply_confirm(
            {"confirm_contour_apply": False}
        )
        self.assertEqual(
            {"confirm_contour_apply": "contour_apply_confirmation_required"},
            refused["errors"],
        )
        result = await options_flow.async_step_climate_contour_apply_confirm(
            {"confirm_contour_apply": True}
        )
        self.assertEqual("climate_contour_apply_result", result["step_id"])
        self.assertEqual(
            "Выполнено",
            result["description_placeholders"]["status"],
        )
        self.assertEqual([], bridge.executed)
        self.assertEqual(1, len(executor.batches))
        self.assertGreater(state_view.read_count, 0)
        closed = await options_flow.async_step_climate_contour_apply_result(
            {"close_contour_apply_result": True}
        )
        self.assertEqual("create_entry", closed["type"])

        schedule_flow = self.config_flow.HausmanHubOptionsFlow()
        schedule_flow.config_entry = FakeConfigEntry(
            options_flow.config_entry.data,
            dict(saved["data"]),
        )
        schedule_flow.hass = options_flow.hass
        schedule_form = await schedule_flow.async_step_contours(
            {"contour_action": "configure_schedule"}
        )
        self.assertEqual("climate_schedule", schedule_form["step_id"])
        schedule_fields = {
            marker.key: (marker, selector)
            for marker, selector in schedule_form["schema"].fields.items()
        }
        self.assertEqual(
            {
                "climate_schedule_enabled",
                "climate_day_start",
                "climate_night_start",
                "confirm_climate_schedule",
            },
            set(schedule_fields),
        )
        self.assertEqual("07:00", schedule_fields["climate_day_start"][0].default)
        self.assertEqual("23:00", schedule_fields["climate_night_start"][0].default)
        refused_schedule = await schedule_flow.async_step_climate_schedule(
            {
                "climate_schedule_enabled": True,
                "climate_day_start": "07:00:00",
                "climate_night_start": "23:00:00",
                "confirm_climate_schedule": False,
            }
        )
        self.assertEqual(
            {
                "confirm_climate_schedule": (
                    "climate_schedule_confirmation_required"
                )
            },
            refused_schedule["errors"],
        )
        schedule_saved = await schedule_flow.async_step_climate_schedule(
            {
                "climate_schedule_enabled": True,
                "climate_day_start": "07:00:00",
                "climate_night_start": "23:00:00",
                "confirm_climate_schedule": True,
            }
        )
        self.assertEqual("create_entry", schedule_saved["type"])
        self.assertTrue(contour_store.registry.contours[0].schedule.enabled)
        self.assertEqual("07:00", contour_store.registry.contours[0].schedule.day_start)
        self.assertEqual(1, len(executor.batches))

        reads_before_schedule = state_view.read_count
        await runtime.async_run_climate_schedule(datetime(2026, 7, 19, 12, 0))
        self.assertGreater(state_view.read_count, reads_before_schedule)
        self.assertEqual(1, len(executor.batches))
        temporary_flow = self.config_flow.HausmanHubOptionsFlow()
        temporary_flow.config_entry = schedule_flow.config_entry
        temporary_flow.hass = options_flow.hass
        room_choice = await temporary_flow.async_step_contours(
            {"contour_action": "temporary_temperature"}
        )
        self.assertEqual("temporary_temperature_room", room_choice["step_id"])
        temperature_form = await temporary_flow.async_step_temporary_temperature_room(
            {"temporary_temperature_room": "living"}
        )
        self.assertEqual("temporary_temperature", temperature_form["step_id"])
        self.assertEqual(
            "Living room",
            temperature_form["description_placeholders"]["room_name"],
        )
        reads_before_temporary = state_view.read_count
        temporary_result = await temporary_flow.async_step_temporary_temperature(
            {
                "temporary_temperature": 23.5,
                "confirm_temporary_temperature": True,
            }
        )
        self.assertEqual("temporary_temperature_result", temporary_result["step_id"])
        self.assertEqual(
            "23.5",
            temporary_result["description_placeholders"]["current_temperature"],
        )
        self.assertEqual(
            "Временно изменить температуру",
            temporary_result["description_placeholders"]["action"],
        )
        self.assertEqual(
            "Выполнено",
            temporary_result["description_placeholders"]["status"],
        )
        self.assertGreater(state_view.read_count, reads_before_temporary)
        self.assertEqual(1, len(executor.batches))
        room_policy = contour_store.registry.contours[0].rooms[0]
        self.assertEqual(23.5, room_policy.target_temperature)
        self.assertEqual(24.5, room_policy.profile_settings.target_temperature)
        await temporary_flow.async_step_temporary_temperature_result(
            {"close_temporary_temperature_result": True}
        )

        return_flow = self.config_flow.HausmanHubOptionsFlow()
        return_flow.config_entry = schedule_flow.config_entry
        return_flow.hass = options_flow.hass
        return_room = await return_flow.async_step_contours(
            {"contour_action": "return_to_schedule"}
        )
        self.assertEqual("temporary_temperature_room", return_room["step_id"])
        return_form = await return_flow.async_step_temporary_temperature_room(
            {"temporary_temperature_room": "living"}
        )
        self.assertEqual("temporary_temperature_clear", return_form["step_id"])
        reads_before_return = state_view.read_count
        return_result = await return_flow.async_step_temporary_temperature_clear(
            {"confirm_temporary_temperature_clear": True}
        )
        self.assertEqual("temporary_temperature_result", return_result["step_id"])
        self.assertEqual(
            "24.5",
            return_result["description_placeholders"]["current_temperature"],
        )
        self.assertEqual(
            "Вернуть температуру по расписанию",
            return_result["description_placeholders"]["action"],
        )
        self.assertEqual(
            "Выполнено",
            return_result["description_placeholders"]["status"],
        )
        self.assertGreater(state_view.read_count, reads_before_return)
        self.assertEqual(1, len(executor.batches))
        self.assertIsNone(contour_store.registry.contours[0].rooms[0].temporary_override)

        blocked_manual_flow = self.config_flow.HausmanHubOptionsFlow()
        blocked_manual_flow.config_entry = schedule_flow.config_entry
        blocked_manual_flow.hass = options_flow.hass
        blocked_manual = await blocked_manual_flow.async_step_contours(
            {"contour_action": "select_profile"}
        )
        self.assertEqual({"base": "schedule_controls_profile"}, blocked_manual["errors"])

        disabled = await options_flow.async_step_contours(
            {"contour_action": "disable_climate"}
        )

        self.assertEqual("create_entry", disabled["type"])
        self.assertEqual("disabled", disabled["data"]["climate_bridge_mode"])
        self.assertNotIn("climate_bridge_target", disabled["data"])
        self.assertEqual("disabled", contour_store.registry.contours[0].mode.value)
        self.assertEqual([], bridge.executed)
        self.assertEqual(1, len(executor.batches))

    async def test_contour_wizard_collects_each_room_parameters_separately(
        self,
    ) -> None:
        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from tests.test_climate_import import source_payload

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        snapshot = import_climate_state(source_payload())
        options_flow._contour_source_target = "http://127.0.0.1:1880"
        options_flow._set_contour_source_snapshot(snapshot)

        living = await options_flow.async_step_climate_contour_setup(
            {
                "contour_name": "Климат",
                "contour_mode": "automatic",
                "contour_rooms": ["living", "kids"],
                "contour_devices": ["device_001", "device_002"],
            }
        )

        self.assertEqual("climate_contour_room", living["step_id"])
        self.assertEqual("Living room", living["description_placeholders"]["room_name"])
        invalid = await options_flow.async_step_climate_contour_room(
            {
                "contour_target_temperature": "25.0",
                "contour_target_humidity": "45",
                "contour_strategy": "normal",
                "hidden": "must-not-pass",
            }
        )
        self.assertEqual(
            {"base": "invalid_contour_room_parameters"},
            invalid["errors"],
        )
        kids = await options_flow.async_step_climate_contour_room(
            {
                "contour_target_temperature": "25.0",
                "contour_target_humidity": "45",
                "contour_strategy": "normal",
            }
        )

        self.assertEqual("climate_contour_room", kids["step_id"])
        self.assertEqual("Kids", kids["description_placeholders"]["room_name"])
        self.assertEqual("2", kids["description_placeholders"]["room_number"])
        await options_flow.async_step_climate_contour_room(
            {
                "contour_target_temperature": "23.5",
                "contour_target_humidity": "50",
                "contour_strategy": "soft",
            }
        )

        placeholders = options_flow._contour_preview_placeholders()
        self.assertEqual("День", placeholders["active_profile"])
        self.assertIn(
            "Living room: 25 °C, 45 %, обычно",
            placeholders["room_settings"],
        )
        self.assertIn(
            "Kids: 23.5 °C, 50 %, мягко и тихо",
            placeholders["room_settings"],
        )
        draft = options_flow._contour_definition_draft
        rooms = draft["contours"][0]["rooms"]  # type: ignore[index]
        by_room = {room["room_id"]: room for room in rooms}
        self.assertEqual(
            25.0,
            by_room["living"]["profiles"]["day"]["target_temperature"],
        )
        self.assertEqual(
            23.5,
            by_room["kids"]["profiles"]["day"]["target_temperature"],
        )
        self.assertEqual(
            "soft",
            by_room["kids"]["profiles"]["day"]["strategy"],
        )

    async def test_options_reject_unsafe_canary_values(self) -> None:
        """No missing target, other entity domain, or truth-like arm value passes."""

        options_flow = self.config_flow.HausmanHubOptionsFlow()
        options_flow.config_entry = FakeConfigEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )
        for invalid_input, expected_error in (
            (
                {"canary_control_enabled": "true"},
                {"canary_control_enabled": "unsafe_canary_control_setting"},
            ),
            (
                {"canary_control_enabled": True},
                {"canary_control_target": "unsafe_canary_control_target"},
            ),
            (
                {
                    "canary_control_enabled": True,
                    "canary_control_target": "light.kitchen",
                },
                {"canary_control_target": "unsafe_canary_control_target"},
            ),
        ):
            with self.subTest(invalid_input=invalid_input):
                result = await options_flow.async_step_test_switch(invalid_input)
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
                result = await options_flow.async_step_general_settings(
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
                result = await options_flow.async_step_general_settings(
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

        await options_flow.async_step_init(
            {"settings_section": "advanced_settings"}
        )
        editor = await options_flow.async_step_advanced_settings(
            {"advanced_settings_action": "climate_registry"}
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

        from tests.test_climate_runtime import SnapshotStateView

        store = Store()
        bridge = Bridge()

        class BridgeView:
            def __init__(self) -> None:
                self.snapshot = import_climate_state(source_payload())

        store.registry = ClimateRegistry(
            rooms=(
                __import__(
                    "custom_components.hausman_hub.domain.climate",
                    fromlist=["ClimateRoom"],
                ).ClimateRoom("living", "Living room"),
                __import__(
                    "custom_components.hausman_hub.domain.climate",
                    fromlist=["ClimateRoom"],
                ).ClimateRoom("kids", "Kids"),
            ),
            devices=(),
        )
        state_view = SnapshotStateView(store.registry, BridgeView())
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.SHADOW,
            ),
            registry_store=store,
            bridge_client=bridge,
            ha_state_view=state_view,
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

        await options_flow.async_step_init({"settings_section": "advanced_settings"})
        await options_flow.async_step_advanced_settings(
            {"advanced_settings_action": "climate_registry"}
        )
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
        # Native candidates are unassigned: the wizard must ask for the room.
        self.assertIn("climate_device_room", ac_fields)
        self.assertEqual(
            "climate",
            ac_fields["climate_device_control_entity"].config.domain,
        )
        menu_after_ac = await options_flow.async_step_climate_import_device(
            {
                "climate_device_id": "living_ac",
                "climate_device_name": "Living AC",
                "climate_device_kind": "air_conditioner",
                "climate_device_room": "living",
                "climate_device_control_scope": "canary",
                "climate_device_control_owner": "climate_core",
                "climate_device_control_entity": "climate.synthetic_ac_source_living",
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
                "climate_device_room": "kids",
                "climate_device_control_scope": "observed",
                "climate_device_control_owner": "observed",
                "climate_device_control_entity": "humidifier.synthetic_humidifier_source_kids",
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
        # Native candidates receive a fresh private source id on import;
        # the entity id itself is never copied into the registry binding.
        self.assertEqual(
            [
                "hausmanhub-native-climate.synthetic_ac_source_living",
                "hausmanhub-native-humidifier.synthetic_humidifier_source_kids",
            ],
            [device.source_id for device in store.saved[0].devices],
        )
        self.assertEqual(
            ["climate.synthetic_ac_source_living",
             "humidifier.synthetic_humidifier_source_kids"],
            [
                device.endpoints[0].entity_id
                for device in store.saved[0].devices
            ],
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

        await options_flow.async_step_init({"settings_section": "advanced_settings"})
        await options_flow.async_step_advanced_settings(
            {"advanced_settings_action": "climate_registry"}
        )
        candidate_form = await options_flow.async_step_climate_registry(
            {"climate_registry_action": "review_shadow_evidence"}
        )
        evidence_form = await options_flow.async_step_climate_shadow_candidate(
            {"climate_shadow_candidate_room": "living"}
        )

        self.assertEqual("climate_shadow_candidate", candidate_form["step_id"])
        self.assertEqual("climate_shadow_evidence", evidence_form["step_id"])
        self.assertEqual(
            "Living room",
            evidence_form["description_placeholders"]["room_id"],
        )
        self.assertEqual(
            "нужно больше наблюдений",
            evidence_form["description_placeholders"]["status"],
        )
        self.assertEqual([], store.saved)
        self.assertEqual([], bridge.executed)
        returned = await options_flow.async_step_climate_shadow_evidence(
            {"close_shadow_evidence": True}
        )
        self.assertEqual("climate_registry", returned["step_id"])

    async def test_options_show_complete_canary_preflight_without_activation(
        self,
    ) -> None:
        """One screen combines only redacted rollout checks and cannot save."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_registry import (
            registry_from_payload,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from tests.test_climate_import import complete_registry_payload, source_payload
        from tests.test_climate_runtime import ready_evidence_store

        registry = complete_registry_payload()

        class Store:
            def __init__(self) -> None:
                self.saved = []

            async def async_load(self):
                return registry_from_payload(registry)

            async def async_save(self, value):
                self.saved.append(value)

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
            evidence_store=ready_evidence_store(registry),
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

        await options_flow.async_step_init({"settings_section": "advanced_settings"})
        await options_flow.async_step_advanced_settings(
            {"advanced_settings_action": "climate_registry"}
        )
        options_flow._registry_draft = {  # type: ignore[attr-defined]
            **registry,
            "rooms": [
                *registry["rooms"],  # type: ignore[union-attr]
                {"id": "draft_only", "name": "Unsaved room"},
            ],
        }
        candidate = await options_flow.async_step_climate_registry(
            {"climate_registry_action": "review_canary_preflight"}
        )
        candidate_selector = next(iter(candidate["schema"].fields.values()))
        self.assertNotIn("draft_only", str(candidate_selector.config.options))
        result = await options_flow.async_step_climate_preflight_candidate(
            {"climate_preflight_room": "living"}
        )

        self.assertEqual("climate_preflight_candidate", candidate["step_id"])
        self.assertEqual("climate_canary_preflight", result["step_id"])
        placeholders = result["description_placeholders"]
        self.assertEqual("Living room", placeholders["room_id"])
        self.assertEqual("готово", placeholders["status"])
        self.assertEqual("да", placeholders["registry_matches"])
        self.assertEqual("нет", placeholders["operation"])
        self.assertEqual("готово", placeholders["rollback"])
        self.assertIn("установка температуры", placeholders["scope"])
        serialized = str(placeholders)
        self.assertNotIn("set_room_target", serialized)
        self.assertNotIn("turn_room_off", serialized)
        self.assertNotIn("synthetic-ac-source", serialized)
        self.assertNotIn("climate.synthetic", serialized)
        self.assertEqual([], store.saved)
        self.assertEqual([], bridge.executed)
        returned = await options_flow.async_step_climate_canary_preflight(
            {"close_canary_preflight": True}
        )
        self.assertEqual("climate_registry", returned["step_id"])


if __name__ == "__main__":
    unittest.main()
