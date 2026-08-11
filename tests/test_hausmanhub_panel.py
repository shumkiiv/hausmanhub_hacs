"""Contract tests for the HausmanHub sidebar panel (roadmap item 37)."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
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
BUTTONS_CSS = PANEL_JS.with_name("hausman-hub-buttons.css")
TOKENS_CSS = PANEL_JS.with_name("hausman-hub-tokens.css")
HOME_SECTIONS_JS = PANEL_JS.with_name("hausman-hub-home-sections.js")
ROOM_SETUP_JS = PANEL_JS.with_name("hausman-hub-room-setup.js")
ROOM_DEVICE_GROUPS_JS = PANEL_JS.with_name("hausman-hub-room-device-groups.js")
CONTROL_CHANNEL_JS = PANEL_JS.with_name("hausman-hub-control-channel.js")
CONTROL_CHANNEL_CSS = PANEL_JS.with_name("hausman-hub-control-channel.css")
ROOM_CLIMATE_SOURCES_JS = PANEL_JS.with_name("hausman-hub-room-climate-sources.js")
DEVICE_INVENTORY_JS = PANEL_JS.with_name("hausman-hub-device-inventory.js")
INVENTORY_DUPLICATES_JS = PANEL_JS.with_name("hausman-hub-inventory-duplicates.js")
DEVICE_BINDINGS_JS = PANEL_JS.with_name("hausman-hub-device-bindings.js")
AREA_BINDING_JS = PANEL_JS.with_name("hausman-hub-area-binding.js")
FIRST_RUN_DRAFT_JS = PANEL_JS.with_name("hausman-hub-first-run-draft.js")
NAVIGATION_JS = PANEL_JS.with_name("hausman-hub-navigation.js")
NAVIGATION_CSS = PANEL_JS.with_name("hausman-hub-navigation.css")
KIOSK_JS = PANEL_JS.with_name("hausman-hub-kiosk.js")
KIOSK_CSS = PANEL_JS.with_name("hausman-hub-kiosk.css")
ENERGY_JS = PANEL_JS.with_name("hausman-hub-energy.js")
ENERGY_CSS = PANEL_JS.with_name("hausman-hub-energy.css")
ENERGY_CHART_JS = PANEL_JS.with_name("hausman-hub-energy-chart.js")
ENERGY_CHART_CSS = PANEL_JS.with_name("hausman-hub-energy-chart.css")
WEATHER_SOURCES_JS = PANEL_JS.with_name("hausman-hub-weather-sources.js")
MEDIA_DEVICE_JS = PANEL_JS.with_name("hausman-hub-media-device.js")
DEVICE_CARD_JS = PANEL_JS.with_name("hausman-hub-device-card.js")
DEVICE_CARD_CSS = PANEL_JS.with_name("hausman-hub-device-card.css")
SCENARIOS_JS = PANEL_JS.with_name("hausman-hub-scenarios.js")
SCENARIO_ICONS_JS = PANEL_JS.with_name("hausman-hub-scenario-icons.js")
SCENARIO_FIELDS_JS = PANEL_JS.with_name("hausman-hub-scenario-fields.js")
ROLLOUT_JS = PANEL_JS.with_name("hausman-hub-rollout.js")
OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-overview.js")
OVERVIEW_HERO_STATE_JS = PANEL_JS.with_name("hausman-hub-overview-hero-state.js")
ROOM_ICONS_JS = PANEL_JS.with_name("hausman-hub-room-icons.js")
HERO_ROOM_NAVIGATION_JS = PANEL_JS.with_name("hausman-hub-hero-room-navigation.js")
LIBRARY_HERO_JS = PANEL_JS.with_name("hausman-hub-library-hero.js")
LIBRARY_HERO_CSS = PANEL_JS.with_name("hausman-hub-library-hero.css")
LIBRARY_HERO_CONSUMERS = (
    PANEL_JS.with_name("hausman-hub-lighting.js"),
    PANEL_JS.with_name("hausman-hub-climate-overview.js"),
    PANEL_JS.with_name("hausman-hub-rooms.js"),
    PANEL_JS.with_name("hausman-hub-media-overview.js"),
    PANEL_JS.with_name("hausman-hub-security-overview.js"),
    PANEL_JS.with_name("hausman-hub-devices-overview.js"),
    PANEL_JS.with_name("hausman-hub-scenarios.js"),
)
OVERVIEW_CSS = PANEL_JS.with_name("hausman-hub-overview.css")
OVERVIEW_HERO = PANEL_JS.parent / "assets" / "dashboard-hero-living-room-day.png"
WEATHER_SOURCES_CSS = PANEL_JS.with_name("hausman-hub-weather-sources.css")
SCENARIOS_CSS = PANEL_JS.with_name("hausman-hub-scenarios.css")
ROLLOUT_CSS = PANEL_JS.with_name("hausman-hub-rollout.css")
SETTINGS_CSS = PANEL_JS.with_name("hausman-hub-settings.css")
SWITCH_CSS = PANEL_JS.with_name("hausman-hub-switch.css")
NOTICE_CSS = PANEL_JS.with_name("hausman-hub-notice.css")
FEEDBACK_JS = PANEL_JS.with_name("hausman-hub-feedback.js")
WIZARD_VALIDATION_CSS = PANEL_JS.with_name("hausman-hub-wizard-validation.css")
CATALOG_CSS = PANEL_JS.with_name("hausman-hub-catalog.css")
DEVICE_MAINTENANCE_CSS = PANEL_JS.with_name("hausman-hub-device-maintenance.css")
DEVICES_OVERVIEW_CSS = PANEL_JS.with_name("hausman-hub-devices-overview.css")
ROOMS_CSS = PANEL_JS.with_name("hausman-hub-rooms.css")
MAX_PANEL_JS_BYTES = 280 * 1024
MAX_HOME_SECTIONS_JS_BYTES = 16 * 1024
MAX_ROOM_SETUP_JS_BYTES = 24 * 1024
MAX_ROOM_DEVICE_GROUPS_JS_BYTES = 12 * 1024
MAX_CONTROL_CHANNEL_JS_BYTES = 16 * 1024
MAX_ROOM_CLIMATE_SOURCES_JS_BYTES = 16 * 1024
MAX_DEVICE_INVENTORY_JS_BYTES = 22 * 1024
MAX_DEVICE_BINDINGS_JS_BYTES = 20 * 1024
MAX_AREA_BINDING_JS_BYTES = 24 * 1024
MAX_NAVIGATION_JS_BYTES = 16 * 1024
MAX_ENERGY_JS_BYTES = 36 * 1024
MAX_MEDIA_DEVICE_JS_BYTES = 12 * 1024
MAX_SCENARIOS_JS_BYTES = 42 * 1024
MAX_PANEL_CSS_BYTES = 56 * 1024
MAX_SETTINGS_CSS_BYTES = 24 * 1024


class PanelJavaScriptContractTest(unittest.TestCase):
    """The local panel assets stay bounded and loadable."""

    def test_tablet_library_pages_share_one_canonical_hero(self) -> None:
        component = LIBRARY_HERO_JS.read_text(encoding="utf-8")
        styles = LIBRARY_HERO_CSS.read_text(encoding="utf-8")
        panel_styles = PANEL_CSS.read_text(encoding="utf-8")

        self.assertIn("export function createLibraryHero", component)
        self.assertIn("roomHeroImage", component)
        self.assertIn(".hmh-library-hero-overlay", styles)
        self.assertIn(".hmh-library-hero-facts", styles)
        self.assertIn("hausman-hub-library-hero.css?v=1.52.65", panel_styles)
        for consumer in LIBRARY_HERO_CONSUMERS:
            source = consumer.read_text(encoding="utf-8")
            self.assertIn("createLibraryHero", source, consumer.name)

    def test_panel_script_exists_and_stays_bounded(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")
        home_sections = HOME_SECTIONS_JS.read_text(encoding="utf-8")
        room_setup = ROOM_SETUP_JS.read_text(encoding="utf-8")
        room_device_groups = ROOM_DEVICE_GROUPS_JS.read_text(encoding="utf-8")
        control_channel = CONTROL_CHANNEL_JS.read_text(encoding="utf-8")
        room_climate_sources = ROOM_CLIMATE_SOURCES_JS.read_text(encoding="utf-8")
        device_inventory = DEVICE_INVENTORY_JS.read_text(encoding="utf-8")
        inventory_duplicates = INVENTORY_DUPLICATES_JS.read_text(encoding="utf-8")
        device_bindings = DEVICE_BINDINGS_JS.read_text(encoding="utf-8")
        area_binding = AREA_BINDING_JS.read_text(encoding="utf-8")
        first_run_draft = FIRST_RUN_DRAFT_JS.read_text(encoding="utf-8")
        navigation = NAVIGATION_JS.read_text(encoding="utf-8")
        energy = ENERGY_JS.read_text(encoding="utf-8")
        energy_chart = ENERGY_CHART_JS.read_text(encoding="utf-8")
        weather_sources = WEATHER_SOURCES_JS.read_text(encoding="utf-8")
        media_device = MEDIA_DEVICE_JS.read_text(encoding="utf-8")
        device_card = DEVICE_CARD_JS.read_text(encoding="utf-8")
        device_card_css = DEVICE_CARD_CSS.read_text(encoding="utf-8")
        scenarios = SCENARIOS_JS.read_text(encoding="utf-8")
        scenario_icons = SCENARIO_ICONS_JS.read_text(encoding="utf-8")
        scenario_fields = SCENARIO_FIELDS_JS.read_text(encoding="utf-8")
        rollout = ROLLOUT_JS.read_text(encoding="utf-8")
        overview = OVERVIEW_JS.read_text(encoding="utf-8")
        room_icons = ROOM_ICONS_JS.read_text(encoding="utf-8")
        device_maintenance_css = DEVICE_MAINTENANCE_CSS.read_text(encoding="utf-8")
        feedback = FEEDBACK_JS.read_text(encoding="utf-8")

        self.assertLessEqual(len(content.encode("utf-8")), MAX_PANEL_JS_BYTES)
        self.assertLessEqual(len(rollout.encode("utf-8")), 8 * 1024)
        # Лимит поднят с 16 до 20 КиБ: блок «Ближайшие события» (renderUpcomingEvents
        # и чистые функции форматирования) добавил в модуль около 4,4 КиБ.
        self.assertLessEqual(len(overview.encode("utf-8")), 20 * 1024)
        self.assertLessEqual(len(room_icons.encode("utf-8")), 12 * 1024)
        self.assertIn('hausman-hub-rollout.js?v=1.52.65', content)
        self.assertLessEqual(len(weather_sources.encode("utf-8")), 24 * 1024)
        self.assertLessEqual(
            len(home_sections.encode("utf-8")), MAX_HOME_SECTIONS_JS_BYTES
        )
        self.assertLessEqual(
            len(room_setup.encode("utf-8")), MAX_ROOM_SETUP_JS_BYTES
        )
        self.assertLessEqual(
            len(room_device_groups.encode("utf-8")), MAX_ROOM_DEVICE_GROUPS_JS_BYTES
        )
        self.assertLessEqual(
            len(control_channel.encode("utf-8")), MAX_CONTROL_CHANNEL_JS_BYTES
        )
        self.assertLessEqual(
            len(room_climate_sources.encode("utf-8")), MAX_ROOM_CLIMATE_SOURCES_JS_BYTES
        )
        self.assertLessEqual(
            len(device_inventory.encode("utf-8")), MAX_DEVICE_INVENTORY_JS_BYTES
        )
        self.assertLessEqual(len(inventory_duplicates.encode("utf-8")), 8 * 1024)
        self.assertIn("hausman-hub-inventory-duplicates.js?v=1.52.65", device_inventory)
        self.assertLessEqual(
            len(device_bindings.encode("utf-8")), MAX_DEVICE_BINDINGS_JS_BYTES
        )
        self.assertLessEqual(
            len(area_binding.encode("utf-8")), MAX_AREA_BINDING_JS_BYTES
        )
        self.assertLessEqual(len(first_run_draft.encode("utf-8")), 12 * 1024)
        self.assertIn("restoreFirstRunDraft", first_run_draft)
        self.assertLessEqual(len(navigation.encode("utf-8")), MAX_NAVIGATION_JS_BYTES)
        self.assertLessEqual(len(energy.encode("utf-8")), MAX_ENERGY_JS_BYTES)
        self.assertLessEqual(len(energy_chart.encode("utf-8")), 16 * 1024)
        self.assertIn('hausman-hub-energy-chart.js?v=1.52.65', energy)
        self.assertLessEqual(
            len(media_device.encode("utf-8")), MAX_MEDIA_DEVICE_JS_BYTES
        )
        self.assertLessEqual(len(device_card.encode("utf-8")), 16 * 1024)
        self.assertLessEqual(len(device_card_css.encode("utf-8")), 12 * 1024)
        self.assertLessEqual(len(scenarios.encode("utf-8")), MAX_SCENARIOS_JS_BYTES)
        self.assertLessEqual(len(scenario_icons.encode("utf-8")), 12 * 1024)
        self.assertLessEqual(len(scenario_fields.encode("utf-8")), 12 * 1024)
        self.assertIn('hausman-hub-scenario-fields.js?v=1.52.65', scenarios)
        self.assertIn('["on", "Включено"]', scenarios)
        self.assertIn('["off", "Выключено"]', scenarios)
        self.assertIn('placeholder: "например 23"', scenarios)
        self.assertNotIn('placeholder: "включено / 23 / открыто"', scenarios)
        self.assertLessEqual(len(device_maintenance_css.encode("utf-8")), 8 * 1024)
        self.assertLessEqual(len(feedback.encode("utf-8")), 4 * 1024)
        self.assertIn("applyFeedback", feedback)
        self.assertIn("feedbackTone", feedback)
        self.assertIn("renderHomeSection", home_sections)
        self.assertIn("renderFirstRunRoom", room_setup)
        self.assertIn("renderFirstRunDeviceGroups", room_device_groups)
        self.assertIn("resolveControlChannelTest", control_channel)
        self.assertIn("renderFirstRunClimateSources", room_climate_sources)
        self.assertIn("renderDeviceInventory", device_inventory)
        self.assertIn("DEVICE_MAINTENANCE_API", device_inventory)
        self.assertIn("renderDeviceBindings", device_bindings)
        self.assertIn(
            "if (error && error.status === 409) state.preview = null;",
            device_bindings,
        )
        self.assertIn("renderFirstRunAreaBinding", area_binding)
        self.assertIn("writeNavigationRoute", navigation)
        self.assertIn("renderMediaDeviceCard", media_device)
        self.assertIn("renderPhysicalDeviceCard", device_card)
        self.assertIn("renderEnergySection", energy)
        self.assertIn("renderScenarioSection", scenarios)
        self.assertIn("renderOverviewHero", overview)
        self.assertIn("physicalDeviceCount(devices)", overview)
        self.assertIn('readiness?.status || "not_ready"', overview)
        self.assertTrue(OVERVIEW_CSS.is_file())
        self.assertEqual(b"\x89PNG\r\n\x1a\n", OVERVIEW_HERO.read_bytes()[:8])
        self.assertIn("SCENARIO_ICON_GROUPS", scenario_icons)
        self.assertIn("renderHomeSection.overviewMetrics", home_sections)
        self.assertIn("renderHomeSection.roomCountWord", home_sections)
        self.assertGreaterEqual(scenario_icons.count('["'), 80)
        self.assertIn('el("button", "sidebar-intercom")', content)
        self.assertIn('el("button", "header-intercom")', content)
        self.assertIn(".header-intercom", PANEL_CSS.read_text(encoding="utf-8"))
        self.assertIn("openIntercomFromRail(this)", content)
        self.assertIn("export function openIntercomFromRail(panel)", navigation)
        self.assertIn(
            'const preferences = ["open", "unlock", "open_cover", "open_valve", "turn_on", "press", "activate"]',
            navigation,
        )
        self.assertIn('customElements.get?.("hausman-hub-panel")', content)
        self.assertIn('customElements.define("hausman-hub-panel"', content)

    def test_success_notice_auto_dismisses_and_stays_hidden_on_rerender(self) -> None:
        script = f"""
          const vm = require("vm");
          const fs = require("fs");
          let scheduled = null;
          global.setTimeout = (callback, delay) => {{
            if (delay !== 4500) throw new Error("unexpected feedback delay");
            scheduled = callback;
            return 7;
          }};
          global.clearTimeout = () => {{ scheduled = null; }};
          vm.runInThisContext(fs.readFileSync({str(FEEDBACK_JS)!r}, "utf8").replace(/export /g, ""));
          const element = {{ style: {{}}, textContent: "", className: "" }};
          const setAttr = (target, name, value) => {{ target[name] = value; }};
          const message = "Привязки комнат сохранены в Home Assistant.";
          if (applyFeedback(element, message, setAttr) !== "success" || !scheduled) {{
            throw new Error("success feedback did not schedule dismissal");
          }}
          scheduled();
          if (element.style.display !== "none") throw new Error("success feedback stayed visible");
          applyFeedback(element, message, setAttr);
          if (element.style.display !== "none") throw new Error("rerender restored dismissed feedback");
          applyFeedback(element, "", setAttr);
          applyFeedback(element, message, setAttr);
          if (element.style.display === "none" || !scheduled) {{
            throw new Error("a later success message did not become visible");
          }}
        """
        completed = subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_overview_hero_uses_tablet_semantic_room_icons(self) -> None:
        script = f"""
          const vm = require("vm");
          const fs = require("fs");
          vm.runInThisContext(fs.readFileSync({str(ROOM_ICONS_JS)!r}, "utf8").replace(/export /g, ""));
          const expectations = [
            [{{ id: "vannaia", name: "Ванная" }}, "bathroom"],
            [{{ id: "dop_vanna", name: "Душевая" }}, "bathroom"],
            [{{ id: "kabinet", name: "Кабинет" }}, "office"],
            [{{ id: "kladovka", name: "Кладовка" }}, "storage"],
            [{{ id: "detskaia", name: "Комната Игоря", icon: "mdi:human-male-child" }}, "child"],
            [{{ id: "room_alice", name: "Комната Алисы", icon: "mdi:human-child" }}, "child"],
            [{{ id: "room_shower", name: "Проходная", icon: "mdi:bathtub" }}, "bathroom"],
            [{{ id: "kukhnia", name: "Кухня", icon: "mdi:countertop-outline" }}, "kitchen"],
            [{{ id: "tualet", name: "Туалет" }}, "toilet"],
            [{{ id: "malyi_koridor", name: "Малый коридор" }}, "hallway"],
            [{{ id: "custom", name: "Новая комната" }}, "rooms"],
          ];
          for (const [room, expected] of expectations) {{
            const actual = roomIconName(room);
            if (actual !== expected) throw new Error(`${{room.name}}: ${{actual}} !== ${{expected}}`);
          }}
          if (canonicalRoomMdiIcon("child") !== "mdi:human-child") {{
            throw new Error("child room purpose is not canonical");
          }}
          if (canonicalRoomMdiIcon("bathroom") !== "mdi:bathtub") {{
            throw new Error("bathroom room purpose is not canonical");
          }}
          const heroCases = [
            [{{ id: "vannaia", name: "Ванная" }}, "rainy", "2026-08-04T12:00:00+06:00", "hero_room_bathroom_rain_v2.jpg"],
            [{{ id: "kabinet", name: "Кабинет" }}, "sunny", "2026-08-04T18:00:00+06:00", "hero_room_office_evening.webp"],
            [{{ id: "detskaia", name: "Комната Игоря" }}, "snowy", "2026-08-04T18:00:00+06:00", "hero_room_kids_snow_v2.jpg"],
            [{{ id: "kukhnia", name: "Кухня" }}, "sunny", "2026-08-04T18:00:00+06:00", "hero_kitchen_evening.png"],
            [{{ id: "kukhnia", name: "Кухня" }}, "rainy", "2026-08-04T12:00:00+06:00", "hero_premium_kitchen_rain_v3.png"],
            [null, "clear-night", "2026-08-04T23:00:00+06:00", "hero_living_room_night.png"],
          ];
          for (const [room, weatherCondition, localIso, filename] of heroCases) {{
            const actual = roomHeroImage(room, {{ weatherCondition }}, localIso);
            if (!actual.endsWith(filename)) throw new Error(`${{filename}}: ${{actual}}`);
          }}
        """
        completed = subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        room_icons = ROOM_ICONS_JS.read_text(encoding="utf-8")
        for icon_name in (
            "living",
            "kitchen",
            "child",
            "toilet",
            "bathroom",
            "bedroom",
            "hallway",
            "terrace",
            "spa",
            "office",
            "storage",
            "category",
        ):
            self.assertIn(f"  {icon_name}:", room_icons)
        self.assertIn(
            './hausman-hub-room-icons.js?v=1.52.65',
            OVERVIEW_HERO_STATE_JS.read_text(encoding="utf-8"),
        )
        overview = OVERVIEW_JS.read_text(encoding="utf-8")
        self.assertIn("roomNavigation.bind(selectHeroRoom)", overview)
        self.assertIn("selectHeroRoom(selectedRoom, false)", overview)
        self.assertIn("if (nextImage !== currentImage)", overview)
        hero_navigation = HERO_ROOM_NAVIGATION_JS.read_text(encoding="utf-8")
        self.assertIn('"Предыдущая комната"', hero_navigation)
        self.assertIn('"Следующая комната"', hero_navigation)
        self.assertIn("% slides.length", hero_navigation)
        self.assertIn('details.hidden = true', overview)
        self.assertNotIn('button.addEventListener("click", () => deps.openRoom(room))', overview)
        for surface in (
            PANEL_JS.with_name("hausman-hub-lighting.js"),
            PANEL_JS.with_name("hausman-hub-climate-overview.js"),
            PANEL_JS.with_name("hausman-hub-rooms.js"),
            PANEL_JS.with_name("hausman-hub-media-overview.js"),
        ):
            source = surface.read_text(encoding="utf-8")
            self.assertIn("roomIconName", source, surface.name)
            self.assertIn("roomSvgIcon", source, surface.name)
        asset_dir = PANEL_JS.parent / "assets"
        for filename in (
            "hero_room_bathroom_rain_v2.jpg",
            "hero_room_office_evening.webp",
            "hero_room_kids_snow_v2.jpg",
            "hero_living_room_night.png",
            "hero_kitchen_evening.png",
            "hero_premium_kitchen_rain_v3.png",
        ):
            asset = asset_dir / filename
            self.assertTrue(asset.is_file(), filename)
            self.assertGreater(asset.stat().st_size, 40_000, filename)
        overview_css = OVERVIEW_CSS.read_text(encoding="utf-8")
        self.assertIn(".overview-canon-room-navigation", overview_css)
        self.assertIn("scrollbar-width:none", overview_css)
        self.assertIn(".overview-canon-room-strip::-webkit-scrollbar", overview_css)

    def test_frontend_module_cache_versions_match_manifest(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")
        manifest = json.loads(
            (ROOT / "custom_components" / "hausman_hub" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )

        for module in (
            "hausman-hub-home-sections.js",
            "hausman-hub-room-setup.js",
            "hausman-hub-room-device-groups.js",
            "hausman-hub-room-climate-sources.js",
            "hausman-hub-device-inventory.js",
            "hausman-hub-device-bindings.js",
            "hausman-hub-area-binding.js",
            "hausman-hub-navigation.js",
            "hausman-hub-energy.js",
            "hausman-hub-scenarios.js",
            "hausman-hub-overview.js",
            "hausman-hub-overview-hero-state.js",
            "hausman-hub-feedback.js",
        ):
            self.assertIn(f'./{module}?v={manifest["version"]}', content)
        self.assertIn(
            f'./hausman-hub-scenario-icons.js?v={manifest["version"]}',
            SCENARIOS_JS.read_text(encoding="utf-8"),
        )

    def test_energy_history_uses_hausmanhub_api_not_raw_recorder_socket(self) -> None:
        content = ENERGY_JS.read_text(encoding="utf-8")
        chart = ENERGY_CHART_JS.read_text(encoding="utf-8")
        self.assertIn("hausman_hub/v1/energy/history", content)
        self.assertIn('["year", "Год"]', content)
        self.assertIn('year: { days: 365, interval: "1d" }', content)
        self.assertIn('panel._energyHistoryPeriod = value', content)
        self.assertIn('panel._energyHistoryReloadRequested = true', content)
        self.assertIn('if (panel._energyHistoryReloadRequested)', content)
        self.assertIn('["power", "W"]', content)
        self.assertIn('["energy", "kWh"]', content)
        self.assertIn("panel._energyConsumptionHistory", content)
        self.assertIn('series.scope === "selection" ? "selection"', content)
        self.assertIn("item.id === series.sourceId", content)
        self.assertIn("series.deviceId || series.sourceId", content)
        self.assertIn("energy-history-canvas", chart)
        self.assertIn("energy-chart-metrics", chart)
        self.assertIn('["Сейчас", latest, 1]', chart)
        self.assertIn('["Среднее", average, 1]', chart)
        self.assertIn('["Пик", max, 1]', chart)
        self.assertIn('["Минимум", min, 1]', chart)
        self.assertIn("delete target.selection", content)
        self.assertIn('id: "selection", name: "выбранных источников"', content)
        self.assertNotIn("recorder/statistics_during_period", content)
        self.assertNotIn("detail.entityId", content)

    def test_intercom_quick_action_resolves_physical_device_without_confirmation(self) -> None:
        script = f"""
          const vm = require("vm");
          const fs = require("fs");
          vm.runInThisContext(fs.readFileSync({str(NAVIGATION_JS)!r}, "utf8").replace(/export /g, ""));
          const command = resolveIntercomQuickAction(
            [{{ name: "Домофон", entityId: "switch.intercom" }}],
            [{{ entity_id: "switch.intercom", target_id: "switch.intercom", actions: [{{ action_id: "turn_on", allowed_fields: [] }}] }}]
          );
          if (!command || command.targetId !== "switch.intercom" || command.actionId !== "turn_on") {{
            throw new Error("intercom command was not resolved");
          }}
          if (resolveIntercomQuickAction([{{ name: "Чайник" }}], []) !== null) {{
            throw new Error("unrelated device was classified as intercom");
          }}
          const catalogOnly = resolveIntercomQuickAction(
            [],
            [{{
              name: "Домофон 2",
              entity_id: "button.domofon_2",
              target_id: "entity_domofon_2",
              actions: [{{ action_id: "press", allowed_fields: [] }}],
            }}],
            "button.domofon_2"
          );
          if (!catalogOnly || catalogOnly.device.name !== "Домофон 2"
              || catalogOnly.targetId !== "entity_domofon_2"
              || catalogOnly.actionId !== "press") {{
            throw new Error("catalog-only intercom was not resolved");
          }}
        """
        completed = subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_kiosk_double_tap_ignores_controls_inside_shadow_dom(self) -> None:
        script = f"""
          const vm = require("vm");
          const fs = require("fs");
          vm.runInThisContext(fs.readFileSync({str(NAVIGATION_JS)!r}, "utf8").replace(/export /g, ""));
          const panel = {{ _kioskMode: true, _kioskTapAt: Date.now() }};
          const host = {{ closest: () => null }};
          const button = {{ matches: (selector) => selector.includes("button") }};
          const event = {{ target: host, composedPath: () => [button, host] }};
          handleKioskPointerUp(panel, event);
          handleKioskPointerUp(panel, event);
          if (!panel._kioskMode || panel._kioskTapAt === 0) {{
            throw new Error("kiosk exited after a double tap on a shadow DOM control");
          }}
        """
        completed = subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_panel_styles_are_local_and_stay_bounded(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")
        styles = PANEL_CSS.read_text(encoding="utf-8")
        settings_styles = SETTINGS_CSS.read_text(encoding="utf-8")
        switch_styles = SWITCH_CSS.read_text(encoding="utf-8")
        notice_styles = NOTICE_CSS.read_text(encoding="utf-8")
        navigation_styles = NAVIGATION_CSS.read_text(encoding="utf-8")
        kiosk_styles = KIOSK_CSS.read_text(encoding="utf-8")
        wizard_validation_styles = WIZARD_VALIDATION_CSS.read_text(encoding="utf-8")
        weather_source_styles = WEATHER_SOURCES_CSS.read_text(encoding="utf-8")
        scenario_styles = SCENARIOS_CSS.read_text(encoding="utf-8")
        catalog_styles = CATALOG_CSS.read_text(encoding="utf-8")
        energy_styles = ENERGY_CSS.read_text(encoding="utf-8")
        energy_chart_styles = ENERGY_CHART_CSS.read_text(encoding="utf-8")
        rollout_styles = ROLLOUT_CSS.read_text(encoding="utf-8")
        device_card_css = DEVICE_CARD_CSS.read_text(encoding="utf-8")
        control_channel_styles = CONTROL_CHANNEL_CSS.read_text(encoding="utf-8")
        button_styles = BUTTONS_CSS.read_text(encoding="utf-8")

        self.assertLessEqual(len(styles.encode("utf-8")), MAX_PANEL_CSS_BYTES)
        self.assertLessEqual(len(weather_source_styles.encode("utf-8")), 8 * 1024)
        self.assertLessEqual(len(scenario_styles.encode("utf-8")), 20 * 1024)
        self.assertLessEqual(
            len(settings_styles.encode("utf-8")), MAX_SETTINGS_CSS_BYTES
        )
        self.assertLessEqual(len(switch_styles.encode("utf-8")), 4 * 1024)
        self.assertLessEqual(len(notice_styles.encode("utf-8")), 4 * 1024)
        self.assertLessEqual(len(navigation_styles.encode("utf-8")), 8 * 1024)
        self.assertLessEqual(len(kiosk_styles.encode("utf-8")), 12 * 1024)
        self.assertLessEqual(len(wizard_validation_styles.encode("utf-8")), 8 * 1024)
        self.assertLessEqual(len(catalog_styles.encode("utf-8")), 8 * 1024)
        self.assertLessEqual(len(energy_styles.encode("utf-8")), 18 * 1024)
        self.assertLessEqual(len(energy_chart_styles.encode("utf-8")), 8 * 1024)
        self.assertLessEqual(len(button_styles.encode("utf-8")), 8 * 1024)
        self.assertIn('hausman-hub-buttons.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-energy-chart.css?v=1.52.65', styles)
        self.assertLessEqual(len(rollout_styles.encode("utf-8")), 4 * 1024)
        self.assertIn('"/api/hausman_hub/panel/hausman-hub-panel.css?v=1.52.65"', content)
        self.assertIn('hausman-hub-settings.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-diagnostics.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-switch.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-notice.css?v=1.52.65', styles)
        self.assertIn(".notice { position:fixed", notice_styles)
        self.assertIn(".notice { position:fixed; z-index:1040", notice_styles)
        self.assertIn(".notice.is-error", notice_styles)
        self.assertIn('hausman-hub-device-maintenance.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-control-channel.css?v=1.52.65', styles)
        self.assertIn(".entity-group.device-card { container-type:inline-size; }", control_channel_styles)
        self.assertIn("grid-template-columns:minmax(0,.75fr) minmax(0,1.25fr)", control_channel_styles)
        self.assertIn(".device-channel-field select { width:100%; min-width:0; max-width:100%", control_channel_styles)
        self.assertIn("@container (max-width:520px)", control_channel_styles)
        self.assertIn('hausman-hub-weather-sources.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-wizard-validation.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-catalog.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-media-device.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-device-card.css?v=1.52.65', styles)
        self.assertIn(".device-sheet-backdrop { position:fixed", device_card_css)
        self.assertIn(
            ".inventory-device-card:not([open]) > .device-sheet-backdrop { display:none; pointer-events:none; }",
            device_card_css,
        )
        self.assertIn(".device-sheet {", device_card_css)
        self.assertIn('hausman-hub-scenarios.css?v=1.52.65', styles)
        self.assertIn('.scenario-editor-workspace { display:grid; grid-template-columns:286px minmax(0,1fr);', scenario_styles)
        self.assertIn('.scenario-editor-switch-track { position:relative; display:block!important;', scenario_styles)
        self.assertIn('.scenario-editor-overlay { position:fixed; z-index:1020;', scenario_styles)
        self.assertIn('hausman-hub-climate-overview.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-navigation.css?v=1.52.65', styles)
        self.assertIn('hausman-hub-kiosk.css?v=1.52.65', styles)
        self.assertIn(".kiosk-panorama-metrics", kiosk_styles)
        self.assertIn(".kiosk-panorama-intercom", kiosk_styles)
        self.assertIn('hausman-hub-rollout.css?v=1.52.65', styles)
        self.assertIn(":host(.kiosk-mode) .kiosk-dock", navigation_styles)
        self.assertIn(".banner { position:fixed", navigation_styles)
        self.assertIn(".banner { position:fixed; z-index:1041", navigation_styles)
        self.assertIn(".inventory-device-icon .icon { display:block;", styles)
        self.assertIn("validation-issue-row", content)
        self.assertIn(
            "grid-template-columns:30px minmax(0,1fr) auto",
            wizard_validation_styles,
        )
        self.assertIn(".validation-actions-main", wizard_validation_styles)
        tokens = TOKENS_CSS.read_text(encoding="utf-8")
        self.assertIn("--hmh-surface-canvas:#0B0F14", tokens)
        self.assertIn("--hmh-surface-canvas:#EEF1F6", tokens)
        self.assertIn(".page-header", styles)
        self.assertIn(".page-header { display:none; }", styles)
        self.assertIn("main.setup-shell { grid-template-columns:minmax(0,1fr); }", styles)
        self.assertIn("main.setup-shell > :not(.app-sidebar) { grid-column:1; }", styles)
        self.assertIn("main.setup-shell .page-header { display:flex; }", styles)
        self.assertIn(".inventory-device-card { container-type:inline-size;", catalog_styles)
        self.assertIn(".device-value-action > span { grid-column:1 / -1;", catalog_styles)
        self.assertIn("grid-template-columns:minmax(0,1fr) auto", catalog_styles)
        self.assertIn("@container (max-width:340px)", catalog_styles)
        self.assertIn("energy-device-live", energy_styles)
        self.assertIn("energy-device-accumulated", energy_styles)
        self.assertIn(".energy-device-visual { width:58px; height:58px; }", energy_styles)
        self.assertIn(".energy-device-card-open", energy_styles)
        self.assertIn('grid-template-areas:"visual identity live chevron"', energy_styles)
        self.assertIn(".energy-device-quick", energy_styles)
        self.assertIn(".energy-detail-layout", energy_styles)
        self.assertIn(".energy-device-chart-card", energy_styles)
        devices_styles = DEVICES_OVERVIEW_CSS.read_text(encoding="utf-8")
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", devices_styles)
        self.assertIn("grid-template-columns:24px minmax(0,1fr) auto", devices_styles)
        self.assertIn("overflow-wrap:anywhere", devices_styles)
        rooms_styles = ROOMS_CSS.read_text(encoding="utf-8")
        self.assertIn("@media (max-width:1380px)", rooms_styles)
        self.assertIn(
            ".rooms-canon-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }",
            rooms_styles,
        )
        self.assertIn("выбранные источники", ENERGY_JS.read_text(encoding="utf-8"))
        self.assertIn("Питание подключённой линии будет снято", ENERGY_JS.read_text(encoding="utf-8"))
        self.assertIn("Фактические данные Recorder Home Assistant", ENERGY_JS.read_text(encoding="utf-8"))
        self.assertIn("подтверждение|ожида", FEEDBACK_JS.read_text(encoding="utf-8"))
        self.assertIn("&& !this._deviceBindings.error", content)

    def test_catalog_refresh_is_stable_and_kiosk_is_available(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")
        navigation = NAVIGATION_JS.read_text(encoding="utf-8")
        kiosk = KIOSK_JS.read_text(encoding="utf-8")
        home_sections = HOME_SECTIONS_JS.read_text(encoding="utf-8")
        load_body = content.split("async _load()", 1)[1].split("async _loadScenarios()", 1)[0]
        activate_body = content.split("_activateSection(section, focus = false)", 1)[1].split(
            "_activateClimateView", 1
        )[0]

        self.assertNotIn("this._loadScenarios();", load_body)
        self.assertNotIn("this._loadSettings();", load_body)
        self.assertIn("this._sectionRenderKeys[sectionId] === key", content)
        self.assertIn("export function createKioskButton", navigation)
        self.assertIn("export function createKioskDock", navigation)
        self.assertIn("export function handleKioskPointerUp", navigation)
        self.assertIn("export async function toggleKioskMode", navigation)
        self.assertIn("export const renderKiosk", kiosk)
        self.assertIn("Дважды коснитесь свободного места", kiosk)
        self.assertIn("Открыть домофон", kiosk)
        self.assertIn('panel._activateSection("overview")', navigation)
        self.assertIn('el("section", "catalog-hero")', home_sections)
        self.assertIn('el("div", "catalog-toolbar")', home_sections)
        self.assertNotIn("this._error = false", activate_body)

    def test_visual_harness_covers_the_room_wizard_with_realistic_options(self) -> None:
        harness = (ROOT / "tests" / "visual" / "hausman-hub-panel-harness.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('screen === "wizard"', harness)
        self.assertIn('"first-run-room"', harness)
        self.assertIn('"first-run-home"', harness)
        self.assertIn('"first-run-validation"', harness)
        self.assertIn('panel._activeClimateView = screen === "wizard" ? "contour" : screen', harness)
        self.assertIn("panel._wizard.open = true", harness)
        self.assertIn('candidate_id: "living-temp"', harness)
        self.assertIn('candidate_id: "living-humidity"', harness)
        self.assertIn('candidate_id: "kids-trv"', harness)
        self.assertIn("__hausmanHubHarnessErrors", harness)
        self.assertIn("__hausmanHubHarnessCalls", harness)
        self.assertIn('path === "hausman_hub/v1/admin/scenarios"', harness)
        self.assertIn('path === "hausman_hub/v1/admin/scenarios/run"', harness)
        self.assertIn('path === "hausman_hub/v1/device-actions"', harness)
        self.assertIn('path === "hausman_hub/v1/admin/panel"', harness)
        self.assertIn("cloneHarnessValue(panel._homeDashboard)", harness)
        self.assertIn('get("figmaCapture") === "1"', harness)
        self.assertNotIn('<script src="https://mcp.figma.com/', harness)

    def test_quick_scenario_actions_keep_cards_visible_and_render_notices(self) -> None:
        scenarios = SCENARIOS_JS.read_text(encoding="utf-8")
        quick_save = scenarios.split("async function saveScenarioQuick", 1)[1].split(
            "async function deleteScenarioQuick", 1
        )[0]
        quick_delete = scenarios.split("async function deleteScenarioQuick", 1)[1].split(
            "function renderScenarioEditor", 1
        )[0]

        for body in (quick_save, quick_delete):
            self.assertNotIn("panel._scenarios.list = null", body)
            self.assertIn("panel._render()", body)

    def test_disabled_buttons_use_semantic_surface_border_and_text_tokens(self) -> None:
        styles = BUTTONS_CSS.read_text(encoding="utf-8")
        tokens = TOKENS_CSS.read_text(encoding="utf-8")

        for token in (
            "--hmh-control-disabled-background:#12171D",
            "--hmh-control-disabled-border:#28323D",
            "--hmh-control-disabled-text:#748191",
            "--hmh-control-disabled-background:#F6F8FB",
            "--hmh-control-disabled-border:#D5DCE5",
            "--hmh-control-disabled-text:#7B8796",
            "--hmh-disabled-bg:var(--hmh-control-disabled-background)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, tokens)

        self.assertIn(
            "button:disabled, button.secondary:disabled, button.primary:disabled",
            styles,
        )
        self.assertIn(".settings-page-actions button:disabled", styles)
        self.assertIn("button.danger.subtle, button.danger-outline", styles)
        self.assertIn("button.danger, button.danger-button", styles)

        self.assertNotIn("--disabled-color", styles)

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

    def test_disabled_climate_keeps_configured_counts_visible(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")
        metrics = HOME_SECTIONS_JS.read_text(encoding="utf-8")
        navigation = NAVIGATION_JS.read_text(encoding="utf-8")
        readiness = content.split(
            "_renderReadiness(container, readiness, snapshot, setup = null)", 1
        )[1].split("_renderRooms", 1)[0]

        self.assertIn("summary.room_count", metrics)
        self.assertIn("summary.device_count", metrics)
        self.assertIn("runtimeAvailable ? rooms.length : configuredRoomCount", metrics)
        self.assertIn("activeDevices: runtimeAvailable ? activeDevices : null", metrics)
        self.assertIn('"Устройств настроено"', readiness)
        self.assertIn('"Комнат настроено"', readiness)
        self.assertIn("Конфигурация сохранена:", readiness)
        self.assertIn("после включения наблюдения или управления", readiness)
        self.assertIn('metrics.activeDevices == null ? "Устройства настроены"', navigation)
        self.assertIn('const deviceValue = metrics.activeDevices == null', navigation)

        script = f"""
          const vm = require("vm");
          const fs = require("fs");
          vm.runInThisContext(
            fs.readFileSync({str(HOME_SECTIONS_JS)!r}, "utf8")
              .replace("export function renderHomeSection", "function renderHomeSection")
          );
          const saved = renderHomeSection.overviewMetrics(
            null,
            {{ rooms: [], summary: {{ room_count: 4, device_count: 12 }} }},
            (value) => value == null ? "Нет данных" : `${{value}} °C`,
            (value) => value == null ? "Нет данных" : `${{value}} %`
          );
          if (saved.roomCount !== 4 || saved.deviceCount !== 12) {{
            throw new Error("saved setup counts were not preserved");
          }}
          if (saved.runtimeAvailable || saved.activeDevices !== null) {{
            throw new Error("missing runtime was presented as live data");
          }}
          if (renderHomeSection.roomCountWord(4) !== "комнаты"
              || renderHomeSection.roomCountWord(12) !== "комнат") {{
            throw new Error("room count wording is invalid");
          }}
          const live = renderHomeSection.overviewMetrics(
            {{ rooms: [{{ temperature: 24, humidity: 45, devices: [{{ state: "on" }}] }}] }},
            {{ summary: {{ room_count: 4, device_count: 12 }} }},
            String,
            String
          );
          if (live.roomCount !== 1 || live.deviceCount !== 1 || live.activeDevices !== 1) {{
            throw new Error("live runtime did not take precedence");
          }}
        """
        completed = subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_overview_upcoming_events_block(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")
        overview = OVERVIEW_JS.read_text(encoding="utf-8")
        styles = OVERVIEW_CSS.read_text(encoding="utf-8")

        self.assertIn('"hausman_hub/v1/scenarios/upcoming"', content)
        self.assertIn('"hausman_hub/v1/scenarios/upcoming/cancel"', content)
        self.assertIn("this._upcomingEvents = results[9];", content)
        self.assertIn("upcomingCancelApi: SCENARIOS_UPCOMING_CANCEL_API", content)
        self.assertIn("_refreshUpcomingCountdowns()", content)
        self.assertIn("[data-upcoming-run-at]", content)
        self.assertIn("Ближайшие события", overview)
        self.assertIn("Нет запланированных событий", overview)
        self.assertIn("Пропустить", overview)
        self.assertIn("Пропустить запуск «", overview)
        self.assertIn("overview-canon-upcoming-event", overview)
        self.assertIn("и ещё ", overview)
        self.assertIn("export function upcomingTriggerLabel", overview)
        self.assertIn("export function formatUpcomingRunTime", overview)
        self.assertIn("export function formatUpcomingCountdown", overview)
        self.assertIn("export function upcomingEventsSorted", overview)
        self.assertIn("renderUpcomingEvents(panel, container, deps)", overview)
        self.assertIn(".overview-canon-upcoming-event", styles)
        self.assertIn(".overview-canon-upcoming-cancel", styles)

        script = f"""
          const vm = require("vm");
          const fs = require("fs");
          vm.runInThisContext(
            fs.readFileSync({str(OVERVIEW_JS)!r}, "utf8")
              .replace(/^import[^\\n]*\\n/gm, "")
              .replace(/^export function /gm, "function ")
          );
          if (upcomingTriggerLabel("time") !== "время"
              || upcomingTriggerLabel("sunrise") !== "рассвет"
              || upcomingTriggerLabel("sunset") !== "закат"
              || upcomingTriggerLabel("unknown") !== "время") {{
            throw new Error("trigger labels are invalid");
          }}
          if (formatUpcomingRunTime("2026-08-10T20:50:59.649054+06:00") !== "20:50") {{
            throw new Error("run time label is invalid");
          }}
          const now = Date.parse("2026-08-10T18:00:00+06:00");
          const countdownCases = [
            ["2026-08-10T20:15:00+06:00", "через 2 ч 15 мин"],
            ["2026-08-10T18:40:00+06:00", "через 40 мин"],
            ["2026-08-10T18:00:30+06:00", "менее чем через минуту"],
            ["2026-08-10T20:00:00+06:00", "через 2 ч"],
            ["2026-08-10T17:59:00+06:00", "запуск сейчас"],
            ["2026-08-12T21:00:00+06:00", "через 2 д 3 ч"],
          ];
          for (const [runAt, expected] of countdownCases) {{
            const actual = formatUpcomingCountdown(runAt, now);
            if (actual !== expected) throw new Error(`${{runAt}}: ${{actual}} !== ${{expected}}`);
          }}
          const events = {{ events: [6, 1, 4, 2, 5, 3, 0].map((hour) => ({{
            scenarioId: `s${{hour}}`, triggerId: `t${{hour}}`,
            runAt: `2026-08-10T${{String(10 + hour).padStart(2, "0")}}:00:00+06:00`,
          }})) }};
          const sorted = upcomingEventsSorted(events);
          if (sorted.visible.length !== 5 || sorted.remaining !== 2) {{
            throw new Error("upcoming events are not limited to five");
          }}
          if (sorted.visible[0].scenarioId !== "s0"
              || sorted.visible[4].scenarioId !== "s4") {{
            throw new Error("upcoming events are not sorted by runAt");
          }}
          const empty = upcomingEventsSorted(null);
          if (empty.visible.length !== 0 || empty.remaining !== 0) {{
            throw new Error("missing payload must produce an empty list");
          }}
        """
        completed = subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": "Asia/Omsk"},
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

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
        inventory = DEVICE_INVENTORY_JS.read_text(encoding="utf-8")

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
            '"hausman_hub/v1/admin/reset"',
            '"hausman_hub/v1/admin/device-area-assignments"',
            '"hausman_hub/v1/scenarios/upcoming"',
            '"hausman_hub/v1/scenarios/upcoming/cancel"',
        ):
            with self.subTest(approved=approved):
                self.assertIn(approved, content)
        self.assertIn('"hausman_hub/v1/admin/device-maintenance"', inventory)
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

    def test_climate_start_control_is_presented_as_a_guarded_canary(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")

        self.assertIn("Запустить пилотную комнату", content)
        self.assertIn("rollout.enable_allowed !== true", content)
        self.assertNotIn('managed ? "Остановить управление" : "Включить управление"', content)

    def test_room_assignment_copy_names_home_assistant_as_source_of_truth(self) -> None:
        content = PANEL_JS.read_text(encoding="utf-8")

        self.assertIn("Комната устройства хранится в Home Assistant", content)
        self.assertIn("Сохранить привязки в Home Assistant", content)
        self.assertNotIn(
            "Локальная привязка не изменяет области и устройства Home Assistant",
            content,
        )

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
            fs.readFileSync({str(HOME_SECTIONS_JS)!r}, "utf8").replace("export function renderHomeSection", "function renderHomeSection"),
            {{ filename: {str(HOME_SECTIONS_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(ROOM_SETUP_JS)!r}, "utf8").replace("export function renderFirstRunRoom", "function renderFirstRunRoom"),
            {{ filename: {str(ROOM_SETUP_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(INVENTORY_DUPLICATES_JS)!r}, "utf8").replace(/export /g, ""),
            {{ filename: {str(INVENTORY_DUPLICATES_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(DEVICE_INVENTORY_JS)!r}, "utf8").replace(/^import[\s\S]*?from .*;\s*/, "").replace("export function renderDeviceInventory", "function renderDeviceInventory"),
            {{ filename: {str(DEVICE_INVENTORY_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(DEVICE_BINDINGS_JS)!r}, "utf8").replace(/export /g, ""),
            {{ filename: {str(DEVICE_BINDINGS_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(AREA_BINDING_JS)!r}, "utf8").replace(/export /g, ""),
            {{ filename: {str(AREA_BINDING_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(FIRST_RUN_DRAFT_JS)!r}, "utf8").replace(/export /g, ""),
            {{ filename: {str(FIRST_RUN_DRAFT_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(NAVIGATION_JS)!r}, "utf8").replace(/export /g, ""),
            {{ filename: {str(NAVIGATION_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(ENERGY_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
            {{ filename: {str(ENERGY_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(DEVICE_CARD_JS)!r}, "utf8").replace(/export /g, ""),
            {{ filename: {str(DEVICE_CARD_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(MEDIA_DEVICE_JS)!r}, "utf8").replace(/export /g, ""),
            {{ filename: {str(MEDIA_DEVICE_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(SCENARIO_ICONS_JS)!r}, "utf8").replace(/export /g, ""),
            {{ filename: {str(SCENARIO_ICONS_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(SCENARIOS_JS)!r}, "utf8").replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
            {{ filename: {str(SCENARIOS_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(FEEDBACK_JS)!r}, "utf8").replace(/export /g, ""),
            {{ filename: {str(FEEDBACK_JS)!r} }}
          );
          vm.runInThisContext(
            fs.readFileSync({str(PANEL_JS)!r}, "utf8").replace(/^import .*;\\s*/gm, ""),
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
          if (!text.includes("Главная")) throw new Error("main heading missing");
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
          if (tabs.length !== 10) throw new Error("ten tablet sections missing");
          const tabLabels = tabs.map((node) => node["aria-label"]).join("|");
          if (tabLabels !== "Главная|Освещение|Климат|Комнаты|Медиа|Безопасность|Устройства|Энергия|Сценарии|Настройки") {{
            throw new Error("canonical tab order mismatch: " + tabLabels);
          }}
          const marks = visible.filter((node) => String(node.className).split(" ").includes("brand-mark"));
          if (marks.length < 1) throw new Error("HausmanHub brand mark missing");
          if (visible.some((node) => (
            node.tagName === "BUTTON"
            && !String(node.className).split(" ").includes("tab")
            && !String(node.className).split(" ").includes("theme-switch")
            && !String(node.className).split(" ").includes("header-intercom")
            && !String(node.className).split(" ").includes("sidebar-intercom")
            && !String(node.className).split(" ").includes("kiosk-toggle")
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
    fs.readFileSync(__HOME_SECTIONS_JS__, "utf8").replace("export function renderHomeSection", "function renderHomeSection"),
    { filename: __HOME_SECTIONS_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__ROOM_SETUP_JS__, "utf8").replace("export function renderFirstRunRoom", "function renderFirstRunRoom"),
    { filename: __ROOM_SETUP_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__INVENTORY_DUPLICATES_JS__, "utf8").replace(/export /g, ""),
    { filename: __INVENTORY_DUPLICATES_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__DEVICE_INVENTORY_JS__, "utf8").replace(/^import[\s\S]*?from .*;\s*/, "").replace("export function renderDeviceInventory", "function renderDeviceInventory"),
    { filename: __DEVICE_INVENTORY_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__DEVICE_BINDINGS_JS__, "utf8").replace(/export /g, ""),
    { filename: __DEVICE_BINDINGS_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__AREA_BINDING_JS__, "utf8").replace(/export /g, ""),
    { filename: __AREA_BINDING_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__FIRST_RUN_DRAFT_JS__, "utf8").replace(/export /g, ""),
    { filename: __FIRST_RUN_DRAFT_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__NAVIGATION_JS__, "utf8").replace(/export /g, ""),
    { filename: __NAVIGATION_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__ENERGY_JS__, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
    { filename: __ENERGY_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__DEVICE_CARD_JS__, "utf8").replace(/export /g, ""),
    { filename: __DEVICE_CARD_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__MEDIA_DEVICE_JS__, "utf8").replace(/export /g, ""),
    { filename: __MEDIA_DEVICE_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__SCENARIO_ICONS_JS__, "utf8").replace(/export /g, ""),
    { filename: __SCENARIO_ICONS_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__SCENARIOS_JS__, "utf8").replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
    { filename: __SCENARIOS_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__FEEDBACK_JS__, "utf8").replace(/export /g, ""),
    { filename: __FEEDBACK_JS__ }
  );
  vm.runInThisContext(
    fs.readFileSync(__PANEL_JS__, "utf8").replace(/^import .*;\\s*/gm, ""),
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
        script = (THEME_TEST_HARNESS
            .replace("__PANEL_JS__", repr(str(PANEL_JS)))
            .replace("__HOME_SECTIONS_JS__", repr(str(HOME_SECTIONS_JS)))
            .replace("__ROOM_SETUP_JS__", repr(str(ROOM_SETUP_JS)))
            .replace("__DEVICE_INVENTORY_JS__", repr(str(DEVICE_INVENTORY_JS)))
            .replace("__INVENTORY_DUPLICATES_JS__", repr(str(INVENTORY_DUPLICATES_JS)))
            .replace("__DEVICE_BINDINGS_JS__", repr(str(DEVICE_BINDINGS_JS)))
            .replace("__AREA_BINDING_JS__", repr(str(AREA_BINDING_JS)))
            .replace("__FIRST_RUN_DRAFT_JS__", repr(str(FIRST_RUN_DRAFT_JS)))) + body
        script = script.replace("__NAVIGATION_JS__", repr(str(NAVIGATION_JS)))
        script = script.replace("__ENERGY_JS__", repr(str(ENERGY_JS)))
        script = script.replace("__DEVICE_CARD_JS__", repr(str(DEVICE_CARD_JS)))
        script = script.replace("__MEDIA_DEVICE_JS__", repr(str(MEDIA_DEVICE_JS)))
        script = script.replace("__SCENARIO_ICONS_JS__", repr(str(SCENARIO_ICONS_JS)))
        script = script.replace("__SCENARIOS_JS__", repr(str(SCENARIOS_JS)))
        script = script.replace("__FEEDBACK_JS__", repr(str(FEEDBACK_JS)))
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
  const expectDayLight = (() => { const h = new Date().getHours(); return h >= 6 && h < 22; })();
  button.click();
  s = state();
  if (s.mode !== "daynight" || s.light !== expectDayLight || s.label !== "Тема: день/ночь (по времени суток)") {
    throw new Error("auto -> daynight cycle failed");
  }
  if (s.hint !== "день/ночь") throw new Error("daynight hint missing");
  button.click();
  s = state();
  if (s.mode !== "light" || !s.light || s.label !== "Тема: светлая") {
    throw new Error("daynight -> light cycle failed");
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
                "module_url": "/api/hausman_hub/panel/hausman-hub-panel.js?v=1.52.65",
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
