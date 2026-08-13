"""Theme and accessibility contract for nested HACS panel windows.

Covers the 2026-08-11 handoff: shared modal tokens, themed backdrops and
    shadows, themed energy chart, readable energy main page with a meter modal
window, and Escape/focus behaviour of every nested surface.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "custom_components" / "hausman_hub" / "frontend"
TOKENS_CSS = FRONTEND / "hausman-hub-tokens.css"
MODAL_JS = FRONTEND / "hausman-hub-modal.js"
ENERGY_JS = FRONTEND / "hausman-hub-energy.js"
ENERGY_CSS = FRONTEND / "hausman-hub-energy.css"
ENERGY_METER_JS = FRONTEND / "hausman-hub-energy-meter.js"
ENERGY_CHART_JS = FRONTEND / "hausman-hub-energy-chart.js"
PANEL_JS = FRONTEND / "hausman-hub-panel.js"

# (css file, backdrop class, surface class) for the token contract.
MODAL_TOKEN_SURFACES = (
    ("hausman-hub-device-card.css", "device-sheet-backdrop", "device-sheet"),
    ("hausman-hub-climate-overview.css", "climate-device-sheet-backdrop", "climate-device-sheet"),
    ("hausman-hub-lighting.css", "lighting-room-sheet-backdrop", "lighting-room-sheet"),
    ("hausman-hub-rooms.css", "rooms-detail-backdrop", "rooms-detail-sheet"),
    ("hausman-hub-media-overview.css", "media-zone-sheet-backdrop", "media-zone-sheet"),
    ("hausman-hub-scenarios.css", "scenario-editor-overlay", "scenario-editor-dialog"),
    ("hausman-hub-scenarios.css", "scenario-more-menu", "scenario-more-menu"),
    ("hausman-hub-energy.css", "energy-modal-backdrop", "energy-modal"),
)

# (js file, css file, js classes, css classes) for the DOM inventory.
MODAL_SURFACE_INVENTORY = (
    ("hausman-hub-device-card.js", "hausman-hub-device-card.css", ("device-sheet-backdrop", "device-sheet"), ("device-sheet-backdrop", "device-sheet")),
    ("hausman-hub-media-device.js", "hausman-hub-media-device.css", ("device-sheet-backdrop", "media-device-sheet"), ("media-device-sheet",)),
    ("hausman-hub-climate-overview.js", "hausman-hub-climate-overview.css", ("climate-device-sheet-backdrop", "climate-device-sheet"), ("climate-device-sheet-backdrop", "climate-device-sheet")),
    ("hausman-hub-lighting.js", "hausman-hub-lighting.css", ("lighting-room-sheet-backdrop", "lighting-room-sheet"), ("lighting-room-sheet-backdrop", "lighting-room-sheet")),
    ("hausman-hub-rooms.js", "hausman-hub-rooms.css", ("rooms-detail-backdrop", "rooms-detail-sheet"), ("rooms-detail-backdrop", "rooms-detail-sheet")),
    ("hausman-hub-media-overview.js", "hausman-hub-media-overview.css", ("media-zone-sheet-backdrop", "media-zone-sheet"), ("media-zone-sheet-backdrop", "media-zone-sheet")),
    ("hausman-hub-scenarios.js", "hausman-hub-scenarios.css", ("scenario-editor-overlay", "scenario-editor-dialog", "scenario-more-menu"), ("scenario-editor-overlay", "scenario-editor-dialog", "scenario-more-menu")),
    ("hausman-hub-energy.js", "hausman-hub-energy.css", ("energy-modal-backdrop", "energy-modal"), ("energy-modal-backdrop", "energy-modal")),
)

DARK_COLOR_PATTERN = re.compile(r"#12171D|#181E26|rgba\(3,\s*8,\s*12|rgba\(4,\s*8,\s*13|rgba\(3,\s*8,\s*13|rgba\(4,\s*7,\s*10")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def css_rule(css: str, class_name: str) -> str:
    match = re.search(rf"(?m)^\.{re.escape(class_name)}\s*\{{([^}}]*)\}}", css)
    if match:
        return match.group(1)
    match = re.search(rf"\.{re.escape(class_name)}\s*\{{([^}}]*)\}}", css)
    return match.group(1) if match else ""


class ModalTokensTest(unittest.TestCase):
    def test_tokens_css_defines_modal_tokens_in_both_modes(self) -> None:
        css = read(TOKENS_CSS)
        dark_block, light_tail = css.split(":host(.theme-light)", 1)
        for token in ("--hmh-modal-backdrop", "--hmh-modal-surface", "--hmh-modal-raised",
                      "--hmh-modal-border", "--hmh-modal-shadow"):
            self.assertIn(token, dark_block)
        self.assertIn("--hmh-modal-backdrop:rgba(22,28,38,.30)", light_tail)
        self.assertIn("--hmh-modal-shadow:0 22px 64px rgba(22,28,38,.18)", light_tail)
        self.assertIn("--hmh-modal-backdrop:rgba(3,8,12,.72)", dark_block)
        self.assertIn("--hmh-modal-surface:var(--hmh-surface)", dark_block)
        self.assertIn("--hmh-modal-raised:var(--hmh-raised)", dark_block)
        self.assertIn("--hmh-modal-border:var(--hmh-border)", dark_block)

    def test_every_modal_surface_uses_shared_tokens(self) -> None:
        for css_name, backdrop, surface in MODAL_TOKEN_SURFACES:
            with self.subTest(surface=surface):
                css = read(FRONTEND / css_name)
                backdrop_rule = css_rule(css, backdrop)
                surface_rule = css_rule(css, surface)
                if backdrop != surface:
                    self.assertIn("var(--hmh-modal-backdrop)", backdrop_rule)
                    self.assertNotRegex(backdrop_rule, DARK_COLOR_PATTERN)
                self.assertIn("var(--hmh-modal-shadow)", surface_rule)
                self.assertNotRegex(surface_rule, DARK_COLOR_PATTERN)

    def test_modal_surfaces_are_themed_in_light_mode_via_tokens(self) -> None:
        # The light theme only redefines tokens; no surface may re-hardcode a
        # dark background that would ignore theme-light.
        for css_name, backdrop, surface in MODAL_TOKEN_SURFACES:
            with self.subTest(surface=surface):
                css = read(FRONTEND / css_name)
                for class_name in {backdrop, surface}:
                    rule = css_rule(css, class_name)
                    background = re.search(r"background\s*:([^;]+)", rule)
                    if background:
                        self.assertNotRegex(background.group(1), DARK_COLOR_PATTERN)

    def test_modal_surface_inventory_exists_in_js_and_css(self) -> None:
        for js_name, css_name, js_classes, css_classes in MODAL_SURFACE_INVENTORY:
            with self.subTest(module=js_name):
                js = read(FRONTEND / js_name)
                css = read(FRONTEND / css_name)
                for class_name in js_classes:
                    self.assertIn(class_name, js)
                for class_name in css_classes:
                    self.assertIn(f".{class_name}", css)

    def test_signal_and_priority_choosers_use_themed_variables(self) -> None:
        panel_css = read(FRONTEND / "hausman-hub-panel.css")
        weather_css = read(FRONTEND / "hausman-hub-weather-sources.css")
        chooser = css_rule(panel_css, "signal-chooser")
        self.assertIn("var(--card-background-color", chooser)
        self.assertIn("var(--divider-color", chooser)
        self.assertIn("--card-background-color:var(--hmh-raised)", read(TOKENS_CSS))
        self.assertIn("var(--hmh-field", weather_css)


class ModalBehaviourTest(unittest.TestCase):
    def test_modal_focus_does_not_scroll_the_panel_away(self) -> None:
        js = read(MODAL_JS)
        self.assertIn('focus({ preventScroll: true })', js)

    def test_modal_helper_is_wired_into_every_nested_window(self) -> None:
        expectations = {
            "hausman-hub-device-card.js": "enhanceAppendedModal(backdrop, sheet, () =>",
            "hausman-hub-media-device.js": "enhanceAppendedModal(backdrop, body, () =>",
            "hausman-hub-climate-overview.js": "enhanceAppendedModal(backdrop, sheet, dismiss)",
            "hausman-hub-lighting.js": "enhanceAppendedModal(backdrop, sheet, () => closeSheet(panel, container))",
            "hausman-hub-rooms.js": "enhanceAppendedModal(backdrop, sheet, () => closeRoomOverview(panel, container))",
            "hausman-hub-media-overview.js": "enhanceAppendedModal(backdrop, sheet, dismiss)",
            "hausman-hub-scenarios.js": "trapModalTabKey(event, dialog)",
            "hausman-hub-energy.js": "enhanceAppendedModal(backdrop, sheet, () => closeEnergyDetails(panel)",
        }
        for js_name, snippet in expectations.items():
            with self.subTest(module=js_name):
                js = read(FRONTEND / js_name)
                self.assertIn(snippet, js)
                self.assertIn("hausman-hub-modal.js?v=", js)

    def test_modal_helper_provides_escape_trap_and_focus_restore(self) -> None:
        js = read(MODAL_JS)
        self.assertIn('event.key === "Escape"', js)
        self.assertIn("stopPropagation", js)
        self.assertIn("trapModalTabKey", js)
        self.assertIn("restoreFocusTo", js)
        self.assertIn("focusInitialModalElement", js)
        self.assertIn("activeElementWithin", js)

    def test_scenario_editor_restores_focus_to_opener(self) -> None:
        js = read(FRONTEND / "hausman-hub-scenarios.js")
        self.assertIn("panel._scenarioEditorRestoreFocus = activeElementWithin(panel);", js)
        self.assertIn("panel._scenarioEditorRestoreFocus = null;", js)

    def test_modal_escape_closes_top_window_and_restores_focus(self) -> None:
        script = f"""
          const vm = require("vm");
          const fs = require("fs");
          vm.runInThisContext(fs.readFileSync({str(MODAL_JS)!r}, "utf8").replace(/export /g, ""));
          const root = {{ activeElement: null }};
          const focusLog = [];
          const makeEl = (tag) => ({{
            tagName: tag,
            children: [],
            parent: null,
            hidden: false,
            disabled: false,
            tabIndex: 0,
            isConnected: true,
            _listeners: {{}},
            appendChild(child) {{ this.children.push(child); child.parent = this; return child; }},
            addEventListener(type, handler) {{ (this._listeners[type] = this._listeners[type] || []).push(handler); }},
            remove() {{
              this.isConnected = false;
              if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
            }},
            contains(node) {{ while (node) {{ if (node === this) return true; node = node.parent; }} return false; }},
            querySelectorAll(selector) {{
              const tags = selector.split(",").map((part) => part.trim());
              const out = [];
              const walk = (node) => (node.children || []).forEach((child) => {{
                if (tags.some((tag) => child.tagName === tag || (tag === "[tabindex]" && child.tabIndex >= 0))) out.push(child);
                walk(child);
              }});
              walk(this);
              return out;
            }},
            querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }},
            getRootNode() {{ return root; }},
            focus() {{ root.activeElement = this; focusLog.push(this); }},
          }});
          const fire = (node, type, event) => (node._listeners[type] || []).forEach((handler) => handler(event));

          (async () => {{
            const opener = makeEl("button");
            root.activeElement = opener;
            const backdrop = makeEl("div");
            const sheet = backdrop.appendChild(makeEl("section"));
            const closeButton = sheet.appendChild(makeEl("button"));
            const firstField = sheet.appendChild(makeEl("input"));
            const lastButton = sheet.appendChild(makeEl("button"));
            let closed = 0;
            enhanceAppendedModal(backdrop, sheet, () => {{ closed += 1; backdrop.remove(); }});
            await Promise.resolve();
            if (root.activeElement !== closeButton) throw new Error("initial focus did not land on the first dialog control");

            let prevented = 0;
            const tabEvent = {{ key: "Tab", shiftKey: false, preventDefault: () => {{ prevented += 1; }} }};
            fire(sheet, "keydown", tabEvent);
            if (root.activeElement !== closeButton) throw new Error("tab in the middle of the dialog must move naturally");
            root.activeElement = lastButton;
            fire(sheet, "keydown", tabEvent);
            if (root.activeElement !== closeButton || prevented !== 2) throw new Error("tab on the last control did not wrap to the first");
            fire(sheet, "keydown", {{ key: "Tab", shiftKey: true, preventDefault: () => {{}} }});
            if (root.activeElement !== lastButton) throw new Error("shift+tab on the first control did not wrap to the last");

            let stopped = false;
            fire(sheet, "keydown", {{ key: "Escape", preventDefault: () => {{}}, stopPropagation: () => {{ stopped = true; }} }});
            if (!stopped) throw new Error("escape did not stop propagation to the window below");
            if (closed !== 1) throw new Error("escape did not close the top window");
            if (root.activeElement !== opener) throw new Error("focus did not return to the opening card");

            const details = makeEl("details");
            details.open = true;
            const detailsSheet = details.appendChild(makeEl("section"));
            detailsSheet.appendChild(makeEl("button"));
            let detailsClosed = 0;
            enhanceDetailsModal(details, detailsSheet, () => {{ detailsClosed += 1; details.open = false; }});
            fire(detailsSheet, "keydown", {{ key: "Escape", preventDefault: () => {{}}, stopPropagation: () => {{}} }});
            if (detailsClosed !== 1) throw new Error("details sheet escape did not close");
            details.open = false;
            fire(detailsSheet, "keydown", {{ key: "Escape", preventDefault: () => {{}}, stopPropagation: () => {{}} }});
            if (detailsClosed !== 1) throw new Error("closed details sheet reacted to escape");
          }})().then(() => process.exit(0)).catch((error) => {{ console.error(error && error.message || error); process.exit(1); }});
        """
        completed = subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)


class EnergyLayoutTest(unittest.TestCase):
    def test_main_energy_page_has_no_nested_scroll_regions(self) -> None:
        css = read(ENERGY_CSS)
        for class_name in ("energy-device-list", "energy-compact-sources"):
            rule = css_rule(css, class_name)
            self.assertNotIn("overflow:auto", rule)
            self.assertNotIn("max-height", rule)
        js = read(ENERGY_JS)
        self.assertNotIn("renderEnergySidebar", js)
        self.assertNotIn("energy-page-layout", js)
        self.assertIn("renderMeterReadingStrip(panel, energy, deps)", js)
        self.assertIn("renderEnergyHistory(panel, energy, selected, deps)", js)
        self.assertIn("renderEnergyDevices(panel, container, energy.sources, deps)", js)

    def test_main_energy_summary_shows_key_values(self) -> None:
        js = read(ENERGY_JS)
        for snippet in (
            '"Мощность"', '"Сегодня"', '"Источники"',
            '"Показание счётчика"', '"С последней передачи"',
            '"Нет показаний"',
            "energy-meter-reading-strip",
            "openEnergyDetails(panel)",
        ):
            self.assertIn(snippet, js)

    def test_energy_details_open_as_accessible_modal(self) -> None:
        js = read(ENERGY_JS)
        self.assertIn('setAttr(sheet, "role", "dialog");', js)
        self.assertIn('setAttr(sheet, "aria-modal", "true");', js)
        self.assertIn("energy-modal-backdrop", js)
        self.assertIn("if (event.target === backdrop) closeEnergyDetails(panel);", js)
        self.assertIn("renderEnergyMeterCard(panel, deps)", js)
        self.assertIn("compactEnergySettings(panel, container, energy, deps)", js)
        modal_body = js.split("function renderEnergyModal", 1)[1].split("function renderMeterReadingStrip", 1)[0]
        self.assertNotIn("renderEnergyHistory(", modal_body)
        self.assertIn("renderEnergyDevices(panel, container, energy.sources, deps)", modal_body)
        self.assertIn('"← К списку энергии"', js)
        self.assertIn("panel._energyModalView = sourceId ? \"device\" : \"overview\";", js)
        css = read(ENERGY_CSS)
        body_rule = css_rule(css, "energy-modal-body")
        self.assertIn("overflow:auto", body_rule)
        backdrop_rule = css_rule(css, "energy-modal-backdrop")
        self.assertIn("overscroll-behavior:contain", backdrop_rule)

    def test_meter_card_matches_meter_handoff(self) -> None:
        js = read(ENERGY_METER_JS)
        for snippet in (
            '"hausman_hub/v1/energy/meter"',
            'action: "configure"', 'action: "submit"', 'action: "correct"',
            "expectedRevision",
            "error.status === 409",
            'meter.source.state === "reset_detected"',
            '"расчётное"',
            "meter.history",
            '"Корректировка" : "Передача"',
            '"energy-meter-reminder"' if False else "meterReminderText",
        ):
            self.assertIn(snippet, js)
        main = read(ENERGY_JS)
        self.assertIn("energy-meter-reading-strip", main)
        self.assertIn("energy-overview-reminder", main)

    def test_meter_loading_is_wired_into_panel(self) -> None:
        js = read(PANEL_JS)
        self.assertIn('import { loadEnergyMeter } from "./hausman-hub-energy-meter.js?v=', js)
        self.assertIn("if (this._energyMeter === null) loadEnergyMeter(this);", js)
        self.assertIn("loadEnergyMeter(this);", js)
        self.assertIn("this._energyDetailsOpen = false;", js)


class EnergyChartThemeTest(unittest.TestCase):
    def test_chart_reads_theme_tokens_and_redraws(self) -> None:
        js = read(ENERGY_CHART_JS)
        self.assertIn("window.getComputedStyle(panel)", js)
        self.assertIn('styles.getPropertyValue("--hmh-text-dim")', js)
        self.assertIn('styles.getPropertyValue("--hmh-accent")', js)
        self.assertIn("export function redrawEnergyChartsForTheme(panel)", js)
        self.assertIn("chart._hmhEnergyRedraw = redraw;", js)
        self.assertIn("context.clearRect(0, 0, width, height);", js)
        self.assertNotIn('context.strokeStyle = "#4F8CFF";', js)
        self.assertNotIn('context.fillStyle = "#4F8CFF";', js)
        self.assertNotIn('"rgba(132, 151, 177, .18)"', js)

    def test_theme_switch_redraws_open_charts_without_dom_recreation(self) -> None:
        js = read(PANEL_JS)
        apply_theme = re.search(r"_applyThemeMode\(\) \{([\s\S]*?)\n  \}", js)
        self.assertIsNotNone(apply_theme)
        body = apply_theme.group(1)
        self.assertIn('this.classList.toggle("theme-light", effective === "light");', body)
        self.assertIn("redrawEnergyChartsForTheme(this);", body)
        self.assertNotIn("this._render()", body)

    def test_chart_theme_palette_follows_tokens(self) -> None:
        script = f"""
          const vm = require("vm");
          const fs = require("fs");
          vm.runInThisContext(fs.readFileSync({str(ENERGY_CHART_JS)!r}, "utf8").replace(/export /g, ""));
          global.window = {{
            getComputedStyle: (node) => ({{
              getPropertyValue: (name) => node.__vars[name] || "",
            }}),
          }};
          const light = {{ __vars: {{ "--hmh-text-dim": "#5C6878", "--hmh-accent": "#2F6FE4" }} }};
          const dark = {{ __vars: {{ "--hmh-text-dim": "#A3AAB7", "--hmh-accent": "#4F8CFF" }} }};
          const lightTheme = energyChartTheme(light);
          const darkTheme = energyChartTheme(dark);
          if (lightTheme.line !== "rgba(47, 111, 228, 1)") throw new Error("light accent ignored: " + lightTheme.line);
          if (darkTheme.line !== "rgba(79, 140, 255, 1)") throw new Error("dark accent ignored: " + darkTheme.line);
          if (lightTheme.grid === darkTheme.grid) throw new Error("grid color does not follow the theme");
          if (!lightTheme.fillTop.startsWith("rgba(47, 111, 228")) throw new Error("fill does not follow the accent");
          const fallback = energyChartTheme(null);
          if (!fallback.line) throw new Error("fallback palette missing");
        """
        completed = subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
