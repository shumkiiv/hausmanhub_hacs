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

    def __init__(self, key: str, *, default: str) -> None:
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
        self.assert_mode_field(initial_form["schema"], "read-only")
        self.assertEqual("create_entry", shadow_result["type"])
        self.assertEqual({"mode": "shadow"}, shadow_result["data"])
        self.assertEqual("form", proxy_result["type"])
        self.assertEqual({"mode": "unsafe_mode"}, proxy_result["errors"])


if __name__ == "__main__":
    unittest.main()
