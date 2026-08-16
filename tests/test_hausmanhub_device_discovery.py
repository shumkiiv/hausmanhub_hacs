"""Frontend contract tests for the device discovery UI module."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "custom_components" / "hausman_hub" / "frontend"
DISCOVERY_JS = FRONTEND / "hausman-hub-device-discovery.js"
CORRELATION_JS = FRONTEND / "hausman-hub-correlation.js"

FIXTURE = {
    "contract": {"name": "hausman-hub-device-discovery", "version": 1},
    "revision": 7,
    "updatedAt": "2026-08-11T00:05:00Z",
    "initialized": True,
    "pendingCount": 1,
    "notifications": [
        {
            "id": "notice_0123456789abcdef",
            "deviceId": "device_fedcba9876543210",
            "firstSeenAt": "2026-08-11T00:04:00Z",
            "title": "Новый датчик температуры",
            "roomId": None,
            "roomName": None,
            "kind": "physical",
            "status": "available",
            "domains": ["sensor"],
            "manufacturer": "Example",
            "model": "TH-1",
            "suggestedPlacements": [
                {
                    "kind": "assign_area",
                    "section": "rooms",
                    "title": "Выбрать комнату",
                    "reason": "Устройство ещё не назначено комнате Home Assistant.",
                    "recommended": True,
                    "actionable": True,
                },
                {
                    "kind": "open_settings",
                    "section": "climate",
                    "title": "Использовать в климате",
                    "reason": "Температурный датчик можно выбрать источником комнаты.",
                    "recommended": True,
                    "actionable": False,
                },
                {
                    "kind": "show_on_dashboard",
                    "section": "dashboard",
                    "title": "Показать на главной",
                    "reason": "Устройство можно закрепить среди видимых устройств.",
                    "recommended": False,
                    "actionable": True,
                },
            ],
            "areaOptions": [
                {"id": "living_room", "name": "Гостиная", "current": False, "recommended": False},
                {"id": "office", "name": "Кабинет", "current": False, "recommended": False},
            ],
        },
    ],
}

HARNESS = """
  const fs = require("fs");
  const vm = require("vm");

  class FakeElement {
    constructor(tag = "element") {
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.className = "";
      this.textContent = "";
      this.disabled = false;
      this.hidden = false;
      this.selected = false;
      this.value = undefined;
      this.type = "";
      this.style = {};
      this.attributes = {};
      this._listeners = {};
    }
    appendChild(child) {
      this.children.push(child);
      return child;
    }
    addEventListener(type, handler) {
      (this._listeners[type] = this._listeners[type] || []).push(handler);
    }
    fire(type) {
      (this._listeners[type] || []).forEach((handler) => handler({}));
    }
    click() {
      this.fire("click");
    }
    setAttribute(name, value) {
      this.attributes[name] = value;
    }
    set innerHTML(value) {
      if (value === "") this.children = [];
    }
  }

  const el = (tag, className, text) => {
    const node = new FakeElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const setAttr = (node, name, value) => node.setAttribute(name, String(value));
  const deps = { el, setAttr };

  const visit = (node, action) => {
    action(node);
    node.children.forEach((child) => visit(child, action));
  };
  const findAll = (root, predicate) => {
    const found = [];
    visit(root, (node) => { if (predicate(node)) found.push(node); });
    return found;
  };
  const textOf = (root) => {
    const parts = [];
    visit(root, (node) => { if (node.textContent) parts.push(node.textContent); });
    return parts.join("\\n");
  };

  vm.runInThisContext(
    fs.readFileSync(__CORRELATION_JS__, "utf8").replace(/export /g, ""),
    { filename: __CORRELATION_JS__ }
  );

  vm.runInThisContext(
    fs.readFileSync(__DISCOVERY_JS__, "utf8")
      .replace(/^import .*$/gm, "")
      .replace(/export /g, ""),
    { filename: __DISCOVERY_JS__ }
  );

  const clone = (value) => JSON.parse(JSON.stringify(value));
  const FIXTURE = __FIXTURE__;

  const makePanel = (hass) => {
    const panel = {
      _hass: hass,
      _deviceDiscovery: null,
      _deviceDiscoveryLoading: false,
      _deviceDiscoveryError: null,
      _deviceDiscoveryPending: null,
      _deviceDiscoveryMessages: {},
      _deviceDiscoveryNotice: "",
      _deviceDiscoveryAreaDrafts: {},
      _deviceDiscoveryBadgeNode: null,
      _shell: { tabs: { devices: el("button", "tab") } },
      container: el("div"),
      renders: 0,
    };
    panel._render = () => {
      panel.renders += 1;
      panel.container.innerHTML = "";
      renderDeviceDiscovery(panel, panel.container, deps);
    };
    return panel;
  };

  const makeHass = (behavior) => {
    const calls = [];
    return {
      calls,
      callApi: (method, path, payload) => {
        calls.push({ method, path, payload });
        return behavior(method, path, payload);
      },
    };
  };
"""


class DeviceDiscoveryFrontendTest(unittest.TestCase):
    """The discovery module renders cards, badge and explicit action results."""

    def _run_script(self, body: str) -> subprocess.CompletedProcess[str]:
        script = (HARNESS
            .replace("__CORRELATION_JS__", repr(str(CORRELATION_JS)))
            .replace("__DISCOVERY_JS__", repr(str(DISCOVERY_JS)))
            .replace("__FIXTURE__", json.dumps(FIXTURE, ensure_ascii=False))) + body
        return subprocess.run(
            ("node", "--input-type=commonjs", "--eval", script),
            check=False,
            capture_output=True,
            text=True,
        )

    def assert_script_ok(self, body: str) -> None:
        completed = self._run_script(body)
        self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)

    def test_card_renders_reasons_without_raw_ids(self) -> None:
        self.assert_script_ok(
            """
  const panel = makePanel(makeHass(() => Promise.resolve({})));
  panel._deviceDiscovery = clone(FIXTURE);
  panel._render();
  const text = textOf(panel.container);
  for (const expected of [
    "Новые устройства",
    "Новый датчик температуры",
    "Example TH-1",
    "Комната пока не назначена",
    "На связи",
    "Выбрать комнату",
    "Устройство ещё не назначено комнате Home Assistant.",
    "Использовать в климате",
    "Температурный датчик можно выбрать источником комнаты.",
    "Показать на главной",
    "Устройство можно закрепить среди видимых устройств.",
    "Скрыть",
  ]) {
    if (!text.includes(expected)) throw new Error("missing card text: " + expected);
  }
  for (const forbidden of ["device_fedcba9876543210", "notice_0123456789abcdef", "sensor"]) {
    if (text.includes(forbidden)) throw new Error("raw identifier leaked into UI: " + forbidden);
  }
  const selects = findAll(panel.container, (node) => node.tagName === "SELECT");
  if (selects.length !== 1) throw new Error("assign_area select missing");
  const options = findAll(selects[0], (node) => node.tagName === "OPTION");
  if (options.length !== 2) throw new Error("area options missing");
  if (!textOf(selects[0]).includes("Гостиная") || !textOf(selects[0]).includes("Кабинет")) {
    throw new Error("area option names missing");
  }
  const actionableButtons = findAll(panel.container, (node) =>
    node.tagName === "BUTTON" && String(node.className).split(" ").includes("device-discovery-run"));
  if (actionableButtons.length !== 2) {
    throw new Error("only actionable placements must render action buttons, got " + actionableButtons.length);
  }
            """,
        )

    def test_badge_reflects_pending_count(self) -> None:
        self.assert_script_ok(
            """
  const panel = makePanel(makeHass(() => Promise.resolve({})));
  updateDeviceDiscoveryBadge(panel, deps);
  if (panel._deviceDiscoveryBadgeNode) throw new Error("badge rendered without data");
  panel._deviceDiscovery = clone(FIXTURE);
  updateDeviceDiscoveryBadge(panel, deps);
  const badge = panel._deviceDiscoveryBadgeNode;
  if (!badge) throw new Error("badge missing");
  if (badge.textContent !== "1") throw new Error("badge count mismatch: " + badge.textContent);
  if (badge.hidden) throw new Error("badge hidden while pending");
  if (badge.attributes["aria-label"] !== "Новых устройств: 1") {
    throw new Error("badge aria-label mismatch");
  }
  if (!panel._shell.tabs.devices.children.includes(badge)) {
    throw new Error("badge not attached to devices tab");
  }
  panel._deviceDiscovery.pendingCount = 0;
  panel._deviceDiscovery.notifications = [];
  updateDeviceDiscoveryBadge(panel, deps);
  if (!badge.hidden) throw new Error("badge stays visible without pending notifications");
            """,
        )

    def test_assign_area_posts_payload_and_shows_success(self) -> None:
        self.assert_script_ok(
            """
  (async () => {
    const next = clone(FIXTURE);
    next.revision = 8;
    next.pendingCount = 0;
    next.notifications = [];
    const hass = makeHass((method, path) => {
      if (method === "POST" && path === "hausman_hub/v1/device-discovery") return Promise.resolve(clone(next));
      return Promise.resolve(clone(FIXTURE));
    });
    const panel = makePanel(hass);
    panel._deviceDiscovery = clone(FIXTURE);
    panel._render();
    const select = findAll(panel.container, (node) => node.tagName === "SELECT")[0];
    select.value = "office";
    select.fire("change");
    const button = findAll(panel.container, (node) =>
      node.tagName === "BUTTON" && node.textContent === "Выбрать комнату")[0];
    button.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    const post = hass.calls.find((call) => call.method === "POST");
    if (!post) throw new Error("POST missing");
    const payload = post.payload;
    if (payload.expectedRevision !== 7) throw new Error("expectedRevision mismatch: " + JSON.stringify(payload));
    if (payload.action !== "assign_area") throw new Error("action mismatch: " + JSON.stringify(payload));
    if (payload.notificationId !== "notice_0123456789abcdef") {
      throw new Error("notificationId mismatch: " + JSON.stringify(payload));
    }
    if (payload.areaId !== "office") throw new Error("areaId mismatch: " + JSON.stringify(payload));
    if (panel._deviceDiscovery.revision !== 8) throw new Error("state not replaced by POST response");
    if (panel._deviceDiscoveryNotice !== "Комната назначена.") {
      throw new Error("success notice mismatch: " + panel._deviceDiscoveryNotice);
    }
    if (!textOf(panel.container).includes("Комната назначена.")) {
      throw new Error("success notice not rendered");
    }
  })().catch((error) => { console.error(error); process.exit(1); });
            """,
        )

    def test_pending_action_disables_card_controls(self) -> None:
        self.assert_script_ok(
            """
  (async () => {
    let release;
    const blocker = new Promise((resolve) => { release = resolve; });
    const hass = makeHass((method) => (method === "POST" ? blocker : Promise.resolve(clone(FIXTURE))));
    const panel = makePanel(hass);
    panel._deviceDiscovery = clone(FIXTURE);
    panel._render();
    const button = findAll(panel.container, (node) =>
      node.tagName === "BUTTON" && node.textContent === "Показать на главной")[0];
    button.click();
    await Promise.resolve();
    const pendingButton = findAll(panel.container, (node) =>
      node.tagName === "BUTTON" && node.textContent === "Выполняется…")[0];
    if (!pendingButton) throw new Error("pending indicator missing");
    if (!pendingButton.disabled) throw new Error("pending button not disabled");
    if (pendingButton.attributes["aria-busy"] !== "true") throw new Error("aria-busy missing");
    const enabledButtons = findAll(panel.container, (node) =>
      node.tagName === "BUTTON" && !node.disabled);
    if (enabledButtons.length) throw new Error("other card actions stay enabled during pending");
    release(clone(FIXTURE));
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    if (panel._deviceDiscoveryPending) throw new Error("pending flag not cleared");
    const activeButtons = findAll(panel.container, (node) =>
      node.tagName === "BUTTON" && !node.disabled);
    if (!activeButtons.length) throw new Error("card actions not re-enabled after POST");
  })().catch((error) => { console.error(error); process.exit(1); });
            """,
        )

    def test_error_403_requires_local_admin(self) -> None:
        self.assert_script_ok(
            """
  (async () => {
    const hass = makeHass((method) => {
      if (method === "POST") {
        const error = new Error("forbidden");
        error.status = 403;
        return Promise.reject(error);
      }
      return Promise.resolve(clone(FIXTURE));
    });
    const panel = makePanel(hass);
    panel._deviceDiscovery = clone(FIXTURE);
    panel._render();
    findAll(panel.container, (node) =>
      node.tagName === "BUTTON" && node.textContent === "Показать на главной")[0].click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    const text = textOf(panel.container);
    if (!text.includes("локальному администратору")) {
      throw new Error("403 explanation missing at card: " + text);
    }
    const message = findAll(panel.container, (node) =>
      String(node.className).split(" ").includes("is-error"));
    if (!message.length) throw new Error("403 message is not marked as error");
    if (panel._deviceDiscoveryNotice) throw new Error("generic success fallback leaked: " + panel._deviceDiscoveryNotice);
  })().catch((error) => { console.error(error); process.exit(1); });
            """,
        )

    def test_error_404_marks_notification_dismissed(self) -> None:
        self.assert_script_ok(
            """
  (async () => {
    const hass = makeHass((method) => {
      if (method === "POST") {
        const error = new Error("gone");
        error.status = 404;
        return Promise.reject(error);
      }
      return Promise.resolve(clone(FIXTURE));
    });
    const panel = makePanel(hass);
    panel._deviceDiscovery = clone(FIXTURE);
    panel._render();
    findAll(panel.container, (node) =>
      node.tagName === "BUTTON" && node.textContent === "Скрыть")[0].click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    const text = textOf(panel.container);
    if (!text.includes("Уведомление уже снято")) {
      throw new Error("404 explanation missing at card: " + text);
    }
    if (panel._deviceDiscoveryNotice) throw new Error("generic success fallback leaked");
  })().catch((error) => { console.error(error); process.exit(1); });
            """,
        )

    def test_error_409_reloads_state_before_retry(self) -> None:
        self.assert_script_ok(
            """
  (async () => {
    const hass = makeHass((method) => {
      if (method === "POST") {
        const error = new Error("conflict");
        error.status = 409;
        return Promise.reject(error);
      }
      return Promise.resolve(clone(FIXTURE));
    });
    const panel = makePanel(hass);
    panel._deviceDiscovery = clone(FIXTURE);
    panel._render();
    findAll(panel.container, (node) =>
      node.tagName === "BUTTON" && node.textContent === "Показать на главной")[0].click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    const text = textOf(panel.container);
    if (!text.includes("Список обновлён, повторите действие")) {
      throw new Error("409 explanation missing at card: " + text);
    }
    const order = hass.calls.map((call) => call.method);
    const postIndex = order.indexOf("POST");
    const getAfter = order.indexOf("GET", postIndex + 1);
    if (postIndex < 0 || getAfter < 0) {
      throw new Error("409 did not trigger a fresh GET: " + JSON.stringify(hass.calls));
    }
    if (hass.calls[getAfter].path !== "hausman_hub/v1/device-discovery") {
      throw new Error("unexpected reload path: " + hass.calls[getAfter].path);
    }
    if (panel._deviceDiscoveryNotice) throw new Error("generic success fallback leaked");
  })().catch((error) => { console.error(error); process.exit(1); });
            """,
        )

    def test_load_reads_discovery_and_guards_overlap(self) -> None:
        self.assert_script_ok(
            """
  (async () => {
    const hass = makeHass(() => Promise.resolve(clone(FIXTURE)));
    const panel = makePanel(hass);
    await loadDeviceDiscovery(panel);
    if (panel._deviceDiscovery.revision !== 7) throw new Error("GET state not stored");
    if (hass.calls.length !== 1 || hass.calls[0].method !== "GET"
        || hass.calls[0].path !== "hausman_hub/v1/device-discovery") {
      throw new Error("unexpected GET calls: " + JSON.stringify(hass.calls));
    }
    if (!textOf(panel.container).includes("Новый датчик температуры")) {
      throw new Error("card not rendered after load");
    }
    let release;
    const blocker = new Promise((resolve) => { release = resolve; });
    const slow = makeHass(() => blocker);
    const busy = makePanel(slow);
    const first = loadDeviceDiscovery(busy);
    const second = loadDeviceDiscovery(busy);
    release(clone(FIXTURE));
    await first;
    await second;
    if (slow.calls.length !== 1) throw new Error("overlapping GET not guarded: " + slow.calls.length);
    const failing = makePanel(makeHass(() => Promise.reject(new Error("boom"))));
    await loadDeviceDiscovery(failing);
    if (failing._deviceDiscoveryError !== "boom") throw new Error("GET error not captured");
    if (!textOf(failing.container).includes("Не удалось получить список новых устройств.")) {
      throw new Error("load error not rendered");
    }
  })().catch((error) => { console.error(error); process.exit(1); });
            """,
        )

    def test_acknowledge_posts_notification_id(self) -> None:
        self.assert_script_ok(
            """
  (async () => {
    const next = clone(FIXTURE);
    next.revision = 9;
    next.pendingCount = 0;
    next.notifications = [];
    const hass = makeHass((method) => (method === "POST"
      ? Promise.resolve(clone(next)) : Promise.resolve(clone(FIXTURE))));
    const panel = makePanel(hass);
    panel._deviceDiscovery = clone(FIXTURE);
    panel._render();
    findAll(panel.container, (node) =>
      node.tagName === "BUTTON" && node.textContent === "Скрыть")[0].click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    const post = hass.calls.find((call) => call.method === "POST");
    if (!post) throw new Error("acknowledge POST missing");
    if (post.payload.action !== "acknowledge"
        || post.payload.notificationId !== "notice_0123456789abcdef"
        || post.payload.expectedRevision !== 7
        || "areaId" in post.payload) {
      throw new Error("acknowledge payload mismatch: " + JSON.stringify(post.payload));
    }
    if (panel._deviceDiscoveryNotice !== "Уведомление скрыто.") {
      throw new Error("acknowledge notice mismatch: " + panel._deviceDiscoveryNotice);
    }
  })().catch((error) => { console.error(error); process.exit(1); });
            """,
        )


if __name__ == "__main__":
    unittest.main()
