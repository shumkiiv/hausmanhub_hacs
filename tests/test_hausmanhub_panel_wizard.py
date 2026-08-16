from __future__ import annotations

import copy
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
HOME_SECTIONS_JS = PANEL_JS.with_name("hausman-hub-home-sections.js")
ROOM_SETUP_JS = PANEL_JS.with_name("hausman-hub-room-setup.js")
ROOM_DEVICE_GROUPS_JS = PANEL_JS.with_name("hausman-hub-room-device-groups.js")
CONTROL_CHANNEL_JS = PANEL_JS.with_name("hausman-hub-control-channel.js")
ROOM_CLIMATE_SOURCES_JS = PANEL_JS.with_name("hausman-hub-room-climate-sources.js")
DEVICE_INVENTORY_JS = PANEL_JS.with_name("hausman-hub-device-inventory.js")
DEVICE_BINDINGS_JS = PANEL_JS.with_name("hausman-hub-device-bindings.js")
AREA_BINDING_JS = PANEL_JS.with_name("hausman-hub-area-binding.js")
FIRST_RUN_DRAFT_JS = PANEL_JS.with_name("hausman-hub-first-run-draft.js")
NAVIGATION_JS = PANEL_JS.with_name("hausman-hub-navigation.js")
MODAL_JS = PANEL_JS.with_name("hausman-hub-modal.js")
ENERGY_JS = PANEL_JS.with_name("hausman-hub-energy.js")
ENERGY_CHART_JS = PANEL_JS.with_name("hausman-hub-energy-chart.js")
ENERGY_METER_JS = PANEL_JS.with_name("hausman-hub-energy-meter.js")
DEVICE_DISCOVERY_JS = PANEL_JS.with_name("hausman-hub-device-discovery.js")
WEATHER_SOURCES_JS = PANEL_JS.with_name("hausman-hub-weather-sources.js")
SCENARIOS_JS = PANEL_JS.with_name("hausman-hub-scenarios.js")
SCENARIO_ICONS_JS = PANEL_JS.with_name("hausman-hub-scenario-icons.js")
CLIMATE_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-climate-overview.js")
LIGHTING_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-lighting.js")
ROOMS_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-rooms.js")
MEDIA_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-media-overview.js")
SECURITY_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-security-overview.js")
DEVICES_OVERVIEW_JS = PANEL_JS.with_name("hausman-hub-devices-overview.js")
TECHNICAL_LOG_JS = PANEL_JS.with_name("hausman-hub-technical-log.js")
FEEDBACK_JS = PANEL_JS.with_name("hausman-hub-feedback.js")
ERROR_TAXONOMY_JS = PANEL_JS.with_name("hausman-hub-error-taxonomy.js")
UI_STATE_JS = PANEL_JS.with_name("hausman-hub-ui-state.js")
DEVICE_FEATURES_JS = PANEL_JS.with_name("hausman-hub-device-features.js")
KIOSK_JS = PANEL_JS.with_name("hausman-hub-kiosk.js")
ROLLOUT_JS = PANEL_JS.with_name("hausman-hub-rollout.js")

PANEL_PAYLOAD = {
    "contract": {"name": "hausman-hub-admin-panel", "version": 2},
    "snapshot": None,
    "readiness": {
        "status": "disabled",
        "bridge_mode": "disabled",
        "reasons": ["bridge_disabled"],
    },
}
MODE_PAYLOAD = {"mode": "disabled", "contour_configured": False}
HOME_PAYLOAD = {
    "setup_revision": 5,
    "home": {
        "outdoor_temperature_entity_id": None,
        "presence_entity_id": None,
        "central_heating_entity_id": None,
        "heating_lockout_high": 18.0,
        "heating_lockout_low": 16.0,
    },
    "candidates": {
        "outdoor_temperature": [],
        "presence": [],
        "central_heating": [],
    },
}
WINDOWS_PAYLOAD = {"rooms": [], "candidates": []}
DISPLAY_NAMES = {
    "modes": {"observe": "Наблюдение", "automatic": "Автоматический"},
    "strategies": {"soft": "Плавно", "normal": "Обычно", "aggressive": "Быстро"},
    "profiles": {"day": "День", "night": "Ночь"},
    "device_types": {
        "air_conditioner": "Кондиционер",
        "radiator_thermostat": "Радиаторный термостат",
        "humidifier": "Увлажнитель",
        "floor_heating": "Тёплый пол",
        "temperature_sensor": "Датчик температуры",
        "humidity_sensor": "Датчик влажности",
    },
}
NOT_CONFIGURED_SETUP = {
    "contract": {"name": "hausman-hub-climate-current-setup", "version": 1},
    "generated_at": 1784280000,
    "snapshot_revision": 77,
    "setup_revision": 5,
    "status": "not_configured",
    "editing_allowed": False,
    "display_names": DISPLAY_NAMES,
    "name": None,
    "mode": None,
    "schedule": None,
    "rooms": [],
    "issues": [],
    "summary": {"room_count": 0, "device_count": 0},
}
CONFIGURED_SETUP = {
    "contract": {"name": "hausman-hub-climate-current-setup", "version": 1},
    "generated_at": 1784280000,
    "snapshot_revision": 77,
    "setup_revision": 123,
    "status": "ready",
    "editing_allowed": True,
    "display_names": DISPLAY_NAMES,
    "name": "Домашний климат",
    "mode": "automatic",
    "schedule": {"enabled": False, "day_start": "07:00", "night_start": "23:00"},
    "rooms": [
        {
            "id": "living",
            "name": "Гостиная",
            "devices": [
                {"candidate_id": "candidate_ac", "name": "Кондиционер", "type": "air_conditioner", "type_name": "Кондиционер"},
                {"candidate_id": "candidate_temp_1", "name": "Температура у окна", "type": "temperature_sensor", "type_name": "Датчик температуры"},
                {"candidate_id": "candidate_temp_2", "name": "Температура у двери", "type": "temperature_sensor", "type_name": "Датчик температуры"},
                {"candidate_id": "candidate_humidity", "name": "Влажность гостиной", "type": "humidity_sensor", "type_name": "Датчик влажности"},
            ],
            "profiles": {
                "day": {"target_temperature": 24.5, "target_humidity": 50, "strategy": "aggressive"},
                "night": {"target_temperature": 21.0, "target_humidity": 45, "strategy": "soft"},
                "active_profile": "day",
            },
            "temporary_temperature": None,
        }
    ],
    "issues": [{"code": "attention", "room_id": "living", "message": "Проверьте датчик"}],
    "summary": {"room_count": 1, "device_count": 4},
}
DRAFT_OPTIONS = {
    "contract": {"name": "hausman-hub-climate-setup-options", "version": 1},
    "generated_at": 1784280000,
    "snapshot_revision": 77,
    "data_status": "current",
    "draft_creation_allowed": True,
    "display_names": DISPLAY_NAMES,
    "rooms": [
        {"id": "living", "name": "Гостиная", "status": "available", "selectable": True},
        {"id": "kids", "name": "Детская", "status": "available", "selectable": True},
    ],
    "devices": [
        {
            "candidate_id": "candidate_ac", "candidate_key": "candidate_ac", "name": "Кондиционер", "room_id": "living",
            "suggested_types": ["air_conditioner"], "recommended_type": "air_conditioner",
            "status": "available", "suggested_room_id": "living", "suggested_room_name": "Гостиная",
            "reason": "detected_room", "can_add": True,
        },
        {
            "candidate_id": "candidate_temp_1", "candidate_key": "candidate_temp_1", "name": "Температура у окна", "room_id": "living",
            "suggested_types": ["temperature_sensor"], "recommended_type": "temperature_sensor",
            "status": "available", "suggested_room_id": "living", "suggested_room_name": "Гостиная",
            "reason": "detected_room", "can_add": True,
            "device_group_id": "device_0123456789abcdef",
            "device_name": "Климат Kojima Гостинная",
            "manufacturer": "KOJIMA",
            "model": "Temperature and humidity sensor",
            "image_url": "https://www.zigbee2mqtt.io/images/devices/KOJIMA-THS-ZG-LCD.png",
        },
        {
            "candidate_id": "candidate_temp_2", "candidate_key": "candidate_temp_2", "name": "Температура у двери", "room_id": "",
            "suggested_types": ["temperature_sensor"], "recommended_type": "temperature_sensor",
            "status": "available", "suggested_room_id": "living", "suggested_room_name": "Гостиная",
            "reason": "detected_room", "can_add": True,
        },
        {
            "candidate_id": "candidate_humidity", "candidate_key": "candidate_humidity", "name": "Влажность гостиной", "room_id": "living",
            "suggested_types": ["humidity_sensor"], "recommended_type": "humidity_sensor",
            "status": "available", "suggested_room_id": "living", "suggested_room_name": "Гостиная",
            "reason": "detected_room", "can_add": True,
            "device_group_id": "device_0123456789abcdef",
            "device_name": "Климат Kojima Гостинная",
            "manufacturer": "KOJIMA",
            "model": "Temperature and humidity sensor",
            "image_url": "https://www.zigbee2mqtt.io/images/devices/KOJIMA-THS-ZG-LCD.png",
        },
        {
            "candidate_id": "candidate_trv", "candidate_key": "candidate_trv", "name": "Батарея детской", "room_id": "kids",
            "suggested_types": ["radiator_thermostat"], "recommended_type": "radiator_thermostat",
            "status": "available", "suggested_room_id": "kids", "suggested_room_name": "Детская",
            "reason": "detected_room", "can_add": True,
        },
        {
            "candidate_id": "candidate_kids_temp", "candidate_key": "candidate_kids_temp", "name": "Температура детской", "room_id": "kids",
            "suggested_types": ["temperature_sensor"], "recommended_type": "temperature_sensor",
            "status": "available", "suggested_room_id": "kids", "suggested_room_name": "Детская",
            "reason": "detected_room", "can_add": True,
        },
        {
            "candidate_id": "candidate_kids_humidity", "candidate_key": "candidate_kids_humidity", "name": "Влажность детской", "room_id": "kids",
            "suggested_types": ["humidity_sensor"], "recommended_type": "humidity_sensor",
            "status": "available", "suggested_room_id": "kids", "suggested_room_name": "Детская",
            "reason": "detected_room", "can_add": True,
        },
    ],
    "control_channels": ["universal_ir", "yandex_remote", "direct_wifi"],
}


def get_payloads(
    *,
    setup: dict | None = None,
    options: dict | None = None,
    panel: dict | None = None,
    home: dict | None = None,
    windows: dict | None = None,
    bindings: dict | None = None,
) -> dict:
    return {
        "hausman_hub/v1/admin/panel": panel or PANEL_PAYLOAD,
        "hausman_hub/v1/admin/climate-mode": MODE_PAYLOAD,
        "hausman_hub/v1/admin/home-environment": home or HOME_PAYLOAD,
        "hausman_hub/v1/admin/climate-room-signals": windows or WINDOWS_PAYLOAD,
        "hausman_hub/v1/admin/climate-drafts/current": setup or NOT_CONFIGURED_SETUP,
        "hausman_hub/v1/admin/ir-codes/bindings": bindings or {"bindings": []},
        "hausman_hub/v1/admin/climate-drafts": options or DRAFT_OPTIONS,
    }


def universal_ir_setup() -> dict:
    setup = copy.deepcopy(NOT_CONFIGURED_SETUP)
    setup["rooms"] = [
        {
            "id": "living",
            "name": "Гостиная",
            "devices": [
                {
                    "candidate_id": "candidate_ac",
                    "control_channel": "universal_ir",
                    "name": "Пульт Broadlink гостиной",
                    "type": "air_conditioner",
                    "type_name": "Кондиционер",
                }
            ],
            "profiles": {
                "day": {"target_temperature": 25.0},
                "night": {"target_temperature": 24.0},
            },
        }
    ]
    return setup


def universal_ir_bindings() -> dict:
    return {
        "bindings": [
            {
                "candidate_id": "candidate_ac",
                "configured_device_id": "living_air_conditioner",
                "remote_entity_id": "remote.pult_broadlink_gostinnaya",
            }
        ]
    }


def panel_script(get_table: dict, post_table: dict, assertions: str) -> str:
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
      global.window = {{ confirm: () => true }};
      const browserValues = new Map();
      global.localStorage = {{
        getItem: (key) => browserValues.has(key) ? browserValues.get(key) : null,
        removeItem: (key) => browserValues.delete(key),
        setItem: (key, value) => browserValues.set(key, String(value)),
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
        fs.readFileSync({str(ROOM_SETUP_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(ROOM_SETUP_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(CONTROL_CHANNEL_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(CONTROL_CHANNEL_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ROOM_DEVICE_GROUPS_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(ROOM_DEVICE_GROUPS_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ROOM_CLIMATE_SOURCES_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(ROOM_CLIMATE_SOURCES_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(DEVICE_INVENTORY_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "")
          .replace("export function renderDeviceInventory", "function renderDeviceInventory"),
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
        fs.readFileSync({str(MODAL_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(MODAL_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ENERGY_CHART_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(ENERGY_CHART_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ENERGY_METER_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(ENERGY_METER_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(DEVICE_DISCOVERY_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(DEVICE_DISCOVERY_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ENERGY_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(ENERGY_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(WEATHER_SOURCES_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(WEATHER_SOURCES_JS)!r} }}
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
        fs.readFileSync({str(CLIMATE_OVERVIEW_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(CLIMATE_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(LIGHTING_OVERVIEW_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(LIGHTING_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ROOMS_OVERVIEW_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(ROOMS_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(MEDIA_OVERVIEW_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(MEDIA_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(SECURITY_OVERVIEW_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(SECURITY_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(DEVICES_OVERVIEW_JS)!r}, "utf8")
          .replace(/^import .*;\\s*/gm, "").replace(/export /g, ""),
        {{ filename: {str(DEVICES_OVERVIEW_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ROLLOUT_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(ROLLOUT_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(TECHNICAL_LOG_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(TECHNICAL_LOG_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(FEEDBACK_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(FEEDBACK_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(ERROR_TAXONOMY_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(ERROR_TAXONOMY_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(UI_STATE_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(UI_STATE_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(DEVICE_FEATURES_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(DEVICE_FEATURES_JS)!r} }}
      );
      const log = recordTechnicalEvent;
      vm.runInThisContext(
        fs.readFileSync({str(KIOSK_JS)!r}, "utf8").replace(/export /g, ""),
        {{ filename: {str(KIOSK_JS)!r} }}
      );
      vm.runInThisContext(
        fs.readFileSync({str(PANEL_JS)!r}, "utf8").replace(/^import .*;\\s*/gm, ""),
        {{ filename: {str(PANEL_JS)!r} }}
      );

      const getTable = {json.dumps(get_table, ensure_ascii=False)};
      const postTable = {json.dumps(post_table, ensure_ascii=False)};
      const postIndexes = {{}};
      const calls = [];
      const hass = {{
        callApi: (method, path, payload) => {{
          calls.push({{ method, path, payload }});
          if (method === "GET") {{
            if (!(path in getTable)) return Promise.reject(new Error("unexpected GET " + path));
            const result = getTable[path];
            if (result && result.__fail) return Promise.reject(new Error("GET failed"));
            return Promise.resolve(result);
          }}
          let result = postTable[path];
          if (Array.isArray(result)) {{
            const index = postIndexes[path] || 0;
            postIndexes[path] = index + 1;
            result = result[Math.min(index, result.length - 1)];
          }}
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
      const tick = async (count = 8) => {{
        for (let index = 0; index < count; index += 1) {{
          await new Promise((resolve) => setImmediate(resolve));
        }}
      }};

      (async () => {{
        const Panel = registry.get("hausman-hub-panel");
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
    return subprocess.run(
        ("node", "--input-type=commonjs", "--eval", script),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def draft_for(rooms: list[dict], *, name: str = "Дом", mode: str = "automatic") -> dict:
    device_count = sum(len(room["devices"]) for room in rooms)
    return {
        "contract": {"name": "hausman-hub-climate-contour-draft", "version": 1},
        "generated_at": 1784280000,
        "snapshot_revision": 77,
        "draft_revision": 9001,
        "status": "created",
        "save_allowed": False,
        "validation_required": True,
        "display_names": {
            "modes": DISPLAY_NAMES["modes"],
            "strategies": DISPLAY_NAMES["strategies"],
        },
        "name": name,
        "mode": mode,
        "rooms": rooms,
        "summary": {"room_count": len(rooms), "device_count": device_count},
    }


def roomless_options() -> dict:
    options = copy.deepcopy(DRAFT_OPTIONS)
    options["devices"].append(
        {
            "candidate_id": "candidate_smartir", "candidate_key": "candidate_smartir",
            "name": "Komanchi Living SmartIR",
            "room_id": "",
            "suggested_types": ["air_conditioner"],
            "recommended_type": "air_conditioner",
            "status": "available",
            "suggested_room_id": None,
            "suggested_room_name": None,
            "reason": "unassigned_room",
            "can_add": True,
        }
    )
    return options


def ready_validation(draft: dict) -> dict:
    return {
        "contract": {"name": "hausman-hub-climate-contour-validation", "version": 1},
        "generated_at": 1784280001,
        "snapshot_revision": draft["snapshot_revision"],
        "draft_revision": draft["draft_revision"],
        "status": "ready",
        "save_allowed": True,
        "command_allowed": False,
        "checks": {"rooms_have_active_devices": True},
        "issues": [],
        "summary": draft["summary"],
    }


class PanelContourWizardTest(unittest.TestCase):
    def test_channel_receipts_never_show_pending_or_failed_restore_as_success(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        const confirmed = summarizeControlChannelReceipts(
          {status: "confirmed"}, {status: "up_to_date"}
        );
        const pending = summarizeControlChannelReceipts(
          {status: "pending", accepted: true}, {status: "pending", accepted: true}
        );
        const restoreFailed = summarizeControlChannelReceipts(
          {status: "confirmed"}, {status: "failed", accepted: false}
        );
        const probeFailed = summarizeControlChannelReceipts(
          {status: "denied", accepted: false}, {status: "confirmed"}
        );
        if (confirmed.status !== "confirmed" || confirmed.title !== "Канал работает") {
          throw new Error("observed receipt pair was not recognized as confirmed");
        }
        if (pending.status !== "pending" || pending.title.includes("работает")) {
          throw new Error("pending receipt pair was presented as success");
        }
        if (restoreFailed.status !== "failed"
          || !restoreFailed.title.includes("Возврат настройки")) {
          throw new Error("failed restoration did not receive a dedicated warning");
        }
        if (probeFailed.status !== "failed"
          || !probeFailed.detail.includes("исходная настройка успешно восстановлена")) {
          throw new Error("failed probe did not explain the successful rollback");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_wizard_persists_the_last_interaction_before_page_unload(self) -> None:
        panel_source = PANEL_JS.read_text(encoding="utf-8")
        self.assertIn(
            'window.addEventListener("pagehide", this._persistFirstRunBeforeUnload)',
            panel_source,
        )
        self.assertIn(
            'window.removeEventListener("pagehide", this._persistFirstRunBeforeUnload)',
            panel_source,
        )
        script = panel_script(
            get_payloads(),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        panel._activeRoomSetupPane = "limits";
        panel._firstRun.rooms.living.day.temperature = 26;
        panel._persistFirstRunBeforeUnload();
        const ReloadedPanel = registry.get("hausman-hub-panel");
        const reloaded = new ReloadedPanel();
        reloaded.hass = hass;
        await tick(16);
        if (reloaded._firstRun.step !== "room"
          || reloaded._firstRun.roomId !== "living"
          || reloaded._activeRoomSetupPane !== "limits"
          || reloaded._firstRun.rooms.living.day.temperature !== 26) {
          throw new Error("pagehide did not persist the final wizard interaction");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_open_wizard_adopts_new_server_revision_without_losing_room_selection(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const device = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_ac:air_conditioner");
        device.checkbox.checked = true;
        device.checkbox.fire("change");
        device.controlChannel.value = "direct_wifi";
        device.controlChannel.fire("change");
        const refreshedSetup = JSON.parse(JSON.stringify(getTable[
          "hausman_hub/v1/admin/climate-drafts/current"
        ]));
        refreshedSetup.setup_revision = 6;
        getTable["hausman_hub/v1/admin/climate-drafts/current"] = refreshedSetup;
        const optionCallsBefore = calls.filter((call) => call.method === "GET"
          && call.path === "hausman_hub/v1/admin/climate-drafts").length;
        await panel._load();
        await tick();
        const optionCallsAfter = calls.filter((call) => call.method === "GET"
          && call.path === "hausman_hub/v1/admin/climate-drafts").length;
        const restored = panel._firstRun.rooms.living.devices[
          "candidate_ac:air_conditioner"
        ];
        if (panel._firstRun.setupRevision !== 6) {
          throw new Error("open wizard kept a stale setup revision");
        }
        if (optionCallsAfter !== optionCallsBefore + 1) {
          throw new Error("revision change did not refresh the device inventory exactly once");
        }
        if (!restored.selected || restored.channel !== "direct_wifi") {
          throw new Error("revision refresh discarded the room device selection");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_room_review_receives_active_device_types_as_an_explicit_module_dependency(self) -> None:
        room_setup = ROOM_SETUP_JS.read_text(encoding="utf-8")
        panel = PANEL_JS.read_text(encoding="utf-8")

        self.assertIn(
            "ACTIVE_DEVICE_TYPES, STRATEGY_ORDER, ROOM_SETUP_PANES,",
            room_setup,
        )
        self.assertIn(
            "ACTIVE_DEVICE_TYPES, STRATEGY_ORDER, ROOM_SETUP_PANES,",
            panel,
        )

    def test_open_wizard_selects_visible_contour_view(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        panel._firstRun.completed = true;
        panel._activeClimateView = "overview";
        panel._openWizard(panel._settings.setup);
        await tick();
        if (panel._activeSection !== "climate" || panel._activeClimateView !== "contour") {
          throw new Error("room wizard did not select the climate contour view: "
            + panel._activeSection + "/" + panel._activeClimateView);
        }
        if (panel._shell.climateViews.contour.hidden
          || !panel._shell.climateViews.overview.hidden) {
          throw new Error("room wizard is not visible after opening");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_control_channel_is_explained_recommended_and_safely_confirmed(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        air_conditioner = next(
            candidate
            for candidate in options["devices"]
            if candidate["candidate_id"] == "candidate_ac"
        )
        air_conditioner["device_group_id"] = "device_air_conditioner"
        payloads = get_payloads(options=options)
        payloads["hausman_hub/v1/dashboard"] = {
            "devices": [
                {
                    "id": "device_air_conditioner",
                    "physicalId": "device_air_conditioner",
                    "entityId": "climate.living_room",
                    "name": "Кондиционер",
                    "attributes": {"temperature": 25.0},
                    "details": [],
                }
            ]
        }
        payloads["hausman_hub/v1/admin/scenarios"] = {"scenarios": []}
        payloads["hausman_hub/v1/admin/scenarios/catalog"] = {
            "devices": [
                {
                    "target_id": "entity_air_conditioner",
                    "entity_id": "climate.living_room",
                    "name": "Кондиционер",
                    "actions": [
                        {
                            "action_id": "set_temperature",
                            "title": "Температура",
                            "allowed_fields": ["value"],
                        }
                    ],
                }
            ]
        }
        script = panel_script(
            payloads,
            {
                "hausman_hub/v1/device-actions": {
                    "accepted": True,
                    "confirmed": True,
                    "status": "confirmed",
                }
            },
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.include.checked = true;
        panel._firstRunFields.rooms.living.include.fire("change");
        panel._firstRunFields.rooms.living.configure.fire("click");
        await tick();
        let field = panel._firstRunFields.room.devices.find((item) => item.key === "candidate_ac:air_conditioner");
        field.checkbox.checked = true;
        field.checkbox.fire("change");
        let assistant = field.channelAssistant.node;
        if (!textOf(assistant).includes("Рекомендуем: Напрямую через Home Assistant")
          || !textOf(assistant).includes("Как выбрать способ управления")) {
          throw new Error("channel recommendation or comparison help is missing");
        }
        field.controlChannel.value = "direct_wifi";
        field.controlChannel.fire("change");
        panel._activeRoomSetupPane = "review";
        panel._render();
        let reviewText = textOf(panel.shadowRoot);
        if (!reviewText.includes("Устройства управления")
          || !reviewText.includes("1 выбрано")
          || !reviewText.includes("Проверка каналов")
          || !reviewText.includes("0 из 1 подтверждено")
          || !reviewText.includes("Каналы можно сохранить без теста")) {
          throw new Error("room review did not explain the untested control channel");
        }
        panel._activeRoomSetupPane = "devices";
        panel._render();
        field = panel._firstRunFields.room.devices.find((item) => item.key === "candidate_ac:air_conditioner");
        assistant = field.channelAssistant.node;
        const testButton = findAll(assistant, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить канал")[0];
        if (!testButton || !textOf(assistant).includes("без изменения режима")) {
          throw new Error("safe channel test is not offered");
        }
        testButton.fire("click");
        await tick(16);
        const commands = calls.filter((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/device-actions");
        if (commands.length !== 2 || commands.some((command) => (
          command.payload.targetId !== "entity_air_conditioner"
          || command.payload.actionId !== "set_temperature"))
          || commands[0].payload.value !== 25.5 || commands[1].payload.value !== 25) {
          throw new Error("reversible channel test did not probe and restore the setpoint");
        }
        if (!textOf(assistant).includes("Канал работает")
          || !textOf(assistant).includes("возврат исходной")) {
          throw new Error("confirmed read-back was not shown honestly");
        }
        panel._activeRoomSetupPane = "review";
        panel._render();
        reviewText = textOf(panel.shadowRoot);
        if (!reviewText.includes("Проверка каналов")
          || !reviewText.includes("1 из 1 подтверждено")
          || reviewText.includes("Каналы можно сохранить без теста")) {
          throw new Error("confirmed channel status did not reach the room review");
        }
        panel._activeRoomSetupPane = "devices";
        panel._render();
        field = panel._firstRunFields.room.devices.find((item) => item.key === "candidate_ac:air_conditioner");
        assistant = field.channelAssistant.node;
        field.controlChannel.value = "universal_ir";
        field.controlChannel.fire("change");
        if (findAll(assistant, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить канал").length
          || !textOf(assistant).includes("физическую реакцию")) {
          throw new Error("one-way IR channel was presented as automatically confirmable");
        }
        panel._activeRoomSetupPane = "review";
        panel._render();
        reviewText = textOf(panel.shadowRoot);
        if (!reviewText.includes("ИК-канал · ручная проверка")
          || !reviewText.includes("подтвердите физическую реакцию")
          || reviewText.includes("0 из 1 подтверждено")) {
          throw new Error("room review presented a one-way IR channel as automatically testable");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_not_configured_renders_rooms_multiple_sensors_and_keeps_dirty_form(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.include.checked = true;
        panel._firstRunFields.rooms.living.include.fire("change");
        panel._firstRunFields.rooms.living.configure.fire("click");
        const fields = panel._firstRunFields.room;
        const sourceStage = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("climate-source-stage"))[0];
        if (!sourceStage) throw new Error("main climate source stage missing");
        const image = findAll(sourceStage, (node) => node.tagName === "IMG")[0];
        if (!image
          || image.src !== "https://www.zigbee2mqtt.io/images/devices/KOJIMA-THS-ZG-LCD.png"
          || image.loading !== "lazy"
          || image.referrerpolicy !== "no-referrer") {
          throw new Error("official Zigbee2MQTT image is not configured safely");
        }
        const fallback = findAll(sourceStage, (node) =>
          String(node.className).includes("climate-source-thumb-fallback"))[0];
        image.fire("error");
        if (!image.hidden || fallback.hidden) {
          throw new Error("broken device image did not reveal local fallback");
        }
        const groupedChoices = fields.devices.filter((choice) =>
          ["candidate_temp_1:temperature_sensor", "candidate_humidity:humidity_sensor"].includes(choice.key));
        if (groupedChoices.length !== 2 || groupedChoices.some((choice) => choice.checkbox.checked)) {
          throw new Error("новые устройства должны быть не выбраны");
        }
        if (groupedChoices.some((choice) => choice.checkbox.type !== "radio")) {
          throw new Error("главные источники климата должны выбираться по одному");
        }
        const sourceSummary = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("climate-source-summary"))[0];
        if (!sourceSummary || !textOf(sourceSummary).includes("Главные показания комнаты")
          || !textOf(sourceSummary).includes("Обязательно")) {
          throw new Error("обязательные главные источники не объяснены");
        }
        groupedChoices.forEach((choice) => {
          choice.checkbox.checked = true;
          choice.checkbox.fire("change");
        });
        if (!textOf(sourceSummary).includes("Климат Kojima Гостинная")
          || groupedChoices.some((choice) => !choice.selectedMark || choice.selectedMark.hidden)) {
          throw new Error("выбранные главные источники не выделены");
        }
        if (groupedChoices.some((choice) => choice.controlChannel !== null)) {
          throw new Error("observed sensors exposed a control-channel selector");
        }
        const deviceCards = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("device-card"));
        if (deviceCards.some((card) => textOf(card).includes("Датчик температуры")
          || textOf(card).includes("Датчик влажности"))) {
          throw new Error("sensor choices leaked into actuator cards");
        }
        const airConditionerCard = deviceCards.find((card) => textOf(card).includes("Кондиционер"));
        if (!airConditionerCard || !textOf(airConditionerCard).includes("Использовать в контуре")
          || !textOf(airConditionerCard).includes("Способ управления")
          || textOf(airConditionerCard).includes("Можно добавить")
          || textOf(airConditionerCard).includes("Устройство найдено в этой комнате")) {
          throw new Error("actuator card is not structured or still shows routine technical statuses");
        }
        const groupedPayload = panel._firstRunPayload(["living"]);
        const livingPayload = groupedPayload.payload.rooms.find((room) => room.room_id === "living");
        if (!livingPayload
          || !livingPayload.devices.some((device) => device.candidate_id === "candidate_temp_1")
          || !livingPayload.devices.some((device) => device.candidate_id === "candidate_humidity")) {
          throw new Error("physical presentation changed the entity-level draft payload");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_one_physical_device_shows_each_climate_purpose_once(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        duplicate = copy.deepcopy(
            next(
                candidate
                for candidate in options["devices"]
                if candidate["candidate_id"] == "candidate_temp_1"
            )
        )
        duplicate["candidate_id"] = "candidate_temp_external"
        duplicate["candidate_key"] = "candidate_temp_external"
        duplicate["name"] = "Внешняя температура у окна"
        options["devices"].append(duplicate)
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.include.checked = true;
        panel._firstRunFields.rooms.living.include.fire("change");
        panel._firstRunFields.rooms.living.configure.fire("click");
        const fields = panel._firstRunFields.room.devices;
        const physical = fields.filter((choice) => [
          "candidate_temp_1:temperature_sensor",
          "candidate_temp_external:temperature_sensor",
          "candidate_humidity:humidity_sensor",
        ].includes(choice.key));
        if (physical.length !== 2
          || !physical.some((choice) => choice.key === "candidate_temp_1:temperature_sensor")
          || !physical.some((choice) => choice.key === "candidate_humidity:humidity_sensor")) {
          throw new Error("physical device purposes were not canonicalized: "
            + JSON.stringify(physical.map((choice) => choice.key)));
        }
        const sourceStage = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("climate-source-stage"))[0];
        if (!sourceStage || !textOf(sourceStage).includes("Температура комнаты")
          || !textOf(sourceStage).includes("Влажность комнаты")
          || textOf(sourceStage).includes("Внешняя температура у окна")) {
          throw new Error("duplicate purpose is still visible in the source picker");
        }
        const deviceCards = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("device-card"));
        if (deviceCards.some((card) => textOf(card).includes("Датчик температуры")
          || textOf(card).includes("Датчик влажности"))) {
          throw new Error("sensor purpose leaked into an actuator card");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)


class PanelFirstRunWizardTest(unittest.TestCase):
    def test_deleted_yandex_candidate_is_not_recommended_as_a_live_channel(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        const owner = {
          _firstRun: {options: {
            control_channels: ["universal_ir", "yandex_remote", "direct_wifi"],
            ir_remotes: [{entity_id: "remote.pult_broadlink_gostinnaia"}],
          }},
          _homeDashboard: {devices: []},
        };
        const deletedYandex = {
          candidate: {
            name: "Кондиционер Яндекса",
            manufacturer: "Yandex",
            status: "unavailable",
            can_add: false,
          },
          device: {channel: null},
          type: "air_conditioner",
        };
        const deletedRecommendation = recommendControlChannel(owner, deletedYandex);
        if (deletedRecommendation.channel !== "universal_ir"
          || deletedRecommendation.channel === "yandex_remote") {
          throw new Error("deleted Yandex entity was recommended as a live control route");
        }
        const liveYandex = {
          ...deletedYandex,
          candidate: {...deletedYandex.candidate, status: "available", can_add: true},
        };
        const liveRecommendation = recommendControlChannel(owner, liveYandex);
        if (liveRecommendation.channel !== "yandex_remote") {
          throw new Error("available Yandex entity lost its explicit route");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_ir_command_names_stay_internal_and_visible_labels_are_russian(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        const humidifier = panel._firstRunIrManualCommands({type: "humidifier", profiles: {}});
        const conditioner = panel._firstRunIrManualCommands({
          type: "air_conditioner",
          profiles: {day: {target_temperature: 25}, night: {target_temperature: 24}},
        });
        if (JSON.stringify(humidifier) !== JSON.stringify([
          {commandName: "humidifier.on", label: "Включить"},
          {commandName: "humidifier.off", label: "Выключить"},
        ])) throw new Error("humidifier IR labels are not localized");
        if (!conditioner.some((item) => item.commandName === "ac.off" && item.label === "Выключить")
          || !conditioner.some((item) => item.commandName === "ac.cool.25_0"
            && item.label === "Охлаждение · 25.0 °C")
          || conditioner.some((item) => /^(?:on|off|cool)\b/.test(item.label))) {
          throw new Error("conditioner IR labels expose internal English command names");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_home_signal_draft_and_open_chooser_survive_background_render(self) -> None:
        home_payload = copy.deepcopy(HOME_PAYLOAD)
        home_payload["candidates"] = {
            "outdoor_temperature": [
                {
                    "entity_id": "weather.home",
                    "name": "Погода дома",
                    "domain": "weather",
                    "available": True,
                    "room_id": "",
                }
            ],
            "presence": [
                {
                    "entity_id": "person.owner",
                    "name": "Владелец",
                    "domain": "person",
                    "available": True,
                    "room_id": "",
                }
            ],
            "central_heating": [
                {
                    "entity_id": "binary_sensor.boiler_heat",
                    "name": "Работа котла",
                    "domain": "binary_sensor",
                    "device_class": "heat",
                    "available": True,
                    "room_id": "",
                }
            ],
        }
        script = panel_script(
            get_payloads(home=home_payload),
            {},
            """
        panel._firstRun.home = {
          central_heating_entity_id: null, heating_lockout_high: 18,
          heating_lockout_low: 16, outdoor_temperature_entity_id: null,
          presence_entity_id: null,
        };
        const firstCard = document.createElement("div");
        panel._renderFirstRunHome(firstCard);
        const choose = (value) => {
          const control = findAll(firstCard, (node) => node.value === value
            && (node.type === "radio" || node.tagName === "BUTTON"))[0];
          if (!control) throw new Error("choice missing: " + value);
          if (control.type === "radio") {
            control.checked = true;
            control.fire("change");
          } else {
            control.fire("click");
          }
        };
        choose("weather.home");
        choose("person.owner");
        choose("binary_sensor.boiler_heat");
        const details = findAll(firstCard, (node) => node.tagName === "DETAILS")[0];
        details.open = true;
        details.fire("toggle");
        const secondCard = document.createElement("div");
        panel._renderFirstRunHome(secondCard);
        const selected = [
          ...panel._firstRun.home.outdoor_temperature_entity_ids,
          ...findAll(secondCard, (node) => node.type === "radio" && node.checked)
            .map((node) => node.value).filter(Boolean),
        ].sort();
        const expected = ["binary_sensor.boiler_heat", "person.owner", "weather.home"].sort();
        if (JSON.stringify(selected) !== JSON.stringify(expected)) {
          throw new Error("background render lost the signal draft: " + JSON.stringify(selected));
        }
        const reopened = findAll(secondCard, (node) => node.tagName === "DETAILS")[0];
        if (!reopened.open) throw new Error("open chooser collapsed after background render");
        for (const expectedText of [
          "Зачем это нужно", "Как выбрать", "Источник температуры именно на улице",
          "Общий режим дома", "Температура батареи",
        ]) {
          if (!textOf(secondCard).includes(expectedText)) {
            throw new Error("wizard explanation missing: " + expectedText);
          }
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_home_save_refreshes_revision_used_by_full_validation(self) -> None:
        script = panel_script(
            get_payloads(),
            {
                "hausman_hub/v1/admin/home-environment": {
                    "home": HOME_PAYLOAD["home"],
                    "setup_revision": 6,
                }
            },
            """
        panel._firstRun.setupRevision = 5;
        panel._firstRun.home = {
          central_heating_entity_id: null, heating_lockout_high: 18,
          heating_lockout_low: 16, outdoor_temperature_entity_id: null,
          presence_entity_id: null
        };
        await panel._saveFirstRunHome();
        if (panel._firstRun.setupRevision !== 6 || panel._firstRun.step !== "validation") {
          throw new Error("home save did not refresh the setup revision before validation");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_home_continue_keeps_weather_priority_payload_unambiguous(self) -> None:
        home_payload = copy.deepcopy(HOME_PAYLOAD)
        home_payload["home"]["outdoor_temperature_entity_id"] = None
        home_payload["home"]["outdoor_temperature_entity_ids"] = []
        home_payload["candidates"]["outdoor_temperature"] = [
            {
                "entity_id": "weather.forecast_home_assistant",
                "name": "Forecast",
                "device_name": "Forecast",
                "domain": "weather",
                "available": True,
                "room_id": "",
            },
            {
                "entity_id": "weather.forecast_omsk",
                "name": "Forecast",
                "device_name": "Forecast",
                "domain": "weather",
                "available": True,
                "room_id": "",
            },
        ]
        script = panel_script(
            get_payloads(home=home_payload),
            {
                "hausman_hub/v1/admin/home-environment": {
                    "home": home_payload["home"],
                    "setup_revision": 7,
                }
            },
            """
        panel._firstRun.home = JSON.parse(JSON.stringify(getTable[
          "hausman_hub/v1/admin/home-environment"
        ].home));
        const card = document.createElement("div");
        panel._renderFirstRunHome(card);
        const choose = (entityId) => {
          const choices = findAll(card, (node) =>
            node.tagName === "BUTTON" && node.value === entityId);
          if (choices.length !== 1) throw new Error("weather choice missing: " + entityId);
          choices[0].fire("click");
        };
        choose("weather.forecast_home_assistant");
        choose("weather.forecast_omsk");
        const next = findAll(card, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Продолжить к проверке");
        if (next.length !== 1) throw new Error("home continue action missing");
        next[0].fire("click");
        await tick();
        const saved = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/home-environment");
        if (!saved) throw new Error("home environment was not posted");
        if (saved.payload.outdoor_temperature_entity_id
          !== "weather.forecast_home_assistant") {
          throw new Error("primary weather source was corrupted: "
            + JSON.stringify(saved.payload.outdoor_temperature_entity_id));
        }
        if (saved.payload.outdoor_temperature_entity_ids.join(",")
          !== "weather.forecast_home_assistant,weather.forecast_omsk") {
          throw new Error("weather priority order was corrupted: "
            + JSON.stringify(saved.payload.outdoor_temperature_entity_ids));
        }
        if (panel._firstRun.step !== "validation" || panel._firstRun.setupRevision !== 7) {
          throw new Error("valid home sources did not advance to validation");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_home_save_error_points_to_sources_instead_of_valid_thresholds(self) -> None:
        script = panel_script(
            get_payloads(),
            {"hausman_hub/v1/admin/home-environment": {"__fail": 400}},
            """
        panel._firstRun.home = {
          central_heating_entity_id: null,
          central_heating_temperature_on: 35,
          central_heating_temperature_off: 30,
          heating_lockout_high: 18,
          heating_lockout_low: 16,
          air_conditioner_minimum_outdoor_temperature: -5,
          outdoor_temperature_entity_id: "weather.home",
          outdoor_temperature_entity_ids: ["weather.home"],
          presence_entity_id: null,
        };
        await panel._saveFirstRunHome();
        if (!panel._firstRun.homeError.includes("отклонил выбранный источник")) {
          throw new Error("source rejection is still hidden: " + panel._firstRun.homeError);
        }
        if (!panel._firstRun.homeError.includes("Числовые пороги ниже корректны")) {
          throw new Error("valid thresholds are still blamed for the source error");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_home_save_recovers_priority_array_left_by_previous_ui(self) -> None:
        script = panel_script(
            get_payloads(),
            {
                "hausman_hub/v1/admin/home-environment": {
                    "home": HOME_PAYLOAD["home"],
                    "setup_revision": 8,
                }
            },
            """
        panel._firstRun.home = {
          central_heating_entity_id: null,
          central_heating_temperature_on: 35,
          central_heating_temperature_off: 30,
          heating_lockout_high: 18,
          heating_lockout_low: 16,
          air_conditioner_minimum_outdoor_temperature: -5,
          outdoor_temperature_entity_id: ["weather.home", "sensor.outdoor_temperature"],
          presence_entity_id: null,
        };
        await panel._saveFirstRunHome();
        const saved = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/home-environment");
        if (!saved || saved.payload.outdoor_temperature_entity_id !== "weather.home") {
          throw new Error("legacy array was not repaired to one primary source");
        }
        if (saved.payload.outdoor_temperature_entity_ids.join(",")
          !== "weather.home,sensor.outdoor_temperature") {
          throw new Error("legacy priority order was not recovered");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_full_validation_explains_revision_conflict(self) -> None:
        script = panel_script(
            get_payloads(),
            {"hausman_hub/v1/admin/climate-drafts": {"__fail": 409}},
            """
        panel._firstRunPayload = () => ({payload: {}});
        await panel._validateFirstRun();
        if (!panel._firstRun.issues[0].message.includes("Состояние изменилось. Обновите данные")) {
          throw new Error("revision conflict explanation missing");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_default_room_catalog_hides_only_unavailable_shadow_duplicate(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        shared = {
            "name": "Кондиционер",
            "room_id": "kids",
            "suggested_types": ["air_conditioner"],
            "recommended_type": "air_conditioner",
            "suggested_room_id": "kids",
            "suggested_room_name": "Детская",
            "reason": "detected_room",
            "can_add": True,
            "device_name": "Кондиционер",
            "manufacturer": "Yandex",
            "model": "YNDX-0006",
        }
        options["devices"].extend(
            [
                {
                    **shared,
                    "candidate_id": "candidate_kids_ac_live",
                    "candidate_key": "candidate_kids_ac_live",
                    "device_group_id": "device_live",
                    "status": "available",
                },
                {
                    **shared,
                    "candidate_id": "candidate_kids_ac_stale",
                    "candidate_key": "candidate_kids_ac_stale",
                    "device_group_id": "device_stale",
                    "status": "unavailable",
                    "reason": "device_unavailable",
                },
            ]
        )
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.kids.configure.fire("click");
        const visibleKeys = panel._firstRunFields.room.devices.map((item) => item.key);
        if (!visibleKeys.includes("candidate_kids_ac_live:air_conditioner")
          || visibleKeys.includes("candidate_kids_ac_stale:air_conditioner")) {
          throw new Error("default room catalog did not suppress the unavailable shadow duplicate");
        }
        const showAll = findAll(panel.shadowRoot, (node) => node.tagName === "LABEL"
          && textOf(node).includes("Показать устройства из других комнат"))[0].children[0];
        showAll.checked = true;
        showAll.fire("change");
        const allKeys = panel._firstRunFields.room.devices.map((item) => item.key);
        if (!allKeys.includes("candidate_kids_ac_live:air_conditioner")
          || !allKeys.includes("candidate_kids_ac_stale:air_conditioner")) {
          throw new Error("show-all catalog must preserve both HA registry records for audit");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_unavailable_room_device_stays_selectable_with_badge_and_warning(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["display_names"]["device_status"] = {"unavailable": "Недоступно"}
        options["display_names"]["suggestion_reasons"] = {
            "device_unavailable": "Устройство недоступно",
        }
        options["devices"].append(
            {
                "candidate_id": "candidate_offline", "candidate_key": "candidate_offline",
                "name": "Датчик у дивана",
                "room_id": "living",
                "suggested_types": ["temperature_sensor"],
                "recommended_type": "temperature_sensor",
                "status": "unavailable",
                "suggested_room_id": "living",
                "suggested_room_name": "Гостиная",
                "reason": "device_unavailable",
                "can_add": True,
            }
        )
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const offline = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_offline:temperature_sensor");
        if (!offline || offline.checkbox.disabled || offline.checkbox.checked) {
          throw new Error("unavailable room candidate must stay selectable and unchecked");
        }
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Сейчас недоступен")) {
          throw new Error("unavailable source status missing: " + text);
        }
        offline.checkbox.checked = true;
        offline.checkbox.fire("change");
        if (!offline.selectedMark || offline.selectedMark.hidden
          || !textOf(panel.shadowRoot).includes("Выбран")) {
          throw new Error("unavailable source did not become the explicit main source");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_similar_room_name_cross_room_device_is_visible_but_disabled(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["devices"].append(
            {
                "candidate_id": "candidate_similar_room", "candidate_key": "candidate_similar_room",
                "name": "Климат Kojima Гостинная Температура",
                "device_name": "Климат Kojima Гостинная",
                "room_id": "kids",
                "suggested_types": ["air_conditioner"],
                "recommended_type": "air_conditioner",
                "status": "available",
                "suggested_room_id": "kids",
                "suggested_room_name": "Детская",
                "reason": "detected_room",
                "can_add": True,
            }
        )
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const similar = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_similar_room:air_conditioner");
        if (!similar || similar.checkbox.disabled !== true || similar.checkbox.checked) {
          throw new Error("similar cross-room candidate must be shown disabled and unchecked");
        }
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Возможно, относится к этой комнате")
          || !text.includes("Сейчас: Детская")
          || !text.includes("переназначьте область в Home Assistant")) {
          throw new Error("similar-room group or current-room guidance missing: " + text);
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_show_all_catalog_groups_other_rooms_and_roomless_disabled_candidates(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["devices"].append(
            {
                "candidate_id": "candidate_roomless_elsewhere", "candidate_key": "candidate_roomless_elsewhere",
                "name": "Кондиционер без комнаты",
                "room_id": "",
                "suggested_types": ["air_conditioner"],
                "recommended_type": "air_conditioner",
                "status": "available",
                "suggested_room_id": "kids",
                "suggested_room_name": "Детская",
                "reason": "detected_room",
                "can_add": True,
            }
        )
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const toggle = findAll(panel.shadowRoot, (node) => node.tagName === "LABEL"
          && String(node.className).includes("checkbox-field")
          && textOf(node).includes("Показать устройства из других комнат"))[0].children[0];
        toggle.checked = true;
        toggle.fire("change");
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Детская") || !text.includes("Без комнаты")) {
          throw new Error("show-all catalog did not render room and roomless groups");
        }
        const otherRoom = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_trv:radiator_thermostat");
        const roomless = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_roomless_elsewhere:air_conditioner");
        if (!otherRoom || !roomless || !otherRoom.checkbox.disabled || !roomless.checkbox.disabled) {
          throw new Error("show-all candidates outside this room became selectable");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_show_all_catalog_keeps_unsupported_device_as_disabled_unknown_type(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["display_names"]["device_status"] = {"unavailable": "Недоступно"}
        options["display_names"]["suggestion_reasons"] = {
            "unsupported_type": "Тип не поддерживается",
        }
        options["devices"].append(
            {
                "candidate_id": "candidate_unknown", "candidate_key": "candidate_unknown",
                "name": "Неизвестное устройство", "room_id": "living",
                "suggested_types": [], "recommended_type": None,
                "status": "unavailable", "suggested_room_id": "living",
                "suggested_room_name": "Гостиная", "reason": "unsupported_type",
                "can_add": False,
            }
        )
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const toggle = findAll(panel.shadowRoot, (node) => node.tagName === "LABEL"
          && String(node.className).includes("checkbox-field")
          && textOf(node).includes("Показать устройства из других комнат"))[0].children[0];
        toggle.checked = true;
        toggle.fire("change");
        const unknown = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_unknown:unknown");
        if (!unknown || !unknown.checkbox.disabled || unknown.checkbox.checked) {
          throw new Error("неподдерживаемое устройство должно быть отключено");
        }
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Тип не определён") || !text.includes("Сейчас недоступно")
          || !text.includes("Тип не поддерживается")) {
          throw new Error("для неподдерживаемого устройства нет статуса и причины");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_show_all_catalog_uses_russian_labels_for_all_core_device_types(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const toggle = findAll(panel.shadowRoot, (node) => node.tagName === "LABEL"
          && String(node.className).includes("checkbox-field")
          && textOf(node).includes("Показать устройства из других комнат"))[0].children[0];
        toggle.checked = true;
        toggle.fire("change");
        const text = textOf(panel.shadowRoot);
        ["Радиаторный термостат", "Кондиционер", "Температура комнаты", "Влажность комнаты"]
          .forEach((label) => {
            if (!text.includes(label)) throw new Error("нет русского названия типа: " + label);
          });
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_room_name_matching_keeps_vannaya_and_zal(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["rooms"] = [
            {"id": "bath", "name": "Ванная", "status": "available", "selectable": True},
            {"id": "hall", "name": "Зал", "status": "available", "selectable": True},
            {"id": "other", "name": "Другая", "status": "available", "selectable": True},
        ]
        options["devices"] = [
            {
                "candidate_id": "candidate_bath", "candidate_key": "candidate_bath",
                "name": "Кондиционер Ванная", "room_id": "other",
                "suggested_types": ["air_conditioner"], "recommended_type": "air_conditioner",
                "status": "available", "suggested_room_id": "other",
                "suggested_room_name": "Другая", "reason": "detected_room", "can_add": True,
            },
            {
                "candidate_id": "candidate_hall", "candidate_key": "candidate_hall",
                "name": "Кондиционер Зал", "room_id": "other",
                "suggested_types": ["air_conditioner"], "recommended_type": "air_conditioner",
                "status": "available", "suggested_room_id": "other",
                "suggested_room_name": "Другая", "reason": "detected_room", "can_add": True,
            },
        ]
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        const bath = panel._firstRun.options.rooms.find((room) => room.id === "bath");
        const hall = panel._firstRun.options.rooms.find((room) => room.id === "hall");
        const bathMatches = panel._firstRunPossibleRoomCandidates(bath);
        const hallMatches = panel._firstRunPossibleRoomCandidates(hall);
        if (!bathMatches.some((candidate) => candidate.candidate_key === "candidate_bath")
          || !hallMatches.some((candidate) => candidate.candidate_key === "candidate_hall")) {
          throw new Error("полное имя комнаты не нашло устройство");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_refresh_preserves_selection_and_merges_new_candidate_rows(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        refreshed = copy.deepcopy(DRAFT_OPTIONS)
        for candidate in refreshed["devices"]:
            candidate["candidate_id"] = f"{candidate['candidate_id']}_refreshed"
        refreshed["devices"].append(
            {
                "candidate_id": "candidate_new_after_refresh", "candidate_key": "candidate_new_after_refresh",
                "name": "Новый датчик гостиной",
                "room_id": "living",
                "suggested_types": ["temperature_sensor"],
                "recommended_type": "temperature_sensor",
                "status": "available",
                "suggested_room_id": None,
                "suggested_room_name": None,
                "reason": "detected_room",
                "can_add": True,
            }
        )
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const before = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_ac:air_conditioner");
        before.checkbox.checked = true;
        before.checkbox.fire("change");
        before.controlChannel.value = "direct_wifi";
        before.controlChannel.fire("change");
        ["temperature_sensor", "humidity_sensor"].forEach((type) => {
          const source = panel._firstRunFields.room.devices.find((item) => item.type === type);
          source.checkbox.checked = true;
          source.checkbox.fire("change");
        });
        panel._firstRun.rooms.living.report = {status: "ready", save_allowed: true};
        panel._firstRun.validRooms.add("living");
        panel._firstRun.validation = {status: "ready", save_allowed: true};
        getTable["hausman_hub/v1/admin/climate-drafts"] = """
            + json.dumps(refreshed, ensure_ascii=False)
            + """;
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Обновить список устройств")[0].fire("click");
        await tick();
        const preserved = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_ac:air_conditioner");
        const added = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_new_after_refresh:temperature_sensor");
        if (!preserved || !preserved.checkbox.checked
          || preserved.controlChannel.value !== "direct_wifi") {
          throw new Error("refresh discarded the existing device selection or channel");
        }
        if (!added || added.checkbox.checked || added.checkbox.disabled) {
          throw new Error("refresh did not merge a new selectable candidate as unchecked");
        }
        if (!panel._firstRun.rooms.living.report
          || !panel._firstRun.validRooms.has("living")) {
          throw new Error("refresh discarded the unchanged configured room");
        }
        if (panel._firstRun.validation) {
          throw new Error("refresh retained stale whole-home validation");
        }
        panel._firstRun.rooms.living.included = true;
        const collected = panel._firstRunPayload(["living"]);
        const refreshedAc = collected.payload.rooms[0].devices.find((device) =>
          device.type === "air_conditioner");
        if (!refreshedAc || refreshedAc.candidate_id !== "candidate_ac_refreshed") {
          throw new Error("draft did not use the refreshed candidate id");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_refresh_invalidates_room_when_selected_device_disappears(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        refreshed = copy.deepcopy(DRAFT_OPTIONS)
        refreshed["devices"] = [
            candidate for candidate in refreshed["devices"]
            if candidate["candidate_key"] != "candidate_ac"
        ]
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const ac = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_ac:air_conditioner");
        ac.checkbox.checked = true;
        ac.checkbox.fire("change");
        ac.controlChannel.value = "direct_wifi";
        ac.controlChannel.fire("change");
        ["temperature_sensor", "humidity_sensor"].forEach((type) => {
          const source = panel._firstRunFields.room.devices.find((item) => item.type === type);
          source.checkbox.checked = true;
          source.checkbox.fire("change");
        });
        panel._firstRun.rooms.living.report = {status: "ready", save_allowed: true};
        panel._firstRun.validRooms.add("living");
        getTable["hausman_hub/v1/admin/climate-drafts"] = """
            + json.dumps(refreshed, ensure_ascii=False)
            + """;
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Обновить список устройств")[0].fire("click");
        await tick();
        if (panel._firstRun.validRooms.has("living")
          || panel._firstRun.rooms.living.report) {
          throw new Error("refresh retained validation after a selected device disappeared");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_page_reload_restores_current_room_step_and_device_selection(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const device = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_ac:air_conditioner");
        device.checkbox.checked = true;
        device.checkbox.fire("change");
        device.controlChannel.value = "direct_wifi";
        device.controlChannel.fire("change");
        panel._activeRoomSetupPane = "comfort";
        panel._render();
        const ReloadedPanel = registry.get("hausman-hub-panel");
        const reloaded = new ReloadedPanel();
        reloaded.hass = hass;
        await tick(16);
        if (reloaded._firstRun.step !== "room" || reloaded._firstRun.roomId !== "living") {
          throw new Error("page reload did not restore the current room step");
        }
        if (reloaded._activeRoomSetupPane !== "comfort") {
          throw new Error("page reload did not restore the room substep");
        }
        const restored = reloaded._firstRun.rooms.living.devices["candidate_ac:air_conditioner"];
        if (!restored.selected || restored.channel !== "direct_wifi") {
          throw new Error("page reload discarded the selected device or control channel");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_page_reload_restores_current_setup_revision_and_room_check_works(self) -> None:
        checked_draft = draft_for(
            [{
                "id": "living",
                "name": "Гостиная",
                "targets": {
                    "target_temperature": 25,
                    "target_humidity": 53,
                    "strategy": "normal",
                },
                "devices": [{
                    "candidate_id": "candidate_ac",
                    "name": "Кондиционер",
                    "type": "air_conditioner",
                    "type_name": "Кондиционер",
                }],
            }]
        )
        script = panel_script(
            get_payloads(),
            {
                "hausman_hub/v1/admin/climate-drafts": checked_draft,
                "hausman_hub/v1/admin/climate-drafts/validate": ready_validation(checked_draft),
            },
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const device = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_ac:air_conditioner");
        device.checkbox.checked = true;
        device.checkbox.fire("change");
        device.controlChannel.value = "direct_wifi";
        device.controlChannel.fire("change");
        ["temperature_sensor", "humidity_sensor"].forEach((type) => {
          const source = panel._firstRunFields.room.devices.find((item) => item.type === type);
          source.checkbox.checked = true;
          source.checkbox.fire("change");
        });
        panel._activeRoomSetupPane = "review";
        panel._render();

        const ReloadedPanel = registry.get("hausman-hub-panel");
        const reloaded = new ReloadedPanel();
        reloaded.hass = hass;
        await tick(16);
        if (reloaded._firstRun.setupRevision !== 5) {
          throw new Error("page reload lost the authoritative setup revision");
        }
        const check = findAll(reloaded.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить комнату")[0];
        if (!check || check.disabled) throw new Error("room check is unavailable after reload");
        check.fire("click");
        await tick(4);
        if (!reloaded._firstRun.validRooms.has("living")) {
          throw new Error("room check did not work after a full page reload");
        }
        const request = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/climate-drafts");
        if (!request || request.payload.setup_revision !== 5) {
          throw new Error("room check sent a stale setup revision after reload");
        }
        const verifiedReload = new ReloadedPanel();
        verifiedReload.hass = hass;
        await tick(16);
        if (!verifiedReload._firstRun.validRooms.has("living")
          || verifiedReload._firstRun.rooms.living.report?.status !== "ready") {
          throw new Error("verified room became unconfigured after the next page reload");
        }
        const finish = findAll(verifiedReload.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Завершить")[0];
        if (!finish || finish.disabled) {
          throw new Error("verified room did not retain its completion action after reload");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_roomless_warning_lists_five_names_and_remaining_count(self) -> None:
        options = roomless_options()
        template = options["devices"][-1]
        for index in range(2, 7):
            candidate = copy.deepcopy(template)
            candidate["candidate_id"] = f"candidate_roomless_{index}"
            candidate["candidate_key"] = f"candidate_roomless_{index}"
            candidate["name"] = f"Устройство без комнаты {index}"
            options["devices"].append(candidate)
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const warnings = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("wizard-warning"));
        if (warnings.length !== 1) {
          throw new Error("room step must show one roomless warning");
        }
        const warning = warnings[0].textContent;
        if (!warning.includes("Устройств без комнаты: 6")
          || !warning.includes("не участвуют в климате")) {
          throw new Error("roomless warning count or explanation mismatch: " + warning);
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_binding_step_saves_roomless_physical_device_to_home_assistant(self) -> None:
        options = roomless_options()
        options["devices"][-1]["suggested_room_id"] = "living"
        options["devices"][-1]["suggested_room_name"] = "Гостиная"
        script = panel_script(
            get_payloads(options=options),
            {"hausman_hub/v1/admin/device-area-assignments": {
                "status": "saved", "updated_devices": 1, "updated_entities": 0,
            }},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Привязка комнат и устройств")
          || !text.includes("Устройства без комнаты")
          || !text.includes("Komanchi Living SmartIR")) {
          throw new Error("binding inventory is incomplete: " + text);
        }
        const rows = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("binding-device-row"));
        const target = rows.find((row) => textOf(row).includes("Komanchi Living SmartIR"));
        if (!target) throw new Error("suggested but unassigned physical device is missing");
        const picker = findAll(target, (node) => node.tagName === "SELECT")[0];
        if (!picker || picker.value !== "") throw new Error("room picker missing or preselected");
        picker.value = "living";
        picker.fire("change");
        if (panel._firstRun.rooms.living.included
          || panel._firstRun.rooms.living.devices["candidate_smartir:air_conditioner"].selected) {
          throw new Error("area draft leaked into the climate contour draft");
        }
        panel._firstRun.rooms.living.report = {status: "ready", save_allowed: true};
        panel._firstRun.validRooms.add("living");
        const save = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить привязки в Home Assistant")[0];
        if (!save || save.disabled) throw new Error("explicit HA save action is unavailable");
        save.fire("click");
        await tick(4);
        if (panel._busy) throw new Error("room binding save left the panel busy");
        const request = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/device-area-assignments");
        if (!request || request.payload.snapshot_revision !== 77
          || request.payload.assignments.length !== 1
          || request.payload.assignments[0].room_id !== "living"
          || request.payload.assignments[0].candidate_ids[0] !== "candidate_smartir") {
          throw new Error("HA area assignment payload mismatch: " + JSON.stringify(request));
        }
        if (!textOf(panel.shadowRoot).includes("Привязки комнат сохранены")) {
          throw new Error("successful HA assignment feedback is missing");
        }
        if (!panel._firstRun.validRooms.has("living")
          || !panel._firstRun.rooms.living.report) {
          throw new Error("saving an unrelated room binding discarded configured rooms");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_binding_draft_survives_full_page_reload(self) -> None:
        options = roomless_options()
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        const target = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("binding-device-row"))
          .find((row) => textOf(row).includes("Komanchi Living SmartIR"));
        const picker = findAll(target, (node) => node.tagName === "SELECT")[0];
        picker.value = "living";
        picker.fire("change");
        if (!Object.values(panel._firstRun.areaAssignments).includes("living")) {
          throw new Error("binding fixture was not selected");
        }
        const ReloadedPanel = registry.get("hausman-hub-panel");
        const reloaded = new ReloadedPanel();
        reloaded.hass = hass;
        await tick(16);
        if (reloaded._firstRun.step !== "rooms") {
          throw new Error("binding step was not restored after page reload");
        }
        if (!Object.values(reloaded._firstRun.areaAssignments).includes("living")) {
          throw new Error("unsaved Home Assistant room binding was lost after page reload");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_binding_step_creates_home_assistant_room_and_selects_it_for_device(self) -> None:
        options = roomless_options()
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        const websocketCalls = [];
        panel._hass.connection = {
          sendMessagePromise: (payload) => {
            websocketCalls.push(payload);
            return Promise.resolve({area_id: "guest_room", name: payload.name});
          },
        };
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        const target = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("binding-device-row"))
          .find((row) => textOf(row).includes("Komanchi Living SmartIR"));
        const picker = findAll(target, (node) => node.tagName === "SELECT")[0];
        picker.value = "__create_area__";
        picker.fire("change");
        const creator = panel._firstRunFields.areaCreator;
        if (!creator || !textOf(panel.shadowRoot).includes("Новая комната Home Assistant")) {
          throw new Error("inline Home Assistant room creator did not open");
        }
        creator.input.value = "  Гостевая   комната  ";
        creator.create.fire("click");
        await tick(12);
        if (websocketCalls.length !== 1
          || websocketCalls[0].type !== "config/area_registry/create"
          || websocketCalls[0].name !== "Гостевая комната") {
          throw new Error("native Home Assistant area request mismatch: "
            + JSON.stringify(websocketCalls));
        }
        if (!panel._firstRun.options.rooms.some((room) =>
          room.id === "guest_room" && room.name === "Гостевая комната")) {
          throw new Error("created Home Assistant room was not added to room choices");
        }
        if (!Object.values(panel._firstRun.areaAssignments).includes("guest_room")) {
          throw new Error("created room was not selected for the originating device");
        }
        if (!textOf(panel.shadowRoot).includes("Комната «Гостевая комната» создана")) {
          throw new Error("room creation confirmation is missing");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_binding_step_rejects_empty_and_duplicate_room_names(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        const websocketCalls = [];
        panel._hass.connection = {
          sendMessagePromise: (payload) => {
            websocketCalls.push(payload);
            return Promise.resolve({area_id: "unexpected"});
          },
        };
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.addRoom.fire("click");
        panel._firstRunFields.areaCreator.create.fire("click");
        if (!textOf(panel.shadowRoot).includes("Введите название комнаты")) {
          throw new Error("empty room name validation is missing");
        }
        panel._firstRunFields.areaCreator.input.value = "гостиная";
        panel._firstRunFields.areaCreator.create.fire("click");
        if (!textOf(panel.shadowRoot).includes("Комната «Гостиная» уже существует")) {
          throw new Error("duplicate room name validation is missing");
        }
        if (websocketCalls.length !== 0) {
          throw new Error("invalid room name reached Home Assistant");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_binding_step_keeps_room_form_open_when_home_assistant_rejects_creation(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        panel._hass.connection = {
          sendMessagePromise: () => Promise.reject(new Error("forbidden")),
        };
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.addRoom.fire("click");
        panel._firstRunFields.areaCreator.input.value = "Гостевая";
        panel._firstRunFields.areaCreator.create.fire("click");
        await tick(6);
        if (!panel._firstRun.areaCreator.open || panel._busy) {
          throw new Error("failed creation closed the form or left the panel busy");
        }
        if (!textOf(panel.shadowRoot).includes("Не удалось создать комнату")) {
          throw new Error("Home Assistant room creation error is not visible");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_binding_step_moves_or_clears_devices_already_in_rooms(self) -> None:
        script = panel_script(
            get_payloads(),
            {"hausman_hub/v1/admin/device-area-assignments": {
                "status": "saved",
                "updated_devices": 2,
                "updated_entities": 0,
                "cleared_assignments": 1,
            }},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        const showLabel = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("checkbox-field")
          && textOf(node).includes("Показать устройства, уже привязанные"))[0];
        const showAssigned = findAll(showLabel, (node) => node.type === "checkbox")[0];
        showAssigned.checked = true;
        showAssigned.fire("change");
        const rows = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("binding-device-row"));
        const trv = rows.find((row) => textOf(row).includes("Батарея детской"));
        const ac = rows.find((row) => textOf(row).includes("Кондиционер"));
        if (!trv || !ac || !textOf(panel.shadowRoot).includes("Устройства в комнатах")) {
          throw new Error("assigned device inventory is missing");
        }
        const trvRoom = findAll(trv, (node) => node.tagName === "SELECT")[0];
        const acRoom = findAll(ac, (node) => node.tagName === "SELECT")[0];
        if (trvRoom.value !== "kids" || acRoom.value !== "living") {
          throw new Error("current Home Assistant areas are not selected");
        }
        trvRoom.value = "living";
        trvRoom.fire("change");
        const refreshedRows = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("binding-device-row"));
        const refreshedAc = refreshedRows.find((row) => textOf(row).includes("Кондиционер"));
        const refreshedAcRoom = findAll(refreshedAc, (node) => node.tagName === "SELECT")[0];
        refreshedAcRoom.value = "";
        refreshedAcRoom.fire("change");
        const save = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить привязки в Home Assistant")[0];
        if (save.disabled || !textOf(panel.shadowRoot).includes("Подготовлено изменений: 2")) {
          throw new Error("move and clear changes were not retained");
        }
        save.fire("click");
        await tick(4);
        const request = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/device-area-assignments");
        const byCandidate = Object.fromEntries(request.payload.assignments.map(
          (assignment) => [assignment.candidate_ids[0], assignment.room_id]
        ));
        if (byCandidate.candidate_trv !== "living" || byCandidate.candidate_ac !== "") {
          throw new Error("move/clear area payload mismatch: " + JSON.stringify(request));
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_binding_step_protects_unavailable_configured_device(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        configured = next(
            device for device in options["devices"]
            if device["candidate_id"] == "candidate_ac"
        )
        configured["configured"] = True
        configured["status"] = "unavailable"
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        const showLabel = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("checkbox-field")
          && textOf(node).includes("Показать устройства, уже привязанные"))[0];
        const showAssigned = findAll(showLabel, (node) => node.type === "checkbox")[0];
        showAssigned.checked = true;
        showAssigned.fire("change");
        const row = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("binding-device-row"))
          .find((node) => textOf(node).includes("Кондиционер"));
        const picker = findAll(row, (node) => node.tagName === "SELECT")[0];
        if (!picker.disabled || !textOf(row).includes("уже используется контуром")) {
          throw new Error("configured unavailable device is not protected");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_binding_step_disambiguates_same_name_yandex_virtual_devices(self) -> None:
        options = roomless_options()
        options["devices"] = [
            {
                "candidate_id": "candidate_0101",
                "candidate_key": "ckey_11111111a1b2",
                "name": "Кондиционер",
                "room_id": "",
                "suggested_types": ["air_conditioner"],
                "recommended_type": "air_conditioner",
                "status": "available",
                "suggested_room_id": None,
                "suggested_room_name": None,
                "reason": "unassigned_room",
                "can_add": True,
                "device_group_id": "device_1111111111111111",
                "device_name": "Кондиционер",
                "manufacturer": "Yandex",
                "model": "YNDX-0006",
            },
            {
                "candidate_id": "candidate_0102",
                "candidate_key": "ckey_22222222c3d4",
                "name": "Кондиционер",
                "room_id": "",
                "suggested_types": ["air_conditioner"],
                "recommended_type": "air_conditioner",
                "status": "unavailable",
                "suggested_room_id": None,
                "suggested_room_name": None,
                "reason": "unassigned_room",
                "can_add": True,
                "device_group_id": "device_2222222222222222",
                "device_name": "Кондиционер",
                "manufacturer": "Yandex",
                "model": "YNDX-0006",
            },
        ]
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        const rows = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("binding-device-row"));
        if (rows.length !== 2) throw new Error("virtual entities were incorrectly merged");
        const rendered = rows.map((row) => textOf(row));
        if (!rendered.some((text) => text.includes("№ A1B2") && text.includes("Доступно"))) {
          throw new Error("available virtual entity lacks a stable public identity");
        }
        if (!rendered.some((text) => text.includes("№ C3D4") && text.includes("Недоступно"))) {
          throw new Error("unavailable virtual entity lacks a stable public identity");
        }
        if (!rendered.every((text) => text.includes("Виртуальное устройство Яндекса"))) {
          throw new Error("virtual provider is not explained");
        }
        if (!rendered.some((text) => text.includes("устаревшей виртуальной сущностью"))) {
          throw new Error("stale duplicate guidance is missing");
        }
        const full = textOf(panel.shadowRoot);
        if (full.includes("climate.konditsioner") || full.includes("source_id")
          || full.includes("entity_id")) {
          throw new Error("private HA binding leaked into the wizard");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_roomless_devices_require_home_assistant_assignment_first(self) -> None:
        script = panel_script(
            get_payloads(options=roomless_options()),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.include.checked = true;
        panel._firstRunFields.rooms.living.include.fire("change");
        panel._firstRunFields.rooms.living.configure.fire("click");
        if (!textOf(panel.shadowRoot).includes("Устройства без комнаты")) {
          throw new Error("roomless device group is missing");
        }
        if (!textOf(panel.shadowRoot).includes("сохраните область устройства в Home Assistant")) {
          throw new Error("roomless note does not explain the HA source of truth");
        }
        const entry = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_smartir:air_conditioner");
        if (!entry) throw new Error("roomless candidate checkbox missing");
        if (entry.checkbox.checked) {
          throw new Error("roomless candidate must stay unselected by default");
        }
        if (!entry.checkbox.disabled) {
          throw new Error("roomless candidate can bypass the HA area registry");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_failed_home_assistant_assignment_preserves_the_draft(self) -> None:
        script = panel_script(
            get_payloads(options=roomless_options()),
            {"hausman_hub/v1/admin/device-area-assignments": {"__fail": 500}},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        const row = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("binding-device-row"))
          .find((node) => textOf(node).includes("Komanchi Living SmartIR"));
        const picker = findAll(row, (node) => node.tagName === "SELECT")[0];
        picker.value = "living";
        picker.fire("change");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить привязки в Home Assistant")[0].fire("click");
        await tick(3);
        const preservedAssignments = Object.values(panel._firstRun.areaAssignments);
        if (preservedAssignments.length !== 1 || preservedAssignments[0] !== "living") {
          throw new Error("failed save discarded the assignment draft");
        }
        if (!textOf(panel.shadowRoot).includes("Выбор не потерян")) {
          throw new Error("failed save did not explain that the draft is preserved");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_ir_remote_without_facade_shows_smartir_hint(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["devices"] = [
            device for device in options["devices"]
            if device["candidate_id"] != "candidate_ac"
        ]
        options["ir_remotes"] = [
            {"name": "Пульт гостиной", "room_id": "living", "available": True},
        ]
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const text = textOf(panel.shadowRoot);
        if (!text.includes("SmartIR climate")
          || !text.includes("«Пульт гостиной»")) {
          throw new Error("SmartIR facade guidance is missing");
        }
        panel._firstRunBackToRooms();
        panel._firstRunFields.rooms.kids.configure.fire("click");
        if (textOf(panel.shadowRoot).includes("SmartIR climate")) {
          throw new Error("hint leaked into a room without an IR remote");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_climate_facade_suppresses_smartir_hint_and_copy_is_honest(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["ir_remotes"] = [
            {"name": "Пульт гостиной", "room_id": "living", "available": True},
        ]
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const text = textOf(panel.shadowRoot);
        if (text.includes("climate-обёртку SmartIR с готовым кодом")) {
          throw new Error("hint shown although a climate facade exists");
        }
        if (!text.includes("Канал управления определяет")) {
          throw new Error("channel copy does not describe the honest transport");
        }
        if (text.includes("остаются в наблюдении")) {
          throw new Error("stale channel copy survived");
        }
        if (text.includes("Устройства без комнаты")) {
          throw new Error("roomless group rendered without roomless candidates");
        }
        if (findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("wizard-warning")).length) {
          throw new Error("roomless warning rendered without roomless candidates");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_not_configured_shows_first_run_only_and_defer_stays_in_memory(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        if (!panel._shell.nav || panel._shell.nav.hidden !== true) {
          throw new Error("first-run did not hide the tab navigation");
        }
        if (!panel._shell.wizard || panel._shell.wizard.hidden !== false) {
          throw new Error("first-run wizard is not visible");
        }
        if (panel._activeSection !== "overview") {
          throw new Error("first-run exposed an ordinary section");
        }
        const firstRunText = textOf(panel.shadowRoot);
        if (!firstRunText.includes("Климатический контур ещё не настроен")) {
          throw new Error("first-run reason is not explicit");
        }
        if (!firstRunText.includes("0 комнат · 0 устройств")) {
          throw new Error("first-run saved configuration count is missing");
        }
        if (!firstRunText.includes("не найден сохранённый климатический контур")) {
          throw new Error("first-run storage explanation is missing");
        }
        if (!firstRunText.includes("проверьте резервную копию Home Assistant")) {
          throw new Error("first-run recovery guidance is missing");
        }
        const progress = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("wizard-progress"));
        if (progress.length) throw new Error("welcome screen must not masquerade as a setup step");
        const getCount = calls.filter((call) => call.method === "GET").length;
        const later = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Настроить позже")[0];
        if (!later) throw new Error("defer action missing");
        later.fire("click");
        if (panel._firstRun.deferred !== true || panel._shell.wizard.hidden !== true) {
          throw new Error("defer did not hide the first-run wizard");
        }
        if (panel._shell.nav.hidden !== true || panel._shell.sectionNodes.overview.hidden) {
          throw new Error("defer did not show a read-only overview");
        }
        if (calls.filter((call) => call.method === "GET").length !== getCount) {
          throw new Error("defer unexpectedly called an API");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_room_check_gates_progress_and_shows_per_room_report(self) -> None:
        checked_draft = draft_for(
            [{
                "id": "living",
                "name": "Гостиная",
                "targets": {
                    "target_temperature": 22,
                    "target_humidity": 45,
                    "strategy": "normal",
                },
                "devices": [{
                    "candidate_id": "candidate_ac",
                    "name": "Кондиционер",
                    "type": "air_conditioner",
                    "type_name": "Кондиционер",
                }],
            }]
        )
        blocked = {
            "status": "blocked",
            "save_allowed": False,
            "issues": [{
                "code": "no_controllable_device",
                "room_id": "living",
                "message": "В комнате нет устройства управления",
            }],
            "summary": checked_draft["summary"],
        }
        ready_with_warning = ready_validation(checked_draft)
        ready_with_warning["issues"] = [{
            "code": "device_unavailable",
            "level": "warning",
            "room_id": "living",
            "message": "Устройство временно недоступно и будет применено после восстановления связи.",
        }]
        script = panel_script(
            get_payloads(),
            {
                "hausman_hub/v1/admin/climate-drafts": [checked_draft, checked_draft],
                "hausman_hub/v1/admin/climate-drafts/validate": [blocked, ready_with_warning],
            },
            """
        const start = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Начать настройку")[0];
        start.fire("click");
        await tick();
        const finish = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Завершить настройку")[0];
        if (!finish || finish.disabled !== true || !finish.title) {
          throw new Error("finish must explain the zero-valid-room gate");
        }
        panel._firstRunFields.rooms.living.include.checked = true;
        panel._firstRunFields.rooms.living.include.fire("change");
        panel._firstRunFields.rooms.living.configure.fire("click");
        const airConditioner = panel._firstRunFields.room.devices.find((item) =>
          item.type === "air_conditioner");
        airConditioner.checkbox.checked = true;
        airConditioner.checkbox.fire("change");
        ["temperature_sensor", "humidity_sensor"].forEach((type) => {
          const source = panel._firstRunFields.room.devices.find((item) => item.type === type);
          source.checkbox.checked = true;
          source.checkbox.fire("change");
        });
        panel._activeRoomSetupPane = "review";
        panel._render();
        const check = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Проверить комнату")[0];
        check.fire("click");
        await tick();
        if (!textOf(panel.shadowRoot).includes("В комнате нет устройства управления")) {
          throw new Error("room validation report is missing");
        }
        if (panel._firstRun.validRooms.has("living")) {
          throw new Error("blocked room became configured");
        }
        if (findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Завершить").length) {
          throw new Error("blocked room exposed the finish action");
        }
        findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Проверить комнату")[0].fire("click");
        await tick();
        if (!panel._firstRun.validRooms.has("living")) {
          throw new Error("checked room was not retained as configured");
        }
        const roomFinish = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Завершить")[0];
        if (!roomFinish || roomFinish.disabled) {
          throw new Error("ready room with a warning did not expose the finish action");
        }
        roomFinish.fire("click");
        if (panel._firstRun.step !== "rooms" || panel._firstRun.roomId !== null) {
          throw new Error("room finish did not return to the room settings overview");
        }
        const enabledFinish = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Завершить настройку")[0];
        if (!enabledFinish || enabledFinish.disabled) {
          throw new Error("one checked room did not open progress");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_room_check_requires_one_main_temperature_and_humidity_source(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["devices"].append(
            {
                "candidate_id": "candidate_temp_extra",
                "candidate_key": "candidate_temp_extra",
                "name": "Резервная температура гостиной",
                "room_id": "living",
                "suggested_types": ["temperature_sensor"],
                "recommended_type": "temperature_sensor",
                "status": "available",
                "suggested_room_id": "living",
                "suggested_room_name": "Гостиная",
                "reason": "detected_room",
                "can_add": True,
            }
        )
        script = panel_script(
            get_payloads(options=options),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.include.checked = true;
        panel._firstRunFields.rooms.living.include.fire("change");
        panel._firstRunFields.rooms.living.configure.fire("click");
        const airConditioner = panel._firstRunFields.room.devices.find((item) =>
          item.type === "air_conditioner");
        airConditioner.checkbox.checked = true;
        airConditioner.checkbox.fire("change");
        let collected = panel._firstRunPayload(["living"]);
        if (!collected.error || !collected.error.includes("главный датчик температуры")) {
          throw new Error("room check accepted a room without the main temperature source");
        }
        const temperature = panel._firstRunFields.room.devices.find((item) =>
          item.type === "temperature_sensor");
        temperature.checkbox.checked = true;
        temperature.checkbox.fire("change");
        const alternativeTemperature = panel._firstRunFields.room.devices.find((item) =>
          item.key === "candidate_temp_extra:temperature_sensor");
        alternativeTemperature.checkbox.checked = true;
        alternativeTemperature.checkbox.fire("change");
        if (temperature.checkbox.checked
          || panel._firstRun.rooms.living.devices[temperature.key].selected) {
          throw new Error("selecting another main temperature source kept the old source active");
        }
        collected = panel._firstRunPayload(["living"]);
        if (!collected.error || !collected.error.includes("главный датчик влажности")) {
          throw new Error("room check accepted a room without the main humidity source");
        }
        const humidity = panel._firstRunFields.room.devices.find((item) =>
          item.type === "humidity_sensor");
        humidity.checkbox.checked = true;
        humidity.checkbox.fire("change");
        collected = panel._firstRunPayload(["living"]);
        if (!collected.payload || collected.payload.rooms[0].devices.length !== 3) {
          throw new Error("room check rejected the complete climate source selection");
        }
        if (calls.some((call) => call.method === "POST")) {
          throw new Error("local required-source checks unexpectedly sent commands");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_room_editor_is_split_into_clear_stable_steps(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const steps = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && String(node.className).split(" ").includes("room-setup-step"));
        if (steps.length !== 5) throw new Error("room setup must have five short steps");
        for (const label of ["Комфорт", "Устройства", "Режим дня", "Защита", "Проверка"]) {
          if (!steps.some((node) => textOf(node).includes(label))) {
            throw new Error("room setup step missing: " + label);
          }
        }
        let sections = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("wizard-section"));
        if (sections.length !== 1 || !textOf(sections[0]).includes("Главные показания комнаты")
          || !textOf(sections[0]).includes("Устройства управления")) {
          throw new Error("device page is not the focused default");
        }
        const livingState = panel._firstRun.rooms.living;
        const initialTemperature = livingState.day.temperature;
        steps.find((node) => textOf(node).includes("Комфорт")).fire("click");
        sections = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("wizard-section"));
        if (sections.length !== 1 || !textOf(sections[0]).includes("Дневной и ночной профиль")) {
          throw new Error("comfort page did not replace the long room form");
        }
        const refreshedSteps = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && String(node.className).split(" ").includes("room-setup-step"));
        refreshedSteps.find((node) => textOf(node).includes("Защита")).fire("click");
        sections = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("wizard-section"));
        if (sections.length !== 1 || !textOf(sections[0]).includes("не являются целевой температурой")) {
          throw new Error("protection explanation is missing");
        }
        if (livingState.day.temperature !== initialTemperature) {
          throw new Error("room selection changed while switching pages");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_checking_second_room_auto_includes_it_and_keeps_first_room_valid(self) -> None:
        kids_draft = draft_for(
            [{
                "id": "kids",
                "name": "Детская",
                "targets": {
                    "target_temperature": 25,
                    "target_humidity": 53,
                    "strategy": "normal",
                },
                "devices": [{
                    "candidate_id": "candidate_trv",
                    "name": "Батарея детской",
                    "type": "radiator_thermostat",
                    "type_name": "Радиаторный термостат",
                }],
            }]
        )
        living_draft = draft_for(
            [{
                "id": "living",
                "name": "Гостиная",
                "targets": {
                    "target_temperature": 25,
                    "target_humidity": 53,
                    "strategy": "normal",
                },
                "devices": [{
                    "candidate_id": "candidate_ac",
                    "name": "Кондиционер",
                    "type": "air_conditioner",
                    "type_name": "Кондиционер",
                }],
            }]
        )
        script = panel_script(
            get_payloads(),
            {
                "hausman_hub/v1/admin/climate-drafts": [kids_draft, living_draft],
                "hausman_hub/v1/admin/climate-drafts/validate": [
                    ready_validation(kids_draft),
                    ready_validation(living_draft),
                ],
            },
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();

        panel._firstRunFields.rooms.kids.include.checked = true;
        panel._firstRunFields.rooms.kids.include.fire("change");
        panel._firstRunFields.rooms.kids.configure.fire("click");
        const kidsDevice = panel._firstRunFields.room.devices.find((item) =>
          item.type === "radiator_thermostat");
        kidsDevice.checkbox.checked = true;
        kidsDevice.checkbox.fire("change");
        ["temperature_sensor", "humidity_sensor"].forEach((type) => {
          const source = panel._firstRunFields.room.devices.find((item) => item.type === type);
          source.checkbox.checked = true;
          source.checkbox.fire("change");
        });
        panel._activeRoomSetupPane = "review";
        panel._render();
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить комнату")[0].fire("click");
        await tick();
        if (!panel._firstRun.validRooms.has("kids")) {
          throw new Error("first room did not remain configured");
        }

        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Назад к списку комнат")[0].fire("click");
        if (panel._firstRun.rooms.living.included) {
          throw new Error("living room unexpectedly started included");
        }
        panel._firstRunFields.rooms.living.configure.fire("click");
        const livingDevice = panel._firstRunFields.room.devices.find((item) =>
          item.type === "air_conditioner");
        livingDevice.checkbox.checked = true;
        livingDevice.checkbox.fire("change");
        ["temperature_sensor", "humidity_sensor"].forEach((type) => {
          const source = panel._firstRunFields.room.devices.find((item) => item.type === type);
          source.checkbox.checked = true;
          source.checkbox.fire("change");
        });
        panel._activeRoomSetupPane = "review";
        panel._render();
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить комнату")[0].fire("click");
        await tick();

        if (!panel._firstRun.rooms.living.included
          || !panel._firstRun.validRooms.has("living")
          || !panel._firstRun.validRooms.has("kids")) {
          throw new Error("checking the second room did not include it independently");
        }
        const drafts = calls.filter((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/climate-drafts");
        if (drafts.length !== 2
          || drafts[1].payload.rooms.length !== 1
          || drafts[1].payload.rooms[0].room_id !== "living") {
          throw new Error("second room check sent an invalid room draft");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_final_save_preserves_optional_fields_and_returns_to_tabs(self) -> None:
        draft = draft_for(
            [{
                "id": "living",
                "name": "Гостиная",
                "targets": {
                    "target_temperature": 22,
                    "target_humidity": 45,
                    "strategy": "normal",
                },
                "devices": [
                    {
                        "candidate_id": "candidate_ac",
                        "name": "Кондиционер",
                        "type": "air_conditioner",
                        "type_name": "Кондиционер",
                        "control_channel": "direct_wifi",
                    },
                    {
                        "candidate_id": "candidate_temp_1",
                        "name": "Температура у окна",
                        "type": "temperature_sensor",
                        "type_name": "Датчик температуры",
                    },
                    {
                        "candidate_id": "candidate_humidity",
                        "name": "Влажность гостиной",
                        "type": "humidity_sensor",
                        "type_name": "Датчик влажности",
                    },
                ],
                "min_temperature": 20,
                "max_temperature": 26,
            }]
        )
        expected_request = {
            "snapshot_revision": 77,
            "setup_revision": 5,
            "name": "Климат",
            "mode": "automatic",
             "rooms": [{
                "room_id": "living",
                "target_temperature": 25,
                "target_humidity": 53,
                "strategy": "normal",
                "min_temperature": 20,
                "max_temperature": 26,
                "devices": [
                    {
                        "candidate_id": "candidate_ac",
                        "type": "air_conditioner",
                        "control_channel": "direct_wifi",
                    },
                    {"candidate_id": "candidate_temp_1", "type": "temperature_sensor"},
                    {"candidate_id": "candidate_humidity", "type": "humidity_sensor"},
                ],
            }],
        }
        script = panel_script(
            get_payloads(),
            {
                "hausman_hub/v1/admin/home-environment": HOME_PAYLOAD,
                "hausman_hub/v1/admin/climate-drafts": [draft, draft],
                "hausman_hub/v1/admin/climate-drafts/validate": [ready_validation(draft), ready_validation(draft)],
                "hausman_hub/v1/admin/climate-drafts/save": {
                    "status": "saved", "commands_sent": False, "restart_required": False,
                },
            },
            f"""
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.include.checked = true;
        panel._firstRunFields.rooms.living.include.fire("change");
        panel._firstRunFields.rooms.living.configure.fire("click");
        const fields = panel._firstRunFields.room;
        fields.devices.forEach((device) => {{
          device.checkbox.checked = true;
          device.checkbox.fire("change");
        }});
        fields.minTemperature.value = "20";
        fields.minTemperature.fire("input");
        fields.maxTemperature.value = "26";
        fields.maxTemperature.fire("input");
        const airConditioner = fields.devices.find((device) =>
          device.type === "air_conditioner");
        airConditioner.controlChannel.value = "direct_wifi";
        airConditioner.controlChannel.fire("change");
        panel._activeRoomSetupPane = "review";
        panel._render();
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить комнату")[0].fire("click");
        await tick();
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Назад к списку комнат")[0].fire("click");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Завершить настройку")[0].fire("click");
        const saveHome = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Продолжить к проверке")[0];
        if (!saveHome) throw new Error("home step did not render a continue action");
        saveHome.fire("click");
        await tick();
        const validate = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить настройку")[0];
        if (!validate) throw new Error("home step did not advance to validation");
        validate.fire("click");
        await tick();
        const saveStep = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Перейти к сохранению")[0];
        if (!saveStep) throw new Error("ready validation did not open the save step");
        saveStep.fire("click");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить настройку")[0].fire("click");
        await tick(12);
        const finishTablet = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Перейти к завершению")[0];
        if (!finishTablet) throw new Error("save did not open the tablet step");
        finishTablet.fire("click");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Открыть панель")[0].fire("click");
        const created = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/climate-drafts");
        const saved = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/climate-drafts/save");
        const expected = {json.dumps(expected_request, ensure_ascii=False)};
        const expectedDraft = {json.dumps(draft, ensure_ascii=False)};
        if (!created || JSON.stringify(created.payload) !== JSON.stringify(expected)) {{
          throw new Error("first-run create payload mismatch: "
            + JSON.stringify(created && created.payload));
        }}
        if (!saved || JSON.stringify(saved.payload) !== JSON.stringify(expectedDraft)) {{
          throw new Error("first-run save did not receive the exact validated draft");
        }}
        if (panel._shell.nav.hidden || !panel._shell.wizard.hidden || panel._activeSection !== "overview") {{
          throw new Error("successful first-run save did not return to normal tabs");
        }}
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_final_save_conflict_keeps_first_run_and_offers_reload(self) -> None:
        script = panel_script(
            get_payloads(),
            {"hausman_hub/v1/admin/climate-drafts/save": {"__fail": 409}},
            """
        panel._firstRun.step = "completion";
        panel._firstRun.options = {rooms: [], devices: [], control_channels: []};
        panel._firstRun.draft = {status: "created"};
        panel._firstRun.validation = {status: "ready", save_allowed: true};
        panel._render();
        const save = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить настройку")[0];
        save.fire("click");
        await tick();
        if (!textOf(panel.shadowRoot).includes("изменились в другом окне")) {
          throw new Error("first-run conflict explanation missing");
        }
        const reload = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Обновить мастер")[0];
        if (!reload || panel._shell.wizard.hidden) {
          throw new Error("conflict did not retain a reloadable first-run wizard");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_blocked_creation_explains_missing_rooms_and_refreshes_options(self) -> None:
        blocked = copy.deepcopy(DRAFT_OPTIONS)
        blocked["draft_creation_allowed"] = False
        blocked["rooms"] = []
        for candidate in blocked["devices"]:
            candidate["can_add"] = False
            candidate["suggested_room_id"] = None
            candidate["suggested_room_name"] = None
            candidate["reason"] = "room_unavailable"
        script = panel_script(
            get_payloads(options=blocked),
            {},
            """
        panel._firstRun.completed = true;
        panel._openWizard(panel._settings.setup);
        await tick();
        let text = textOf(panel.shadowRoot);
        if (!text.includes("не найдены зоны (комнаты)")) {
          throw new Error("missing-room explanation absent");
        }
        let buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const check = buttons.find((node) => node.textContent === "Проверить контур");
        const save = buttons.find((node) => node.textContent === "Сохранить контур");
        const refresh = buttons.find((node) => node.textContent === "Обновить комнаты и устройства");
        if (!check || check.disabled !== true) throw new Error("blocked check must be disabled");
        if (!save || save.disabled !== true || !save.title) {
          throw new Error("save prerequisite must be explicit");
        }
        if (!refresh || refresh.disabled) throw new Error("working refresh action missing");
        getTable["hausman_hub/v1/admin/climate-drafts"] = """
            + json.dumps(DRAFT_OPTIONS, ensure_ascii=False)
            + """;
        refresh.fire("click");
        await tick();
        if (!panel._wizardFields.rooms.living || !panel._wizardFields.rooms.kids) {
          throw new Error("refreshed rooms were not rendered");
        }
        buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const refreshedCheck = buttons.find((node) => node.textContent === "Проверить контур");
        if (!refreshedCheck || refreshedCheck.disabled) {
          throw new Error("check stayed disabled after fresh options");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_happy_path_posts_exact_request_validates_and_saves_exact_draft(self) -> None:
        living_devices = [
            {"candidate_id": "candidate_ac", "name": "Кондиционер", "type": "air_conditioner", "type_name": "Кондиционер"},
            {"candidate_id": "candidate_temp_1", "name": "Температура у окна", "type": "temperature_sensor", "type_name": "Датчик температуры"},
            {"candidate_id": "candidate_temp_2", "name": "Температура у двери", "type": "temperature_sensor", "type_name": "Датчик температуры"},
            {"candidate_id": "candidate_humidity", "name": "Влажность гостиной", "type": "humidity_sensor", "type_name": "Датчик влажности"},
        ]
        kids_devices = [
            {"candidate_id": "candidate_trv", "name": "Батарея детской", "type": "radiator_thermostat", "type_name": "Радиаторный термостат"},
            {"candidate_id": "candidate_kids_temp", "name": "Температура детской", "type": "temperature_sensor", "type_name": "Датчик температуры"},
            {"candidate_id": "candidate_kids_humidity", "name": "Влажность детской", "type": "humidity_sensor", "type_name": "Датчик влажности"},
        ]
        draft = draft_for(
            [
                {"id": "kids", "name": "Детская", "targets": {"target_temperature": 20.5, "target_humidity": 40, "strategy": "soft"}, "devices": kids_devices},
                {"id": "living", "name": "Гостиная", "targets": {"target_temperature": 24, "target_humidity": 50, "strategy": "aggressive"}, "devices": living_devices},
            ]
        )
        post_table = {
            "hausman_hub/v1/admin/climate-drafts": draft,
            "hausman_hub/v1/admin/climate-drafts/validate": ready_validation(draft),
            "hausman_hub/v1/admin/climate-drafts/save": {
                "status": "saved", "commands_sent": False, "restart_required": False
            },
        }
        expected_request = {
            "snapshot_revision": 77,
            "setup_revision": 5,
            "name": "Дом",
            "mode": "automatic",
            "rooms": [
                {
                    "room_id": "living", "target_temperature": 24, "target_humidity": 50,
                    "strategy": "aggressive",
                    "devices": [
                        {"candidate_id": "candidate_ac", "type": "air_conditioner"},
                        {"candidate_id": "candidate_temp_1", "type": "temperature_sensor"},
                        {"candidate_id": "candidate_temp_2", "type": "temperature_sensor"},
                        {"candidate_id": "candidate_humidity", "type": "humidity_sensor"},
                    ],
                },
                {
                    "room_id": "kids", "target_temperature": 20.5, "target_humidity": 40,
                    "strategy": "soft",
                    "devices": [
                        {"candidate_id": "candidate_trv", "type": "radiator_thermostat"},
                        {"candidate_id": "candidate_kids_temp", "type": "temperature_sensor"},
                        {"candidate_id": "candidate_kids_humidity", "type": "humidity_sensor"},
                    ],
                },
            ],
        }
        script = panel_script(
            get_payloads(),
            post_table,
            f"""
        panel._firstRun.completed = true;
        panel._openWizard(panel._settings.setup);
        await tick();
        const fields = panel._wizardFields;
        fields.name.value = "Дом";
        fields.name.fire("input");
        fields.mode.value = "automatic";
        fields.mode.fire("change");
        fields.rooms.living.temperature.value = "24";
        fields.rooms.living.temperature.fire("input");
        fields.rooms.living.humidity.value = "50";
        fields.rooms.living.humidity.fire("input");
        fields.rooms.living.strategy.value = "aggressive";
        fields.rooms.living.strategy.fire("change");
        fields.rooms.kids.temperature.value = "20.5";
        fields.rooms.kids.temperature.fire("input");
        fields.rooms.kids.humidity.value = "40";
        fields.rooms.kids.humidity.fire("input");
        fields.rooms.kids.strategy.value = "soft";
        fields.rooms.kids.strategy.fire("change");
        const check = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Проверить контур");
        check.fire("click");
        await tick();
        const create = calls.find((call) => call.method === "POST" && call.path === "hausman_hub/v1/admin/climate-drafts");
        const expected = {json.dumps(expected_request, ensure_ascii=False)};
        if (!create || JSON.stringify(create.payload) !== JSON.stringify(expected)) {{
          throw new Error("create payload mismatch: " + JSON.stringify(create && create.payload));
        }}
        const validation = calls.find((call) => call.method === "POST" && call.path.endsWith("/validate"));
        const expectedDraft = {json.dumps(draft, ensure_ascii=False)};
        if (!validation || JSON.stringify(validation.payload) !== JSON.stringify(expectedDraft)) {{
          throw new Error("validation did not receive exact draft");
        }}
        const save = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Сохранить контур");
        if (!save || save.disabled) throw new Error("ready draft did not enable save");
        save.fire("click");
        await tick(12);
        const saved = calls.find((call) => call.method === "POST" && call.path.endsWith("/save"));
        if (!saved || JSON.stringify(saved.payload) !== JSON.stringify(expectedDraft)) {{
          throw new Error("save did not receive exact draft");
        }}
        if (!textOf(panel.shadowRoot).includes("Контур сохранён. Команды устройствам не отправлялись.")) {{
          throw new Error("truthful save notice missing");
        }}
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_blocked_issue_is_grouped_by_room_then_active_device_allows_ready(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["rooms"] = [options["rooms"][0]]
        options["devices"] = [options["devices"][0], options["devices"][1]]
        options["devices"][0]["suggested_room_id"] = None
        options["devices"][0]["suggested_room_name"] = None
        sensor_device = [
            {"candidate_id": "candidate_temp_1", "name": "Температура у окна", "type": "temperature_sensor", "type_name": "Датчик температуры"}
        ]
        active_device = [
            {"candidate_id": "candidate_ac", "name": "Кондиционер", "type": "air_conditioner", "type_name": "Кондиционер"},
            *sensor_device,
        ]
        blocked_draft = draft_for(
            [{"id": "living", "name": "Гостиная", "targets": {"target_temperature": 22, "target_humidity": 45, "strategy": "normal"}, "devices": sensor_device}],
            name="Климат",
            mode="observe",
        )
        ready_draft = draft_for(
            [{"id": "living", "name": "Гостиная", "targets": {"target_temperature": 22, "target_humidity": 45, "strategy": "normal"}, "devices": active_device}],
            name="Климат",
            mode="observe",
        )
        blocked = {
            "status": "blocked", "save_allowed": False, "command_allowed": False,
            "issues": [{"code": "no_controllable_device", "room_id": "living", "message": "В комнате нет устройства управления"}],
            "summary": blocked_draft["summary"],
        }
        script = panel_script(
            get_payloads(options=options),
            {
                "hausman_hub/v1/admin/climate-drafts": [blocked_draft, ready_draft],
                "hausman_hub/v1/admin/climate-drafts/validate": [blocked, ready_validation(ready_draft)],
            },
            """
        panel._firstRun.completed = true;
        panel._openWizard(panel._settings.setup);
        await tick();
        const check = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Проверить контур");
        check.fire("click");
        await tick();
        if (!textOf(panel._wizardIssues.rooms.living).includes("В комнате нет устройства управления")) {
          throw new Error("room issue missing");
        }
        if (!panel._wizardButtons.save.disabled) throw new Error("blocked draft enabled save");
        const active = panel._wizardFields.rooms.living.devices
          .find((choice) => choice.type === "air_conditioner");
        active.checkbox.checked = true;
        active.checkbox.fire("change");
        if (textOf(panel._wizardIssues.rooms.living).includes("нет устройства")) {
          throw new Error("stale issue survived form edit");
        }
        check.fire("click");
        await tick();
        if (panel._wizardButtons.save.disabled) throw new Error("ready validation did not enable save");
        if (!textOf(panel.shadowRoot).includes("Контур проверен. Можно сохранять.")) {
          throw new Error("ready message missing");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_invalid_comfort_steps_are_rejected_before_any_draft_post(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        panel._firstRun.completed = true;
        panel._openWizard(panel._settings.setup);
        await tick();
        const living = panel._wizardFields.rooms.living;
        const check = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Проверить контур");
        living.temperature.value = "17.5";
        living.temperature.fire("input");
        check.fire("click");
        await tick();
        if (!textOf(panel.shadowRoot).includes("18-28 °C")) {
          throw new Error("temperature contract hint missing");
        }
        if (panel._activeSection !== "climate" || living.editor.hidden || !living.temperature.focused) {
          throw new Error("temperature error did not reveal and focus its room");
        }
        living.temperature.value = "22";
        living.temperature.fire("input");
        living.humidity.value = "41.5";
        living.humidity.fire("input");
        check.fire("click");
        await tick();
        if (!textOf(panel.shadowRoot).includes("шаг 1 %")) {
          throw new Error("humidity contract hint missing");
        }
        if (!living.humidity.focused) throw new Error("humidity error field was not focused");
        if (calls.some((call) => call.method === "POST")) {
          throw new Error("invalid comfort values reached backend");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_edit_mode_fetches_lazily_and_prefills_existing_multiple_sensors(self) -> None:
        options = copy.deepcopy(DRAFT_OPTIONS)
        options["draft_creation_allowed"] = False
        options["rooms"] = [options["rooms"][0]]
        options["devices"] = options["devices"][:4]
        for candidate in options["devices"]:
            candidate["status"] = "already_configured"
            candidate["can_add"] = False
        panel_payload = copy.deepcopy(PANEL_PAYLOAD)
        panel_payload["snapshot"] = {
            "display_names": {},
            "rooms": [{"id": "living", "name": "Гостиная", "temperature": 23.5, "humidity": 46, "target_temperature": 24.5, "mode": "automatic", "actual": {"data_status": "current"}, "devices": []}],
            "contours": [],
        }
        windows = {"rooms": [{"id": "living", "name": "Гостиная", "window_entity_id": None}], "candidates": []}
        configured_setup = copy.deepcopy(CONFIGURED_SETUP)
        configured_setup["rooms"][0]["profiles"]["active_profile"] = "night"
        script = panel_script(
            get_payloads(setup=configured_setup, options=options, panel=panel_payload, windows=windows),
            {},
            """
        if (calls.some((call) => call.method === "GET" && call.path === "hausman_hub/v1/admin/climate-drafts")) {
          throw new Error("configured summary fetched options eagerly");
        }
        const initial = textOf(panel.shadowRoot);
        const order = [
          "Главная", "Освещение", "Климат", "Комнаты", "Медиа",
          "Безопасность", "Устройства", "Сценарии", "Настройки",
        ];
        let cursor = -1;
        order.forEach((heading) => {
          const next = initial.indexOf(heading, cursor + 1);
          if (next <= cursor) throw new Error("tab order broken at " + heading);
          cursor = next;
        });
        if (panel._activeSection !== "overview") throw new Error("configured default tab mismatch");
        const edit = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Изменить контур");
        edit.fire("click");
        await tick();
        const optionGets = calls.filter((call) => call.method === "GET" && call.path === "hausman_hub/v1/admin/climate-drafts");
        if (optionGets.length !== 1) throw new Error("edit did not fetch options exactly once");
        const fields = panel._wizardFields;
        if (fields.name.value !== "Домашний климат" || fields.mode.value !== "automatic") {
          throw new Error("contour values not prefilled");
        }
        const living = fields.rooms.living;
        if (!living.include.checked || String(living.temperature.value) !== "21" || String(living.humidity.value) !== "45") {
          throw new Error("active profile targets not prefilled");
        }
        const collected = panel._collectWizardPayload();
        if (collected.error || collected.payload.setup_revision !== 123) {
          throw new Error("edit setup revision missing");
        }
        const checkedSensors = living.devices.filter((choice) =>
          choice.type.endsWith("_sensor") && choice.checkbox.checked);
        if (checkedSensors.length !== 3) throw new Error("existing sensors not kept checkable");
        const checkedTemperature = checkedSensors.filter((choice) => choice.type === "temperature_sensor");
        if (checkedTemperature.length !== 2) throw new Error("multiple existing temperature sensors not prefilled");
        const rendered = textOf(panel.shadowRoot);
        if (rendered.includes("entity_id") || rendered.includes("source_id")) {
          throw new Error("private binding rendered in edit mode");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_save_conflict_shows_notice_and_reloads_current_setup(self) -> None:
        one_room_options = copy.deepcopy(DRAFT_OPTIONS)
        one_room_options["rooms"] = [one_room_options["rooms"][0]]
        one_room_options["devices"] = [one_room_options["devices"][0]]
        device = [
            {"candidate_id": "candidate_ac", "name": "Кондиционер", "type": "air_conditioner", "type_name": "Кондиционер"}
        ]
        draft = draft_for(
            [{"id": "living", "name": "Гостиная", "targets": {"target_temperature": 22, "target_humidity": 45, "strategy": "normal"}, "devices": device}],
            name="Климат",
            mode="observe",
        )
        script = panel_script(
            get_payloads(options=one_room_options),
            {
                "hausman_hub/v1/admin/climate-drafts": draft,
                "hausman_hub/v1/admin/climate-drafts/validate": ready_validation(draft),
                "hausman_hub/v1/admin/climate-drafts/save": {"__fail": 409},
            },
            """
        panel._firstRun.completed = true;
        panel._openWizard(panel._settings.setup);
        await tick();
        const check = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON")
          .find((node) => node.textContent === "Проверить контур");
        check.fire("click");
        await tick();
        panel._wizardButtons.save.fire("click");
        await tick(12);
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Состояние изменилось. Обновите данные")) throw new Error("conflict notice missing");
        const setupGets = calls.filter((call) =>
          call.method === "GET" && call.path === "hausman_hub/v1/admin/climate-drafts/current");
        if (setupGets.length < 2) throw new Error("current setup was not reloaded after conflict");
        const optionGets = calls.filter((call) =>
          call.method === "GET" && call.path === "hausman_hub/v1/admin/climate-drafts");
        if (optionGets.length < 2) throw new Error("wizard options were not reloaded after conflict");
        if (panel._dirty.wizard !== false) throw new Error("conflict left stale dirty form active");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_universal_ir_code_source_renders_priority_import_and_collapsed_broadlink(self) -> None:
        imported = {
            "codes": [
                {
                    "code_id": "living_air_conditioner_off",
                    "device_id": "living_air_conditioner",
                    "remote_entity_id": "remote.pult_broadlink_gostinnaya",
                    "command_name": "ac.off",
                    "code_data": "CODE_OFF",
                    "source": "manual",
                    "created_at": 1784280000,
                }
            ]
        }
        scan = {
            "smartir": {"1001": "Daikin FTX"},
            "broadlink_remotes": [],
            "smartir_catalog": [
                {
                    "brand": "Daikin",
                    "models": [
                        {
                            "device_code": "1001",
                            "model": "FTX",
                            "name": "Daikin FTX",
                            "commands": [
                                {"command_name": "ac.off", "code_data": "SMARTIR_OFF"}
                            ],
                        }
                    ],
                }
            ],
            "broadlink_catalog": [
                {
                    "remote_entity_id": "remote.other_broadlink",
                    "commands": [
                        {"command_name": "broadlink_only", "code_data": "BROADLINK_COOL", "slot": 0}
                    ],
                }
            ],
        }
        get_table = get_payloads(
            setup=universal_ir_setup(), bindings=universal_ir_bindings()
        )
        get_table.update(
            {
                "hausman_hub/v1/admin/ir-codes": imported,
                "hausman_hub/v1/admin/ir-codes/scan": scan,
            }
        )
        script = panel_script(
            get_table,
            {"hausman_hub/v1/admin/ir-codes": {"ok": True, "code_id": "new_code"}},
            """
        panel._firstRun.options = {rooms: [], devices: [], control_channels: []};
        panel._firstRun.step = "code_source";
        panel._firstRun.ir = {
          activeDeviceId: "living_air_conditioner", broadlinkExpanded: false,
          codes: getTable["hausman_hub/v1/admin/ir-codes"].codes,
          error: "", loading: false, manual: {deviceId: null, index: 0, statuses: {}},
          scan: getTable["hausman_hub/v1/admin/ir-codes/scan"],
          smartir: {brand: "", deviceCode: "", commandName: ""},
        };
        panel._render();
        const text = textOf(panel.shadowRoot);
        ["База кодов SmartIR", "Выученные коды Broadlink", "Ручное обучение"].reduce((cursor, title) => {
          const next = text.indexOf(title, cursor + 1);
          if (next <= cursor) throw new Error("IR source priority order mismatch at " + title);
          return next;
        }, -1);
        if (!text.includes("Пульт Broadlink гостиной")
          || !text.includes("Канал: universal_ir")
          || !text.includes("remote.pult_broadlink_gostinnaya")) {
          throw new Error("universal IR device summary is incomplete");
        }
        if (text.includes("broadlink_only")) {
          throw new Error("Broadlink commands must stay collapsed initially");
        }
        const showCommands = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Показать команды")[0];
        showCommands.fire("click");
        if (!textOf(panel.shadowRoot).includes("broadlink_only")) {
          throw new Error("Broadlink commands did not expand");
        }
        const smartirImport = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Импортировать")[0];
        smartirImport.fire("click");
        await tick();
        const importedCall = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/ir-codes");
        if (!importedCall || JSON.stringify(importedCall.payload) !== JSON.stringify({
          device_id: "living_air_conditioner",
          remote_entity_id: "remote.pult_broadlink_gostinnaya",
          command_name: "ac.off",
          code_data: "SMARTIR_OFF",
          source: "smartir",
        })) {
          throw new Error("SmartIR import payload mismatch: " + JSON.stringify(importedCall && importedCall.payload));
        }
        if (!textOf(panel.shadowRoot).includes("ir_command_not_learned")) {
          throw new Error("IR warning note is missing");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_manual_ir_learning_reports_timeout_then_progresses(self) -> None:
        get_table = get_payloads(
            setup=universal_ir_setup(), bindings=universal_ir_bindings()
        )
        get_table.update(
            {
                "hausman_hub/v1/admin/ir-codes": {"codes": []},
                "hausman_hub/v1/admin/ir-codes/scan": {
                    "smartir_catalog": [], "broadlink_catalog": [],
                },
            }
        )
        script = panel_script(
            get_table,
            {
                "hausman_hub/v1/admin/ir-codes/learn": [
                    {"__fail": 408}, {"ok": True, "code_id": "learned_off", "source": "manual"},
                ]
            },
            """
        panel._firstRun.options = {rooms: [], devices: [], control_channels: []};
        panel._firstRun.step = "code_source";
        panel._firstRun.ir = {
          activeDeviceId: "living_air_conditioner", broadlinkExpanded: false, codes: [], error: "",
          loading: false, manual: {deviceId: null, index: 0, statuses: {}},
          scan: getTable["hausman_hub/v1/admin/ir-codes/scan"],
          smartir: {brand: "", deviceCode: "", commandName: ""},
        };
        panel._render();
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать обучение")[0].fire("click");
        await tick();
        if (!textOf(panel.shadowRoot).includes("Время ожидания истекло")
          || !textOf(panel.shadowRoot).includes("Повторить обучение")) {
          throw new Error("manual learning timeout retry hint is missing");
        }
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Повторить обучение")[0].fire("click");
        await tick();
        const learns = calls.filter((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/ir-codes/learn");
        if (learns.length !== 2 || learns.some((call) => call.payload.command_name !== "ac.off")) {
          throw new Error("manual learning did not retry the first off command");
        }
        if (!textOf(panel.shadowRoot).includes("Готово")
          || !textOf(panel.shadowRoot).includes("Охлаждение · 25.0 °C")) {
          throw new Error("manual sequence did not retain progress and profile presets");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_imported_ir_codes_test_delete_and_non_ir_step_is_absent(self) -> None:
        imported = {
            "codes": [
                {
                    "code_id": "living_air_conditioner_off",
                    "device_id": "living_air_conditioner",
                    "remote_entity_id": "remote.pult_broadlink_gostinnaya",
                    "command_name": "ac.off",
                    "code_data": "CODE_OFF",
                    "source": "manual",
                    "created_at": 1784280000,
                }
            ]
        }
        get_table = get_payloads(
            setup=universal_ir_setup(), bindings=universal_ir_bindings()
        )
        get_table.update(
            {
                "hausman_hub/v1/admin/ir-codes": imported,
                "hausman_hub/v1/admin/ir-codes/scan": {"smartir_catalog": [], "broadlink_catalog": []},
            }
        )
        script = panel_script(
            get_table,
            {
                "hausman_hub/v1/admin/ir-codes/test": {"ok": True},
                "hausman_hub/v1/admin/ir-codes/delete": {"ok": True},
            },
            """
        panel._firstRun.options = {rooms: [], devices: [], control_channels: []};
        panel._firstRun.step = "code_source";
        panel._firstRun.ir = {
          activeDeviceId: "living_air_conditioner", broadlinkExpanded: false,
          codes: getTable["hausman_hub/v1/admin/ir-codes"].codes, error: "", loading: false,
          manual: {deviceId: null, index: 0, statuses: {}},
          scan: getTable["hausman_hub/v1/admin/ir-codes/scan"],
          smartir: {brand: "", deviceCode: "", commandName: ""},
        };
        panel._render();
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Тест-отправка").slice(-1)[0].fire("click");
        await tick();
        const testCall = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/ir-codes/test");
        if (!testCall || JSON.stringify(testCall.payload) !== JSON.stringify({code_id: "living_air_conditioner_off"})) {
          throw new Error("imported code test payload mismatch");
        }
        const remove = findAll(panel.shadowRoot, (node) => node["aria-label"] === "Удалить код ac.off")[0];
        getTable["hausman_hub/v1/admin/ir-codes"] = {codes: []};
        remove.fire("click");
        await tick();
        const deleteCall = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/ir-codes/delete");
        if (!deleteCall || JSON.stringify(deleteCall.payload) !== JSON.stringify({code_id: "living_air_conditioner_off"})) {
          throw new Error("imported code delete payload mismatch");
        }
        panel._settings.setup.rooms[0].devices[0].control_channel = "direct_wifi";
        panel._firstRun.ir = {activeDeviceId: null, broadlinkExpanded: false, codes: [], error: "",
          loading: false, manual: {deviceId: null, index: 0, statuses: {}}, scan: {},
          smartir: {brand: "", deviceCode: "", commandName: ""}};
        panel._render();
        if (textOf(panel.shadowRoot).includes("База кодов SmartIR")) {
          throw new Error("IR code sources rendered for a non-IR device");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_saved_universal_ir_contour_opens_code_source_with_runtime_device_id(self) -> None:
        saved_setup = universal_ir_setup()
        saved_setup.update(
            {
                "status": "ready",
                "editing_allowed": True,
                "name": "Климат",
                "mode": "automatic",
                "schedule": {"enabled": False, "day_start": "07:00", "night_start": "23:00"},
            }
        )
        get_table = get_payloads(bindings=universal_ir_bindings())
        get_table.update(
            {
                "hausman_hub/v1/admin/ir-codes": {"codes": []},
                "hausman_hub/v1/admin/ir-codes/scan": {"smartir_catalog": [], "broadlink_catalog": []},
            }
        )
        script = panel_script(
            get_table,
            {
                "hausman_hub/v1/admin/climate-drafts/save": {"status": "saved"},
                "hausman_hub/v1/admin/climate-profiles": {"setup_revision": 6},
                "hausman_hub/v1/admin/climate-schedule": {"status": "saved"},
            },
            f"""
        getTable["hausman_hub/v1/admin/climate-drafts/current"] = {json.dumps(saved_setup, ensure_ascii=False)};
        panel._firstRun.options = {{rooms: [], devices: [], control_channels: []}};
        panel._firstRun.draft = {{status: "created"}};
        panel._firstRun.validation = {{status: "ready", save_allowed: true}};
        panel._firstRun.step = "completion";
        panel._saveFirstRun();
        await tick(16);
        if (panel._firstRun.step !== "code_source" || panel._firstRun.completed) {{
          throw new Error("saved universal IR contour did not open the code-source step");
        }}
        const scans = calls.filter((call) => call.method === "GET"
          && call.path === "hausman_hub/v1/admin/ir-codes/scan");
        if (scans.length !== 1 || !textOf(panel.shadowRoot).includes("remote.pult_broadlink_gostinnaya")) {{
          throw new Error("code source did not use the saved runtime remote binding");
        }}
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Продолжить к подключению планшета")[0].fire("click");
        if (!textOf(panel.shadowRoot).includes("Подключение планшета")) {{
          throw new Error("code-source continuation did not retain the wizard");
        }}
        const tabletText = textOf(panel.shadowRoot);
        [
          "/api/hausman_hub/v1/capabilities",
          "/api/hausman_hub/v1/dashboard",
          "/api/hausman_hub/v1/events",
          "/api/hausman_hub/v1/device-actions",
        ].forEach((path) => {{
          if (!tabletText.includes(path)) throw new Error(`tablet setup omitted ${{path}}`);
        }});
        if (tabletText.includes("/endpoint/smart-home-center")
          || tabletText.includes("/endpoint/climate/api")) {{
          throw new Error("tablet setup still advertises a legacy Center endpoint");
        }}
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Перейти к завершению")[0].fire("click");
        if (!findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Открыть панель")[0]) {{
          throw new Error("post-save completion did not offer panel navigation");
        }}
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_configured_universal_ir_contour_reopens_resumable_code_source(self) -> None:
        setup = universal_ir_setup()
        setup.update({"status": "ready", "editing_allowed": True, "name": "Климат"})
        get_table = get_payloads(setup=setup, bindings=universal_ir_bindings())
        get_table.update(
            {
                "hausman_hub/v1/admin/ir-codes": {"codes": []},
                "hausman_hub/v1/admin/ir-codes/scan": {
                    "smartir_catalog": [], "broadlink_catalog": [],
                },
            }
        )
        script = panel_script(
            get_table,
            {},
            """
        const resume = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Настроить IR-коды")[0];
        if (!resume) throw new Error("configured contour has no IR setup entry point");
        resume.fire("click");
        await tick();
        if (!textOf(panel.shadowRoot).includes("Источник IR-кодов")
          || !textOf(panel.shadowRoot).includes("remote.pult_broadlink_gostinnaya")) {
          throw new Error("resumable IR setup did not render saved device binding");
        }
        const scans = calls.filter((call) => call.method === "GET"
          && call.path === "hausman_hub/v1/admin/ir-codes/scan");
        if (scans.length !== 1) throw new Error("resumable IR setup did not load sources once");
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_smartir_import_uses_the_latest_selected_command(self) -> None:
        scan = {
            "smartir_catalog": [
                {
                    "brand": "Daikin",
                    "models": [
                        {
                            "device_code": "1001",
                            "model": "FTX",
                            "name": "Daikin FTX",
                            "commands": [
                                {"command_name": "ac.off", "code_data": "SMARTIR_OFF"},
                                {"command_name": "ac.cool.25_0", "code_data": "SMARTIR_COOL"},
                            ],
                        }
                    ],
                }
            ],
            "broadlink_catalog": [],
        }
        get_table = get_payloads(
            setup=universal_ir_setup(), bindings=universal_ir_bindings()
        )
        get_table.update(
            {
                "hausman_hub/v1/admin/ir-codes": {"codes": []},
                "hausman_hub/v1/admin/ir-codes/scan": scan,
            }
        )
        script = panel_script(
            get_table,
            {"hausman_hub/v1/admin/ir-codes": {"ok": True, "code_id": "new_code"}},
            """
        panel._firstRun.options = {rooms: [], devices: [], control_channels: []};
        panel._firstRun.step = "code_source";
        panel._firstRun.ir = {
          activeDeviceId: "living_air_conditioner", broadlinkExpanded: false, codes: [], error: "",
          loading: false, manual: {deviceId: null, index: 0, statuses: {}},
          scan: getTable["hausman_hub/v1/admin/ir-codes/scan"],
          smartir: {brand: "", deviceCode: "", commandName: ""},
        };
        panel._render();
        const commandPicker = findAll(panel.shadowRoot, (node) => node.tagName === "SELECT").slice(-1)[0];
        commandPicker.value = "ac.cool.25_0";
        commandPicker.fire("change");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Импортировать")[0].fire("click");
        await tick();
        const imported = calls.find((call) => call.method === "POST"
          && call.path === "hausman_hub/v1/admin/ir-codes");
        if (!imported || imported.payload.command_name !== "ac.cool.25_0"
          || imported.payload.code_data !== "SMARTIR_COOL") {
          throw new Error("SmartIR import used stale command selection");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
