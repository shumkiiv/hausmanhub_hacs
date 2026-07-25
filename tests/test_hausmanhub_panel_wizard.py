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
            "candidate_id": "candidate_ac", "name": "Кондиционер", "room_id": "living",
            "suggested_types": ["air_conditioner"], "recommended_type": "air_conditioner",
            "status": "available", "suggested_room_id": "living", "suggested_room_name": "Гостиная",
            "reason": "detected_room", "can_add": True,
        },
        {
            "candidate_id": "candidate_temp_1", "name": "Температура у окна", "room_id": "living",
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
            "candidate_id": "candidate_temp_2", "name": "Температура у двери", "room_id": "",
            "suggested_types": ["temperature_sensor"], "recommended_type": "temperature_sensor",
            "status": "available", "suggested_room_id": "living", "suggested_room_name": "Гостиная",
            "reason": "detected_room", "can_add": True,
        },
        {
            "candidate_id": "candidate_humidity", "name": "Влажность гостиной", "room_id": "living",
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
            "candidate_id": "candidate_trv", "name": "Батарея детской", "room_id": "kids",
            "suggested_types": ["radiator_thermostat"], "recommended_type": "radiator_thermostat",
            "status": "available", "suggested_room_id": "kids", "suggested_room_name": "Детская",
            "reason": "detected_room", "can_add": True,
        },
        {
            "candidate_id": "candidate_kids_temp", "name": "Температура детской", "room_id": "kids",
            "suggested_types": ["temperature_sensor"], "recommended_type": "temperature_sensor",
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
    windows: dict | None = None,
) -> dict:
    return {
        "hausman_hub/v1/admin/panel": panel or PANEL_PAYLOAD,
        "hausman_hub/v1/admin/climate-mode": MODE_PAYLOAD,
        "hausman_hub/v1/admin/home-environment": HOME_PAYLOAD,
        "hausman_hub/v1/admin/climate-room-signals": windows or WINDOWS_PAYLOAD,
        "hausman_hub/v1/admin/climate-drafts/current": setup or NOT_CONFIGURED_SETUP,
        "hausman_hub/v1/admin/climate-drafts": options or DRAFT_OPTIONS,
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
        addEventListener() {{}},
        removeEventListener() {{}},
      }};
      global.window = {{ confirm: () => true }};
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
    def test_not_configured_renders_rooms_multiple_sensors_and_keeps_dirty_form(self) -> None:
        script = panel_script(
            get_payloads(),
            {},
            """
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Начать настройку")[0].fire("click");
        await tick();
        panel._firstRunFields.rooms.living.configure.fire("click");
        const fields = panel._firstRunFields.room;
        const groups = findAll(panel.shadowRoot, (node) =>
          String(node.className).split(" ").includes("entity-group"));
        const physicalDevice = groups.find((node) =>
          node["data-device-group-id"] === "device_0123456789abcdef");
        if (!physicalDevice) throw new Error("physical HA device group missing");
        const image = findAll(physicalDevice, (node) => node.tagName === "IMG")[0];
        if (!image
          || image.src !== "https://www.zigbee2mqtt.io/images/devices/KOJIMA-THS-ZG-LCD.png"
          || image.loading !== "lazy"
          || image.referrerpolicy !== "no-referrer") {
          throw new Error("official Zigbee2MQTT image is not configured safely");
        }
        const fallback = findAll(physicalDevice, (node) =>
          String(node.className).includes("device-thumb-fallback"))[0];
        image.fire("error");
        if (!image.hidden || fallback.hidden) {
          throw new Error("broken device image did not reveal local fallback");
        }
        const groupedChoices = fields.devices.filter((choice) =>
          ["candidate_temp_1:temperature_sensor", "candidate_humidity:humidity_sensor"].includes(choice.key));
        if (groupedChoices.length !== 2 || !groupedChoices.every((choice) => choice.checkbox.checked)) {
          throw new Error("physical device bindings were not preserved independently");
        }
        if (groupedChoices.some((choice) => choice.controlChannel !== null)) {
          throw new Error("observed sensors exposed a control-channel selector");
        }
        const sensorSearch = findAll(panel.shadowRoot, (node) => node.type === "search")[0];
        sensorSearch.value = "KOJIMA";
        sensorSearch.fire("input");
        if (physicalDevice.hidden) throw new Error("device metadata search hid the matching group");
        const ungroupedSensor = groups.find((node) =>
          node["data-device-group-id"] === "candidate:candidate_temp_2");
        if (!ungroupedSensor || !ungroupedSensor.hidden) {
          throw new Error("device search did not filter a non-matching group");
        }
        sensorSearch.value = "";
        sensorSearch.fire("input");
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


class PanelFirstRunWizardTest(unittest.TestCase):
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
        const activeStep = findAll(panel.shadowRoot, (node) =>
          node["aria-current"] === "step");
        if (activeStep.length !== 1 || activeStep[0].textContent !== "Инструкция") {
          throw new Error("active wizard step is not announced semantically");
        }
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
        script = panel_script(
            get_payloads(),
            {
                "hausman_hub/v1/admin/climate-drafts": [checked_draft, checked_draft],
                "hausman_hub/v1/admin/climate-drafts/validate": [blocked, ready_validation(checked_draft)],
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
        panel._firstRunFields.rooms.living.configure.fire("click");
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
        check.fire("click");
        await tick();
        if (!panel._firstRun.validRooms.has("living")) {
          throw new Error("checked room was not retained as configured");
        }
        const back = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Назад к списку комнат")[0];
        back.fire("click");
        const enabledFinish = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && node.textContent === "Завершить настройку")[0];
        if (!enabledFinish || enabledFinish.disabled) {
          throw new Error("one checked room did not open progress");
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
                "target_temperature": 22,
                "target_humidity": 45,
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
                    {"candidate_id": "candidate_temp_2", "type": "temperature_sensor"},
                    {"candidate_id": "candidate_humidity", "type": "humidity_sensor"},
                ],
            }],
        }
        script = panel_script(
            get_payloads(),
            {
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
        panel._firstRunFields.rooms.living.configure.fire("click");
        const fields = panel._firstRunFields.room;
        fields.minTemperature.value = "20";
        fields.minTemperature.fire("input");
        fields.maxTemperature.value = "26";
        fields.maxTemperature.fire("input");
        const airConditioner = fields.devices.find((device) =>
          device.type === "air_conditioner");
        airConditioner.controlChannel.value = "direct_wifi";
        airConditioner.controlChannel.fire("change");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить комнату")[0].fire("click");
        await tick();
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Назад к списку комнат")[0].fire("click");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Завершить настройку")[0].fire("click");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Продолжить к проверке")[0].fire("click");
        await tick();
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Проверить настройку")[0].fire("click");
        await tick();
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Продолжить к подключению планшета")[0].fire("click");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Перейти к завершению")[0].fire("click");
        findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON"
          && node.textContent === "Сохранить настройку")[0].fire("click");
        await new Promise((resolve) => setTimeout(resolve, 750));
        await tick(12);
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
        if (panel._activeSection !== "contour" || living.editor.hidden || !living.temperature.focused) {
          throw new Error("temperature error did not reveal and focus its room");
        }
        living.temperature.value = "22";
        living.temperature.fire("input");
        living.humidity.value = "41";
        living.humidity.fire("input");
        check.fire("click");
        await tick();
        if (!textOf(panel.shadowRoot).includes("шаг 5 %")) {
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
        const order = ["Обзор", "Контур", "Профили", "Расписание", "Дом", "Сигналы комнат"];
        let cursor = -1;
        order.forEach((heading) => {
          const next = initial.indexOf(heading, cursor + 1);
          if (next <= cursor) throw new Error("section order broken at " + heading);
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
        if (!text.includes("изменились в другом окне")) throw new Error("conflict notice missing");
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


if __name__ == "__main__":
    unittest.main()
