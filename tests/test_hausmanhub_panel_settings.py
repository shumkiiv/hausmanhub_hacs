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
ROOM_SETUP_JS = PANEL_JS.with_name("hausman-hub-room-setup.js")
DEVICE_INVENTORY_JS = PANEL_JS.with_name("hausman-hub-device-inventory.js")
DEVICE_BINDINGS_JS = PANEL_JS.with_name("hausman-hub-device-bindings.js")
AREA_BINDING_JS = PANEL_JS.with_name("hausman-hub-area-binding.js")
NAVIGATION_JS = PANEL_JS.with_name("hausman-hub-navigation.js")
ENERGY_JS = PANEL_JS.with_name("hausman-hub-energy.js")
WEATHER_SOURCES_JS = PANEL_JS.with_name("hausman-hub-weather-sources.js")
SETTINGS_CSS = PANEL_JS.with_name("hausman-hub-settings.css")
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
          this._listeners = {{}};
        }}
        appendChild(child) {{
          this.children.push(child);
          return child;
        }}
        addEventListener(type, handler) {{
          (this._listeners[type] = this._listeners[type] || []).push(handler);
        }}
        fire(type, event = {{}}) {{
          (this._listeners[type] || []).forEach((handler) => handler(event));
        }}
        focus() {{
          this.focused = true;
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
        fs.readFileSync({str(ROOM_SETUP_JS)!r}, "utf8").replace("export function renderFirstRunRoom", "function renderFirstRunRoom"),
        {{ filename: {str(ROOM_SETUP_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(DEVICE_INVENTORY_JS)!r}, "utf8").replace("export function renderDeviceInventory", "function renderDeviceInventory"),
        {{ filename: {str(DEVICE_INVENTORY_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(DEVICE_BINDINGS_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(DEVICE_BINDINGS_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(AREA_BINDING_JS)!r}, "utf8").replace("export function renderFirstRunAreaBinding", "function renderFirstRunAreaBinding"),
        {{ filename: {str(AREA_BINDING_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(NAVIGATION_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(NAVIGATION_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ENERGY_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(ENERGY_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(WEATHER_SOURCES_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(WEATHER_SOURCES_JS)!r} }}
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
            return Promise.reject(new Error("unexpected WS " + message.type));
          }},
        }},
        callApi: (method, path, payload) => {{
          calls.push({{ method, path, payload }});
          if (method === "GET") {{
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
        ("node", "--input-type=commonjs", "--eval", script),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


class PanelSettingsSectionsTest(unittest.TestCase):
    """The settings sections render and post the strict admin contracts."""

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
        if (!text.includes("Первичная настройка климата")) {
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
        ordered.forEach((section) => {
          panel._shell.tabs[section].fire("click");
          if (panel._activeSection !== section || panel._shell.sectionNodes[section].hidden) {
            throw new Error("top-level section is not functional: " + section);
          }
        });
        panel._shell.tabs.climate.fire("click");
        panel._shell.climateTabs.profiles.fire("click");
        const climateRoute = historyCalls[historyCalls.length - 1].value;
        if (!climateRoute.includes("hh_section=climate") || !climateRoute.includes("hh_view=profiles")) {
          throw new Error("climate deep link mismatch: " + climateRoute);
        }
        panel._shell.tabs.settings.fire("click");
        const systemButton = findAll(panel._shell.settings, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Система")[0];
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
                    "entityId": "switch.kettle",
                    "name": "Чайник",
                    "roomName": "Кухня",
                    "domain": "switch",
                    "category": "switch",
                    "state": "on",
                    "stateLabel": "включено",
                    "tone": "good",
                    "details": [
                        {"label": "Мощность", "value": "1850 W", "entityId": "sensor.kettle_power", "domain": "sensor", "state": "1850"},
                        {"label": "Ток", "value": "8.04 A", "entityId": "sensor.kettle_current", "domain": "sensor", "state": "8.04"},
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
                    {"id": "device_0123456789abcdef", "deviceId": "device_0123456789abcdef", "name": "Чайник", "roomName": "Кухня", "available": True, "currentPowerW": 1850, "currentA": 8.04, "voltageV": 230.1, "totalKwh": 12.4},
                    {"id": "device_fedcba9876543210", "deviceId": "device_fedcba9876543210", "name": "Торшер", "roomName": "Гостиная", "available": True, "currentPowerW": 0, "currentA": 0, "voltageV": 230.0, "totalKwh": 2.1}
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
            },
            {"hausman_hub/v1/energy-settings": {"revision": 1}},
            """
        panel._shell.tabs.energy.fire("click");
        await tick();
        let text = textOf(panel._shell.homeSections.energy);
        if (!text.includes("Энергия") || !text.includes("850") || !text.includes("230,1") || !text.includes("Торшер") || !text.includes("2 доступно · 1 на главной") || !text.includes("Единый источник истины")) {
          throw new Error("energy summary is incomplete: " + text);
        }
        const configure = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Настроить карточку")[0];
        configure.fire("click");
        const both = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Оба")[0];
        both.fire("click");
        const save = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Сохранить настройку")[0];
        save.fire("click");
        await tick(10);
        const post = calls.find((call) => call.method === "PUT" && call.path === "hausman_hub/v1/energy-settings");
        if (!post || post.payload.expectedRevision !== 0 || post.payload.settings.displayUnits !== "both" || post.payload.settings.useAllDevices !== true) {
          throw new Error("energy settings post mismatch: " + JSON.stringify(post));
        }
        panel._shell.tabs.energy.fire("click");
        const device = findAll(panel._shell.homeSections.energy, (node) =>
          node.tagName === "BUTTON" && String(node.className).includes("energy-device-card"))[0];
        device.fire("click");
        text = textOf(panel._shell.homeSections.energy);
        if (!text.includes("Устройство и управление") || !text.includes("Чайник")) {
          throw new Error("energy device detail did not open: " + text);
        }
            """,
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
            "rooms": [
                {"id": "living", "name": "Гостиная", "temp": 24.5, "humidity": 46},
                {"id": "bedroom", "name": "Спальня", "temp": 23.5, "humidity": 50},
            ],
            "devices": [],
            "alarms": [],
        }
        script = panel_script(
            payloads,
            {},
            """
        const overview = panel._shell.sectionNodes.overview;
        const text = textOf(overview);
        for (const label of [
          "Дом в комфортном режиме", "Сводка", "Комнаты", "Гостиная", "Спальня",
          "24.0 °C", "48 %", "1 из 2", "Дневной профиль", "Ночной профиль",
        ]) {
          if (!text.includes(label)) throw new Error("overview text missing: " + label);
        }
        if (text.includes("day профиль") || text.includes("night профиль")) {
          throw new Error("raw profile code exposed");
        }
        const byClass = (name) => findAll(overview, (node) =>
          String(node.className).split(" ").includes(name));
        if (byClass("overview-hero-metric").length !== 4) {
          throw new Error("overview hero must contain four metrics");
        }
        if (byClass("summary-item").length !== 3 || byClass("overview-room-card").length !== 2) {
          throw new Error("overview summary or room hierarchy mismatch");
        }
        if (byClass("summary-icon").some((node) => node.children.length !== 1)) {
          throw new Error("overview summary icon missing");
        }
        const summaryLinks = byClass("summary-link");
        if (summaryLinks.some((node) => node.tagName !== "BUTTON")) {
          throw new Error("overview summary is not keyboard-clickable");
        }
        summaryLinks[0].fire("click");
        if (panel._activeSection !== "climate") {
          throw new Error("temperature summary did not open climate");
        }
        panel._shell.tabs.overview.fire("click");
        const overviewAfterReturn = panel._shell.sectionNodes.overview;
        const deviceLink = findAll(overviewAfterReturn, (node) =>
          String(node.className).split(" ").includes("summary-link")
          && String(node["aria-label"]).includes("Активные устройства"))[0];
        deviceLink.fire("click");
        if (panel._activeSection !== "devices") {
          throw new Error("device summary did not open devices");
        }
        panel._shell.tabs.overview.fire("click");
        const roomCard = findAll(panel._shell.sectionNodes.overview, (node) =>
          String(node.className).split(" ").includes("overview-room-card")
          && String(node["aria-label"]).includes("Гостиная"))[0];
        roomCard.fire("click");
        if (panel._activeSection !== "rooms") {
          throw new Error("overview room did not open rooms");
        }
        const expandedRoom = findAll(panel._shell.homeSections.rooms, (node) =>
          String(node.className).split(" ").includes("room-inventory-card")
          && node.open === true)[0];
        if (!expandedRoom || !textOf(expandedRoom).includes("Гостиная")) {
          throw new Error("selected room was not expanded after navigation");
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
                    "roomId": "living",
                    "roomName": "Гостиная",
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
                }
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
        const valueInput = findAll(lighting, (node) => node.tagName === "INPUT")[0];
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
        const refreshedInput = findAll(refreshedLighting, (node) => node.tagName === "INPUT")[0];
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
        if (!roomText.includes("Гостиная") || !roomText.includes("1 устройство")) {
          throw new Error("room inventory does not match tablet navigation");
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
        const expected = ["Контур", "Профили", "Расписание", "Сигналы дома", "Сигналы комнат", "AI-помощник"];
        if (JSON.stringify(subtabs.map((node) => node.textContent)) !== JSON.stringify(expected)) {
          throw new Error("climate subtab labels mismatch");
        }
        const visible = () => Object.entries(panel._shell.climateViews)
          .filter(([, node]) => !node.hidden).map(([name]) => name);
        if (JSON.stringify(visible()) !== JSON.stringify(["contour"])) {
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
          "Поставщик AI", "Ключ сохранён", "Последний совет", "Статистика вызовов",
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
                "hausman_hub/v1/admin/scenarios/run": {"status": "confirmed"},
                "hausman_hub/v1/admin/scenarios/test": {"ok": True},
                "hausman_hub/v1/admin/scenarios/delete": {"status": "confirmed"},
            },
            """
        panel._shell.tabs.scenarios.fire("click");
        await tick();
        const screen = panel._shell.scenarios;
        const text = textOf(screen);
        for (const label of ["Доброе утро", "Уходим из дома", "Ночной режим", "требуется подтверждение", "выключен"]) {
          if (!text.includes(label)) throw new Error("scenario text missing: " + label);
        }
        if (text.includes("mdi:")) throw new Error("raw MDI icon name exposed");
        const rows = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-row"));
        const icons = findAll(screen, (node) => String(node.className).split(" ").includes("scenario-icon"));
        if (rows.length !== 3 || icons.length !== 3 || icons.some((node) => node.children.length !== 1)) {
          throw new Error("scenario Figma row hierarchy mismatch");
        }
        const rowButtons = (row) => findAll(row, (node) => node.tagName === "BUTTON");
        if (rowButtons(rows[0]).length !== 3 || rowButtons(rows[2]).length !== 2) {
          throw new Error("enabled/disabled scenario actions mismatch");
        }
        let confirmation = "";
        window.confirm = (message) => { confirmation = message; return true; };
        rowButtons(rows[1]).find((node) => node.textContent === "Запустить").fire("click");
        await tick();
        if (!confirmation.includes("Уходим из дома")) throw new Error("camelCase confirmation flag ignored");
        const run = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/scenarios/run");
        if (!run || run.payload.scenario_id !== "scenario.leaving_home") {
          throw new Error("scenario run payload mismatch");
        }
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

    def test_settings_match_figma_and_controls_have_real_behavior(self) -> None:
        css = (
            PANEL_CSS.read_text(encoding="utf-8")
            + SETTINGS_CSS.read_text(encoding="utf-8")
        )
        for tablet_rule in (
            ".settings-subnav",
            ".settings-overview-grid",
            ".settings-menu-card",
            ".settings-room-grid",
            ".settings-page-actions { position:sticky",
            ".room-setup-nav",
            "backdrop-filter:blur(18px)",
        ):
            self.assertIn(tablet_rule, css)
        payloads = dict(GET_PATHS)
        payloads["hausman_hub/v1/admin/connection-settings"] = {
            "connection_mode": "center",
            "smart_home_center_url": "http://hausmanhub.local:8099",
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
          "Настройки HausmanHub", "Обзор", "Комнаты", "Подключение", "Интерфейс", "Система",
          "Комнаты и устройства", "Home Assistant остаётся единым источником устройств", "Версия",
        ]) {
          if (!text.includes(label)) throw new Error("settings text missing: " + label);
        }
        if (text.includes("Центр умного дома")) throw new Error("obsolete Smart Home Center label exposed");
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Подключение")[0].fire("click");
        screen = panel._shell.settings;
        const urls = findAll(screen, (node) => node.type === "url");
        if (urls.length !== 2 || !textOf(screen).includes("Источник данных и команд")) {
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
        findAll(screen, (node) => node.tagName === "BUTTON"
          && textOf(node).includes("Только Home Assistant"))[0].fire("click");
        screen = panel._shell.settings;
        const centerField = findAll(screen, (node) =>
          String(node.className).split(" ").includes("settings-field")
          && textOf(node).includes("Адрес HausmanHub"))[0];
        if (centerField) throw new Error("HausmanHub URL remains visible in HA-only mode");
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
          smart_home_center_url: "http://hausmanhub.local:8099",
          home_assistant_url: "https://ha.example.test",
        };
        if (!post || JSON.stringify(post.payload) !== JSON.stringify(expected)) {
          throw new Error("settings payload mismatch: " + JSON.stringify(post && post.payload));
        }
        screen = panel._shell.settings;
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Интерфейс")[0].fire("click");
        screen = panel._shell.settings;
        const toggles = findAll(screen, (node) => String(node.className).split(" ").includes("settings-toggle"));
        if (toggles.length !== 3 || !textOf(screen).includes("Тема панели")) {
          throw new Error("interface settings page mismatch");
        }
        const motionToggle = findAll(screen, (node) =>
          String(node.className).split(" ").includes("settings-toggle"))[1];
        motionToggle.checked = true;
        motionToggle.fire("change");
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
          && node.textContent === "Система")[0].fire("click");
        screen = panel._shell.settings;
        const systemText = textOf(screen);
        for (const label of ["Состояние системы", "Устройства климата", "Копировать техническую сводку"]) {
          if (!systemText.includes(label)) throw new Error("system status missing: " + label);
        }
        if (systemText.includes("native")) throw new Error("raw bridge mode exposed");
        findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Копировать техническую сводку")[0].fire("click");
        await tick();
        if (clipboardWrites.length !== 1
          || !clipboardWrites[0].includes("HausmanHub — техническая сводка")
          || clipboardWrites[0].includes("ready")
          || clipboardWrites[0].includes("homeassistant.local")) {
          throw new Error("redacted technical summary copy mismatch: " + JSON.stringify(clipboardWrites));
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
          "Привязка к сущностям Home Assistant", "HausmanHub не выбирает дубли автоматически",
          "Кондиционер гостиная", "Не привязано", "Проверить", "Сохранить привязки",
        ]) {
          if (!text.includes(label)) throw new Error("binding wizard text missing: " + label);
        }
        let select = findAll(screen, (node) => node.tagName === "SELECT")[0];
        if (!select || select.value !== "" || select.children.length !== 2) {
          throw new Error("wizard auto-selected or exposed another-room entity");
        }
        const otherToggle = findAll(screen, (node) => node.type === "checkbox")[0];
        otherToggle.checked = true;
        otherToggle.fire("change");
        screen = panel._shell.settings;
        select = findAll(screen, (node) => node.tagName === "SELECT")[0];
        if (select.children.length !== 3 || select.children[2].disabled !== true) {
          throw new Error("other-room diagnostic option is not visibly fail-closed");
        }
        select.value = "climate.living_ac";
        select.fire("change");
        await new Promise((resolve) => setTimeout(resolve, 400));
        screen = panel._shell.settings;
        const check = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить")[0];
        const saveBefore = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить привязки")[0];
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
        const save = findAll(screen, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить привязки")[0];
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
          "Что Home Assistant считает устройствами", "основных устройств",
          "Возможный дубль", "Не привязано",
        ]) {
          if (!text.includes(label)) throw new Error("inventory text missing: " + label);
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

    def test_panel_shell_has_status_accessibility_and_responsive_rules(self) -> None:
        css = PANEL_CSS.read_text(encoding="utf-8")
        for rule in (
            "max-width:1440px",
            "overflow-x:auto",
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
        if (!stylesheet || !String(stylesheet.href).endsWith("hausman-hub-panel.css")) {
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
        if (!text.includes("Нужен сигнал «работает», а не температура батареи.")) {
          throw new Error("central heating helper does not explain the binary signal");
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
            available: true, domain: "weather", room_id: "" },
          { entity_id: "sensor.outdoor_temperature", name: "Внешний датчик температуры",
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
        for (const label of ["Основной источник", "Резерв 1", "Добавить резервный источник"]) {
          if (!text.includes(label)) throw new Error("priority UI missing: " + label);
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
        ];
        const filtered = panel._signalCandidatesForPicker(candidates, null, "central_heating")
          .map((candidate) => candidate.entity_id).sort();
        const expected = ["binary_sensor.boiler_heat", "input_boolean.central_heating"].sort();
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
            "rollout": {"enable_allowed": True},
        }
        script = panel_script(
            payloads,
            {"hausman_hub/v1/admin/climate-mode": {"mode": "managed"}},
            """
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const enable = buttons.find((node) => node.textContent === "Запустить пилотную комнату");
        if (!enable || enable.disabled) throw new Error("enabled switch missing");
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
          heating_lockout_high: 18,
          heating_lockout_low: 16,
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
