"""Executed-JavaScript tests for the HausmanHub panel settings sections."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
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
HOME_SECTIONS_JS = PANEL_JS.with_name("hausman-hub-home-sections.js")
CLIMATE_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-climate-overview.js")
LIGHTING_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-lighting.js")
ROOMS_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-rooms.js")
MEDIA_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-media-overview.js")
SECURITY_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-security-overview.js")
DEVICES_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-devices-overview.js")
TECHNICAL_LOG_JS = PANEL_JS.with_name("hausman-hub-technical-log.js")
FEEDBACK_JS = PANEL_JS.with_name("hausman-hub-feedback.js")
KIOSK_JS = PANEL_JS.with_name("hausman-hub-kiosk.js")
SETTINGS_PROFILE_JS = PANEL_JS.with_name("hausman-hub-settings-profile.js")
SETTINGS_ROOMS_JS = PANEL_JS.with_name("hausman-hub-settings-rooms.js")
ROOM_SETUP_JS = PANEL_JS.with_name("hausman-hub-room-setup.js")
DEVICE_INVENTORY_JS = PANEL_JS.with_name("hausman-hub-device-inventory.js")
INVENTORY_DUPLICATES_JS = PANEL_JS.with_name("hausman-hub-inventory-duplicates.js")
DEVICE_BINDINGS_JS = PANEL_JS.with_name("hausman-hub-device-bindings.js")
AREA_BINDING_JS = PANEL_JS.with_name("hausman-hub-area-binding.js")
FIRST_RUN_DRAFT_JS = PANEL_JS.with_name("hausman-hub-first-run-draft.js")
NAVIGATION_JS = PANEL_JS.with_name("hausman-hub-navigation.js")
ENERGY_JS = PANEL_JS.with_name("hausman-hub-energy.js")
ENERGY_CHART_JS = PANEL_JS.with_name("hausman-hub-energy-chart.js")
WEATHER_SOURCES_JS = PANEL_JS.with_name("hausman-hub-weather-sources.js")
MEDIA_DEVICE_JS = PANEL_JS.with_name("hausman-hub-media-device.js")
DEVICE_CARD_JS = PANEL_JS.with_name("hausman-hub-device-card.js")
SCENARIOS_JS = PANEL_JS.with_name("hausman-hub-scenarios.js")
SCENARIO_ICONS_JS = PANEL_JS.with_name("hausman-hub-scenario-icons.js")
SCENARIO_FIELDS_JS = PANEL_JS.with_name("hausman-hub-scenario-fields.js")
SETTINGS_CSS = PANEL_JS.with_name("hausman-hub-settings.css")
OVERVIEW_CSS = PANEL_JS.with_name("hausman-hub-overview.css")
SECURITY_OVERVIEW_CSS = PANEL_JS.with_name("hausman-hub-security-overview.css")
DEVICES_OVERVIEW_CSS = PANEL_JS.with_name("hausman-hub-devices-overview.css")
DIAGNOSTICS_JS = PANEL_JS.with_name("hausman-hub-diagnostics.js")
ROLLOUT_JS = PANEL_JS.with_name("hausman-hub-rollout.js")
OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-overview.js")
OVERVIEW_HERO_STATE_JS = PANEL_JS.with_name("hausman-hub-overview-hero-state.js")
ROOM_ICONS_JS = PANEL_JS.with_name("hausman-hub-room-icons.js")
HERO_ROOM_NAVIGATION_JS = PANEL_JS.with_name("hausman-hub-hero-room-navigation.js")
LIBRARY_HERO_JS = PANEL_JS.with_name("hausman-hub-library-hero.js")
DIAGNOSTICS_CSS = PANEL_JS.with_name("hausman-hub-diagnostics.css")
SWITCH_CSS = PANEL_JS.with_name("hausman-hub-switch.css")
DEVICE_BINDINGS_CSS = PANEL_JS.with_name("hausman-hub-device-bindings.css")

PANEL_PAYLOAD = {
    "contract": {"name": "hausman-hub-admin-panel", "version": 2},
    "snapshot": None,
    "readiness": {
        "status": "disabled",
        "bridge_mode": "disabled",
        "reasons": ["bridge_disabled"],
    },
}
MODE_PAYLOAD = {"mode": "disabled", "contour_configured": True}
HOME_PAYLOAD = {
    "home": {
        "outdoor_temperature_entity_id": None,
        "presence_entity_id": None,
        "central_heating_entity_id": None,
        "heating_lockout_high": 18.0,
        "heating_lockout_low": 16.0,
    },
    "candidates": {
        "outdoor_temperature": [
            {
                "entity_id": "sensor.external_temperature",
                "name": "Внешний датчик температуры",
                "available": True,
                "domain": "sensor",
                "device_class": "temperature",
                "room_id": "outside",
                "room_name": "Улица",
            },
            {
                "entity_id": "weather.home",
                "name": "Погода дома",
                "available": True,
                "domain": "weather",
                "room_id": "",
            },
        ],
        "presence": [
            {
                "entity_id": "person.ivsh_home",
                "name": "Дом",
                "available": True,
                "domain": "person",
                "room_id": "",
            },
        ],
        "central_heating": [],
    },
}
WINDOWS_PAYLOAD = {
    "rooms": [
        {
            "id": "living",
            "name": "Гостиная",
            "window_entity_id": None,
            "presence_entity_ids": [],
        },
        {
            "id": "kids",
            "name": "Детская",
            "window_entity_id": "binary_sensor.kids_window",
            "presence_entity_ids": [],
        },
    ],
    "candidates": [
        {
            "entity_id": "binary_sensor.living_window",
            "name": "Окно гостиной",
            "available": True,
            "device_class": "window",
            "domain": "binary_sensor",
            "room_id": "living",
            "room_name": "Гостиная",
        },
    ],
    "presence_candidates": [
        {
            "entity_id": "binary_sensor.living_motion",
            "name": "Движение гостиной",
            "available": True,
            "device_class": "motion",
            "domain": "binary_sensor",
            "room_id": "living",
            "room_name": "Гостиная",
        },
        {
            "entity_id": "binary_sensor.living_occupancy",
            "name": "Присутствие гостиной",
            "available": True,
            "device_class": "occupancy",
            "domain": "binary_sensor",
            "room_id": "living",
            "room_name": "Гостиная",
        },
    ],
}
DISPLAY_NAMES = {
    "strategies": {"soft": "Плавно", "normal": "Обычно", "aggressive": "Быстро"},
    "profiles": {"day": "День", "night": "Ночь"},
    "modes": {"observe": "Наблюдение", "automatic": "Автоматический"},
    "statuses": {},
    "issues": {},
}
CONFIGURED_SETUP = {
    "contract": {"name": "hausman-hub-climate-current-setup", "version": 1},
    "generated_at": 1784280000,
    "snapshot_revision": 10,
    "setup_revision": 123,
    "status": "ready",
    "editing_allowed": True,
    "display_names": DISPLAY_NAMES,
    "name": "Климат",
    "mode": "automatic",
    "schedule": {
        "enabled": False,
        "day_start": "07:00",
        "night_start": "23:00",
        "last_applied_profile": None,
    },
    "rooms": [
        {
            "id": "living",
            "name": "Гостиная",
            "devices": [],
            "profiles": {
                "day": {
                    "target_temperature": 23.0,
                    "target_humidity": 45,
                    "strategy": "normal",
                },
                "night": {
                    "target_temperature": 20.0,
                    "target_humidity": 40,
                    "strategy": "soft",
                },
                "active_profile": "day",
            },
            "temporary_temperature": None,
        }
    ],
    "issues": [],
    "summary": {"room_count": 1, "device_count": 0},
}
NOT_CONFIGURED_SETUP = {
    "contract": {"name": "hausman-hub-climate-current-setup", "version": 1},
    "generated_at": 1784280000,
    "snapshot_revision": 10,
    "setup_revision": 5,
    "status": "not_configured",
    "editing_allowed": False,
    "display_names": DISPLAY_NAMES,
    "name": None,
    "mode": None,
    "schedule": None,
    "rooms": [],
    "issues": [{"code": "not_configured", "room_id": None, "candidate_id": None, "message": "Ещё не настроен"}],
    "summary": {"room_count": 0, "device_count": 0},
}

GET_PATHS = {
    "hausman_hub/v1/admin/panel": PANEL_PAYLOAD,
    "hausman_hub/v1/admin/climate-mode": MODE_PAYLOAD,
    "hausman_hub/v1/admin/home-environment": HOME_PAYLOAD,
    "hausman_hub/v1/admin/climate-room-signals": WINDOWS_PAYLOAD,
    "hausman_hub/v1/admin/climate-drafts/current": CONFIGURED_SETUP,
    "hausman_hub/v1/tablet-profile": {
        "revision": 0,
        "settings": {
            "startScreen": {"mode": "dashboard", "heroTargetId": None},
            "kiosk": {"enabled": False, "enterAfterIdleSeconds": 300, "doubleTapToExit": True},
            "displayAutomation": {
                "enabled": False, "sensorEntityIds": [], "signalMode": "any",
                "wakeBrightnessPercent": 100, "dimBrightnessPercent": 50,
                "dimAfterSeconds": 60, "sleepAfterSeconds": 300,
                "unavailablePolicy": "keep_awake",
            },
            "dayNight": {
                "enabled": False, "dayStartsAt": "07:00", "nightStartsAt": "22:00",
                "deepNightStartsAt": "00:30", "dayVolumePercent": 60,
                "nightVolumePercent": 30, "deepNightVolumePercent": 15,
                "appExitBrightnessPercent": 50,
            },
            "alerts": {
                "lowBatteryThresholdPercent": 8, "lowBatterySnoozeMinutes": 60,
                "criticalSoundEnabled": True, "criticalVolumePercent": 100,
            },
            "dashboard": {"favoriteScenarioIds": [], "visibleDeviceIds": []},
            "intercom": {"showQuickAccess": False, "deviceId": None},
        },
    },
}
DEVICE_BINDINGS_PAYLOAD = {
    "contract": {"name": "hausman-hub-climate-device-binding-options", "version": 1},
    "snapshot_revision": 456,
    "rooms": [
        {
            "id": "living",
            "name": "Гостиная",
            "devices": [
                {
                    "device_id": "living_ac",
                    "name": "Кондиционер гостиная",
                    "room_id": "living",
                    "room_name": "Гостиная",
                    "kind": "air_conditioner",
                    "kind_name": "Кондиционер",
                    "role": "control",
                    "current_entity_id": None,
                    "current_available": False,
                    "candidates": [
                        {
                            "entity_id": "climate.living_ac",
                            "name": "Кондиционер гостиная",
                            "room_id": "living",
                            "room_name": "Гостиная",
                            "same_room": True,
                            "available": True,
                            "device_name": "Кондиционер гостиная",
                            "manufacturer": "Yandex",
                            "model": "YNDX-0006",
                            "image_url": None,
                        },
                        {
                            "entity_id": "climate.kids_ac",
                            "name": "Кондиционер детская",
                            "room_id": "kids",
                            "room_name": "Детская",
                            "same_room": False,
                            "available": True,
                            "device_name": "Кондиционер детская",
                            "manufacturer": "Yandex",
                            "model": "YNDX-0006",
                            "image_url": None,
                        },
                    ],
                }
            ],
        }
    ],
    "summary": {"device_count": 1, "bound_count": 0, "missing_count": 1, "candidate_count": 2},
}
AI_ASSISTANT_PATH = "hausman_hub/v1/admin/ai-assistant"
AI_ASSISTANT_SETTINGS_PATH = f"{AI_ASSISTANT_PATH}/settings"
AI_ASSISTANT_REFRESH_PATH = f"{AI_ASSISTANT_PATH}/refresh"
AI_ASSISTANT_PAYLOAD = {
    "settings": {
        "enabled": True,
        "preset": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_set": True,
    },
    "stats": {
        "aggregates": [
            {
                "preset": "deepseek",
                "model": "deepseek-chat",
                "calls": 3,
                "successes": 2,
                "auth_errors": 0,
                "http_errors": 0,
                "timeout_errors": 1,
                "invalid_errors": 0,
                "prompt_tokens": 120,
                "completion_tokens": 48,
                "latency_ms": 1500,
            }
        ],
        "recent_calls": [
            {
                "ts": 1784280000000,
                "preset": "deepseek",
                "model": "deepseek-chat",
                "status": "provider_timeout",
                "summary_code": "evidence_limited",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "latency_ms": 500,
                "error_class": "timeout",
            }
        ],
    },
    "last_advisory": {
        "version": 1,
        "source": "provider",
        "generated_at": 1784280000000,
        "status": "ready",
        "summary": "advisory_available",
        "recommendations": [
            {
                "code": "review_temperature_gap",
                "priority": "warning",
                "evidence": ["temperature_above_comfort"],
                "room_id": "living",
            }
        ],
        "risk_flags": [
            {
                "code": "temperature_outside_comfort_band",
                "priority": "warning",
                "evidence": ["temperature_above_comfort"],
                "room_id": "living",
            }
        ],
    },
}


def panel_script(
    get_payloads: dict,
    post_table: dict,
    assertions: str,
    before_panel: str = "",
) -> str:
    """Build one executed-JavaScript scenario around the real panel module."""

    return f"""
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
          this.value = "";
          this.checked = false;
          this.attributes = {{}};
          this._listeners = {{}};
          this.parentElement = null;
          this.classList = {{
            add: (...names) => {{
              const values = new Set(String(this.className).split(" ").filter(Boolean));
              names.forEach((name) => values.add(name));
              this.className = [...values].join(" ");
            }},
            remove: (...names) => {{
              const removed = new Set(names);
              this.className = String(this.className).split(" ")
                .filter((name) => name && !removed.has(name)).join(" ");
            }},
            contains: (name) => String(this.className).split(" ").includes(name),
            toggle: (name, enabled) => {{
              if (enabled) this.classList.add(name); else this.classList.remove(name);
            }},
          }};
        }}
        appendChild(child) {{
          child.parentElement = this;
          this.children.push(child);
          return child;
        }}
        setAttribute(name, value) {{
          this.attributes[name] = String(value);
          this[name] = String(value);
          if (name === "class") this.className = String(value);
        }}
        removeAttribute(name) {{
          delete this.attributes[name];
          delete this[name];
        }}
        addEventListener(type, handler) {{
          (this._listeners[type] = this._listeners[type] || []).push(handler);
        }}
        fire(type, event = {{}}) {{
          (this._listeners[type] || []).forEach((handler) => handler(event));
        }}
        click() {{ this.fire("click"); }}
        focus() {{
          this.focused = true;
        }}
        querySelector(selector) {{
          const className = String(selector).startsWith(".") ? String(selector).slice(1) : null;
          let result = null;
          const visitNode = (node) => {{
            if (result) return;
            if (className && String(node.className).split(" ").includes(className)) result = node;
            node.children.forEach(visitNode);
          }};
          this.children.forEach(visitNode);
          return result;
        }}
        remove() {{
          if (!this.parentElement) return;
          this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
          this.parentElement = null;
        }}
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
      const clipboardWrites = [];
      const historyCalls = [];
      const windowListeners = {{}};
      const setWindowLocation = (value) => {{
        const parsed = new URL(value, "https://homeassistant.local/hausman-hub");
        global.window.location.href = parsed.href;
        global.window.location.search = parsed.search;
      }};
      global.window = {{
        confirm: () => true,
        location: {{
          href: "https://homeassistant.local/hausman-hub",
          search: "",
        }},
        history: {{
          pushState: (state, title, value) => {{
            historyCalls.push({{ state, value: String(value) }});
            setWindowLocation(String(value));
          }},
        }},
        addEventListener: (type, handler) => {{ windowListeners[type] = handler; }},
        removeEventListener: (type, handler) => {{
          if (windowListeners[type] === handler) delete windowListeners[type];
        }},
      }};
      Object.defineProperty(globalThis, "navigator", {{
        configurable: true,
        value: {{
          clipboard: {{
            writeText: (value) => {{
              clipboardWrites.push(String(value));
              return Promise.resolve();
            }},
          }},
        }},
      }});
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
        fs.readFileSync({str(CLIMATE_OVERVIEW_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(CLIMATE_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(LIGHTING_OVERVIEW_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(LIGHTING_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ROOMS_OVERVIEW_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(ROOMS_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(MEDIA_OVERVIEW_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(MEDIA_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(SECURITY_OVERVIEW_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(SECURITY_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(DEVICES_OVERVIEW_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(DEVICES_OVERVIEW_JS)!r} }}
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
        fs.readFileSync({str(ENERGY_CHART_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(ENERGY_CHART_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ENERGY_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(ENERGY_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(WEATHER_SOURCES_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(WEATHER_SOURCES_JS)!r} }}
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
        fs.readFileSync({str(SCENARIO_FIELDS_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(SCENARIO_FIELDS_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(SCENARIOS_JS)!r}, "utf8").replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(SCENARIOS_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(DIAGNOSTICS_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(DIAGNOSTICS_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ROLLOUT_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(ROLLOUT_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ROOM_ICONS_JS)!r}, "utf8").replace(/SVG_NAMESPACE/g, "ROOM_ICON_SVG_NAMESPACE").replace(/export /g, ""),
        {{ filename: {str(ROOM_ICONS_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(HERO_ROOM_NAVIGATION_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(HERO_ROOM_NAVIGATION_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(LIBRARY_HERO_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(LIBRARY_HERO_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(OVERVIEW_HERO_STATE_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(OVERVIEW_HERO_STATE_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(OVERVIEW_JS)!r}, "utf8").replace(/^import .*;\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(TECHNICAL_LOG_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(TECHNICAL_LOG_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(FEEDBACK_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(FEEDBACK_JS)!r} }}
      );
      const log = recordTechnicalEvent;
      vm.runInThisContext(
        fs.readFileSync({str(KIOSK_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(KIOSK_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(SETTINGS_PROFILE_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(SETTINGS_PROFILE_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(SETTINGS_ROOMS_JS)!r}, "utf8").replace(/^import[\s\S]*?from .*;\s*/m, "").replace(/export /g, ""),
        {{ filename: {str(SETTINGS_ROOMS_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(PANEL_JS)!r}, "utf8").replace(/^import .*;\\s*/gm, ""),
        {{ filename: {str(PANEL_JS)!r} }}
      );

      const getTable = {json.dumps(get_payloads, ensure_ascii=False)};
      const postTable = {json.dumps(post_table, ensure_ascii=False)};
      const calls = [];
      const wsMessages = [];
      let userPreferenceValue = null;
      let userPreferenceReadResolve = null;
      const hass = {{
        connection: {{
          sendMessagePromise: (message) => {{
            wsMessages.push(message);
            if (message.type === "frontend/get_user_data") {{
              if (global.deferUserPreferenceRead) {{
                return new Promise((resolve) => {{ userPreferenceReadResolve = resolve; }});
              }}
              return Promise.resolve({{ value: userPreferenceValue }});
            }}
            if (message.type === "frontend/set_user_data") {{
              userPreferenceValue = message.value;
              return Promise.resolve();
            }}
            if (message.type === "config/area_registry/update") {{
              return Promise.resolve({{
                area_id: message.area_id,
                icon: message.icon,
              }});
            }}
            return Promise.reject(new Error("unexpected WS " + message.type));
          }},
        }},
        callApi: (method, path, payload) => {{
          calls.push({{ method, path, payload }});
          if (method === "GET") {{
            if (path.startsWith("hausman_hub/v1/energy/history?") && "__energy_history__" in getTable) {{
              return Promise.resolve(getTable.__energy_history__);
            }}
            if (!(path in getTable)) return Promise.reject(new Error("unexpected GET " + path));
            const result = getTable[path];
            if (result && result.__fail) return Promise.reject(new Error("GET failed"));
            return Promise.resolve(result);
          }}
          const result = postTable[path];
          if (result && result.__fail) {{
            const error = new Error("POST failed");
            error.status = result.__fail;
            return Promise.reject(error);
          }}
          return Promise.resolve(result || {{ status: "up_to_date" }});
        }},
      }};

      const visit = (node, action) => {{
        action(node);
        node.children.forEach((child) => visit(child, action));
      }};
      const findAll = (root, predicate) => {{
        const found = [];
        visit(root, (node) => {{ if (predicate(node)) found.push(node); }});
        return found;
      }};
      const textOf = (root) => {{
        const parts = [];
        visit(root, (node) => parts.push(node.textContent));
        return parts.join("\\n");
      }};
      const tick = async (count = 5) => {{
        for (let index = 0; index < count; index += 1) {{
          await new Promise((resolve) => setImmediate(resolve));
        }}
      }};

      (async () => {{
        const Panel = registry.get("hausman-hub-panel");
        {before_panel}
        const panel = new Panel();
        panel.hass = hass;
        await tick();
        {assertions}
      }})().catch((error) => {{
        console.error(error);
        process.exit(1);
      }});
    """


def run_panel_script(script: str) -> subprocess.CompletedProcess[str]:
    """Execute one panel scenario in Node and return the completed process."""

    return subprocess.run(
        ("node", "--input-type=commonjs"),
        input=script,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


class PanelSettingsSectionsTest(unittest.TestCase):
    """The settings sections render and post the strict admin contracts."""

    def test_panel_stays_hidden_until_styles_load_and_logo_has_safe_size(self) -> None:
        script = panel_script(
            dict(GET_PATHS),
            {},
            """
        const stylesheet = panel.shadowRoot.children.find((node) => node.tagName === "LINK");
        const main = panel.shadowRoot.children.find((node) => node.tagName === "MAIN");
        if (!stylesheet || !main || main.style.visibility !== "hidden") {
          throw new Error("panel content became visible before its stylesheet loaded");
        }
        const logoShells = findAll(panel.shadowRoot, (node) =>
          String(node.className || node.class).split(" ").includes("brand-mark-shell"));
        const logoLetters = findAll(panel.shadowRoot, (node) =>
          String(node.className || node.class).split(" ").includes("brand-mark-letter"));
        if (!logoShells.length || logoShells.some((node) => node.width !== "32" || node.height !== "38")) {
          throw new Error("logo shell has no safe intrinsic size");
        }
        if (!logoLetters.length || logoLetters.some((node) => node.width !== "18" || node.height !== "16")) {
          throw new Error("logo letter has no safe intrinsic size");
        }
        stylesheet.fire("load");
        if (main.style.visibility !== "") throw new Error("panel stayed hidden after stylesheet load");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_disabled_not_configured_state_stays_honest(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/climate-drafts/current"] = NOT_CONFIGURED_SETUP
        payloads["hausman_hub/v1/admin/climate-mode"] = {
            "mode": "disabled",
            "contour_configured": False,
        }
        script = panel_script(
            payloads,
            {},
            """
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Климатический контур ещё не настроен")) {
          throw new Error("first-run instruction missing");
        }
        if (!panel._shell.nav.hidden || panel._shell.wizard.hidden) {
          throw new Error("unconfigured setup left regular tabs visible");
        }
        if (panel._activeSection !== "overview" || !panel._shell.sectionNodes.overview.hidden) {
          throw new Error("first-run exposed an editable overview section");
        }
        if (text.includes("Сохранить профили") || text.includes("Сохранить сигналы дома")) {
          throw new Error("editable settings rendered before contour setup");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_tablet_sections_switch_locally_keep_dirty_values_and_support_keyboard(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const tabs = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && String(node.className).split(" ").includes("tab"));
        const labels = tabs.map((node) => node["aria-label"]);
        const expected = [
          "Главная", "Освещение", "Климат", "Комнаты", "Медиа",
          "Безопасность", "Устройства", "Энергия", "Сценарии", "Настройки",
        ];
        if (JSON.stringify(labels) !== JSON.stringify(expected)) {
          throw new Error("tab labels mismatch: " + JSON.stringify(labels));
        }
        if (panel._activeSection !== "overview" || panel._shell.sectionNodes.overview.hidden) {
          throw new Error("configured setup did not default to overview");
        }
        if (tabs[0]["aria-current"] !== "page") throw new Error("overview aria-current missing");
        if (tabs[0].role !== "tab" || tabs[0]["aria-selected"] !== "true"
          || panel._shell.nav.role !== "tablist"
          || panel._shell.sectionNodes.overview.role !== "tabpanel") {
          throw new Error("tab accessibility semantics missing");
        }
        const getCount = calls.filter((call) => call.method === "GET").length;
        tabs[2].fire("click");
        await tick();
        const climateLoadedCount = calls.filter((call) => call.method === "GET").length;
        const dayTemperature = findAll(panel.shadowRoot, (node) => node.type === "number")
          .find((node) => String(node.value) === "23");
        dayTemperature.value = "24.5";
        dayTemperature.fire("input");
        tabs[0].fire("click");
        tabs[2].fire("click");
        if (dayTemperature.value !== "24.5" || panel._dirty.profiles !== true) {
          throw new Error("tab switch discarded dirty profile value");
        }
        if (!String(tabs[2].className).includes("is-dirty")) {
          throw new Error("dirty tab indicator missing");
        }
        if (calls.filter((call) => call.method === "GET").length !== climateLoadedCount) {
          throw new Error("tab switch called an API");
        }
        let prevented = false;
        tabs[2].fire("keydown", {
          key: "ArrowRight",
          preventDefault: () => { prevented = true; },
        });
        if (!prevented || panel._activeSection !== "rooms" || !tabs[3].focused) {
          throw new Error("keyboard tab navigation failed");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_tablet_navigation_has_deep_links_back_state_and_working_sections(self) -> None:
        script = panel_script(
            GET_PATHS | {AI_ASSISTANT_PATH: AI_ASSISTANT_PAYLOAD},
            {},
            """
        const ordered = [
          "overview", "lighting", "climate", "rooms", "media",
          "security", "devices", "energy", "scenarios", "settings",
        ];
        const expectedCopy = {
          overview: ["Управление климатом"],
          lighting: ["Освещение"],
          climate: ["Климат"],
          rooms: ["Комнаты"],
          media: ["Медиа"],
          security: ["Безопасность"],
          devices: ["Устройства"],
          energy: ["Энергия", "Данные энергии"],
          scenarios: ["Сценарии", "сценарий"],
          settings: ["Настройки"],
        };
        const persistentControls = {
          intercom: findAll(panel.shadowRoot, (node) =>
            node.tagName === "BUTTON" && node["aria-label"] === "Открыть домофон")[0],
          headerKiosk: panel._shell.kioskButton,
          sidebarKiosk: panel._shell.sidebarKiosk,
          kioskDock: panel._shell.kioskDock,
        };
        if (Object.values(persistentControls).some((control) => !control)) {
          throw new Error("persistent tablet controls are incomplete");
        }
        if (persistentControls.intercom.style.display !== "none") {
          throw new Error("unconfigured intercom quick access must stay hidden");
        }
        for (const section of ordered) {
          panel._shell.tabs[section].fire("click");
          await tick();
          if (panel._activeSection !== section || panel._shell.sectionNodes[section].hidden) {
            throw new Error("top-level section is not functional: " + section);
          }
          const visibleSections = ordered.filter((candidate) =>
            panel._shell.sectionNodes[candidate].hidden === false);
          if (visibleSections.length !== 1 || visibleSections[0] !== section) {
            throw new Error("section visibility contract failed: " + visibleSections.join(","));
          }
          const selectedTabs = ordered.filter((candidate) =>
            panel._shell.tabs[candidate]["aria-selected"] === "true"
            && panel._shell.tabs[candidate]["aria-current"] === "page");
          if (selectedTabs.length !== 1 || selectedTabs[0] !== section) {
            throw new Error("navigation accessibility state failed: " + selectedTabs.join(","));
          }
          const sectionCopy = textOf(panel._shell.sectionNodes[section]).replace(/\\s+/g, " ").trim();
          if (sectionCopy.length < 20 || !expectedCopy[section].some((copy) => sectionCopy.includes(copy))) {
            throw new Error("section has no useful content: " + section + " / " + sectionCopy);
          }
          if (persistentControls.intercom.hidden
              || persistentControls.headerKiosk.hidden
              || persistentControls.sidebarKiosk.hidden) {
            throw new Error("persistent control disappeared in section: " + section);
          }
        }
        if (persistentControls.intercom !== findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node["aria-label"] === "Открыть домофон")[0]) {
          throw new Error("navigation rerendered the persistent intercom control");
        }
        panel._shell.tabs.climate.fire("click");
        panel._shell.climateTabs.profiles.fire("click");
        const climateRoute = historyCalls[historyCalls.length - 1].value;
        if (!climateRoute.includes("hh_section=climate") || !climateRoute.includes("hh_view=profiles")) {
          throw new Error("climate deep link mismatch: " + climateRoute);
        }
        panel._shell.tabs.settings.fire("click");
        const systemButton = findAll(panel._shell.settings, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Диагностика")[0];
        systemButton.fire("click");
        const settingsRoute = historyCalls[historyCalls.length - 1].value;
        if (!settingsRoute.includes("hh_section=settings") || !settingsRoute.includes("hh_view=system")) {
          throw new Error("settings deep link mismatch: " + settingsRoute);
        }
        setWindowLocation("https://homeassistant.local/hausman-hub?hh_section=rooms");
        panel._onNavigationPop();
        if (panel._activeSection !== "rooms" || panel._shell.sectionNodes.rooms.hidden) {
          throw new Error("browser back state did not restore rooms");
        }
        setWindowLocation("https://homeassistant.local/hausman-hub?hh_section=climate&hh_view=assistant");
        panel._onNavigationPop();
        await tick();
        if (!panel._assistant.loaded || panel._assistant.data?.settings?.enabled !== true) {
          throw new Error("assistant deep link did not load its data");
        }
        if (!calls.some((call) => call.method === "GET" && call.path === "hausman_hub/v1/admin/ai-assistant")) {
          throw new Error("assistant deep link did not call its read-only API");
        }
        setWindowLocation("https://homeassistant.local/hausman-hub");
        panel._onNavigationPop();
        if (panel._activeSection !== "overview" || panel._shell.sectionNodes.overview.hidden) {
          throw new Error("route without HausmanHub params did not restore overview");
        }
        setKioskState(panel, true);
        const kioskCopy = textOf(panel._shell.kioskSurface).replace(/\\s+/g, " ");
        for (const required of ["HAUSMANHUB", "Климат", "Энергия", "Воздух", "Избранные сценарии", "Погода", "Дом сейчас"]) {
          if (!kioskCopy.includes(required)) throw new Error("kiosk panorama is incomplete: " + required);
        }
        if (kioskCopy.includes("Открыть домофон") || kioskCopy.includes("Без подтверждения")) {
          throw new Error("unconfigured intercom leaked into kiosk panorama");
        }
        if (ordered.some((section) => panel._shell.sectionNodes[section].hidden === false)) {
          throw new Error("regular section stayed visible behind kiosk panorama");
        }
        const climateMetric = findAll(panel._shell.kioskSurface, (node) =>
          node.tagName === "BUTTON" && textOf(node).includes("Климат"))[0];
        climateMetric.fire("click");
        await tick();
        if (panel._kioskMode || panel._activeSection !== "climate" || panel._shell.sectionNodes.climate.hidden) {
          throw new Error("kiosk metric did not open its full section");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_intercom_requires_explicit_profile_and_saves_through_tablet_api(self) -> None:
        profile = json.loads(json.dumps(GET_PATHS["hausman_hub/v1/tablet-profile"]))
        profile["revision"] = 4
        profile["settings"]["intercom"] = {
            "showQuickAccess": True,
            "deviceId": "entry_intercom",
        }
        dashboard = {
            "devices": [
                {
                    "id": "entry_intercom",
                    "physicalId": "entry_intercom",
                    "entityId": "button.entry_intercom_open",
                    "name": "Домофон у входа",
                    "roomName": "Тамбур",
                    "domain": "button",
                    "category": "security",
                    "details": [],
                    "controls": [],
                }
            ],
            "rooms": [],
            "alarms": [],
        }
        catalog = {
            "devices": [
                {
                    "entity_id": "button.entry_intercom_open",
                    "target_id": "button.entry_intercom_open",
                    "actions": [{"action_id": "press", "allowed_fields": []}],
                }
            ]
        }
        saved_profile = json.loads(json.dumps(profile))
        saved_profile["revision"] = 5
        script = panel_script(
            GET_PATHS
            | {
                "hausman_hub/v1/tablet-profile": profile,
                "hausman_hub/v1/dashboard": dashboard,
                "hausman_hub/v1/admin/scenarios/catalog": catalog,
            },
            {"hausman_hub/v1/tablet-profile": saved_profile},
            """
        const headerIntercom = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node["aria-label"] === "Открыть домофон")[0];
        if (!headerIntercom || headerIntercom.style.display === "none") {
          throw new Error("configured intercom quick access is hidden");
        }
        setKioskState(panel, true);
        if (!textOf(panel._shell.kioskSurface).includes("Открыть домофон")) {
          throw new Error("configured intercom is missing in kiosk");
        }
        setKioskState(panel, false);
        panel._activateSection("settings");
        panel._activateSettingsView("intercom");
        const settingsText = textOf(panel._shell.settings);
        if (!settingsText.includes("Домофон у входа") || !settingsText.includes("Устройство найдено")) {
          throw new Error("intercom settings are not understandable: " + settingsText);
        }
        const quick = findAll(panel._shell.settings, (node) =>
          node.tagName === "BUTTON" && node["aria-label"] === "Показывать быстрый доступ к домофону")[0];
        quick.fire("click");
        const save = findAll(panel._shell.settings, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Сохранить домофон")[0];
        save.fire("click");
        await tick();
        const write = calls.find((call) => call.method === "PUT"
          && call.path === "hausman_hub/v1/tablet-profile");
        if (!write || write.payload.expectedRevision !== 4
            || write.payload.settings.intercom.showQuickAccess !== false
            || write.payload.settings.intercom.deviceId !== "entry_intercom") {
          throw new Error("tablet profile write contract mismatch: " + JSON.stringify(write));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_legacy_empty_intercom_profile_is_hidden_and_redirects_to_settings(self) -> None:
        profile = json.loads(json.dumps(GET_PATHS["hausman_hub/v1/tablet-profile"]))
        profile["settings"]["intercom"] = {"showQuickAccess": True, "deviceId": None}
        script = panel_script(
            GET_PATHS | {"hausman_hub/v1/tablet-profile": profile},
            {},
            """
        const intercom = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node["aria-label"] === "Открыть домофон")[0];
        if (!intercom || intercom.style.display !== "none") {
          throw new Error("legacy empty intercom profile must stay hidden");
        }
        openIntercomFromRail(panel);
        if (panel._activeSection !== "settings" || panel._activeSettingsView !== "intercom") {
          throw new Error("unconfigured intercom did not open its settings");
        }
        if (!textOf(panel.shadowRoot).includes("Домофон ещё не настроен")) {
          throw new Error("unconfigured intercom explanation is missing");
        }
        if (calls.some((call) => call.path === "hausman_hub/v1/device-actions")) {
          throw new Error("unconfigured intercom sent a device command");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_room_purpose_is_saved_to_home_assistant_area_registry(self) -> None:
        dashboard = {
            "summary": {},
            "rooms": [
                {
                    "id": "room_alice",
                    "name": "Комната Алисы",
                    "icon": None,
                    "deviceIds": [],
                },
                {
                    "id": "shower",
                    "name": "Душевая",
                    "icon": None,
                    "deviceIds": [],
                },
            ],
            "devices": [],
            "alarms": [],
        }
        script = panel_script(
            GET_PATHS | {"hausman_hub/v1/dashboard": dashboard},
            {},
            """
        panel._activateSection("settings");
        panel._activateSettingsView("rooms");
        let screen = panel._shell.settings;
        const roomCards = findAll(screen, (node) =>
          String(node.className || "").split(" ").includes("settings-room-card"));
        if (roomCards.length !== 2) {
          throw new Error("all Home Assistant rooms are not shown");
        }
        const aliceCard = roomCards.find((node) => textOf(node).includes("Комната Алисы"));
        const showerCard = roomCards.find((node) => textOf(node).includes("Душевая"));
        if (!aliceCard || !showerCard) throw new Error("named rooms are missing");
        const aliceSelect = findAll(aliceCard, (node) =>
          String(node.className || "").split(" ").includes("settings-room-type-select"))[0];
        const showerSelect = findAll(showerCard, (node) =>
          String(node.className || "").split(" ").includes("settings-room-type-select"))[0];
        const optionLabels = findAll(screen, (node) => node.tagName === "OPTION")
          .map((node) => node.textContent);
        if (!optionLabels.includes("Детская") || !optionLabels.includes("Ванная или душевая")) {
          throw new Error("canonical tablet room purposes are missing");
        }
        aliceSelect.value = "child";
        aliceSelect.fire("change");
        const save = findAll(aliceCard, (node) =>
          String(node.className || "").split(" ").includes("settings-room-type-save"))[0];
        if (!save || save.disabled) throw new Error("room purpose cannot be saved");
        save.fire("click");
        await tick();
        const update = wsMessages.find((message) =>
          message.type === "config/area_registry/update" && message.area_id === "room_alice");
        if (!update || update.icon !== "mdi:human-child") {
          throw new Error("canonical room icon was not written to Area Registry: "
            + JSON.stringify(update));
        }
        if (!panel._homeDashboard.rooms.find((room) => room.id === "room_alice")
          || !String(panel._notice || "").includes("сохранено в Home Assistant")) {
          throw new Error("saved room purpose is not reflected in the interface");
        }
        if (showerSelect.value !== "bathroom") {
          throw new Error("existing room name fallback is not mapped to bathroom");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_intercom_picker_includes_catalog_only_doorphone_and_hides_unrelated_devices(self) -> None:
        profile = json.loads(json.dumps(GET_PATHS["hausman_hub/v1/tablet-profile"]))
        profile["settings"]["intercom"] = {"showQuickAccess": False, "deviceId": None}
        dashboard = {
            "devices": [
                {
                    "id": "bathroom_fan",
                    "physicalId": "bathroom_fan",
                    "entityId": "switch.bathroom_fan",
                    "name": "Вытяжка ванна",
                    "roomName": "Ванная",
                    "details": [],
                }
            ],
            "rooms": [],
            "alarms": [],
        }
        catalog = {
            "devices": [
                {
                    "name": "Домофон 2",
                    "entity_id": "button.domofon_2",
                    "target_id": "entity_domofon_2",
                    "actions": [{"action_id": "press", "allowed_fields": []}],
                },
                {
                    "name": "Вытяжка ванна",
                    "entity_id": "switch.bathroom_fan",
                    "target_id": "entity_bathroom_fan",
                    "actions": [{"action_id": "turn_on", "allowed_fields": []}],
                },
            ]
        }
        script = panel_script(
            GET_PATHS
            | {
                "hausman_hub/v1/tablet-profile": profile,
                "hausman_hub/v1/dashboard": dashboard,
                "hausman_hub/v1/admin/scenarios/catalog": catalog,
            },
            {},
            """
        panel._activateSection("settings");
        panel._activateSettingsView("intercom");
        const select = findAll(panel._shell.settings, (node) =>
          node.tagName === "SELECT" && String(node.className).includes("intercom-device-select"))[0];
        const labels = findAll(select, (node) => node.tagName === "OPTION")
          .map((option) => option.textContent);
        if (!labels.includes("Домофон 2")) {
          throw new Error("catalog-only Домофон 2 is missing: " + JSON.stringify(labels));
        }
        if (labels.some((label) => label.includes("Вытяжка"))) {
          throw new Error("unrelated controllable device leaked into intercom picker: " + JSON.stringify(labels));
        }
        select.value = "button.domofon_2";
        select.fire("change");
        if (panel._intercomDraft.deviceId !== "button.domofon_2") {
          throw new Error("catalog-only intercom selection was not stored in draft");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_physical_device_card_prefers_zigbee_image_and_opens_fixed_dialog(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const device = {
          id: "living_trv",
          physicalId: "living_trv",
          name: "Термоголовка Sonoff",
          roomName: "Гостиная",
          state: "heat",
          imageUrl: "https://www.zigbee2mqtt.io/images/devices/TRVZB.png",
          manufacturer: "SONOFF",
          model: "TRVZB",
          details: [
            { label: "temperature", value: "25,0 °C" },
            { label: "temperature", value: "25,0 °C" },
            { label: "battery", value: "88 %" },
          ],
          controls: [],
        };
        const card = panel._deviceInventoryCard(device);
        const images = findAll(card, (node) => node.tagName === "IMG");
        if (images.length !== 2 || !images.every((node) => node.src.includes("TRVZB.png"))) {
          throw new Error("Zigbee2MQTT image was not preferred in summary and detail");
        }
        const dialogs = findAll(card, (node) => node.role === "dialog");
        if (dialogs.length !== 1 || dialogs[0]["aria-label"] !== "Термоголовка Sonoff") {
          throw new Error("one fixed device dialog was not created");
        }
        const text = textOf(card);
        if (!text.includes("Обогрев") || text.includes("heat")) {
          throw new Error("device state was not localized: " + text);
        }
        const factLabels = findAll(card, (node) => node.tagName === "DT")
          .map((node) => node.textContent);
        if (JSON.stringify(factLabels) !== JSON.stringify(["Температура", "Заряд"])) {
          throw new Error("device facts were not deduplicated: " + JSON.stringify(factLabels));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_physical_sensor_card_formats_primary_measurement_with_unit(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const device = {
          id: "temperature_sensor",
          entityId: "sensor.room_temperature",
          physicalId: "temperature_sensor",
          name: "Климат комнаты",
          roomName: "Гостиная",
          domain: "sensor",
          state: "20.5",
          stateLabel: "20.5",
          active: false,
          unavailable: false,
          details: [
            { entityId: "sensor.room_temperature", label: "Температура", value: "20.5 °C", state: "20.5" },
          ],
        };
        const card = panel._deviceInventoryCard(device);
        const summary = findAll(card, (node) => node.tagName === "SUMMARY")[0];
        const text = textOf(summary);
        if (!text.includes("20,5 °C") || text.includes("20.5")) {
          throw new Error("numeric primary state is not localized with its unit: " + text);
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_energy_section_configures_units_and_opens_one_physical_device(self) -> None:
        dashboard = {
            "devices": [
                {
                    "id": "device_0123456789abcdef",
                    "physicalId": "device_0123456789abcdef",
                    "entityId": "switch.kitchen_breaker",
                    "name": "Автомат кухни",
                    "roomName": "Кухня",
                    "domain": "switch",
                    "category": "switch",
                    "manufacturer": "Tuya",
                    "model": "DIN RCBO",
                    "imageUrl": "https://www.zigbee2mqtt.io/images/devices/TS011F_plug_1.png",
                    "state": "on",
                    "stateLabel": "включено",
                    "tone": "good",
                    "details": [
                        {"label": "Мощность", "value": "1850 W", "entityId": "sensor.kitchen_breaker_power", "domain": "sensor", "state": "1850"},
                        {"label": "Ток", "value": "8.04 A", "entityId": "sensor.kitchen_breaker_current", "domain": "sensor", "state": "8.04"},
                    ],
                    "actions": [],
                },
                {
                    "id": "device_fedcba9876543210",
                    "physicalId": "device_fedcba9876543210",
                    "entityId": "light.floor_lamp",
                    "name": "Торшер",
                    "roomName": "Гостиная",
                    "domain": "light",
                    "category": "light",
                    "state": "off",
                    "stateLabel": "выключено",
                    "tone": "neutral",
                    "details": [{"label": "Мощность", "value": "0 W", "entityId": "sensor.floor_lamp_power", "domain": "sensor", "state": "0"}],
                    "actions": [{"id": "turn_on", "label": "Включить"}],
                },
            ],
            "energy": {
                "available": True,
                "currentPowerW": 1850,
                "currentA": 8.04,
                "voltageV": 230.1,
                "totalKwh": 12.4,
                "selectedSourceIds": ["device_0123456789abcdef"],
                "settings": {"displayUnits": "watts", "showVoltage": True, "aggregation": "combined", "useAllDevices": True},
                "sources": [
                    {"id": "device_0123456789abcdef", "deviceId": "device_0123456789abcdef", "name": "Автомат кухни", "roomName": "Кухня", "available": True, "powered": True, "currentPowerW": 1850, "currentA": 8.04, "voltageV": 230.1, "totalKwh": 12.4},
                    {"id": "device_fedcba9876543210", "deviceId": "device_fedcba9876543210", "name": "Торшер", "roomName": "Гостиная", "available": True, "powered": False, "currentPowerW": 0, "currentA": 0, "voltageV": 230.0, "totalKwh": 2.1}
                ],
            },
            "rooms": [], "alarms": [],
        }
        script = panel_script(
            GET_PATHS | {
                "hausman_hub/v1/dashboard": dashboard,
                "hausman_hub/v1/energy-settings": {
                    "revision": 0,
                    "settings": dashboard["energy"]["settings"],
                },
                "__energy_history__": {
                    "series": [
                        {
                            "sourceId": "device_0123456789abcdef",
                            "deviceId": "device_0123456789abcdef",
                            "metric": "power",
                            "unit": "W",
                            "scope": "device",
                            "points": [
                                {"at": "2026-08-04T12:00:00+06:00", "value": 320},
                                {"at": "2026-08-04T13:00:00+06:00", "value": 540},
                            ],
                        },
                        {
                            "sourceId": "device_0123456789abcdef",
                            "deviceId": "device_0123456789abcdef",
                            "metric": "energy",
                            "unit": "kWh",
                            "scope": "device",
                            "points": [
                                {"at": "2026-08-04T12:00:00+06:00", "value": 0.18},
                                {"at": "2026-08-04T13:00:00+06:00", "value": 0.24},
                            ],
                        },
                    ],
                },
            },
            {"hausman_hub/v1/energy-settings": {"revision": 1}},
            """
        restoreNavigationFromLocation(panel, false, PANEL_SECTIONS, CLIMATE_VIEWS, SETTINGS_VIEWS);
        await panel._load();
        await tick();
        if (!calls.some((call) => call.method === "GET" && call.path.startsWith("hausman_hub/v1/energy/history?"))) {
          throw new Error("energy deep link did not load Recorder history");
        }
        panel._shell.tabs.energy.fire("click");
        await tick();
        let text = textOf(panel._shell.homeSections.energy);
        if (!text.includes("Энергия сейчас") || !text.includes("850") || !text.includes("230,1") || !text.includes("0,42 кВт·ч") || !text.includes("Торшер") || !text.includes("Выключен") || !text.includes("питание отключено") || !text.includes("Устройства энергии") || !text.includes("Карточка на главной") || text.includes("Единый источник истины")) {
          throw new Error("energy summary is incomplete: " + text);
        }
        const rows = findAll(panel._shell.homeSections.energy, (node) =>
          String(node.className).split(" ").includes("energy-device-card"));
        if (rows.length !== 2 || !findAll(rows[0], (node) => node.tagName === "IMG").length) {
          throw new Error("energy sources must use one tablet row and product image per physical device");
        }
        const poweredOffStatus = findAll(rows[1], (node) =>
          String(node.className).includes("energy-device-status"))[0];
        if (!poweredOffStatus || !String(poweredOffStatus.className).includes("is-powered-off")) {
          throw new Error("powered-off energy source must use a neutral status tone");
        }
        panel._energyHistory = { selection: [
          { start: "2026-08-04T12:00:00+06:00", mean: 320 },
          { start: "2026-08-04T13:00:00+06:00", mean: 540 },
          { start: "2026-08-04T14:00:00+06:00", mean: 410 },
        ] };
        panel._renderEnergySection(panel._shell.homeSections.energy);
        const chartMetrics = findAll(panel._shell.homeSections.energy, (node) =>
          String(node.className).split(" ").includes("energy-chart-metric"));
        if (chartMetrics.length !== 4 || !chartMetrics.some((node) => textOf(node).includes("Среднее"))
            || !chartMetrics.some((node) => textOf(node).includes("Пик"))) {
          throw new Error("energy chart must expose readable summary metrics: "
            + chartMetrics.length + " / " + textOf(panel._shell.homeSections.energy));
        }
        panel._energyDraft.useAllDevices = false;
        panel._energyDraft.selectedDeviceIds = [];
        panel._renderEnergySection(panel._shell.homeSections.energy);
        const sourceCheckbox = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "INPUT" && node.type === "checkbox")[0];
        const initiallyDisabledSave = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Сохранить настройки")[0];
        if (!sourceCheckbox || !initiallyDisabledSave || !initiallyDisabledSave.disabled) {
          throw new Error("selective source state is incomplete");
        }
        sourceCheckbox.checked = true;
        sourceCheckbox.fire("change");
        if (initiallyDisabledSave.disabled) {
          throw new Error("energy save must become available immediately after source selection");
        }
        const both = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Вт + А")[0];
        both.fire("click");
        const save = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Сохранить настройки")[0];
        save.fire("click");
        await tick(10);
        const post = calls.find((call) => call.method === "PUT" && call.path === "hausman_hub/v1/energy-settings");
        if (!post || post.payload.expectedRevision !== 0 || post.payload.settings.displayUnits !== "both" || post.payload.settings.useAllDevices !== false
            || post.payload.settings.selectedDeviceIds.length !== 1) {
          throw new Error("energy settings post mismatch: " + JSON.stringify(post));
        }
        panel._shell.tabs.energy.fire("click");
        panel._scenarios.catalog = { devices: [{
          entity_id: "switch.kitchen_breaker",
          target_id: "target-kitchen-breaker",
          actions: [
            { action_id: "turn_on", title: "Включить", allowed_fields: [] },
            { action_id: "turn_off", title: "Выключить", allowed_fields: [] },
          ],
        }] };
        panel._renderEnergySection(panel._shell.homeSections.energy);
        const quickPower = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && String(node.className).includes("energy-device-quick"))[0];
        if (!quickPower || quickPower.textContent !== "Отключить") {
          throw new Error("energy row quick power action is missing");
        }
        const device = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && String(node.className).includes("energy-device-card-open"))[0];
        device.fire("click");
        text = textOf(panel._shell.homeSections.energy);
        if (!text.includes("История мощности") || !text.includes("Питание") || !text.includes("Об устройстве") || !text.includes("Автомат кухни") || !text.includes("DIN RCBO")) {
          throw new Error("energy device detail did not open: " + text);
        }
        const consumption = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Расход")[0];
        if (!consumption) throw new Error("energy consumption history selector is missing");
        consumption.fire("click");
        text = textOf(panel._shell.homeSections.energy);
        if (!text.includes("История расхода") || (!text.includes("За период") && !text.includes("История расхода пока недоступна") && !text.includes("Не удалось получить историю"))) {
          throw new Error("energy consumption history did not open: " + text);
        }
        let confirmation = "";
        window.confirm = (message) => { confirmation = message; return false; };
        const powerOff = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Отключить")[0];
        if (!powerOff) throw new Error("breaker power control is missing");
        powerOff.fire("click");
        if (!confirmation.includes("Питание подключённой линии будет снято")) {
          throw new Error("breaker did not request an explicit confirmation");
        }
        if (calls.some((call) => call.path === "hausman_hub/v1/device-actions")) {
          throw new Error("cancelled breaker command reached the API");
        }
            """,
            before_panel='setWindowLocation("https://homeassistant.local/hausman-hub?hh_section=energy");',
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_overview_matches_figma_hierarchy_and_counts_physical_devices(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/panel"] = {
            **PANEL_PAYLOAD,
            "readiness": {
                "status": "ready",
                "bridge_mode": "native",
                "reasons": [],
            },
            "snapshot": {
                "rooms": [
                    {
                        "id": "living",
                        "name": "Гостиная",
                        "mode": "automatic",
                        "active_profile": "day",
                        "temperature": 24.5,
                        "humidity": 46,
                        "actual": {"data_status": "current"},
                        "devices": [
                            {
                                "name": "Кондиционер",
                                "state": "cool",
                                "entities": ["climate.ac", "sensor.ac_temperature"],
                            },
                            {
                                "name": "Увлажнитель",
                                "state": "off",
                                "entities": ["humidifier.room", "sensor.room_humidity"],
                            },
                        ],
                    },
                    {
                        "id": "bedroom",
                        "name": "Спальня",
                        "mode": "automatic",
                        "active_profile": "night",
                        "temperature": 23.5,
                        "humidity": 50,
                        "actual": {"data_status": "current"},
                        "devices": [],
                    },
                ]
            },
        }
        payloads["hausman_hub/v1/dashboard"] = {
            "summary": {"homeName": "Дом"},
            "localIso": "2026-08-05T12:40:00+06:00",
            "rooms": [
                {"id": "living", "name": "Гостиная", "temp": 24.5, "humidity": 46, "targetTemp": 25, "targetHumidity": 45},
                {"id": "bedroom", "name": "Спальня", "temp": 23.5, "humidity": 50, "targetTemp": 24, "targetHumidity": 50},
                {"id": "office", "name": "Кабинет", "temp": None, "humidity": None, "targetTemp": None, "targetHumidity": None},
            ],
            "devices": [
                {"id": "light.main", "physicalId": "light-fixture", "domain": "light", "state": "on", "active": True},
                {"id": "light.level", "physicalId": "light-fixture", "domain": "light", "state": "on", "active": True},
                {"id": "climate.living", "physicalId": "ac-living", "domain": "climate", "state": "cool", "active": False},
                {"id": "sensor.temperature", "physicalId": "temperature-sensor", "domain": "sensor", "state": "24.5", "active": False},
            ],
            "alarms": [],
            "scenarios": [{"id": "morning", "title": "Доброе утро", "favorite": True}],
            "weather": {"temperatureC": 20, "condition": "sunny", "windSpeedMps": 2.1},
        }
        script = panel_script(
            payloads,
            {},
            """
        const overview = panel._shell.sectionNodes.overview;
        const text = textOf(overview);
        for (const label of [
          "Дом", "Гостиная", "Спальня", "Кабинет", "Климат", "Цель климата",
          "Комфорт в доме", "Избранные сценарии", "Доброе утро", "Погода",
        ]) {
          if (!text.includes(label)) throw new Error("overview text missing: " + label);
        }
        if (text.includes("0,0–25,0°") || text.includes("0–50%")) {
          throw new Error("missing climate target was coerced to zero: " + text);
        }
        const byClass = (name) => findAll(overview, (node) =>
          String(node.className).split(" ").includes(name));
        if (byClass("overview-canon-hero-fact").length !== 4) {
          throw new Error("canonical hero must contain four facts");
        }
        const heroFacts = byClass("overview-canon-hero-fact");
        const activeValue = findAll(heroFacts[2], (node) => node.tagName === "STRONG")[0];
        if (!activeValue || activeValue.textContent !== "2") {
          throw new Error("numeric sensors were counted as active devices: " + JSON.stringify(heroFacts.map(textOf)));
        }
        if (byClass("overview-canon-primary-card").length !== 3) {
          throw new Error("canonical first row must contain three cards");
        }
        const stableHero = byClass("overview-canon-hero")[0];
        const stableMedia = byClass("overview-canon-hero-media")[0];
        panel._homeDashboard = { ...panel._homeDashboard, localIso: "2026-08-05T12:40:15+06:00" };
        panel._render();
        if (byClass("overview-canon-hero")[0] !== stableHero
          || byClass("overview-canon-hero-media")[0] !== stableMedia) {
          throw new Error("volatile dashboard timestamp recreated the Hero");
        }
        panel._homeDashboard = { ...panel._homeDashboard, localIso: "", weather: {} };
        panel._render();
        if (byClass("overview-canon-hero-media")[0] !== stableMedia) {
          throw new Error("partial dashboard response replaced the stable Hero image");
        }
        const homeButton = findAll(overview, (node) => node.tagName === "BUTTON"
          && node["aria-current"] === "page")[0];
        if (!homeButton || homeButton.disabled) {
          throw new Error("home slide must be the active selectable hero state");
        }
        const climateCard = byClass("overview-canon-primary-card")[0];
        climateCard.fire("click");
        if (panel._activeSection !== "climate") {
          throw new Error("climate card did not open climate");
        }
        panel._shell.tabs.overview.fire("click");
        const roomCard = findAll(panel._shell.sectionNodes.overview, (node) =>
          node.tagName === "BUTTON" && String(node["aria-label"]).includes("Спальня"))[0];
        roomCard.fire("click");
        if (panel._activeSection !== "overview") {
          throw new Error("room switch navigated away from the hero");
        }
        const currentOverview = panel._shell.sectionNodes.overview;
        const heroTitle = findAll(currentOverview, (node) => node.tagName === "H1")[0];
        const heroMedia = byClass("overview-canon-hero-media")[0];
        if (!heroTitle || heroTitle.textContent !== "Спальня"
          || !textOf(currentOverview).includes("23,5 °C")
          || !String(heroMedia?.style?.backgroundImage).includes("hero_room_bedroom_")) {
          throw new Error("selected room did not replace the hero content and image");
        }
        if (findAll(panel._shell.homeSections.rooms, (node) =>
          String(node.className).split(" ").includes("rooms-detail-sheet")).length) {
          throw new Error("room switch opened a separate detail sheet");
        }
        homeButton.fire("click");
        if (heroTitle.textContent !== "Дом" || panel._overviewHeroRoomId !== null) {
          throw new Error("home control did not restore the home hero state");
        }
        const roomArrows = findAll(currentOverview, (node) =>
          String(node.className || "").split(" ").includes("overview-canon-room-arrow"));
        if (roomArrows.length !== 2) throw new Error("cyclic room navigation arrows are missing");
        roomArrows[1].fire("click");
        if (heroTitle.textContent !== "Гостиная" || panel._overviewHeroRoomId !== "living") {
          throw new Error("next room arrow did not select the next Hero slide");
        }
        roomArrows[0].fire("click");
        if (heroTitle.textContent !== "Дом" || panel._overviewHeroRoomId !== null) {
          throw new Error("previous room arrow did not cycle back to Home");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_overview_renders_with_missing_readiness_payload(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/panel"] = {
            **PANEL_PAYLOAD,
            "readiness": None,
        }
        payloads["hausman_hub/v1/dashboard"] = {
            "summary": {"homeName": "Дом"},
            "rooms": [],
            "devices": [],
            "alarms": [],
            "scenarios": [],
            "weather": {},
        }
        script = panel_script(
            payloads,
            {},
            """
        const hero = panel._shell.readiness;
        const text = textOf(hero);
        if (!text.includes("Проверьте настройки") || !text.includes("Требуется внимание")) {
          throw new Error("overview did not render safe fallback for missing readiness: " + text);
        }
        if (panel._shell.statusPill.textContent !== "Состояние уточняется") {
          throw new Error("header did not render unknown readiness safely");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_tablet_device_sections_group_physical_device_and_execute_action(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/dashboard"] = {
            "rooms": [
                {
                    "id": "living",
                    "name": "Гостиная",
                    "temp": 24.5,
                    "humidity": 46,
                }
            ],
            "devices": [
                {
                    "id": "device-light",
                    "physicalId": "device-light",
                    "entityId": "light.living_main",
                    "name": "Выключатель гостиная",
                    "roomId": None,
                    "roomName": "  Гостиная ",
                    "domain": "light",
                    "category": "lighting",
                    "stateLabel": "Включён",
                    "tone": "good",
                    "unavailable": False,
                    "attributes": {"brightness": 178},
                    "details": [
                        {
                            "entityId": "light.living_main",
                            "label": "Основной свет",
                            "value": "Включён",
                        },
                        {
                            "entityId": "sensor.living_power",
                            "label": "Мощность",
                            "value": "12 Вт",
                        },
                    ],
                },
                {
                    "id": "device-socket",
                    "physicalId": "device-socket",
                    "entityId": "switch.living_socket",
                    "name": "Умная розетка",
                    "roomId": "living",
                    "roomName": "Гостиная",
                    "domain": "switch",
                    "category": "lighting",
                    "stateLabel": "Выключена",
                    "tone": "neutral",
                    "unavailable": False,
                    "details": [],
                },
            ],
            "alarms": [],
        }
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {
            "devices": [
                {
                    "target_id": "target-living-main",
                    "entity_id": "light.living_main",
                    "name": "Основной свет",
                    "actions": [
                        {
                            "action_id": "turn_on",
                            "title": "Включить",
                            "allowed_fields": [],
                        },
                        {
                            "action_id": "set_brightness",
                            "title": "Яркость",
                            "allowed_fields": ["value"],
                        }
                    ],
                }
            ]
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/device-actions": {"status": "confirmed"}},
            """
        await tick();
        panel._shell.tabs.lighting.fire("click");
        const lighting = panel._shell.homeSections.lighting;
        const cards = findAll(lighting, (node) =>
          String(node.className).split(" ").includes("inventory-device-card"));
        if (cards.length !== 1) {
          throw new Error("one physical device rendered as " + cards.length + " cards");
        }
        const text = textOf(lighting);
        if (!text.includes("Выключатель гостиная") || !text.includes("Основной свет")
          || !text.includes("Мощность") || !text.includes("12 Вт")) {
          throw new Error("device or its function missing from lighting section");
        }
        if (text.includes("Умная розетка")) {
          throw new Error("unrelated switch was promoted to lighting");
        }
        const roomCard = findAll(lighting, (node) => node.tagName === "BUTTON"
          && String(node.className).split(" ").includes("lighting-room-card"))[0];
        if (!roomCard) throw new Error("room-name fallback did not create a room card");
        roomCard.fire("click");
        const sheet = findAll(lighting, (node) =>
          String(node.className).split(" ").includes("lighting-room-sheet"))[0];
        if (!sheet || !textOf(sheet).includes("Выключатель гостиная")) {
          throw new Error("room drill-down did not open the physical device");
        }
        const closeSheet = findAll(sheet, (node) =>
          String(node.className).split(" ").includes("lighting-room-sheet-close"))[0];
        closeSheet.fire("click");
        const valueInput = findAll(lighting, (node) => node.tagName === "INPUT" && node.type !== "search")[0];
        if (!valueInput || valueInput.value !== "178") {
          throw new Error("device action did not use current brightness");
        }
        cards[0].open = true;
        cards[0].fire("toggle");
        const on = findAll(lighting, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Включить")[0];
        if (!on) throw new Error("device action control missing");
        on.fire("click", { preventDefault() {} });
        await tick(10);
        const post = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/device-actions");
        if (!post || post.payload.targetId !== "target-living-main"
          || post.payload.actionId !== "turn_on") {
          throw new Error("device action payload mismatch: " + JSON.stringify(post));
        }
        const refreshedLighting = panel._shell.homeSections.lighting;
        const refreshedCard = findAll(refreshedLighting, (node) =>
          String(node.className).split(" ").includes("inventory-device-card"))[0];
        if (!refreshedCard || refreshedCard.open !== true) {
          throw new Error("expanded device card collapsed after confirmed action");
        }
        const refreshedInput = findAll(refreshedLighting, (node) => node.tagName === "INPUT" && node.type !== "search")[0];
        const apply = findAll(refreshedLighting, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Применить")[0];
        if (!refreshedInput || !apply || apply.disabled) {
          throw new Error("current value action is not available");
        }
        apply.fire("click", { preventDefault() {} });
        await tick(10);
        const brightnessPost = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/device-actions"
          && call.payload.actionId === "set_brightness");
        if (!brightnessPost || brightnessPost.payload.value !== 178) {
          throw new Error("current brightness payload mismatch: " + JSON.stringify(brightnessPost));
        }
        panel._shell.tabs.rooms.fire("click");
        const roomText = textOf(panel._shell.homeSections.rooms);
        if (!roomText.includes("Гостиная") || !roomText.includes("2 устройства")) {
          throw new Error("room inventory does not match tablet navigation");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_climate_overview_matches_tablet_categories_rooms_and_drill_down(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/dashboard"] = {
            "rooms": [
                {
                    "id": "living", "name": "Гостиная", "temp": 24.5,
                    "humidity": 46, "targetTemp": 25.0,
                },
                {
                    "id": "kids", "name": "Детская", "temp": 23.0,
                    "humidity": 51, "targetTemp": 24.0,
                },
                {
                    "id": "office", "name": "Кабинет", "temp": None,
                    "humidity": None, "targetTemp": None,
                },
            ],
            "devices": [
                {
                    "id": "device-ac", "physicalId": "device-ac",
                    "entityId": "climate.living", "name": "Кондиционер гостиная",
                    "roomId": "living", "roomName": "Гостиная", "domain": "climate",
                    "category": "climate", "state": "cool", "stateLabel": "cool",
                    "active": True, "tone": "good", "unavailable": False,
                    "imageUrl": "https://www.zigbee2mqtt.io/images/devices/ac.png",
                    "details": [{"entityId": "climate.living", "label": "Режим", "value": "Охлаждение"}],
                },
                {
                    "id": "device-trv", "physicalId": "device-trv",
                    "entityId": "climate.living_trv", "name": "Термоголовка Sonoff гостиная",
                    "roomId": "living", "roomName": "Гостиная", "domain": "climate",
                    "category": "climate", "state": "off", "stateLabel": "off",
                    "active": False, "tone": "neutral", "unavailable": False,
                    "details": [],
                },
                {
                    "id": "device-humidifier", "physicalId": "device-humidifier",
                    "entityId": "humidifier.kids", "name": "Увлажнитель детская",
                    "roomId": "kids", "roomName": "Детская", "domain": "humidifier",
                    "category": "climate", "state": "unavailable", "stateLabel": "unavailable",
                    "active": False, "tone": "bad", "unavailable": True,
                    "details": [],
                },
            ],
            "alarms": [],
        }
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {
            "devices": [
                {
                    "target_id": "target-ac", "entity_id": "climate.living",
                    "name": "Кондиционер гостиная", "actions": [
                        {"action_id": "turn_off", "title": "Выключить", "allowed_fields": []},
                        {"action_id": "set_temperature", "title": "Температура", "allowed_fields": ["value"]},
                    ],
                }
            ]
        }
        script = panel_script(
            payloads,
            {},
            """
        await tick();
        panel._shell.tabs.climate.fire("click");
        const climate = panel._shell.climateOverview;
        const text = textOf(climate);
        if (!text) throw new Error("climate overview empty state: active=" + panel._activeSection
          + ", dashboard=" + JSON.stringify(panel._homeDashboard)
          + ", key=" + panel._sectionRenderKeys.climate);
        for (const label of [
          "Климат по комнатам", "Обзор климата", "Комнаты и цели",
          "Кондиционеры", "Термоголовки", "Тёплый пол", "Увлажнители",
          "Очистители", "Вытяжки", "Гостиная", "Детская", "24.5 °C", "46 %",
          "Кабинет", "Нет данных", "Цель не задана",
        ]) {
          if (!text.includes(label)) throw new Error("climate tablet text missing: " + label + " :: " + text);
        }
        if (text.includes("0 °C") || text.includes("0 %")) {
          throw new Error("null climate reading was coerced to zero: " + text);
        }
        if (text.includes("cool") || text.includes("off") || text.includes("unavailable")) {
          throw new Error("raw climate state leaked into the overview: " + text);
        }
        const categories = findAll(climate, (node) =>
          String(node.className).split(" ").includes("climate-category-card"));
        if (categories.length !== 6) throw new Error("climate category count mismatch");
        const conditioner = categories.find((node) => textOf(node).includes("Кондиционеры"));
        conditioner.fire("click");
        let sheet = findAll(climate, (node) =>
          String(node.className).split(" ").includes("climate-device-sheet"))[0];
        if (!sheet || !textOf(sheet).includes("Кондиционер гостиная")
          || !textOf(sheet).includes("Охлаждение")) {
          throw new Error("category did not open all conditioner devices");
        }
        let products = findAll(sheet, (node) =>
          String(node.className).split(" ").includes("climate-product-card"));
        if (products.length !== 1) throw new Error("one physical AC rendered more than once");
        products[0].fire("click");
        sheet = findAll(climate, (node) =>
          String(node.className).split(" ").includes("climate-device-sheet"))[0];
        if (!findAll(sheet, (node) =>
          String(node.className).split(" ").includes("inventory-device-card") && node.open === true).length) {
          throw new Error("specific climate device did not open its detailed card");
        }
        const close = findAll(sheet, (node) =>
          String(node.className).split(" ").includes("climate-sheet-close"))[0];
        close.fire("click");
        if (panel._climateOverlay !== null) throw new Error("climate overlay state was not cleared");
        const room = findAll(climate, (node) =>
          String(node.className).split(" ").includes("climate-room-card")
          && textOf(node).includes("Гостиная"))[0];
        room.fire("click");
        const roomSheet = findAll(climate, (node) =>
          String(node.className).split(" ").includes("climate-device-sheet"))[0];
        if (!roomSheet || findAll(roomSheet, (node) =>
          String(node.className).split(" ").includes("climate-product-card")).length !== 2) {
          throw new Error("room climate drill-down did not show its physical devices");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_television_card_uses_tablet_presentation_not_entity_dump(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/dashboard"] = {
            "rooms": [{"id": "living", "name": "Гостиная", "temp": 24.5, "humidity": 46}],
            "devices": [
                {
                    "id": "device-tv",
                    "physicalId": "device-tv",
                    "entityId": "media_player.tv_cast",
                    "name": "58PUS8506/60",
                    "roomId": "living",
                    "roomName": "Гостиная",
                    "domain": "media_player",
                    "category": "media",
                    "state": "playing",
                    "stateLabel": "Воспроизводится",
                    "tone": "good",
                    "unavailable": False,
                    "manufacturer": "Philips",
                    "model": "58PUS8506/60",
                    "attributes": {"media_title": "Кинопоиск"},
                    "details": [
                        {"entityId": "media_player.tv_cast", "label": "Медиа", "value": "Воспроизводится"},
                        {"entityId": "media_player.tv_android", "label": "Медиа", "value": "Нет связи"},
                        {"entityId": "media_player.tv_philips", "label": "Медиа", "value": "Включено"},
                        {"entityId": "light.tv_ambilight", "label": "Освещение", "value": "Выключено"},
                        {"entityId": "switch.tv_screen", "label": "Состояние экрана", "value": "Включено"},
                    ],
                }
            ],
            "alarms": [],
        }
        actions = [
            {"action_id": "turn_on", "title": "Включить", "allowed_fields": []},
            {"action_id": "turn_off", "title": "Выключить", "allowed_fields": []},
            {"action_id": "media_play", "title": "Играть", "allowed_fields": []},
            {"action_id": "media_pause", "title": "Пауза", "allowed_fields": []},
        ]
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {
            "devices": [
                {"target_id": "tv-cast", "entity_id": "media_player.tv_cast", "name": "58PUS8506/60", "actions": actions},
                {"target_id": "tv-android", "entity_id": "media_player.tv_android", "name": "Android TV", "actions": actions},
                {"target_id": "tv-philips", "entity_id": "media_player.tv_philips", "name": "Philips TV", "actions": actions},
                {"target_id": "tv-ambilight", "entity_id": "light.tv_ambilight", "name": "Ambilight", "actions": actions},
            ]
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/device-actions": {"status": "confirmed"}},
            """
        await tick();
        panel._shell.tabs.media.fire("click");
        let media = panel._shell.homeSections.media;
        const mediaText = textOf(media);
        if (!mediaText.includes("СЕЙЧАС ВОСПРОИЗВОДИТСЯ")
          || !mediaText.includes("По комнатам") || !mediaText.includes("Медиаустройства")
          || !mediaText.includes("Кинопоиск")) {
          throw new Error("canonical media hierarchy is incomplete: " + mediaText);
        }
        const zone = findAll(media, (node) =>
          String(node.className).split(" ").includes("media-zone-card"))[0];
        if (!zone || zone.tagName !== "BUTTON"
            || !String(zone["aria-label"] || "").startsWith("Открыть медиоустройства комнаты")) {
          throw new Error("media room is not an accessible drill-down control");
        }
        zone.fire("click");
        await tick();
        media = panel._shell.homeSections.media;
        const zoneSheet = findAll(media, (node) =>
          String(node.className).split(" ").includes("media-zone-sheet"))[0];
        if (!zoneSheet || !textOf(zoneSheet).includes("выберите устройство для управления")
            || findAll(zoneSheet, (node) =>
              String(node.className).split(" ").includes("media-device-card")).length !== 1) {
          throw new Error("media room drill-down did not show its physical devices");
        }
        findAll(zoneSheet, (node) =>
          String(node.className).split(" ").includes("media-zone-sheet-close"))[0].fire("click");
        if (panel._mediaOverlay !== null) throw new Error("media room overlay state was not cleared");
        const cards = findAll(media, (node) =>
          String(node.className).split(" ").includes("media-device-card"));
        if (cards.length !== 1) throw new Error("TV did not render as one physical card");
        const text = textOf(cards[0]);
        if (!text.includes("Телевизор") || !text.includes("Гостиная · 58PUS8506/60")
          || !text.includes("Кинопоиск") || !text.includes("Сейчас воспроизводится")) {
          throw new Error("tablet TV identity or state is missing: " + text);
        }
        if (text.includes("Ambilight") || text.includes("Состояние экрана")
          || text.includes("Android TV") || text.includes("Philips TV")) {
          throw new Error("technical HA entities leaked into TV card: " + text);
        }
        const buttons = findAll(cards[0], (node) => node.tagName === "BUTTON"
          && !String(node.className).split(" ").includes("device-sheet-close"));
        if (buttons.length !== 3 || !buttons.some((node) => node.textContent === "Играть")
          || !buttons.some((node) => node.textContent === "Пауза")
          || !buttons.some((node) => node.textContent === "Выключить")) {
          throw new Error("semantic tablet controls mismatch: " + buttons.map((node) => node.textContent));
        }
        buttons.find((node) => node.textContent === "Пауза").fire("click", {
          preventDefault() {}, stopPropagation() {},
        });
        await tick(10);
        const post = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/device-actions");
        if (!post || post.payload.targetId !== "tv-cast" || post.payload.actionId !== "media_pause") {
          throw new Error("TV command used a secondary entity: " + JSON.stringify(post));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_security_cards_use_russian_states_and_distinct_centered_icons(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/dashboard"] = {
            "rooms": [{"id": "entry", "name": "Тамбур", "temp": 24.0, "humidity": 45}],
            "devices": [
                {
                    "id": "device-lock", "physicalId": "device-lock",
                    "entityId": "lock.front_door", "name": "Aqara Smart Lock A100",
                    "roomId": "entry", "roomName": "Тамбур", "domain": "lock",
                    "category": "security", "state": "locked", "stateLabel": "закрыт",
                    "active": False, "tone": "neutral", "unavailable": False,
                    "details": [{"entityId": "lock.front_door", "label": "Замок", "value": "закрыт"}],
                },
                {
                    "id": "device-alarm", "physicalId": "device-alarm",
                    "entityId": "alarm_control_panel.entry", "name": "EZVIZ Alarm",
                    "roomId": "entry", "roomName": "Тамбур", "domain": "alarm_control_panel",
                    "category": "security", "state": "disarmed", "stateLabel": "охрана выключена",
                    "active": False, "tone": "neutral", "unavailable": False,
                    "details": [{"entityId": "alarm_control_panel.entry", "label": "Охрана", "value": "охрана выключена"}],
                },
                {
                    "id": "device-leak", "physicalId": "device-leak",
                    "entityId": "binary_sensor.entry_leak", "name": "Датчик протечки",
                    "roomId": "entry", "roomName": "Тамбур", "domain": "binary_sensor",
                    "category": "moisture", "state": "off", "stateLabel": "сухо",
                    "active": False, "tone": "neutral", "unavailable": False,
                    "details": [{"entityId": "binary_sensor.entry_leak", "label": "Протечка", "value": "сухо"}],
                },
            ],
            "alarms": [],
        }
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {"devices": []}
        script = panel_script(
            payloads,
            {},
            """
        await tick();
        panel._shell.tabs.security.fire("click");
        const security = panel._shell.homeSections.security;
        const text = textOf(security);
        if (!text.includes("Дом под наблюдением") || !text.includes("Контуры безопасности")
          || !text.includes("Датчики и доступ") || !text.includes("Тамбур · Закрыт")
          || !text.includes("Тамбур · Без охраны") || !text.includes("Тамбур · Сухо")) {
          throw new Error("security state is not semantic Russian copy: " + text);
        }
        if (text.includes("locked") || text.includes("disarmed") || text.includes("Устройство ·")) {
          throw new Error("raw Home Assistant state leaked into security card: " + text);
        }
        const types = findAll(security, (node) =>
          String(node.className).split(" ").includes("security-canon-type"));
        if (types.length !== 3 || !types.some((node) => textOf(node).includes("Протечки"))
          || !types.some((node) => textOf(node).includes("Двери и замки"))
          || !types.some((node) => textOf(node).includes("Охрана"))) {
          throw new Error("security contours are incomplete: " + types.map(textOf));
        }
        const cards = findAll(security, (node) =>
          String(node.className).split(" ").includes("inventory-device-card"));
        if (cards.length !== 3) throw new Error("security physical cards missing");
        const lockPath = findAll(cards[0], (node) => node.tagName === "PATH")[0];
        const alarmPath = findAll(cards[1], (node) => node.tagName === "PATH")[0];
        if (!lockPath || !alarmPath || !lockPath.d || !alarmPath.d || lockPath.d === alarmPath.d) {
          throw new Error("lock and alarm must have distinct associative icons");
        }
        types.find((node) => textOf(node).includes("Протечки")).fire("click");
        const filteredCards = findAll(security, (node) =>
          String(node.className).split(" ").includes("inventory-device-card"));
        if (filteredCards.length !== 1 || !textOf(filteredCards[0]).includes("Датчик протечки")) {
          throw new Error("security contour did not drill down to its physical devices");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_devices_overview_counts_physical_devices_once_and_drills_into_category(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/dashboard"] = {
            "rooms": [
                {"id": "living", "name": "Гостиная"},
                {"id": "kitchen", "name": "Кухня"},
            ],
            "devices": [
                {
                    "id": "device-ac", "physicalId": "physical-ac",
                    "entityId": "climate.living", "name": "Кондиционер гостиная",
                    "roomId": "living", "roomName": "Гостиная", "domain": "climate",
                    "category": "climate", "state": "cool", "stateLabel": "охлаждение",
                    "active": True, "unavailable": False,
                    "imageUrl": "https://www.zigbee2mqtt.io/images/devices/ac.png",
                    "details": [{"entityId": "climate.living", "label": "Климат", "value": "охлаждение"}],
                },
                {
                    "id": "device-ac-shadow", "physicalId": "physical-ac",
                    "entityId": "switch.living_ac", "name": "Кондиционер гостиная",
                    "roomId": "living", "roomName": "Гостиная", "domain": "switch",
                    "category": "appliance", "state": "on", "stateLabel": "включено",
                    "active": True, "unavailable": False, "details": [],
                },
                {
                    "id": "device-kettle", "physicalId": "physical-kettle",
                    "entityId": "switch.kettle", "name": "Чайник",
                    "roomId": "kitchen", "roomName": "Кухня", "domain": "switch",
                    "category": "appliance", "state": "unavailable", "stateLabel": "нет связи",
                    "active": False, "unavailable": True, "details": [],
                },
            ],
            "alarms": [],
        }
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {"devices": []}
        script = panel_script(
            payloads,
            {},
            """
        await tick();
        panel._shell.tabs.devices.fire("click");
        const devices = panel._shell.homeSections.devices;
        const text = textOf(devices);
        if (!text.includes("ФИЗИЧЕСКИЕ УСТРОЙСТВА ДОМА")
          || !text.includes("Категории устройств") || !text.includes("Источник данных")
          || !text.includes("HausmanHub") || text.includes("Smart Home Center")) {
          throw new Error("canonical devices hierarchy is incomplete: " + text);
        }
        let cards = findAll(devices, (node) =>
          String(node.className).split(" ").includes("inventory-device-card"));
        if (cards.length !== 2) throw new Error("one physical device rendered more than once");
        const images = findAll(devices, (node) => node.tagName === "IMG");
        if (!images.some((node) => String(node.src).includes("zigbee2mqtt.io"))) {
          throw new Error("Zigbee2MQTT image did not have presentation priority");
        }
        const categories = findAll(devices, (node) =>
          String(node.className).split(" ").includes("devices-canon-category"));
        const climate = categories.find((node) => textOf(node).includes("Климат"));
        if (!climate || !categories.some((node) => textOf(node).includes("Техника"))) {
          throw new Error("device categories missing");
        }
        climate.fire("click");
        cards = findAll(devices, (node) =>
          String(node.className).split(" ").includes("inventory-device-card"));
        if (cards.length !== 1 || !textOf(cards[0]).includes("Кондиционер гостиная")) {
          throw new Error("device category did not drill down to its physical devices");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_overview_translates_readiness_reasons_without_snapshot(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const overview = panel._shell.sectionNodes.overview;
        const text = textOf(overview);
        if (!text.includes("Контур выключен в настройках")) {
          throw new Error("disabled bridge reason is not translated");
        }
        if (text.includes("bridge_disabled")) {
          throw new Error("raw disabled bridge reason exposed");
        }
        panel._data.readiness.reasons = ["future_internal_reason"];
        panel._render();
        const fallbackText = textOf(panel._shell.sectionNodes.overview);
        if (!fallbackText.includes("Требуется дополнительная настройка")) {
          throw new Error("safe fallback for an unknown reason missing");
        }
        if (fallbackText.includes("future_internal_reason")) {
          throw new Error("unknown internal reason exposed");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_climate_screens_are_separate_accessible_views(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const climateMainTab = panel._shell.tabs.climate;
        climateMainTab.fire("click");
        const subtabs = Object.values(panel._shell.climateTabs);
        const expected = ["Обзор", "Контур", "Профили", "Расписание", "Сигналы дома", "Сигналы комнат", "Умный помощник"];
        if (JSON.stringify(subtabs.map((node) => node.textContent)) !== JSON.stringify(expected)) {
          throw new Error("climate subtab labels mismatch");
        }
        const visible = () => Object.entries(panel._shell.climateViews)
          .filter(([, node]) => !node.hidden).map(([name]) => name);
        if (JSON.stringify(visible()) !== JSON.stringify(["overview"])) {
          throw new Error("climate default view is not isolated");
        }
        panel._shell.climateTabs.profiles.fire("click");
        if (JSON.stringify(visible()) !== JSON.stringify(["profiles"])) {
          throw new Error("profile view is not isolated");
        }
        if (panel._shell.brandSubtitle.textContent !== "Профили и расписание комфорта") {
          throw new Error("climate view subtitle did not follow the selected screen");
        }
        if (panel._shell.climateTabs.profiles["aria-selected"] !== "true"
          || panel._shell.climateTabs.contour["aria-selected"] !== "false") {
          throw new Error("climate subtab accessibility state missing");
        }
        let prevented = false;
        panel._shell.climateTabs.profiles.fire("keydown", {
          key: "ArrowRight",
          preventDefault: () => { prevented = true; },
        });
        if (!prevented || panel._activeClimateView !== "schedule"
          || !panel._shell.climateTabs.schedule.focused) {
          throw new Error("climate keyboard navigation failed");
        }
        if (calls.some((call) => call.method === "GET"
          && call.path === "hausman_hub/v1/admin/ai-assistant")) {
          throw new Error("AI assistant loaded before opening its screen");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_assistant_loads_lazily_saves_settings_and_refreshes_advisory(self) -> None:
        script = panel_script(
            GET_PATHS | {AI_ASSISTANT_PATH: AI_ASSISTANT_PAYLOAD},
            {
                AI_ASSISTANT_SETTINGS_PATH: {
                    "settings": {
                        "enabled": True,
                        "preset": "openai",
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4o-mini",
                        "key_set": True,
                    }
                },
                AI_ASSISTANT_REFRESH_PATH: {
                    "advisory": AI_ASSISTANT_PAYLOAD["last_advisory"]
                },
            },
            """
        const tabs = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && String(node.className).split(" ").includes("tab"));
        const climateTab = tabs.find((node) => node["aria-label"] === "Климат");
        if (!climateTab) throw new Error("climate tab missing");
        if (calls.some((call) => call.method === "GET" && call.path === "hausman_hub/v1/admin/ai-assistant")) {
          throw new Error("assistant loaded eagerly");
        }
        climateTab.fire("click");
        const assistantTab = panel._shell.climateTabs.assistant;
        if (!assistantTab) throw new Error("assistant climate subtab missing");
        assistantTab.fire("click");
        await tick();
        const assistantGets = calls.filter((call) =>
          call.method === "GET" && call.path === "hausman_hub/v1/admin/ai-assistant");
        if (assistantGets.length !== 1) throw new Error("assistant lazy GET missing");
        const assistant = panel._shell.assistant;
        const text = textOf(assistant);
        for (const label of [
          "Умный помощник", "Поставщик сервиса", "Совместимый с OpenAI", "Ключ сохранён", "Последний совет", "Статистика вызовов",
          "Проверьте расхождение температур", "Превышение комфортного диапазона", "Гостиная",
          "Таймаут", "Готово",
        ]) {
          if (!text.includes(label)) throw new Error("assistant text missing: " + label);
        }
        if (text.includes("(living)")) throw new Error("raw room id exposed in assistant advice");
        const byClass = (name) => findAll(assistant, (node) =>
          String(node.className).split(" ").includes(name));
        if (byClass("assistant-form-grid").length !== 1) {
          throw new Error("assistant two-column form grid missing");
        }
        if (byClass("assistant-stat").length !== 5) {
          throw new Error("assistant metric grid must contain five metrics");
        }
        if (byClass("assistant-call").length < 1 || byClass("assistant-actions").length !== 2) {
          throw new Error("assistant calls or aligned action groups missing");
        }
        const inputs = findAll(assistant, (node) => node.tagName === "INPUT");
        const enabled = inputs.find((node) => node.type === "checkbox");
        const baseUrl = inputs.find((node) => node.type === "text" && node.value === "https://api.deepseek.com");
        const model = inputs.find((node) => node.type === "text" && node.value === "deepseek-chat");
        const apiKey = inputs.find((node) => node.type === "password");
        const preset = findAll(assistant, (node) => node.tagName === "SELECT")[0];
        if (!enabled || !baseUrl || !model || !apiKey || !preset || apiKey.value !== "") {
          throw new Error("assistant settings controls missing or key exposed");
        }
        preset.value = "openai";
        preset.fire("change");
        baseUrl.value = "https://api.openai.com/v1";
        baseUrl.fire("input");
        model.value = "gpt-4o-mini";
        model.fire("input");
        apiKey.value = "new-secret";
        apiKey.fire("input");
        const save = findAll(assistant, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Сохранить настройки");
        save.fire("click");
        await tick();
        const savePost = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/ai-assistant/settings");
        const expectedSettings = {
          enabled: true,
          preset: "openai",
          base_url: "https://api.openai.com/v1",
          model: "gpt-4o-mini",
          api_key: "new-secret",
        };
        if (!savePost || JSON.stringify(savePost.payload) !== JSON.stringify(expectedSettings)) {
          throw new Error("assistant settings payload mismatch: " + JSON.stringify(savePost && savePost.payload));
        }
        const refresh = findAll(assistant, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Обновить совет");
        refresh.fire("click");
        await tick();
        const refreshPost = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/ai-assistant/refresh");
        if (!refreshPost || JSON.stringify(refreshPost.payload) !== JSON.stringify({})) {
          throw new Error("assistant refresh payload mismatch");
        }
        if (calls.filter((call) => call.method === "GET"
          && call.path === "hausman_hub/v1/admin/ai-assistant").length !== 2) {
          throw new Error("assistant state did not refresh after advisory update");
        }
        if (!textOf(panel.shadowRoot).includes("Совет обновлён.")) {
          throw new Error("assistant refresh success notice missing");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_scenario_rows_match_figma_and_all_actions_call_the_api(self) -> None:
        scenarios = {
            "scenarios": [
                {
                    "id": "scenario.good_morning",
                    "title": "Доброе утро",
                    "icon": "mdi:weather-sunset-up",
                    "enabled": True,
                    "requiresConfirmation": False,
                },
                {
                    "id": "scenario.leaving_home",
                    "title": "Уходим из дома",
                    "icon": "mdi:home-export-outline",
                    "enabled": True,
                    "requiresConfirmation": True,
                },
                {
                    "id": "scenario.night_mode",
                    "title": "Ночной режим",
                    "icon": "mdi:weather-night",
                    "enabled": False,
                    "requiresConfirmation": False,
                },
            ]
        }
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/scenarios"] = scenarios
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {"devices": []}
        script = panel_script(
            payloads,
            {
                "hausman_hub/v1/admin/scenarios": {"ok": True},
                "hausman_hub/v1/admin/scenarios/run": {"status": "confirmed"},
                "hausman_hub/v1/admin/scenarios/test": {"ok": True},
                "hausman_hub/v1/admin/scenarios/delete": {"status": "confirmed"},
            },
            """
        panel._shell.tabs.scenarios.fire("click");
        await tick();
        const screen = panel._shell.scenarios;
        const text = textOf(screen);
        for (const label of ["Дом работает по вашим правилам", "На главной", "Доброе утро", "Уходим из дома", "Ночной режим", "с подтверждением", "Выключен"]) {
          if (!text.includes(label)) throw new Error("scenario text missing: " + label);
        }
        const search = findAll(screen, (node) => node.tagName === "INPUT" && node["placeholder"] === "Найти сценарий")[0];
        if (!search) throw new Error("scenario search field missing");
        search.value = "ноч";
        search.fire("input");
        const searchRows = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-row"));
        if (searchRows.filter((node) => !node.hidden).length !== 1
            || findAll(screen, (node) => node === search).length !== 1) {
          throw new Error("scenario search rerendered the field or filtered incorrectly");
        }
        search.value = "";
        search.fire("input");
        if (text.includes("mdi:")) throw new Error("raw MDI icon name exposed");
        const rows = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-row"));
        const icons = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-icon"));
        if (rows.length !== 3 || icons.length !== 3 || icons.some((node) => node.children.length !== 1)) {
          throw new Error("scenario Figma row hierarchy mismatch");
        }
        const rowButtons = (row) => findAll(row, (node) => node.tagName === "BUTTON");
        if (rowButtons(rows[0]).length < 6 || rowButtons(rows[2]).length < 6) {
          throw new Error("scenario maintenance actions mismatch");
        }
        let confirmation = "";
        window.confirm = (message) => { confirmation = message; return true; };
        rowButtons(rows[1]).find((node) => String(node["aria-label"] || "").startsWith("Запустить сценарий")).fire("click");
        await tick();
        if (!confirmation.includes("Уходим из дома")) throw new Error("camelCase confirmation flag ignored");
        const run = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/scenarios/run");
        if (!run || run.payload.scenario_id !== "scenario.leaving_home") {
          throw new Error("scenario run payload mismatch");
        }
        panel._shell.tabs.scenarios.fire("click");
        await tick();
        const favoriteButton = findAll(panel._shell.scenarios, (node) => node.tagName === "BUTTON")
          .find((node) => node["aria-label"] === "Добавить на главный экран");
        favoriteButton.fire("click");
        await tick();
        await tick();
        const favoriteSave = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/scenarios" && call.payload.favorite === true);
        if (!favoriteSave) throw new Error("favorite quick-save contract missing");
        panel._shell.tabs.scenarios.fire("click");
        await tick();
        const refreshedRows = findAll(panel._shell.scenarios, (node) =>
          String(node.className).split(" ").includes("scenario-row"));
        rowButtons(refreshedRows[0]).find((node) => node.textContent === "Проверить").fire("click");
        await tick();
        if (!calls.some((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/scenarios/test")) {
          throw new Error("scenario test API missing");
        }
        rowButtons(refreshedRows[0]).find((node) => node.textContent === "Удалить").fire("click");
        await tick();
        if (!calls.some((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/scenarios/delete")) {
          throw new Error("scenario delete API missing");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_scenario_deep_link_loads_library_without_sidebar_click(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/scenarios"] = {"scenarios": []}
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {"devices": []}
        script = panel_script(
            payloads,
            {},
            """
        setWindowLocation("?hh_section=scenarios");
        panel._onNavigationPop();
        await tick();
        const scenarioGets = calls.filter((call) => call.method === "GET"
          && call.path === "hausman_hub/v1/admin/scenarios");
        if (scenarioGets.length !== 1) {
          throw new Error("scenario deep link did not load the library");
        }
        if (!textOf(panel._shell.scenarios).includes("Создайте первый сценарий")) {
          throw new Error("scenario deep link did not render the empty state");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_empty_scenario_library_opens_editor_and_saves_device_action(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/scenarios"] = {"scenarios": []}
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {
            "devices": [
                {
                    "target_id": "light.living",
                    "name": "Основной свет · Гостиная",
                    "entity_id": "light.living",
                    "actions": [
                        {
                            "action_id": "turn_on",
                            "title": "Включить",
                            "domain": "light",
                            "service": "turn_on",
                            "allowed_fields": [],
                        }
                    ],
                }
            ]
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/admin/scenarios": {"ok": True, "status": "success"}},
            """
        panel._shell.tabs.scenarios.fire("click");
        await tick();
        const screen = panel._shell.scenarios;
        const create = findAll(screen, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "+ Новый сценарий" || node.textContent === "Создать сценарий");
        if (!create) throw new Error("scenario create action missing from empty state");
        create.fire("click");
        if (!panel._scenarioEditor || !textOf(screen).includes("РЕДАКТОР СЦЕНАРИЯ")) {
          throw new Error("full scenario editor did not open");
        }
        const iconButtons = findAll(screen, (node) => node.tagName === "BUTTON"
          && String(node["aria-label"] || "").startsWith("Выбрать иконку"));
        const materialIcons = iconButtons.map((button) => findAll(button, (node) => node.tagName === "HA-ICON")[0])
          .filter(Boolean).map((node) => node.icon);
        if (iconButtons.length < 80 || new Set(materialIcons).size < 80
            || !textOf(screen).includes("Безопасность") || !textOf(screen).includes("Энергия")) {
          throw new Error("semantic scenario icon catalog is incomplete");
        }
        const currentIconLabel = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-icon-current"))[0]
          .children.find((node) => node.tagName === "STRONG");
        const differentIcon = iconButtons.find((button) => !button.className.includes("is-selected"));
        const expectedIconLabel = differentIcon.children.find((node) => node.tagName === "SPAN").textContent;
        differentIcon.fire("click");
        if (currentIconLabel.textContent !== expectedIconLabel) throw new Error("selected icon summary stayed stale");

        const labelledControl = (label) => {
          const wrapper = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-editor-field"))
            .find((node) => node.children[0] && node.children[0].textContent === label);
          if (!wrapper) throw new Error("scenario field missing: " + label);
          return wrapper.children.find((node) => ["INPUT", "TEXTAREA", "SELECT"].includes(node.tagName));
        };
        const title = labelledControl("Название");
        title.value = "Вечерний свет";
        title.fire("input");
        const actionStep = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-editor-step"))
          .find((node) => textOf(node).includes("Тогда"));
        if (!actionStep) throw new Error("scenario action step is missing");
        actionStep.fire("click");
        const addAction = findAll(screen, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "+ Добавить действие");
        addAction.fire("click");

        let device = labelledControl("Устройство");
        device.value = "light.living";
        device.fire("change");
        const command = labelledControl("Команда устройства");
        command.value = "turn_on";
        command.fire("change");
        const save = findAll(screen, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Сохранить");
        save.fire("click");
        await tick();
        await tick();

        const request = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/scenarios");
        if (!request) throw new Error("scenario save API was not called");
        if (request.payload.title !== "Вечерний свет"
            || request.payload.definition.triggers[0].type !== "manual"
            || request.payload.definition.actions[0].targetId !== "light.living"
            || request.payload.definition.actions[0].actionId !== "turn_on") {
          throw new Error("scenario save contract mismatch: " + JSON.stringify(request.payload));
        }
        if (panel._scenarioEditor !== null) throw new Error("editor stayed open after successful save");
        if (!textOf(panel.shadowRoot).includes("сохранён")) throw new Error("scenario success feedback missing");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_scenario_state_selector_localizes_labels_and_saves_raw_ha_value(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/scenarios"] = {"scenarios": []}
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {
            "devices": [
                {
                    "target_id": "switch.living",
                    "name": "Свет гостиной",
                    "entity_id": "switch.living",
                    "properties": [],
                    "actions": [{"action_id": "turn_on", "title": "Включить"}],
                }
            ]
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/admin/scenarios": {"ok": True, "status": "success"}},
            """
        panel._shell.tabs.scenarios.fire("click");
        await tick();
        const screen = panel._shell.scenarios;
        findAll(screen, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "+ Новый сценарий" || node.textContent === "Создать сценарий")
          .fire("click");
        const field = (label) => {
          const wrapper = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-editor-field"))
            .find((node) => node.children[0] && node.children[0].textContent === label);
          if (!wrapper) throw new Error("scenario field missing: " + label);
          return wrapper.children.find((node) => ["INPUT", "TEXTAREA", "SELECT"].includes(node.tagName));
        };
        const title = field("Название");
        title.value = "Проверка света";
        title.fire("input");
        findAll(screen, (node) => String(node.className).split(" ").includes("scenario-editor-step"))
          .find((node) => textOf(node).includes("Когда")).fire("click");
        let triggerType = field("Тип триггера");
        triggerType.value = "device_state";
        triggerType.fire("change");
        let device = field("Устройство");
        device.value = "switch.living";
        device.fire("change");
        const state = field("Состояние");
        const enabledOption = state.children.find((option) => option.value === "on");
        if (!enabledOption || enabledOption.textContent !== "Включено") {
          throw new Error("localized state option does not preserve raw HA value");
        }
        state.value = "on";
        state.fire("change");
        if (panel._scenarioEditor.definition.triggers[0].value !== "on") {
          throw new Error("localized selector stored a translated value");
        }
        panel._scenarioEditor.definition.actions = [{
          id: "action-1", type: "device_action", targetId: "switch.living", actionId: "turn_on",
        }];
        panel._renderScenarios(screen);
        findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "Сохранить")[0].fire("click");
        await tick();
        await tick();
        const request = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/scenarios");
        if (!request || request.payload.definition.triggers[0].value !== "on") {
          throw new Error("scenario API did not receive raw Home Assistant state: "
            + JSON.stringify(request && request.payload));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_scenario_editor_has_five_clear_steps_switches_and_safe_escape(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/scenarios"] = {"scenarios": []}
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {"devices": []}
        script = panel_script(
            payloads,
            {},
            """
        panel._shell.tabs.scenarios.fire("click");
        await tick();
        const screen = panel._shell.scenarios;
        findAll(screen, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "+ Новый сценарий" || node.textContent === "Создать сценарий")
          .fire("click");
        await tick();
        const steps = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-editor-step"));
        const labels = steps.map((node) => findAll(node, (child) => child.tagName === "B")[0].textContent);
        if (JSON.stringify(labels) !== JSON.stringify(["Основное", "Когда", "Если", "Тогда", "Доступ"])) {
          throw new Error("scenario editor step contract mismatch: " + JSON.stringify(labels));
        }
        steps[3].fire("click");
        const addAction = findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "+ Добавить действие")[0];
        addAction.fire("click");
        findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "+ Добавить действие")[0].fire("click");
        const originalOrder = panel._scenarioEditor.definition.actions.map((item) => item.id);
        const moveDown = findAll(screen, (node) => node.tagName === "BUTTON" && node["aria-label"] === "Опустить шаг 1")[0];
        if (!moveDown || moveDown.disabled) throw new Error("scenario action ordering control missing");
        moveDown.fire("click");
        const movedOrder = panel._scenarioEditor.definition.actions.map((item) => item.id);
        if (movedOrder[0] !== originalOrder[1] || movedOrder[1] !== originalOrder[0]) {
          throw new Error("scenario action order did not change");
        }
        const actionIssue = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-editor-step-issue"))[0];
        if (!actionIssue) throw new Error("incomplete scenario action is not flagged before API validation");
        const refreshedSteps = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-editor-step"));
        refreshedSteps[4].fire("click");
        const switches = findAll(screen, (node) => node.role === "switch");
        if (switches.length !== 3 || switches.some((node) => !["true", "false"].includes(node["aria-checked"]))) {
          throw new Error("scenario publication switches are not accessible");
        }
        const favorite = switches.find((node) => textOf(node).includes("Показывать на главной"));
        favorite.fire("click");
        if (favorite["aria-checked"] !== "true" || panel._scenarioEditor.favorite !== true) {
          throw new Error("scenario favorite switch did not update the draft");
        }
        favorite.fire("click");
        if (favorite["aria-checked"] !== "false" || panel._scenarioEditor.favorite !== false) {
          throw new Error("scenario favorite switch did not toggle off");
        }
        const enabled = switches.find((node) => textOf(node).includes("Сценарий включён"));
        enabled.fire("click");
        if (enabled["aria-checked"] !== "false" || panel._scenarioEditor.enabled !== false) {
          throw new Error("initially enabled scenario switch did not toggle off");
        }
        let confirmations = 0;
        window.confirm = () => { confirmations += 1; return true; };
        const overlay = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-editor-overlay"))[0];
        let prevented = false;
        overlay.fire("keydown", { key: "Escape", preventDefault: () => { prevented = true; } });
        if (!prevented || confirmations !== 1 || panel._scenarioEditor !== null) {
          throw new Error("Escape did not safely close a dirty scenario editor");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_settings_match_figma_and_controls_have_real_behavior(self) -> None:
        css = (
            PANEL_CSS.read_text(encoding="utf-8")
            + SETTINGS_CSS.read_text(encoding="utf-8")
            + DIAGNOSTICS_CSS.read_text(encoding="utf-8")
            + SWITCH_CSS.read_text(encoding="utf-8")
        )
        for tablet_rule in (
            ".settings-subnav",
            ".settings-overview-grid",
            ".settings-menu-card",
            ".settings-room-grid",
            ".settings-page-actions { position:sticky",
            ".room-setup-nav",
            "backdrop-filter:blur(18px)",
            ".settings-switch-track",
            ".settings-switch.is-on .settings-switch-knob",
            ".system-diagnostic-grid",
            ".system-technical-report",
        ):
            self.assertIn(tablet_rule, css)
        self.assertNotIn("input.settings-toggle", css)
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/connection-settings"] = {
            "connection_mode": "home_assistant",
            "smart_home_center_url": None,
            "home_assistant_url": "https://homeassistant.local",
        }
        script = panel_script(
            payloads,
            {
                "hausman_hub/v1/admin/connection-settings": {"status": "success"},
                "hausman_hub/v1/admin/reset": {"status": "reset"},
            },
            """
        panel._shell.tabs.settings.fire("click");
        await tick();
        let screen = panel._shell.settings;
        let text = textOf(screen);
        for (const label of [
          "Настройки HausmanHub", "Обзор", "Комнаты", "Подключение", "Интерфейс", "Диагностика",
          "Комнаты и устройства", "Home Assistant остаётся единым источником устройств", "Версия",
        ]) {
          if (!text.includes(label)) throw new Error("settings text missing: " + label);
        }
        if (text.includes("Центр умного дома")) throw new Error("obsolete Smart Home Center label exposed");
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Подключение")[0].fire("click");
        screen = panel._shell.settings;
        const urls = findAll(screen, (node) => node.type === "url");
        if (urls.length !== 1 || !textOf(screen).includes("Источник данных и команд")
          || !textOf(screen).includes("Внешний Center и Node-RED для работы не требуются")) {
          throw new Error("connection settings page mismatch");
        }
        const panelGets = calls.filter((call) => call.method === "GET"
          && call.path === "hausman_hub/v1/admin/panel").length;
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить доступность панели")[0].fire("click");
        await tick();
        if (calls.filter((call) => call.method === "GET"
          && call.path === "hausman_hub/v1/admin/panel").length !== panelGets + 1) {
          throw new Error("connection check did not call the live panel API");
        }
        screen = panel._shell.settings;
        const centerField = findAll(screen, (node) =>
          String(node.className).split(" ").includes("settings-field")
          && textOf(node).includes("Адрес совместимого API"))[0];
        if (centerField) throw new Error("external API URL remains visible in HA-owned mode");
        const haUrl = findAll(screen, (node) => node.type === "url")[0];
        const stableUrlField = haUrl;
        haUrl.value = "https://ha.example.test";
        haUrl.fire("input");
        if (findAll(screen, (node) => node.type === "url")[0] !== stableUrlField) {
          throw new Error("connection form was rerendered while typing");
        }
        panel.shadowRoot.activeElement = haUrl;
        panel._render();
        if (findAll(screen, (node) => node.type === "url")[0] !== stableUrlField) {
          throw new Error("background render replaced the focused dirty connection field");
        }
        panel.shadowRoot.activeElement = null;
        screen = panel._shell.settings;
        const save = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить")[0];
        if (save.disabled) throw new Error("settings save stayed disabled after edit");
        save.fire("click");
        await tick();
        const post = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/connection-settings");
        const expected = {
          connection_mode: "home_assistant",
          smart_home_center_url: "",
          home_assistant_url: "https://ha.example.test",
        };
        if (!post || JSON.stringify(post.payload) !== JSON.stringify(expected)) {
          throw new Error("settings payload mismatch: " + JSON.stringify(post && post.payload));
        }
        screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Интерфейс")[0].fire("click");
        screen = panel._shell.settings;
        const toggles = findAll(screen, (node) => String(node.className).split(" ").includes("settings-switch"));
        if (toggles.length !== 3 || !textOf(screen).includes("Тема панели")) {
          throw new Error("interface settings page mismatch");
        }
        if (toggles.some((node) => node.tagName !== "BUTTON" || node.role !== "switch"
          || !["true", "false"].includes(node["aria-checked"]))) {
          throw new Error("interface preference is not an accessible custom switch");
        }
        const motionToggle = findAll(screen, (node) =>
          String(node.className).split(" ").includes("settings-switch"))[1];
        motionToggle.fire("click");
        if (panel._settingsPrefs.reduced_motion !== true) {
          throw new Error("reduced motion preference did not apply");
        }
        await tick();
        const savedPreference = wsMessages.find((message) =>
          message.type === "frontend/set_user_data" && message.key === "hausman_hub");
        if (!savedPreference || savedPreference.value.reduced_motion !== true) {
          throw new Error("interface preferences were not persisted in the HA user profile");
        }
        if (wsMessages.filter((message) => message.type === "frontend/get_user_data").length !== 1) {
          throw new Error("HA user preferences were loaded more than once");
        }
        const restored = new (registry.get("hausman-hub-panel"))();
        restored.hass = hass;
        await tick();
        if (restored._settingsPrefs.reduced_motion !== true) {
          throw new Error("HA user interface preferences were not restored");
        }
        screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Диагностика")[0].fire("click");
        screen = panel._shell.settings;
        const systemText = textOf(screen);
        for (const label of [
          "Состояние системы", "Устройства климата", "Копировать техническую сводку",
          "Технический журнал", "Копировать журнал",
          "Связь с Home Assistant", "Сохранённая конфигурация", "Климатический контур",
          "Сценарии", "Энергия", "Проверки", "Показать обезличенную техническую сводку",
        ]) {
          if (!systemText.includes(label)) throw new Error("system status missing: " + label);
        }
        if (systemText.includes("native")) throw new Error("raw bridge mode exposed");
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Копировать техническую сводку")[0].fire("click");
        await tick();
        if (clipboardWrites.length !== 1
          || !clipboardWrites[0].includes("HausmanHub — техническая сводка")
          || !clipboardWrites[0].includes("Связь с Home Assistant: Соединение работает")
          || !clipboardWrites[0].includes("Сохранённая конфигурация: 1 комната · 0 устройств")
          || !clipboardWrites[0].includes("Климатический контур: Выключен в настройках")
          || clipboardWrites[0].includes("ready")
          || clipboardWrites[0].includes("homeassistant.local")
          || clipboardWrites[0].includes("entity_id")) {
          throw new Error("redacted technical summary copy mismatch: " + JSON.stringify(clipboardWrites));
        }
        screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Копировать журнал")[0].fire("click");
        await tick();
        if (clipboardWrites.length !== 2
          || !clipboardWrites[1].includes("Технический журнал текущего сеанса")
          || !clipboardWrites[1].includes("Связь с HausmanHub установлена")
          || clipboardWrites[1].includes("homeassistant.local")
          || clipboardWrites[1].includes("entity_id")) {
          throw new Error("redacted technical log copy mismatch: " + JSON.stringify(clipboardWrites));
        }
        screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Подготовить полный сброс")[0].fire("click");
        screen = panel._shell.settings;
        const confirmReset = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сбросить все настройки")[0];
        if (!confirmReset || !textOf(screen).includes("Home Assistant останутся без изменений")) {
          throw new Error("safe reset confirmation is incomplete");
        }
        confirmReset.fire("click");
        await tick();
        const resetPost = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/reset");
        if (!resetPost || resetPost.payload.confirmation !== "RESET_HAUSMANHUB") {
          throw new Error("full reset confirmation contract mismatch");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_composite_cards_keep_structure_inside_home_assistant_sidebar(self) -> None:
        panel_css = PANEL_CSS.read_text(encoding="utf-8")
        overview_css = OVERVIEW_CSS.read_text(encoding="utf-8")
        security_css = SECURITY_OVERVIEW_CSS.read_text(encoding="utf-8")
        devices_css = DEVICES_OVERVIEW_CSS.read_text(encoding="utf-8")
        settings_css = SETTINGS_CSS.read_text(encoding="utf-8")

        self.assertIn("container-name:hausmanhub-panel", panel_css)
        self.assertIn("@container hausmanhub-panel (min-width:1050px)", panel_css)
        self.assertIn(
            ".overview-canon-primary-card, .overview-canon-light-card, .overview-canon-side-card",
            overview_css,
        )
        self.assertIn("flex-direction:column; white-space:normal", overview_css)
        self.assertIn("repeat(auto-fit,minmax(min(100%,240px),1fr))", overview_css)
        self.assertIn("flex-direction:column", security_css)
        self.assertIn("white-space:normal", security_css)
        self.assertIn("flex-direction:column", devices_css)
        self.assertIn("white-space:normal", devices_css)
        self.assertIn("repeat(auto-fit,minmax(min(100%,420px),1fr))", settings_css)
        self.assertIn("text-align:left; white-space:normal", settings_css)

    def test_system_diagnostics_explain_component_health_without_private_ids(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/dashboard"] = {
            "devices": [], "rooms": [], "alarms": [],
            "energy": {
                "available": True,
                "sources": [
                    {"id": "source_private_a", "available": True},
                    {"id": "source_private_b", "available": False},
                ],
            },
        }
        payloads["hausman_hub/v1/admin/scenarios"] = {
            "scenarios": [
                {"id": "private_morning", "enabled": True},
                {"id": "private_night", "enabled": False},
            ]
        }
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {"devices": []}
        script = panel_script(
            payloads,
            {},
            """
        panel._shell.tabs.settings.fire("click");
        findAll(panel._shell.settings, (node) => node.tagName === "BUTTON"
          && node.textContent === "Диагностика")[0].fire("click");
        await tick();
        let screen = panel._shell.settings;
        let text = textOf(screen);
        for (const label of [
          "Все проверки пройдены", "Соединение работает", "1 комната · 0 устройств",
          "Выключен в настройках", "2 сохранено · 1 включено",
          "1 из 2 источников доступны",
        ]) {
          if (!text.includes(label)) throw new Error("healthy diagnostic missing: " + label);
        }
        if (text.includes("Что проверить") || text.includes("source_private")
          || text.includes("private_morning")) {
          throw new Error("healthy diagnostics exposed a false warning or private id: " + text);
        }
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Копировать техническую сводку")[0].fire("click");
        await tick();
        if (clipboardWrites.length !== 1
          || clipboardWrites[0].includes("source_private")
          || clipboardWrites[0].includes("private_morning")) {
          throw new Error("copied diagnostics exposed private identifiers");
        }
        panel._error = true;
        panel._renderSettings(panel._shell.settings);
        screen = panel._shell.settings;
        text = textOf(screen);
        if (!text.includes("Связь потеряна") || !text.includes("Нет ответа")
          || !text.includes("Проверьте Home Assistant и повторите обновление.")) {
          throw new Error("connection loss is not explained by diagnostics: " + text);
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_native_device_binding_wizard_previews_before_safe_save(self) -> None:
        payloads = dict(GET_PATHS)
        payloads[
            "hausman_hub/v1/admin/climate-device-bindings"
        ] = DEVICE_BINDINGS_PAYLOAD
        script = panel_script(
            payloads,
            {
                "hausman_hub/v1/admin/climate-device-bindings/preview": {
                    "snapshot_revision": 456,
                    "preview_revision": 789,
                    "save_allowed": True,
                    "commands_sent": False,
                    "issues": [],
                    "summary": {"selected_count": 1, "ready_count": 1},
                },
                "hausman_hub/v1/admin/climate-device-bindings": {
                    "status": "saved",
                    "updated_devices": 1,
                    "commands_sent": False,
                    "restart_required": False,
                },
            },
            """
        panel._shell.tabs.settings.fire("click");
        await tick();
        let screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Привязки")[0].fire("click");
        await tick();
        screen = panel._shell.settings;
        const text = textOf(screen);
        for (const label of [
          "Связи с устройствами Home Assistant", "Служебное восстановление",
          "Кондиционер гостиная", "Нужно выбрать", "Проверить изменения", "Сохранить в Home Assistant",
          "В этой комнате", "Во всём доме", "Проверка безопасна", "Единственное доступное совпадение",
        ]) {
          if (!text.includes(label)) throw new Error("binding wizard text missing: " + label);
        }
        let select = findAll(screen, (node) => node.tagName === "SELECT")[0];
        if (!select || select.value !== "" || select.children.length !== 2) {
          throw new Error("wizard auto-selected or exposed another-room entity");
        }
        const otherRooms = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Во всём доме")[0];
        if (!otherRooms) throw new Error("clear all-home diagnostic choice is missing");
        otherRooms.fire("click");
        screen = panel._shell.settings;
        select = findAll(screen, (node) => node.tagName === "SELECT")[0];
        if (select.children.length !== 3 || select.children[2].disabled !== true) {
          throw new Error("other-room diagnostic option is not visibly fail-closed");
        }
        const recommended = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Выбрать")[0];
        if (!recommended) throw new Error("safe same-room recommendation is missing");
        recommended.fire("click");
        await new Promise((resolve) => setTimeout(resolve, 400));
        screen = panel._shell.settings;
        const check = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить изменения")[0];
        const saveBefore = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить в Home Assistant")[0];
        if (!check || check.disabled || saveBefore.disabled) {
          throw new Error("automatic preview did not enable safe save");
        }
        const previewPost = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/climate-device-bindings/preview");
        const expectedPreview = {
          snapshot_revision: 456,
          bindings: [{ device_id: "living_ac", entity_id: "climate.living_ac" }],
        };
        if (!previewPost || JSON.stringify(previewPost.payload) !== JSON.stringify(expectedPreview)) {
          throw new Error("binding preview payload mismatch: " + JSON.stringify(previewPost));
        }
        screen = panel._shell.settings;
        if (!textOf(screen).includes("Проверка пройдена — можно сохранить")) {
          throw new Error("binding action state did not explain that saving is available");
        }
        const save = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить в Home Assistant")[0];
        if (save.disabled) throw new Error("save stayed disabled after successful preview");
        save.fire("click");
        await tick(10);
        const savePost = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/climate-device-bindings");
        const expectedSave = {
          snapshot_revision: 456,
          preview_revision: 789,
          bindings: expectedPreview.bindings,
        };
        if (!savePost || JSON.stringify(savePost.payload) !== JSON.stringify(expectedSave)) {
          throw new Error("binding save payload mismatch: " + JSON.stringify(savePost));
        }
        if (!panel._notice.includes("Команды устройствам не отправлялись")) {
          throw new Error("command-free receipt is not explained");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_user_preference_edit_wins_over_delayed_initial_load(self) -> None:
        script = panel_script(
            dict(GET_PATHS),
            {},
            """
        panel._settingsPrefs.large_text = true;
        panel._persistUserPreferences();
        await tick();
        if (!userPreferenceReadResolve) {
          throw new Error("preference load was not delayed");
        }
        userPreferenceReadResolve({
          value: {
            theme_mode: "dark",
            large_text: false,
            reduced_motion: true,
            show_hints: false,
          },
        });
        global.deferUserPreferenceRead = false;
        await tick();
        if (panel._settingsPrefs.large_text !== true
          || panel._settingsPrefs.reduced_motion !== false
          || panel._themeMode !== "auto") {
          throw new Error("delayed preference load overwrote the current user edit");
        }
        const writes = wsMessages.filter((message) =>
          message.type === "frontend/set_user_data" && message.key === "hausman_hub");
        if (writes.length !== 1
          || writes[0].value.large_text !== true
          || writes[0].value.theme_mode !== "auto") {
          throw new Error("current preferences were not persisted after delayed load");
        }
            """,
            before_panel="""
        global.deferUserPreferenceRead = true;
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_failed_device_action_uses_error_toast_without_hiding_panel(self) -> None:
        script = panel_script(
            dict(GET_PATHS),
            {"hausman_hub/v1/device-actions": {"__fail": 503}},
            """
        await panel._executeDeviceAction("target-living-main", "turn_on", null);
        await tick();
        const notice = panel._shell.notice;
        if (!String(notice.className).split(" ").includes("is-error")) {
          throw new Error("failed device action was not rendered as an error toast");
        }
        if (notice.role !== "alert" || notice["aria-live"] !== "assertive") {
          throw new Error("error toast accessibility contract mismatch");
        }
        if (panel._error) throw new Error("device failure hid the otherwise available panel");
        if (panel._notice !== "Команда устройству не выполнена. Откройте карточку и проверьте доступность.") {
          throw new Error("device failure explanation missing");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_settings_show_canonical_device_inventory_with_working_filters(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/dashboard"] = {
            "rooms": [],
            "devices": [],
            "alarms": [],
            "inventory": {
                "summary": {
                    "registeredCount": 3,
                    "canonicalDeviceCount": 2,
                    "physicalDeviceCount": 1,
                    "logicalEntityCount": 1,
                    "virtualCount": 2,
                    "unassignedCount": 1,
                    "unavailableCount": 1,
                    "emptyCount": 0,
                    "duplicateGroupCount": 1,
                    "attentionCount": 2,
                },
                "devices": [
                    {
                        "id": "inventory-1",
                        "canonicalId": "device-1",
                        "name": "Кондиционер",
                        "roomId": "kids",
                        "roomName": "Детская",
                        "kind": "virtual",
                        "status": "available",
                        "canonical": True,
                        "possibleDuplicate": False,
                        "entityCount": 1,
                        "domains": ["climate"],
                        "manufacturer": "Yandex",
                        "model": "YNDX-0006",
                    },
                    {
                        "id": "inventory-2",
                        "canonicalId": "device-1",
                        "name": "Кондиционер",
                        "roomId": "kids",
                        "roomName": "Детская",
                        "kind": "virtual",
                        "status": "unavailable",
                        "canonical": False,
                        "possibleDuplicate": True,
                        "entityCount": 1,
                        "domains": ["climate"],
                        "reason": "Похожий виртуальный контур уже представлен одной основной карточкой.",
                    },
                    {
                        "id": "inventory-3",
                        "canonicalId": "device-3",
                        "name": "Датчик температуры",
                        "roomId": None,
                        "roomName": None,
                        "kind": "physical",
                        "status": "available",
                        "canonical": True,
                        "possibleDuplicate": False,
                        "entityCount": 2,
                        "domains": ["sensor"],
                        "manufacturer": "Tuya",
                    },
                ],
            },
        }
        payloads["hausman_hub/v1/admin/device-maintenance"] = {
            "snapshotRevision": "0123456789abcdef",
            "areas": [
                {"id": "kids", "name": "Детская"},
                {"id": "living", "name": "Гостиная"},
            ],
            "devices": {
                "inventory-1": {
                    "roomAreaId": "kids",
                    "name": "Кондиционер",
                    "haUrl": "/config/devices/device/native-one",
                    "entityCount": 1,
                    "entities": [{"id": "climate.kids", "name": "Климат детской", "disabled": False}],
                    "integrationCount": 1,
                    "uses": [{"kind": "climate", "title": "Климатический контур", "detail": "Устройство управляет климатом."}],
                    "used": True,
                    "identifySupported": True,
                    "identifyLabel": "Identify",
                    "deleteBlocked": True,
                    "deleteBlockers": ["Климатический контур"],
                },
            },
        }
        script = panel_script(
            payloads,
            {},
            """
        panel._shell.tabs.settings.fire("click");
        await tick();
        let screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Комнаты")[0].fire("click");
        screen = panel._shell.settings;
        let text = textOf(screen);
        for (const label of [
          "Устройства Home Assistant", "физических устройств", "отдельных сущностей", "Обновить список",
          "Рекомендуется оставить", "Копия записи", "Основная запись · 1 из 2", "Не привязано",
        ]) {
          if (!text.includes(label)) throw new Error("inventory text missing: " + label);
        }
        const duplicates = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Возможные дубли")[0];
        duplicates.fire("click");
        const duplicateRows = findAll(screen, (node) =>
          String(node.className).split(" ").includes("device-inventory-row"));
        if (duplicateRows.length !== 2
          || !textOf(duplicateRows[0]).includes("Рекомендуется оставить")
          || !textOf(duplicateRows[1]).includes("Копия записи")
          || !textOf(screen).includes("Оставьте рекомендуемую основную запись")) {
          throw new Error("duplicate filter did not expose the complete comparison group");
        }
        const all = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Все")[0];
        all.fire("click");
        if (findAll(screen, (node) =>
          String(node.className).split(" ").includes("device-inventory-row")).length !== 3) {
          throw new Error("all inventory filter did not expose every registry record");
        }
        const search = findAll(screen, (node) =>
          String(node.className).split(" ").includes("device-inventory-search"))[0];
        search.value = "датчик";
        search.fire("input");
        const rows = findAll(screen, (node) =>
          String(node.className).split(" ").includes("device-inventory-row"));
        if (rows.length !== 1 || !textOf(rows[0]).includes("Датчик температуры")) {
          throw new Error("device inventory search mismatch");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_inventory_card_edits_native_device_and_explains_safe_actions(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/dashboard"] = {
            "rooms": [], "devices": [], "alarms": [],
            "inventory": {
                "summary": {"canonicalDeviceCount": 1, "attentionCount": 0},
                "devices": [{
                    "id": "inventory-native", "canonicalId": "device-native",
                    "name": "Датчик", "roomId": "living", "roomName": "Гостиная",
                    "kind": "physical", "status": "available", "canonical": True,
                    "possibleDuplicate": False, "entityCount": 2, "domains": ["sensor", "button"],
                    "imageUrl": "https://www.zigbee2mqtt.io/images/devices/TS011F_plug_1.png",
                }],
            },
        }
        payloads["hausman_hub/v1/admin/device-maintenance"] = {
            "snapshotRevision": "0123456789abcdef",
            "areas": [{"id": "living", "name": "Гостиная"}, {"id": "kids", "name": "Детская"}],
            "devices": {"inventory-native": {
                "roomAreaId": "living", "name": "Датчик", "haUrl": "/config/devices/device/native",
                "entityCount": 2, "integrationCount": 1,
                "entities": [{"id": "sensor.value", "name": "Температура", "disabled": False}],
                "uses": [], "used": False, "identifySupported": True,
                "identifyLabel": "Identify", "deleteBlocked": False, "deleteBlockers": [],
            }},
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/admin/device-maintenance": {"status": "saved"}},
            """
        panel._shell.tabs.settings.fire("click");
        await tick();
        let screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "Комнаты")[0].fire("click");
        screen = panel._shell.settings;
        const card = findAll(screen, (node) => String(node.className).includes("device-inventory-summary"))[0];
        const image = findAll(card, (node) => node.tagName === "IMG")[0];
        if (!image || image.src !== "https://www.zigbee2mqtt.io/images/devices/TS011F_plug_1.png") {
          throw new Error("official Zigbee2MQTT maintenance image is missing");
        }
        card.fire("click");
        await tick();
        const text = textOf(screen);
        for (const label of ["Где используется", "Не используется настройками HausmanHub", "Возможности устройства", "Показать состав", "sensor.value", "Открыть в Home Assistant", "Найти устройство", "Удалить из Home Assistant"]) {
          if (!text.includes(label)) throw new Error("maintenance action missing: " + label);
        }
        const name = findAll(screen, (node) => node.tagName === "INPUT" && node.maxLength === 128)[0];
        const room = findAll(screen, (node) => node.tagName === "SELECT")[0];
        name.value = "Главный датчик";
        room.value = "kids";
        findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "Сохранить имя и комнату")[0].fire("click");
        await tick(10);
        const save = calls.find((call) => call.method === "POST" && call.path === "hausman_hub/v1/admin/device-maintenance" && call.payload.action === "update");
        if (!save || save.payload.expectedRevision !== "0123456789abcdef"
          || save.payload.changes.name !== "Главный датчик" || save.payload.changes.areaId !== "kids") {
          throw new Error("native registry update payload mismatch: " + JSON.stringify(save));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_inventory_paginates_and_uses_inline_delete_confirmation(self) -> None:
        payloads = dict(GET_PATHS)
        devices = [
            {
                "id": f"inventory-{index}",
                "canonicalId": f"device-{index}",
                "name": f"Устройство {index:02d}",
                "roomId": "living",
                "roomName": "Гостиная",
                "kind": "physical",
                "status": "available",
                "canonical": True,
                "possibleDuplicate": False,
                "entityCount": 1,
                "domains": ["sensor"],
            }
            for index in range(18)
        ]
        payloads["hausman_hub/v1/dashboard"] = {
            "rooms": [], "devices": [], "alarms": [],
            "inventory": {
                "summary": {"canonicalDeviceCount": 18, "attentionCount": 0},
                "devices": devices,
            },
        }
        payloads["hausman_hub/v1/admin/device-maintenance"] = {
            "areas": [{"id": "living", "name": "Гостиная"}],
            "devices": {
                "inventory-0": {
                    "roomAreaId": "living", "name": "Устройство 00",
                    "haUrl": "/config/devices/device/native", "entityCount": 1,
                    "integrationCount": 1,
                    "entities": [{"id": "sensor.value", "name": "Значение", "disabled": False}],
                    "uses": [], "used": False, "identifySupported": False,
                    "identifyLabel": None, "deleteBlocked": False, "deleteBlockers": [],
                }
            },
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/admin/device-maintenance": {"status": "deleted"}},
            """
        panel._shell.tabs.settings.fire("click");
        await tick();
        let screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "Комнаты")[0].fire("click");
        screen = panel._shell.settings;
        let rows = findAll(screen, (node) => String(node.className).split(" ").includes("device-inventory-row"));
        if (rows.length !== 16) throw new Error("inventory first page must contain 16 rows");
        const more = findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "Показать ещё 2")[0];
        if (!more) throw new Error("inventory pagination control is missing");
        more.fire("click");
        rows = findAll(screen, (node) => String(node.className).split(" ").includes("device-inventory-row"));
        if (rows.length !== 18) throw new Error("inventory pagination did not reveal remaining rows");
        findAll(rows[0], (node) => String(node.className).includes("device-inventory-summary"))[0].fire("click");
        await tick();
        findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "Удалить из Home Assistant")[0].fire("click");
        if (calls.some((call) => call.method === "POST" && call.payload.action === "delete")) {
          throw new Error("delete ran before the inline confirmation");
        }
        const confirmationText = textOf(screen);
        if (!confirmationText.includes("Это не отключает физическое устройство")) {
          throw new Error("safe inline delete explanation is missing");
        }
        findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "Удалить запись")[0].fire("click");
        await tick(10);
        const removal = calls.find((call) => call.method === "POST" && call.payload.action === "delete");
        if (!removal || removal.payload.confirmed !== true || removal.payload.deviceId !== "inventory-0") {
          throw new Error("confirmed registry removal payload mismatch: " + JSON.stringify(removal));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_entity_only_inventory_card_is_manageable_without_fake_device(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/dashboard"] = {
            "rooms": [], "devices": [], "alarms": [],
            "inventory": {
                "summary": {"canonicalDeviceCount": 1, "attentionCount": 0},
                "devices": [{
                    "id": "inventory-entity", "canonicalId": "device-entity",
                    "name": "Отдельное реле", "roomId": None, "roomName": None,
                    "kind": "entity_only", "status": "available", "canonical": True,
                    "possibleDuplicate": False, "entityCount": 1, "domains": ["switch"],
                    "imageUrl": None,
                }],
            },
        }
        payloads["hausman_hub/v1/admin/device-maintenance"] = {
            "areas": [{"id": "living", "name": "Гостиная"}],
            "devices": {"inventory-entity": {
                "kind": "entity_only", "roomAreaId": None, "name": "Отдельное реле",
                "haUrl": "/config/entities/entity/switch.standalone_relay",
                "entityCount": 1, "integrationCount": 1,
                "entities": [{"id": "switch.standalone_relay", "name": "Отдельное реле", "disabled": False}],
                "uses": [], "used": False, "identifySupported": False,
                "identifyLabel": None, "deleteBlocked": False, "deleteBlockers": [],
            }},
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/admin/device-maintenance": {"status": "saved"}},
            """
        panel._shell.tabs.settings.fire("click");
        await tick();
        let screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON" && node.textContent === "Комнаты")[0].fire("click");
        screen = panel._shell.settings;
        findAll(screen, (node) => String(node.className).includes("device-inventory-summary"))[0].fire("click");
        await tick();
        const text = textOf(screen);
        for (const label of [
          "Отдельная сущность Home Assistant", "Сохранить имя и комнату",
          "Открыть в Home Assistant", "Удалить из Home Assistant",
        ]) {
          if (!text.includes(label)) throw new Error("entity-only maintenance action missing: " + label);
        }
        const neutral = findAll(screen, (node) => String(node.className).includes("device-inventory-neutral"))[0];
        if (!neutral || textOf(neutral) !== "◇") throw new Error("neutral unknown-device visual is missing");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_panel_shell_has_status_accessibility_and_responsive_rules(self) -> None:
        css = PANEL_CSS.read_text(encoding="utf-8")
        for rule in (
            "max-width:1440px",
            "overflow-x:auto",
            "@container hausmanhub-panel (max-width:1049px)",
            ".page-header-actions { flex:1 1 100%; width:100%; margin-left:0",
            "@media (max-width:640px)",
            "@media (max-width:380px)",
            "grid-template-columns:minmax(0,1fr)",
            ":focus-visible",
        ):
            self.assertIn(rule, css)
        script = panel_script(
            GET_PATHS,
            {},
            """
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Состояние и управление домом")) {
          throw new Error("header subtitle missing");
        }
        if (!text.includes("Управление климатом выключено")) {
          throw new Error("translated status missing");
        }
        const stylesheet = findAll(panel.shadowRoot, (node) => node.tagName === "LINK")[0];
        if (!stylesheet || !String(stylesheet.href).includes("hausman-hub-panel.css?v=1.52.40")) {
          throw new Error("local panel stylesheet missing");
        }
        const active = panel._shell.sectionNodes.overview;
        const hidden = Object.entries(panel._shell.sectionNodes)
          .filter(([name]) => name !== "overview")
          .every(([, node]) => node.hidden === true);
        if (active.hidden || !hidden) throw new Error("inactive sections are not hidden");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_room_presence_is_searchable_and_selected_items_render_first(self) -> None:
        payloads = dict(GET_PATHS)
        windows = json.loads(json.dumps(WINDOWS_PAYLOAD))
        windows["rooms"][0]["presence_entity_ids"] = [
            "binary_sensor.living_occupancy"
        ]
        payloads["hausman_hub/v1/admin/climate-room-signals"] = windows
        script = panel_script(
            payloads,
            {},
            """
        const searches = findAll(panel.shadowRoot, (node) => node.type === "search");
        const presenceSearch = searches.find((node) =>
          String(node.placeholder).includes("датчик присутствия"));
        if (!presenceSearch) throw new Error("presence search missing");
        const presenceBoxes = findAll(panel.shadowRoot, (node) =>
          node.type === "checkbox" && String(node.value).startsWith("binary_sensor.living_"));
        if (presenceBoxes[0].value !== "binary_sensor.living_occupancy") {
          throw new Error("selected presence candidate is not first");
        }
        const labels = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("device-option"));
        const findLabel = (value) => labels.find((label) =>
          label.children.some((child) => child.type === "checkbox" && child.value === value));
        const selected = findLabel("binary_sensor.living_occupancy");
        const motion = findLabel("binary_sensor.living_motion");
        presenceSearch.value = "motion";
        presenceSearch.fire("input");
        if (!selected.hidden || motion.hidden) throw new Error("presence search filter failed");
        const text = textOf(panel.shadowRoot);
        if (!text.includes("пока не меняет температуру мгновенно")) {
          throw new Error("room-presence policy distinction missing");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_configured_sections_render_saved_values(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Профили климата")) throw new Error("profiles heading missing");
        if (!text.includes("Расписание")) throw new Error("schedule heading missing");
        if (!text.includes("Сигналы дома")) throw new Error("home heading missing");
        if (!text.includes("Сигналы комнат")) throw new Error("room signals heading missing");
        if (!text.includes("Прямой сигнал работы или температура батареи / трубы отопления.")) {
          throw new Error("central heating helper does not explain both supported source types");
        }
        if (!text.includes("Общее присутствие дома")) throw new Error("general presence label missing");
        if (!text.includes("Датчики присутствия")) throw new Error("room presence label missing");
        const numbers = findAll(panel.shadowRoot, (node) => node.type === "number");
        const temperatures = numbers.filter((node) => String(node.value) === "23");
        if (!temperatures.length) throw new Error("saved day temperature not rendered");
        const times = findAll(panel.shadowRoot, (node) => node.type === "time");
        if (times.length !== 2 || times[0].value !== "07:00" || times[1].value !== "23:00") {
          throw new Error("saved schedule times not rendered");
        }
        const savedWindow = findAll(panel.shadowRoot, (node) =>
          node.type === "radio"
          && node.value === "binary_sensor.kids_window"
          && node.checked);
        if (savedWindow.length !== 1) throw new Error("saved window binding not selected");
        if (!text.includes("Ранее выбранный источник сейчас недоступен")) {
          throw new Error("missing candidate fallback absent");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_signal_pickers_are_grouped_cards_and_room_candidates_do_not_leak(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const fieldsets = findAll(panel.shadowRoot, (node) => node.tagName === "FIELDSET");
        const outdoor = fieldsets.find((node) =>
          textOf(node).includes("Наружная температура"));
        if (!outdoor) throw new Error("outdoor card picker missing");
        const outdoorText = textOf(outdoor);
        for (const label of ["Уличные датчики", "Погодные сервисы", "Датчик температуры", "Погодный сервис"]) {
          if (!outdoorText.includes(label)) throw new Error("outdoor grouping missing: " + label);
        }
        if (outdoorText.includes("Детская") || outdoorText.includes("Гостиная")) {
          throw new Error("indoor room leaked into the outdoor temperature picker");
        }
        const weather = findAll(outdoor, (node) =>
          node.tagName === "BUTTON" && node.value === "weather.home");
        if (weather.length !== 1) throw new Error("weather source missing");
        if (findAll(outdoor, (node) => node.tagName === "SELECT").length) {
          throw new Error("outdoor source is still a dropdown");
        }
        const occupancyLabel = panel._signalCandidateType(
          { domain: "binary_sensor", device_class: "occupancy" }, "presence"
        );
        if (occupancyLabel !== "Датчик присутствия") {
          throw new Error("occupancy device class has an unnatural Russian label");
        }
        const presence = fieldsets.find((node) =>
          textOf(node).includes("Общее присутствие дома"));
        const presenceText = textOf(presence);
        for (const label of [
          "Присутствие дома",
          "Члены дома (геолокация)",
          "Дом · профиль пользователя",
          "профиль пользователя: дома / не дома",
        ]) {
          if (!presenceText.includes(label)) {
            throw new Error("person entity is not explained: " + label);
          }
        }
        const roomCards = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("signal-room"));
        const living = roomCards.find((node) => textOf(node).includes("Гостиная"));
        const kids = roomCards.find((node) => textOf(node).includes("Детская"));
        const livingPresence = findAll(living, (node) =>
          node.type === "checkbox" && node.value === "binary_sensor.living_motion");
        const kidsPresence = findAll(kids, (node) =>
          node.type === "checkbox" && node.value === "binary_sensor.living_motion");
        if (livingPresence.length !== 1 || kidsPresence.length !== 0) {
          throw new Error("room presence candidates leaked across rooms");
        }
        const livingWindow = findAll(living, (node) =>
          node.type === "radio" && node.value === "binary_sensor.living_window");
        const kidsWindow = findAll(kids, (node) =>
          node.type === "radio" && node.value === "binary_sensor.living_window");
        if (livingWindow.length !== 1 || kidsWindow.length !== 0) {
          throw new Error("window candidates leaked across rooms");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_signal_picker_collapses_duplicate_entities_of_one_physical_sensor(self) -> None:
        payloads = dict(GET_PATHS)
        home = json.loads(json.dumps(HOME_PAYLOAD))
        home["candidates"]["outdoor_temperature"] = [
            {
                "entity_id": "sensor.vneshnii_datchik_temperatury_temperature",
                "name": "Внешний датчик температуры",
                "available": True,
                "domain": "sensor",
                "device_class": "temperature",
                "room_id": "",
                "device_group_id": "device_outdoor",
                "device_name": "Внешний датчик температуры",
            },
            {
                "entity_id": "sensor.vneshnii_datchik_temperatury_external_temperature",
                "name": "Внешний датчик температуры",
                "available": True,
                "domain": "sensor",
                "device_class": "temperature",
                "room_id": "",
                "device_group_id": "device_outdoor",
                "device_name": "Внешний датчик температуры",
            },
            {
                "entity_id": "sensor.rezervnyi_datchik_temperature",
                "name": "Внешний датчик температуры",
                "available": True,
                "domain": "sensor",
                "device_class": "temperature",
                "room_id": "",
                "device_group_id": "device_reserve",
                "device_name": "Резервный датчик",
            },
        ]
        payloads["hausman_hub/v1/admin/home-environment"] = home
        script = panel_script(
            payloads,
            {},
            """
        const fieldsets = findAll(panel.shadowRoot, (node) => node.tagName === "FIELDSET");
        const outdoor = fieldsets.find((node) =>
          textOf(node).includes("Наружная температура"));
        const physicalSensorChoices = findAll(outdoor, (node) =>
          node.tagName === "BUTTON"
          && String(node.value).startsWith("sensor.vneshnii_datchik_temperatury_"));
        if (physicalSensorChoices.length !== 1
          || physicalSensorChoices[0].value
            !== "sensor.vneshnii_datchik_temperatury_external_temperature") {
          throw new Error("duplicate entities of one physical sensor were not collapsed");
        }
        const reserve = findAll(outdoor, (node) => node.tagName === "BUTTON"
          && node.value === "sensor.rezervnyi_datchik_temperature");
        if (reserve.length !== 1) {
          throw new Error("a different physical sensor was incorrectly collapsed");
        }
        const savedAlias = panel._signalCandidatesForPicker(
          panel._settings.home.candidates.outdoor_temperature,
          "sensor.vneshnii_datchik_temperatury_temperature",
          "outdoor_temperature"
        ).filter((candidate) => candidate.device_group_id === "device_outdoor");
        if (savedAlias.length !== 1
          || savedAlias[0].entity_id !== "sensor.vneshnii_datchik_temperatury_temperature") {
          throw new Error("the already saved entity did not retain priority");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_weather_sources_with_the_same_name_are_visually_distinct(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const candidates = [
          { entity_id: "weather.forecast_home_assistant", name: "Forecast",
            available: true, domain: "weather", room_id: "" },
          { entity_id: "weather.forecast_omsk", name: "Forecast",
            available: true, domain: "weather", room_id: "" },
        ];
        const picker = panel._singleChoicePicker({
          title: "Наружная температура", candidates, current: null,
          signalKind: "outdoor_temperature", onChange: () => {},
        });
        const text = textOf(picker.root);
        if (!text.includes("Погода · Home Assistant") || !text.includes("Погода · Омск")) {
          throw new Error("weather sources with identical friendly names are still ambiguous");
        }
        if (text.split("\\n").filter((part) => part === "Forecast").length) {
          throw new Error("generic English weather label leaked into the Russian picker");
        }
        if (picker.radios.filter(({ radio }) => radio.value.startsWith("weather.")).length !== 2) {
          throw new Error("distinct weather providers were incorrectly deduplicated");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_outdoor_sources_can_be_prioritized_with_a_physical_reserve(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const candidates = [
          { entity_id: "weather.forecast_omsk", name: "Forecast",
            available: false, domain: "weather", room_id: "" },
          { entity_id: "sensor.outdoor_temperature", name: "Внешний датчик температуры",
            available: true, domain: "sensor", device_class: "temperature", room_id: "" },
          { entity_id: "sensor.outdoor_reserve", name: "Резервный уличный датчик",
            available: true, domain: "sensor", device_class: "temperature", room_id: "" },
        ];
        const changes = [];
        const picker = panel._priorityChoicePicker({
          title: "Наружная температура", candidates,
          current: ["weather.forecast_omsk", "sensor.outdoor_temperature"],
          signalKind: "outdoor_temperature", onChange: (value) => changes.push(value),
        });
        if (picker.value().join(",") !== "weather.forecast_omsk,sensor.outdoor_temperature") {
          throw new Error("saved priority order was not restored");
        }
        const down = findAll(picker.root, (node) =>
          node.tagName === "BUTTON" && node.textContent === "↓" && !node.disabled)[0];
        if (!down) throw new Error("priority down control missing");
        down.fire("click");
        if (picker.value().join(",") !== "sensor.outdoor_temperature,weather.forecast_omsk") {
          throw new Error("physical reserve could not be promoted to primary");
        }
        if (changes.length !== 1) throw new Error("priority change was not reported");
        const text = textOf(picker.root);
        for (const label of [
          "Основной источник", "Резерв 1", "Добавить резервный источник",
          "Недоступен — будет пропущен", "Используется сейчас",
        ]) {
          if (!text.includes(label)) throw new Error("priority UI missing: " + label);
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_priority_weather_picker_identifies_same_named_providers(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const candidates = [
          { entity_id: "weather.forecast_home_assistant", name: "Прогноз HA",
            device_name: "Forecast", available: true, domain: "weather", room_id: "" },
          { entity_id: "weather.forecast_omsk", name: "Прогноз Омск",
            device_name: "Forecast", available: true, domain: "weather", room_id: "" },
        ];
        const picker = panel._priorityChoicePicker({
          title: "Наружная температура", candidates, current: [],
          signalKind: "outdoor_temperature", onChange: () => {},
        });
        const text = textOf(picker.root);
        for (const label of [
          "Погода · Home Assistant", "weather.forecast_home_assistant",
          "Погода · Омск", "weather.forecast_omsk",
          "Первый источник будет основным", "Выбрать основным",
        ]) {
          if (!text.includes(label)) throw new Error("weather priority UI missing: " + label);
        }
        if (text.split("\\n").filter((part) => part === "Forecast").length) {
          throw new Error("ambiguous generic weather name leaked into priority picker");
        }
        const buttons = findAll(picker.root, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Выбрать основным");
        if (buttons.length !== 2 || buttons[0].value === buttons[1].value) {
          throw new Error("distinct weather providers cannot be selected independently");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_away_mode_is_not_described_as_a_physical_presence_sensor(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const candidate = {
          entity_id: "binary_sensor.a100_away", name: "A100 Away",
          device_name: "A100 Away", domain: "binary_sensor",
          device_class: "occupancy", available: true,
        };
        const type = panel._signalCandidateType(candidate, "presence");
        const explanation = panel._signalCandidateExplanation(candidate, "presence");
        if (type !== "Режим «Дома / Не дома»") {
          throw new Error("away mode has the wrong type: " + type);
        }
        for (const label of ["включено — никого нет", "выключено — дома", "не физический датчик"]) {
          if (!explanation.includes(label)) throw new Error("away explanation missing: " + label);
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_home_presence_picker_excludes_room_motion_and_presence_sensors(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const candidates = [
          { entity_id: "person.ivan", name: "Иван", domain: "person" },
          { entity_id: "binary_sensor.a100_away", name: "A100 Away",
            domain: "binary_sensor", device_class: "occupancy" },
          { entity_id: "binary_sensor.hall_motion", name: "Движение прихожая",
            domain: "binary_sensor", device_class: "motion", room_name: "Прихожая" },
          { entity_id: "binary_sensor.toilet_occupancy", name: "Присутствие туалет",
            domain: "binary_sensor", device_class: "occupancy", room_name: "Туалет" },
        ];
        const visible = panel._signalCandidatesForPicker(candidates, null, "presence");
        const ids = visible.map(({ entity_id }) => entity_id);
        if (!ids.includes("person.ivan") || !ids.includes("binary_sensor.a100_away")) {
          throw new Error("home-wide presence sources were removed");
        }
        if (ids.includes("binary_sensor.hall_motion") || ids.includes("binary_sensor.toilet_occupancy")) {
          throw new Error("room sensor leaked into home presence picker");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_previously_selected_indoor_sensor_is_explained_as_unsuitable_outdoors(self) -> None:
        payloads = dict(GET_PATHS)
        home = json.loads(json.dumps(HOME_PAYLOAD))
        home["home"]["outdoor_temperature_entity_id"] = "sensor.kids_temperature"
        home["candidates"]["outdoor_temperature"] = [
            candidate
            for candidate in home["candidates"]["outdoor_temperature"]
            if candidate["domain"] == "weather"
        ]
        payloads["hausman_hub/v1/admin/home-environment"] = home
        script = panel_script(
            payloads,
            {},
            """
        const fieldsets = findAll(panel.shadowRoot, (node) => node.tagName === "FIELDSET");
        const outdoor = fieldsets.find((node) =>
          textOf(node).includes("Наружная температура"));
        const text = textOf(outdoor);
        for (const label of [
          "Ранее выбранное",
          "Ранее выбранный источник",
          "не подходит для наружной температуры или сейчас недоступно",
        ]) {
          if (!text.includes(label)) {
            throw new Error("unsuitable saved source is not explained: " + label);
          }
        }
        if (text.includes("Детская")) {
          throw new Error("indoor room is still presented as an outdoor source");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_outdoor_picker_excludes_indoor_sources_and_uses_semantic_group(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const candidates = [
          { entity_id: "sensor.kitchen_temperature", name: "Климат кухня",
            device_name: "Климат Kojima кухня", domain: "sensor",
            device_class: "temperature", room_id: "kitchen", room_name: "Кухня" },
          { entity_id: "sensor.humidifier_temperature", name: "Увлажнитель гостиная",
            device_name: "Увлажнитель гостиная", domain: "sensor",
            device_class: "temperature", room_id: "living", room_name: "Гостиная" },
          { entity_id: "sensor.outdoor_temperature", name: "Внешний датчик температуры",
            device_name: "Внешний датчик температуры", domain: "sensor",
            device_class: "temperature", room_id: "kitchen", room_name: "Кухня" },
          { entity_id: "sensor.terrace_temperature", name: "Температура",
            device_name: "Датчик температуры", domain: "sensor",
            device_class: "temperature", room_id: "outside", room_name: "Улица" },
          { entity_id: "weather.home", name: "Прогноз дома", domain: "weather" },
        ];
        const picker = panel._singleChoicePicker({
          title: "Наружная температура", candidates, current: null,
          signalKind: "outdoor_temperature", onChange: () => {},
        });
        const values = picker.radios.map(({ radio }) => radio.value);
        if (values.includes("sensor.kitchen_temperature")
          || values.includes("sensor.humidifier_temperature")) {
          throw new Error("indoor sources leaked into the outdoor-temperature picker");
        }
        if (!values.includes("sensor.outdoor_temperature")
          || !values.includes("sensor.terrace_temperature")
          || !values.includes("weather.home")) {
          throw new Error("valid outdoor sources are missing");
        }
        const text = textOf(picker.root);
        if (!text.includes("Уличные датчики") || text.includes("КУХНЯ")) {
          throw new Error("outdoor source is grouped by an accidental HA room");
        }
        if (!text.includes("3 варианта")) {
          throw new Error("candidate count has incorrect Russian plural form");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_central_heating_picker_keeps_only_associative_signals(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const candidates = [
          { entity_id: "switch.living_left", name: "Выключатель гостиная", domain: "switch" },
          { entity_id: "switch.trv_child_lock", name: "Термоголовка блокировка", domain: "switch" },
          { entity_id: "switch.trv_heating", name: "TRV heating", domain: "switch" },
          { entity_id: "input_boolean.central_heating", name: "Центральное отопление", domain: "input_boolean" },
          { entity_id: "binary_sensor.boiler_heat", name: "Нагрев", domain: "binary_sensor", device_class: "heat" },
          { entity_id: "sensor.battery_temperature", name: "Температура батарей", domain: "sensor", device_class: "temperature", room_name: "Гостиная" },
          { entity_id: "sensor.living_temperature", name: "Температура гостиной", domain: "sensor", device_class: "temperature", room_name: "Гостиная" },
        ];
        const filtered = panel._signalCandidatesForPicker(candidates, null, "central_heating")
          .map((candidate) => candidate.entity_id).sort();
        const expected = ["binary_sensor.boiler_heat", "input_boolean.central_heating", "sensor.battery_temperature"].sort();
        if (JSON.stringify(filtered) !== JSON.stringify(expected)) {
          throw new Error("central heating picker exposed unrelated devices: " + JSON.stringify(filtered));
        }
        const retained = panel._signalCandidatesForPicker(
          candidates, "switch.living_left", "central_heating"
        ).map((candidate) => candidate.entity_id);
        if (retained.includes("switch.living_left")) {
          throw new Error("a stale unrelated signal remained selectable");
        }
        const picker = panel._singleChoicePicker({
          title: "Центральное отопление", candidates,
          current: "switch.living_left", signalKind: "central_heating", onChange: () => {},
        });
        if (picker.value() !== "") throw new Error("stale signal was not reset in the picker");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_single_choice_picker_keeps_stable_order_and_updates_summary(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const candidates = [
          { entity_id: "sensor.outdoor_b", name: "Уличный датчик Б", domain: "sensor",
            device_class: "temperature", room_id: "outside", room_name: "Улица", available: true },
          { entity_id: "sensor.outdoor_a", name: "Уличный датчик А", domain: "sensor",
            device_class: "temperature", room_id: "outside", room_name: "Улица", available: true },
        ];
        const changes = [];
        const first = panel._singleChoicePicker({
          title: "Наружная температура", candidates,
          current: "sensor.outdoor_b", signalKind: "outdoor_temperature",
          pickerId: "stable-test", purpose: "Проверка назначения.",
          recommendation: "Проверка рекомендации.", onChange: (value) => changes.push(value),
        });
        const second = panel._singleChoicePicker({
          title: "Наружная температура", candidates,
          current: "sensor.outdoor_a", signalKind: "outdoor_temperature",
          pickerId: "stable-test-2", onChange: () => {},
        });
        const order = (picker) => picker.radios.map(({ radio }) => radio.value);
        if (JSON.stringify(order(first)) !== JSON.stringify(order(second))) {
          throw new Error("selected value changed the visual order");
        }
        const target = first.radios.find(({ radio }) => radio.value === "sensor.outdoor_a");
        target.radio.checked = true;
        target.radio.fire("change");
        if (first.value() !== "sensor.outdoor_a" || changes.at(-1) !== "sensor.outdoor_a") {
          throw new Error("picker draft did not update atomically");
        }
        const text = textOf(first.root);
        for (const expected of [
          "Сейчас выбрано", "Уличный датчик А", "Зачем это нужно", "Как выбрать",
          "Изменить источник",
        ]) {
          if (!text.includes(expected)) throw new Error("clear picker copy missing: " + expected);
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_mode_switch_posts_exact_payload(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/climate-mode"] = {
            "mode": "disabled",
            "contour_configured": True,
            "rollout": {
                "phase": "ready_for_canary",
                "enable_allowed": True,
                "shadow_sample_count": 24,
                "shadow_ready_room_count": 1,
                "canary_room_id": "living",
                "reasons": [],
            },
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/admin/climate-mode": {"mode": "managed"}},
            """
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const enable = buttons.find((node) => node.textContent === "Запустить пилотную комнату");
        if (!enable || enable.disabled) throw new Error("enabled switch missing");
        const rolloutCard = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("rollout-readiness"))[0];
        if (!rolloutCard) throw new Error("rollout card missing");
        const rollout = textOf(rolloutCard);
        for (const expected of ["Пилот готов к запуску", "24", "Комнат проверено", "Гостиная"]) {
          if (!rollout.includes(expected)) throw new Error("rollout summary missing: " + expected);
        }
        const readyBadge = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("status-badge")
          && node.textContent === "Готово")[0];
        if (!readyBadge || !String(readyBadge.className).split(" ").includes("is-ready")) {
          throw new Error("ready rollout does not use the shared status style");
        }
        enable.fire("click");
        await tick();
        const post = calls.find((call) => call.method === "POST" && call.path === "hausman_hub/v1/admin/climate-mode");
        if (!post) throw new Error("mode POST missing");
        const expected = { mode: "managed", expected_mode: "disabled", confirm: true };
        if (JSON.stringify(post.payload) !== JSON.stringify(expected)) {
          throw new Error("mode payload mismatch: " + JSON.stringify(post.payload));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_rollout_explains_why_pilot_is_still_blocked(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/climate-mode"] = {
            "mode": "disabled",
            "contour_configured": True,
            "rollout": {
                "phase": "shadow",
                "enable_allowed": False,
                "shadow_sample_count": 7,
                "shadow_ready_room_count": 0,
                "canary_room_id": "living",
                "reasons": ["shadow_evidence_not_ready"],
            },
        }
        script = panel_script(
            payloads,
            {},
            """
        const text = textOf(panel.shadowRoot);
        for (const expected of [
          "Идёт безопасная проверка без команд", "Без команд", "7",
          "Для выбранной комнаты пока недостаточно подтверждённых наблюдений",
        ]) {
          if (!text.includes(expected)) throw new Error("blocked rollout explanation missing: " + expected);
        }
        const enable = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Запустить пилотную комнату");
        if (!enable || !enable.disabled) throw new Error("unsafe pilot start remained enabled");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_profiles_save_posts_exact_contract(self) -> None:
        script = panel_script(
            GET_PATHS,
            {"hausman_hub/v1/admin/climate-profiles": {"status": "saved"}},
            """
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const save = buttons.find((node) => node.textContent === "Сохранить профили");
        if (!save || save.disabled) throw new Error("profiles save missing");
        save.fire("click");
        await tick();
        const post = calls.find((call) => call.method === "POST" && call.path === "hausman_hub/v1/admin/climate-profiles");
        if (!post) throw new Error("profiles POST missing");
        const expected = {
          contract: { name: "hausman-hub-climate-profile-update-request", version: 1 },
          setup_revision: 123,
          rooms: [
            {
              room_id: "living",
              profiles: {
                day: { target_temperature: 23, target_humidity: 45, strategy: "normal" },
                night: { target_temperature: 20, target_humidity: 40, strategy: "soft" },
              },
            },
          ],
        };
        if (JSON.stringify(post.payload) !== JSON.stringify(expected)) {
          throw new Error("profiles payload mismatch: " + JSON.stringify(post.payload));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_schedule_arm_posts_exact_contract(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/climate-mode"] = {
            "mode": "managed",
            "contour_configured": True,
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/admin/climate-schedule": {"status": "saved"}},
            """
        const boxes = findAll(panel.shadowRoot, (node) => node.type === "checkbox");
        const enabled = boxes.find((node) => node.value === "");
        if (!enabled) throw new Error("schedule checkbox missing");
        if (enabled.disabled) throw new Error("schedule checkbox must be enabled in managed mode");
        enabled.checked = true;
        enabled.fire("change");
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const save = buttons.find((node) => node.textContent === "Сохранить расписание");
        save.fire("click");
        await tick();
        const post = calls.find((call) => call.method === "POST" && call.path === "hausman_hub/v1/admin/climate-schedule");
        if (!post) throw new Error("schedule POST missing");
        const expected = {
          contract: { name: "hausman-hub-climate-schedule-update-request", version: 1 },
          setup_revision: 123,
          schedule: { enabled: true, day_start: "07:00", night_start: "23:00" },
          confirm_automatic_application: true,
        };
        if (JSON.stringify(post.payload) !== JSON.stringify(expected)) {
          throw new Error("schedule payload mismatch: " + JSON.stringify(post.payload));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_home_save_posts_exact_priority_aware_fields(self) -> None:
        script = panel_script(
            GET_PATHS,
            {"hausman_hub/v1/admin/home-environment": {"home": {}}},
            """
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const save = buttons.find((node) => node.textContent === "Сохранить сигналы дома");
        if (!save) throw new Error("home save missing");
        save.fire("click");
        await tick();
        const post = calls.find((call) => call.method === "POST" && call.path === "hausman_hub/v1/admin/home-environment");
        if (!post) throw new Error("home POST missing");
        const expected = {
          outdoor_temperature_entity_id: null,
          outdoor_temperature_entity_ids: [],
          presence_entity_id: null,
          central_heating_entity_id: null,
          central_heating_temperature_on: 35,
          central_heating_temperature_off: 30,
          heating_lockout_high: 18,
          heating_lockout_low: 16,
          air_conditioner_minimum_outdoor_temperature: -5,
        };
        if (JSON.stringify(post.payload) !== JSON.stringify(expected)) {
          throw new Error("home payload mismatch: " + JSON.stringify(post.payload));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_window_save_posts_only_changed_rooms(self) -> None:
        script = panel_script(
            GET_PATHS,
            {"hausman_hub/v1/admin/climate-room-signals": {"rooms": []}},
            """
        const living = findAll(panel.shadowRoot, (node) =>
          node.type === "radio" && node.value === "binary_sensor.living_window")[0];
        if (!living) throw new Error("living window choice missing");
        living.checked = true;
        living.fire("change");
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const save = buttons.find((node) => node.textContent === "Сохранить сигналы комнат");
        save.fire("click");
        await tick();
        const posts = calls.filter((call) => call.method === "POST" && call.path === "hausman_hub/v1/admin/climate-room-signals");
        if (posts.length !== 1) throw new Error("expected exactly one window POST, got " + posts.length);
        const expected = {
          rooms: [{
            room_id: "living",
            window_entity_id: "binary_sensor.living_window",
            presence_entity_ids: [],
          }],
        };
        if (JSON.stringify(posts[0].payload) !== JSON.stringify(expected)) {
          throw new Error("window payload mismatch: " + JSON.stringify(posts[0].payload));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_room_signal_save_posts_multiple_presence_sensors(self) -> None:
        script = panel_script(
            GET_PATHS,
            {"hausman_hub/v1/admin/climate-room-signals": {"rooms": []}},
            """
        const boxes = findAll(panel.shadowRoot, (node) => node.type === "checkbox");
        const motion = boxes.find((node) => node.value === "binary_sensor.living_motion");
        const occupancy = boxes.find((node) => node.value === "binary_sensor.living_occupancy");
        if (!motion || !occupancy) throw new Error("room presence choices missing");
        motion.checked = true;
        motion.fire("change");
        occupancy.checked = true;
        occupancy.fire("change");
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const save = buttons.find((node) => node.textContent === "Сохранить сигналы комнат");
        save.fire("click");
        await tick();
        const posts = calls.filter((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/climate-room-signals");
        if (posts.length !== 1) throw new Error("expected one room signal POST");
        const expected = {
          rooms: [{
            room_id: "living",
            window_entity_id: null,
            presence_entity_ids: [
              "binary_sensor.living_motion",
              "binary_sensor.living_occupancy",
            ].sort(),
          }],
        };
        if (JSON.stringify(posts[0].payload) !== JSON.stringify(expected)) {
          throw new Error("room presence payload mismatch: " + JSON.stringify(posts[0].payload));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_room_presence_move_posts_both_rooms_in_one_atomic_batch(self) -> None:
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/climate-room-signals"] = {
            "rooms": [
                {
                    "id": "living",
                    "name": "Гостиная",
                    "window_entity_id": None,
                    "presence_entity_ids": [],
                },
                {
                    "id": "kids",
                    "name": "Детская",
                    "window_entity_id": None,
                    "presence_entity_ids": ["binary_sensor.shared_motion"],
                },
            ],
            "candidates": [],
            "presence_candidates": [
                {
                    "entity_id": "binary_sensor.shared_motion",
                    "name": "Движение",
                    "available": True,
                    "device_class": "motion",
                    "domain": "binary_sensor",
                    "room_id": "living",
                    "room_name": "Гостиная",
                }
            ],
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/admin/climate-room-signals": {"rooms": []}},
            """
        const boxes = findAll(panel.shadowRoot, (node) =>
          node.type === "checkbox" && node.value === "binary_sensor.shared_motion");
        if (boxes.length !== 2 || !boxes[1].checked) {
          throw new Error("saved room presence assignment missing");
        }
        boxes[0].checked = true;
        boxes[0].fire("change");
        if (boxes[1].checked) throw new Error("sensor remained assigned to two rooms");
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const save = buttons.find((node) => node.textContent === "Сохранить сигналы комнат");
        save.fire("click");
        await tick();
        const posts = calls.filter((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/climate-room-signals");
        if (posts.length !== 1) throw new Error("presence move was not atomic");
        const expected = {
          rooms: [
            {
              room_id: "living",
              window_entity_id: null,
              presence_entity_ids: ["binary_sensor.shared_motion"],
            },
            {
              room_id: "kids",
              window_entity_id: null,
              presence_entity_ids: [],
            },
          ],
        };
        if (JSON.stringify(posts[0].payload) !== JSON.stringify(expected)) {
          throw new Error("presence move payload mismatch: " + JSON.stringify(posts[0].payload));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_dirty_profiles_inputs_survive_background_refresh(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const numbers = findAll(panel.shadowRoot, (node) => node.type === "number");
        const dayTemperature = numbers.find((node) => String(node.value) === "23");
        if (!dayTemperature) throw new Error("day temperature input missing");
        dayTemperature.value = "24.5";
        dayTemperature.fire("input");
        await panel._load();
        const after = findAll(panel.shadowRoot, (node) => node.type === "number")
          .find((node) => String(node.value) === "24.5");
        if (!after) throw new Error("edited value was clobbered by background refresh");
        if (panel._dirty.profiles !== true) throw new Error("profiles dirty flag not set");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_stale_schedule_save_shows_conflict_and_reloads(self) -> None:
        script = panel_script(
            GET_PATHS,
            {"hausman_hub/v1/admin/climate-schedule": {"__fail": 409}},
            """
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const save = buttons.find((node) => node.textContent === "Сохранить расписание");
        save.fire("click");
        await tick();
        const text = textOf(panel.shadowRoot);
        if (!text.includes("изменились в другом окне")) throw new Error("conflict notice missing");
        if (panel._dirty.schedule !== false) throw new Error("schedule dirty flag not cleared");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_dirty_form_survives_panel_get_failure_and_recovery(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const numbers = findAll(panel.shadowRoot, (node) => node.type === "number");
        const dayTemperature = numbers.find((node) => String(node.value) === "23");
        dayTemperature.value = "24.5";
        dayTemperature.fire("input");
        getTable["hausman_hub/v1/admin/panel"] = { __fail: true };
        await panel._load();
        let text = textOf(panel.shadowRoot);
        if (!text.includes("недоступны")) throw new Error("error banner missing after GET failure");
        if (panel._shell.banner.style.display === "none") throw new Error("banner must be visible after GET failure");
        const preserved = findAll(panel.shadowRoot, (node) => node.type === "number")
          .find((node) => String(node.value) === "24.5");
        if (!preserved) throw new Error("dirty form destroyed by GET failure");
        getTable["hausman_hub/v1/admin/panel"] = {
          contract: { name: "hausman-hub-admin-panel", version: 2 },
          snapshot: null,
          readiness: { status: "disabled", bridge_mode: "disabled", reasons: [] },
        };
        await panel._load();
        const restored = findAll(panel.shadowRoot, (node) => node.type === "number")
          .find((node) => String(node.value) === "24.5");
        if (!restored) throw new Error("dirty form lost after recovery");
        if (panel._shell.banner.style.display !== "none") throw new Error("error banner not hidden after recovery");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_window_save_ignores_a_second_click_while_busy(self) -> None:
        script = panel_script(
            GET_PATHS,
            {"hausman_hub/v1/admin/climate-room-signals": {"rooms": []}},
            """
        const living = findAll(panel.shadowRoot, (node) =>
          node.type === "radio" && node.value === "binary_sensor.living_window")[0];
        living.checked = true;
        living.fire("change");
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const save = buttons.find((node) => node.textContent === "Сохранить сигналы комнат");
        save.fire("click");
        save.fire("click");
        await tick();
        const posts = calls.filter((call) => call.method === "POST" && call.path === "hausman_hub/v1/admin/climate-room-signals");
        if (posts.length !== 1) throw new Error("double click produced " + posts.length + " POSTs");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_blank_numeric_fields_are_rejected_before_post(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const numbers = findAll(panel.shadowRoot, (node) => node.type === "number");
        const humidity = numbers.find((node) => String(node.value) === "45");
        if (!humidity) throw new Error("humidity input missing");
        humidity.value = "";
        humidity.fire("input");
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const saveProfiles = buttons.find((node) => node.textContent === "Сохранить профили");
        saveProfiles.fire("click");
        await tick();
        let text = textOf(panel.shadowRoot);
        if (!text.includes("Проверьте температуру")) throw new Error("profiles validation notice missing");
        if (calls.some((call) => call.method === "POST")) throw new Error("blank humidity reached POST");
        const thresholds = findAll(panel.shadowRoot, (node) => node.type === "number");
        const high = thresholds.find((node) => String(node.value) === "18");
        if (!high) throw new Error("high threshold input missing");
        high.value = "";
        high.fire("input");
        const saveHome = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Сохранить сигналы дома");
        saveHome.fire("click");
        await tick();
        text = textOf(panel.shadowRoot);
        if (!text.includes("Проверьте пороги")) throw new Error("thresholds validation notice missing");
        if (calls.some((call) => call.method === "POST" && call.path === "hausman_hub/v1/admin/home-environment")) {
          throw new Error("blank threshold reached POST");
        }
        high.value = "18";
        high.fire("input");
        text = textOf(panel.shadowRoot);
        if (text.includes("Проверьте пороги")) throw new Error("corrected thresholds kept a stale error");
        if (!text.includes("Между ними режим не меняется")) {
          throw new Error("threshold behavior explanation missing");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
