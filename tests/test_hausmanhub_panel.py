"""Contract tests for the HausmanHub sidebar panel (roadmap item 37)."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import subprocess
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
PANEL_CSS = PANEL_JS.with_name("hausman-hub-panel.css")
MAX_PANEL_JS_BYTES = 230 * 1024
MAX_PANEL_CSS_BYTES = 48 * 1024


class PanelJavaScriptContractTest(unittest.TestCase):
    """The local panel assets stay bounded and loadable."""

    def test_panel_script_exists_and_stays_bounded(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")

        self.assertLessEqual(len(content.encode("utf-8")), MAX_PANEL_JS_BYTES)
        self.assertIn('customElements.get?.("hausman-hub-panel")', content)
        self.assertIn('customElements.define("hausman-hub-panel"', content)

    def test_panel_styles_are_local_and_stay_bounded(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")
        styles = PANEL_CSS.read_text(encoding="utf-8")

        self.assertLessEqual(len(styles.encode("utf-8")), MAX_PANEL_CSS_BYTES)
        self.assertIn('"/api/hausman_hub/panel/hausman-hub-panel.css"', content)
        self.assertIn("--hmh-bg:#0B0F14", styles)
        self.assertIn("--hmh-bg:#EEF1F6", styles)
        self.assertIn(".page-header", styles)

    def test_overview_climate_and_scenarios_share_tablet_visual_contract(self) -> None:
        styles = PANEL_CSS.read_text(encoding="utf-8")

        for rule in (
            ".overview-hero-copy .hero-status",
            "font-size:clamp(30px,3vw,36px)",
            ".overview-hero-metric:first-child",
            "grid-template-columns:1fr 1fr 1.35fr 1fr",
            ".climate-subnav",
            "min-height:40px",
            ".contour-config-card, .contour-state-card",
            ".scenarios-card",
            ".scenario-row",
            "grid-template-columns:48px minmax(0,1fr) auto",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, styles)

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

    def test_panel_script_posts_only_to_approved_admin_routes(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")

        for approved in (
            '"hausman_hub/v1/admin/panel"',
            '"hausman_hub/v1/admin/climate-mode"',
            '"hausman_hub/v1/admin/home-environment"',
            '"hausman_hub/v1/admin/climate-room-signals"',
            '"hausman_hub/v1/admin/climate-drafts/current"',
            '"hausman_hub/v1/admin/climate-profiles"',
            '"hausman_hub/v1/admin/climate-schedule"',
            '"hausman_hub/v1/admin/ai-assistant"',
            '"hausman_hub/v1/admin/scenarios"',
            '"hausman_hub/v1/admin/scenarios/catalog"',
            '"hausman_hub/v1/admin/scenarios/test"',
            '"hausman_hub/v1/admin/scenarios/delete"',
            '"hausman_hub/v1/admin/scenarios/run"',
            '"hausman_hub/v1/admin/connection-settings"',
        ):
            with self.subTest(approved=approved):
                self.assertIn(approved, content)
        self.assertIn('`${PANEL_API}/apply`', content)
        self.assertIn('`${PANEL_API}/temporary-temperature`', content)
        self.assertIn('`${AI_ASSISTANT_API}/settings`', content)
        self.assertIn('`${AI_ASSISTANT_API}/refresh`', content)
        for retired in (
            "/api/hausman_hub/v1/actions",
            "climate-shadow-evidence",
            "climate-canary-preflight",
            "climate-registry",
            "climate-import",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, content)

    def test_panel_script_tolerates_an_unavailable_climate_snapshot(self) -> None:
        script = f"""
          const fs = require("fs");
          const vm = require("vm");

          class FakeElement {{
            constructor(tag = "element") {{
              this.tagName = tag.toUpperCase();
              this.children = [];
              this.className = "";
              this.textContent = "";
              this.disabled = false;
              this.style = {{}};
            }}
            appendChild(child) {{
              this.children.push(child);
              return child;
            }}
            addEventListener() {{}}
            set innerHTML(value) {{
              if (value === "") this.children = [];
            }}
          }}

          global.document = {{
            hidden: false,
            createElement: (tag) => new FakeElement(tag),
            createElementNS: (ns, tag) => new FakeElement(tag),
            addEventListener() {{}},
            removeEventListener() {{}},
          }};
          global.HTMLElement = class {{
            attachShadow() {{
              this.shadowRoot = new FakeElement("shadow-root");
              return this.shadowRoot;
            }}
          }};
          const registry = new Map();
          global.customElements = {{
            define: (name, value) => registry.set(name, value),
          }};
          vm.runInThisContext(
            fs.readFileSync({str(PANEL_JS)!r}, "utf8"),
            {{ filename: {str(PANEL_JS)!r} }}
          );

          const Panel = registry.get("hausman-hub-panel");
          const panel = new Panel();
          panel._data = {{
            contract: {{ name: "hausman-hub-admin-panel", version: 2 }},
            integration_version: "1.26.1",
            snapshot: null,
            readiness: {{
              status: "disabled",
              bridge_mode: "disabled",
              reasons: ["bridge_disabled"],
            }},
          }};
          panel._render();

          const nodes = [];
          const visit = (node) => {{
            nodes.push(node);
            node.children.forEach(visit);
          }};
          visit(panel.shadowRoot);
          const visible = [];
          const visitVisible = (node) => {{
            if (node.hidden) return;
            visible.push(node);
            node.children.forEach(visitVisible);
          }};
          visitVisible(panel.shadowRoot);
          const text = visible.map((node) => node.textContent).join("\\n");
          if (!text.includes("Обзор")) throw new Error("overview heading missing");
          if (!text.includes("Управление климатом выключено")) {{
            throw new Error("disabled readiness missing");
          }}
          if (!text.includes("Версия 1.26.1")) {{
            throw new Error("integration version badge missing");
          }}
          if (text.includes("Климатический контур")) {{
            throw new Error("contour rendered without snapshot");
          }}
          const tabs = visible.filter((node) => String(node.className).split(" ").includes("tab"));
          if (tabs.length !== 4) throw new Error("four canonical tabs missing");
          const tabLabels = tabs.map((node) => node.textContent).join("|");
          if (tabLabels !== "Обзор|Климат|Сценарии|Настройки") {{
            throw new Error("canonical tab order mismatch: " + tabLabels);
          }}
          const marks = visible.filter((node) => String(node.className).split(" ").includes("brand-mark"));
          if (marks.length !== 1) throw new Error("HausmanHub brand mark missing");
          if (visible.some((node) => (
            node.tagName === "BUTTON"
            && !String(node.className).split(" ").includes("tab")
            && !String(node.className).split(" ").includes("theme-switch")
          ))) {{
            throw new Error("climate action rendered without settings");
          }}
        """
        completed = subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)


THEME_TEST_HARNESS = """
  const fs = require("fs");
  const vm = require("vm");

  class FakeElement {
    constructor(tag = "element") {
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.className = "";
      this.textContent = "";
      this.disabled = false;
      this.style = {};
      this._listeners = {};
      const classes = new Set();
      this.classList = {
        toggle: (name, force) => {
          const should = force === undefined ? !classes.has(name) : !!force;
          if (should) classes.add(name); else classes.delete(name);
          return should;
        },
        contains: (name) => classes.has(name),
        add: (name) => classes.add(name),
        remove: (name) => classes.delete(name),
      };
    }
    appendChild(child) {
      this.children.push(child);
      return child;
    }
    addEventListener(type, handler) {
      (this._listeners[type] = this._listeners[type] || []).push(handler);
    }
    click() {
      (this._listeners.click || []).forEach((handler) => handler({}));
    }
    set innerHTML(value) {
      if (value === "") this.children = [];
    }
  }

  global.document = {
    hidden: false,
    createElement: (tag) => new FakeElement(tag),
    createElementNS: (ns, tag) => new FakeElement(tag),
    addEventListener() {},
    removeEventListener() {},
  };
  global.HTMLElement = class extends FakeElement {
    attachShadow() {
      this.shadowRoot = new FakeElement("shadow-root");
      return this.shadowRoot;
    }
  };
  const registry = new Map();
  global.customElements = {
    define: (name, value) => registry.set(name, value),
  };
  vm.runInThisContext(
    fs.readFileSync(__PANEL_JS__, "utf8"),
    { filename: __PANEL_JS__ }
  );

  const Panel = registry.get("hausman-hub-panel");
  const pendingHass = (darkMode) => ({
    themes: { darkMode },
    callApi: () => new Promise(() => {}),
  });
"""


class PanelThemeSwitcherTest(unittest.TestCase):
    """The panel cycles auto/light/dark and follows the HA theme in auto mode."""

    def _run_script(self, body: str) -> subprocess.CompletedProcess[str]:
        script = THEME_TEST_HARNESS.replace("__PANEL_JS__", repr(str(PANEL_JS))) + body
        return subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )

    def test_auto_mode_follows_hass_dark_mode(self) -> None:
        completed = self._run_script(
            """
  const panel = new Panel();
  panel._render();
  if (panel._themeMode !== "auto") throw new Error("default mode must be auto");
  panel.hass = pendingHass(true);
  if (panel.classList.contains("theme-light")) {
    throw new Error("auto mode with darkMode=true must stay dark");
  }
  panel.hass = pendingHass(false);
  if (!panel.classList.contains("theme-light")) {
    throw new Error("auto mode with darkMode=false must switch to light");
  }
  panel.hass = pendingHass(true);
  if (panel.classList.contains("theme-light")) {
    throw new Error("auto mode must react to hass theme changes");
  }
            """
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_switcher_cycles_modes_and_ignores_hass_when_explicit(self) -> None:
        completed = self._run_script(
            """
  const panel = new Panel();
  panel._render();
  panel.hass = pendingHass(true);
  const button = panel._shell.themeButton;
  if (!button) throw new Error("theme switcher missing");
  const state = () => ({
    mode: panel._themeMode,
    light: panel.classList.contains("theme-light"),
    label: button["aria-label"],
    hint: button.children.length > 1 ? button.children[1].textContent : "",
  });
  let s = state();
  if (s.mode !== "auto" || s.light) throw new Error("initial auto+dark expected");
  if (s.label !== "Тема: авто (следует Home Assistant)") {
    throw new Error("auto aria-label mismatch: " + s.label);
  }
  if (s.hint !== "авто") throw new Error("auto hint missing");
  button.click();
  s = state();
  if (s.mode !== "light" || !s.light || s.label !== "Тема: светлая") {
    throw new Error("auto -> light cycle failed");
  }
  panel.hass = pendingHass(false);
  if (!panel.classList.contains("theme-light")) {
    throw new Error("explicit light must ignore hass darkMode");
  }
  button.click();
  s = state();
  if (s.mode !== "dark" || s.light || s.label !== "Тема: тёмная") {
    throw new Error("light -> dark cycle failed");
  }
  panel.hass = pendingHass(false);
  if (panel.classList.contains("theme-light")) {
    throw new Error("explicit dark must ignore hass darkMode");
  }
  button.click();
  s = state();
  if (s.mode !== "auto" || !s.light) {
    throw new Error("dark -> auto cycle failed, auto must follow hass again");
  }
            """
        )

        self.assertEqual(0, completed.returncode, completed.stderr)


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
        self.executor_jobs: list[object] = []
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
        async def run_executor_job(target, *args):
            self.executor_jobs.append(target)
            return target(*args)

        return SimpleNamespace(
            data={},
            async_add_executor_job=run_executor_job,
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
                "module_url": "/api/hausman_hub/panel/hausman-hub-panel.js?v=1.44.9",
                "require_admin": True,
                "config_panel_domain": "hausman_hub",
            },
            self.registered_panels[0],
        )
        self.assertEqual([self.panel._panel_module_url], self.executor_jobs)

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
