"""Contract tests for the HausmanHub sidebar panel (roadmap item 37)."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PANEL_JS = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "frontend"
    / "hausman-hub-panel.js"
)
MAX_PANEL_JS_BYTES = 48 * 1024


class PanelJavaScriptContractTest(unittest.TestCase):
    """The panel module stays self-contained and loadable."""

    def test_panel_script_exists_and_stays_bounded(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")

        self.assertLessEqual(len(content.encode("utf-8")), MAX_PANEL_JS_BYTES)
        self.assertIn('customElements.define("hausman-hub-panel"', content)

    def test_panel_script_uses_only_relative_local_api_paths(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")

        self.assertIn('"hausman_hub/v1/admin/panel"', content)
        for forbidden in (
            "http://",
            "https://",
            "//cdn",
            "eval(",
            "document.write",
            "import(",
            "XMLHttpRequest",
            "WebSocket",
            "localStorage",
            "sessionStorage",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, content)

    def test_panel_script_posts_only_to_the_admin_panel_routes(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")

        self.assertIn('`${PANEL_API}/apply`', content)
        self.assertIn('`${PANEL_API}/temporary-temperature`', content)
        self.assertNotIn("/api/hausman_hub/v1/actions", content)
        self.assertNotIn("climate-drafts", content)


class PanelRegistrationTest(unittest.TestCase):
    """Setup registers exactly one static path and one sidebar panel."""

    def setUp(self) -> None:
        self.previous_modules = {
            name: sys.modules.get(name)
            for name in (
                "homeassistant",
                "homeassistant.components",
                "homeassistant.components.http",
                "homeassistant.components.frontend",
                "homeassistant.components.panel_custom",
                "custom_components.hausman_hub.panel",
            )
        }
        for name in self.previous_modules:
            sys.modules.pop(name, None)

        homeassistant = ModuleType("homeassistant")
        components = ModuleType("homeassistant.components")
        http = ModuleType("homeassistant.components.http")
        frontend = ModuleType("homeassistant.components.frontend")
        panel_custom = ModuleType("homeassistant.components.panel_custom")

        class StaticPathConfig:
            def __init__(self, url_path: str, path: str, cache_headers: bool) -> None:
                self.url_path = url_path
                self.path = path
                self.cache_headers = cache_headers

        http.StaticPathConfig = StaticPathConfig  # type: ignore[attr-defined]
        self.registered_panels: list[dict[str, object]] = []
        self.removed_panels: list[tuple[str, bool]] = []
        self.existing_panels: set[str] = set()
        async def register_panel(hass, **kwargs):
            self._register_panel(kwargs)

        panel_custom.async_register_panel = register_panel  # type: ignore[attr-defined]

        def remove_panel(hass, url_path, *, warn_if_unknown=True):
            self.removed_panels.append((url_path, warn_if_unknown))
            self.existing_panels.discard(url_path)

        frontend.async_remove_panel = remove_panel  # type: ignore[attr-defined]
        frontend.async_panel_exists = (  # type: ignore[attr-defined]
            lambda hass, url_path: url_path in self.existing_panels
        )
        homeassistant.components = components  # type: ignore[attr-defined]
        components.http = http  # type: ignore[attr-defined]
        components.frontend = frontend  # type: ignore[attr-defined]
        components.panel_custom = panel_custom  # type: ignore[attr-defined]
        sys.modules.update(
            {
                "homeassistant": homeassistant,
                "homeassistant.components": components,
                "homeassistant.components.http": http,
                "homeassistant.components.frontend": frontend,
                "homeassistant.components.panel_custom": panel_custom,
            }
        )
        self.panel = importlib.import_module("custom_components.hausman_hub.panel")

    def tearDown(self) -> None:
        for name in self.previous_modules:
            sys.modules.pop(name, None)
        sys.modules.update(
            {
                name: module
                for name, module in self.previous_modules.items()
                if module is not None
            }
        )

    def _register_panel(self, kwargs: dict[str, object]) -> None:
        url_path = kwargs["frontend_url_path"]
        if url_path in self.existing_panels:
            raise ValueError(f"Overwriting panel {url_path}")
        self.existing_panels.add(url_path)  # type: ignore[arg-type]
        self.registered_panels.append(kwargs)

    def _hass(self, static_configs: list[object]) -> object:
        return SimpleNamespace(
            data={},
            http=SimpleNamespace(
                async_register_static_paths=lambda configs: _record(
                    static_configs, configs
                )
            ),
        )

    def test_register_adds_one_static_path_and_one_panel(self) -> None:
        static_configs: list[object] = []
        hass = self._hass(static_configs)

        asyncio.run(self.panel.async_register_hausmanhub_panel(hass))

        self.assertEqual(1, len(static_configs))
        config = static_configs[0]
        self.assertEqual("/api/hausman_hub/panel", config.url_path)
        self.assertTrue(config.path.endswith("frontend"))
        self.assertFalse(config.cache_headers)
        self.assertEqual(1, len(self.registered_panels))
        self.assertEqual(
            {
                "frontend_url_path": "hausman-hub",
                "webcomponent_name": "hausman-hub-panel",
                "sidebar_title": "HausmanHub",
                "sidebar_icon": "mdi:thermostat",
                "module_url": "/api/hausman_hub/panel/hausman-hub-panel.js",
                "require_admin": True,
                "config_panel_domain": "hausman_hub",
            },
            self.registered_panels[0],
        )

    def test_unregister_removes_the_panel_without_warnings(self) -> None:
        self.panel.unregister_hausmanhub_panel(SimpleNamespace())

        self.assertEqual([("hausman-hub", False)], self.removed_panels)

    def test_repeated_setup_registers_statics_and_panel_only_once(self) -> None:
        static_configs: list[object] = []
        hass = self._hass(static_configs)

        asyncio.run(self.panel.async_register_hausmanhub_panel(hass))
        asyncio.run(self.panel.async_register_hausmanhub_panel(hass))

        self.assertEqual(1, len(static_configs))
        self.assertEqual(1, len(self.registered_panels))

    def test_setup_after_unload_registers_the_panel_again_not_statics(self) -> None:
        static_configs: list[object] = []
        hass = self._hass(static_configs)

        asyncio.run(self.panel.async_register_hausmanhub_panel(hass))
        self.panel.unregister_hausmanhub_panel(hass)
        asyncio.run(self.panel.async_register_hausmanhub_panel(hass))

        self.assertEqual(1, len(static_configs))
        self.assertEqual(2, len(self.registered_panels))
        self.assertEqual([("hausman-hub", False)], self.removed_panels)


async def _record(target: list[object], configs: list[object]) -> None:
    target.extend(configs)
