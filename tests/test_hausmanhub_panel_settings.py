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
                "entity_id": "sensor.outdoor",
                "name": "Улица",
                "available": True,
                "domain": "sensor",
                "device_class": "temperature",
                "room_id": "living",
                "room_name": "Гостиная",
            },
            {
                "entity_id": "weather.home",
                "name": "Погода дома",
                "available": True,
                "domain": "weather",
                "room_id": "",
            },
        ],
        "presence": [],
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


def panel_script(get_payloads: dict, post_table: dict, assertions: str) -> str:
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

      const getTable = {json.dumps(get_payloads, ensure_ascii=False)};
      const postTable = {json.dumps(post_table, ensure_ascii=False)};
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

    def test_six_tabs_switch_locally_keep_dirty_values_and_support_keyboard(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const tabs = findAll(panel.shadowRoot, (node) =>
          node.tagName === "BUTTON" && String(node.className).split(" ").includes("tab"));
        const labels = tabs.map((node) => node.textContent);
        const expected = ["Обзор", "Контур", "Профили", "Расписание", "Дом", "Сигналы комнат"];
        if (JSON.stringify(labels) !== JSON.stringify(expected)) {
          throw new Error("tab labels mismatch: " + JSON.stringify(labels));
        }
        if (panel._activeSection !== "overview" || panel._shell.sectionNodes.overview.hidden) {
          throw new Error("configured setup did not default to overview");
        }
        if (tabs[0]["aria-current"] !== "page") throw new Error("overview aria-current missing");
        const getCount = calls.filter((call) => call.method === "GET").length;
        tabs[2].fire("click");
        const dayTemperature = findAll(panel.shadowRoot, (node) => node.type === "number")
          .find((node) => String(node.value) === "23");
        dayTemperature.value = "24.5";
        dayTemperature.fire("input");
        tabs[4].fire("click");
        tabs[2].fire("click");
        if (dayTemperature.value !== "24.5" || panel._dirty.profiles !== true) {
          throw new Error("tab switch discarded dirty profile value");
        }
        if (!String(tabs[2].className).includes("is-dirty")) {
          throw new Error("dirty tab indicator missing");
        }
        if (calls.filter((call) => call.method === "GET").length !== getCount) {
          throw new Error("tab switch called an API");
        }
        let prevented = false;
        tabs[2].fire("keydown", {
          key: "ArrowRight",
          preventDefault: () => { prevented = true; },
        });
        if (!prevented || panel._activeSection !== "schedule" || !tabs[3].focused) {
          throw new Error("keyboard tab navigation failed");
        }
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_panel_shell_has_status_accessibility_and_responsive_rules(self) -> None:
        script = panel_script(
            GET_PATHS,
            {},
            """
        const text = textOf(panel.shadowRoot);
        if (!text.includes("Климат, комнаты и сценарии — в одном месте")) {
          throw new Error("header subtitle missing");
        }
        if (!text.includes("Управление климатом выключено")) {
          throw new Error("translated status missing");
        }
        const style = findAll(panel.shadowRoot, (node) => node.tagName === "STYLE")[0];
        const css = String(style.textContent);
        for (const rule of [
          "max-width:1440px",
          "overflow-x:auto",
          "@media (max-width:640px)",
          "@media (max-width:380px)",
          "grid-template-columns:minmax(0,1fr)",
          ":focus-visible",
        ]) {
          if (!css.includes(rule)) throw new Error("responsive/accessibility CSS missing: " + rule);
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
        if (!text.includes("Профили «День» и «Ночь»")) throw new Error("profiles heading missing");
        if (!text.includes("Расписание")) throw new Error("schedule heading missing");
        if (!text.includes("Сигналы дома")) throw new Error("home heading missing");
        if (!text.includes("Сигналы комнат")) throw new Error("room signals heading missing");
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
        if (!text.includes("ранее выбранная сущность сейчас недоступна")) {
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
        for (const label of ["Гостиная", "Погодные сервисы", "Датчик температуры", "Погодный сервис"]) {
          if (!outdoorText.includes(label)) throw new Error("outdoor grouping missing: " + label);
        }
        const weather = findAll(outdoor, (node) =>
          node.type === "radio" && node.value === "weather.home");
        if (weather.length !== 1) throw new Error("weather source missing");
        if (findAll(outdoor, (node) => node.tagName === "SELECT").length) {
          throw new Error("outdoor source is still a dropdown");
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

    def test_mode_switch_posts_exact_payload(self) -> None:
        script = panel_script(
            GET_PATHS,
            {"hausman_hub/v1/admin/climate-mode": {"mode": "managed", "contour_configured": True}},
            """
        const buttons = findAll(panel.shadowRoot, (node) => node.tagName === "BUTTON");
        const enable = buttons.find((node) => node.textContent === "Включить управление");
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

    def test_home_save_posts_exact_five_fields(self) -> None:
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
            """,
        )
        completed = run_panel_script(script)
        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
