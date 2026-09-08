import { expect, test } from "@playwright/test";
import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import { HARNESS_INTENT_RULES } from "../../custom_components/hausman_hub/frontend/hausman-hub-harness-intents.js";

const HARNESS = "/tests/visual/hausman-hub-panel-harness.html";
const HARNESS_ORIGIN = "http://127.0.0.1:8765";
const FIXED_NOW = "2026-08-23T02:15:00.000Z";
const PANEL_READY_TIMEOUT_MS = 45_000;
const HARNESS_PHASE_TIMEOUT_MS = 60_000;
const HARNESS_CLOSE_TIMEOUT_MS = 5_000;
const allStates = [
  ["overview", ""], ["lighting", ""], ["climate", ""], ["rooms", ""], ["media", ""],
  ["security", ""], ["devices", ""], ["energy", ""], ["scenarios", ""], ["settings", ""],
  ["climate-profiles", "&screen=profiles"], ["settings-rooms", "&settings=rooms"],
  ["settings-light-protection", "&settings=light-protection"],
  ["scenarios-editor", "&openScenario=Доброе"], ["scenarios-nodered", "&nodeRedEditor=1"], ["kiosk", "&kiosk=1"],
];
function selectedStates(available, requested) {
  if (!requested) return available;
  const names = new Set(requested.split(",").map((item) => item.trim()).filter(Boolean));
  const selected = available.filter(([name]) => names.has(name));
  if (selected.length !== names.size) throw new Error(`Unknown HACS_INTENT_STATES value: ${requested}`);
  return selected;
}
const states = selectedStates(allStates, process.env.HACS_INTENT_STATES || "");
const output = process.env.PLAYWRIGHT_OUTPUT_DIR || process.env.QA_ARTIFACT_ROOT;
const INVENTORY_ONLY = process.env.HACS_INTENT_INVENTORY_ONLY === "1";
const ROOT = path.resolve(process.cwd());
// Exact paths observed in the last successful exhaustive harness run. The
// route policy deliberately does not discover files dynamically: new assets
// must be observed and reviewed before the release gate may load them.
const ALLOWED_LOCAL_REQUEST_PATHS = new Set([
  "/api/hausman_hub/panel/assets/hero_living_room_night.png", "/api/hausman_hub/panel/assets/hero_premium_kitchen_night_v2.png",
  "/api/hausman_hub/panel/assets/hero_room_bedroom_night.webp", "/api/hausman_hub/panel/assets/hero_room_office_night.webp",
  "/api/hausman_hub/panel/hausman-hub-panel.css", "/tests/visual/hausman-hub-panel-harness.html",
  ...[
    "area-binding", "buttons", "catalog", "climate-overview", "climate-side", "command-feedback", "control-channel", "correlation",
    "device-actions", "device-bindings", "device-card", "device-controls", "device-discovery", "device-features", "device-inventory",
    "device-maintenance", "device-property-names", "devices-overview", "diagnostics", "energy-chart", "energy-meter", "energy",
    "error-taxonomy", "feedback", "first-run-draft", "harness-intents", "hero-room-navigation", "home-sections", "intercom",
    "inventory-duplicates", "kiosk", "library-hero", "light-protection", "lighting-side", "lighting", "media-device", "media-overview",
    "media-side", "modal", "navigation", "notice", "overview-events-modal", "overview-hero-state", "overview-side", "overview-utility-cards",
    "overview", "pagination", "panel", "power-links", "rollout", "room-climate-sources", "room-device-groups", "room-icons", "room-setup",
    "rooms-side", "rooms", "scenario-ai", "scenario-badges", "scenario-bulk", "scenario-catalog", "scenario-device-picker",
    "scenario-editor-scroll", "scenario-extensions", "scenario-fields", "scenario-icons", "scenario-node-red", "scenario-rooms", "scenario-state",
    "scenarios", "security-overview", "settings-profile", "settings-rooms", "settings", "switch", "technical-log", "tokens", "ui-state",
    "weather-sources", "wizard-validation",
  ].flatMap((name) => [
    `/custom_components/hausman_hub/frontend/hausman-hub-${name}.js`,
    `/custom_components/hausman_hub/frontend/hausman-hub-${name}.css`,
  ]),
].filter((pathname) => pathname.startsWith("/api/") || fs.existsSync(path.join(ROOT, pathname))));
const EXPECTED_BLOCKED_EXTERNAL_IMAGES = new Set([
  "https://www.zigbee2mqtt.io/images/devices/AC211.png",
  "https://www.zigbee2mqtt.io/images/devices/TO-Q-SY1-JZT.png",
  "https://www.zigbee2mqtt.io/images/devices/TRVZB.png",
  "https://www.zigbee2mqtt.io/images/devices/TS0505B_1.png",
]);
const INTERACTION_MANIFEST = JSON.parse(fs.readFileSync(path.join(ROOT, "qa/full-functional/hacs-interactions.json"), "utf8"));
const INTENTS = new Map((INTERACTION_MANIFEST.interaction_intents || []).map((item) => [`${item.state}:${item.key}`, item]));
const INTENTS_BY_KEY = new Map((INTERACTION_MANIFEST.interaction_intents || []).map((item) => [item.key, item]));
for (const required of [
  "package.json",
  "custom_components/hausman_hub/manifest.json",
  "qa/full-functional/hacs-interactions.json",
]) {
  if (!fs.existsSync(path.join(ROOT, required))) {
    throw new Error(`Playwright must run from the HACS repository root: missing ${required}`);
  }
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

function releaseProvenance() {
  const digest = crypto.createHash("sha256");
  const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "custom_components/hausman_hub/manifest.json"), "utf8"));
  const qaManifest = JSON.parse(fs.readFileSync(path.join(ROOT, "qa/full-functional/hacs-interactions.json"), "utf8"));
  digest.update(`version\0${manifest.version}\0`, "utf8");
  for (const name of ["interactions", "interaction_intents"]) {
    digest.update(`${name}\0`, "utf8");
    digest.update(canonicalJson(qaManifest[name]), "utf8");
    digest.update("\0", "utf8");
  }
  const frontend = path.join(ROOT, "custom_components/hausman_hub/frontend");
  const audited = [
    ...fs.readdirSync(frontend).filter(name => /\.(?:js|css)$/.test(name)).map(name => path.join(frontend, name)),
    path.join(ROOT, "tests/browser/hausman-hub-full-interaction.spec.js"),
    path.join(ROOT, "tests/visual/hausman-hub-panel-harness.html"),
  ].sort((left, right) => path.relative(ROOT, left).localeCompare(path.relative(ROOT, right)));
  for (const file of audited) {
    digest.update(`${path.relative(ROOT, file).split(path.sep).join("/")}\0`, "utf8");
    digest.update(fs.readFileSync(file));
    digest.update("\0", "utf8");
  }
  return { version: manifest.version, content_digest: digest.digest("hex") };
}

function effectMatches(effect, outcome) {
  if (effect?.kind === "dom-or-value-change") return Boolean(outcome?.domChanged || outcome?.valueChanged || outcome?.checkedChanged || outcome?.selected);
  if (effect?.kind === "editor-open") return outcome?.editorOpen === true;
  if (effect?.kind === "scroll-into-view") return outcome?.scrollIntoViewCalls > 0;
  return Boolean(effect && outcome?.attributes && outcome.attributes[effect.attribute] === effect.equals);
}

function requestPath(pathname) {
  const value = String(pathname || "");
  return value.startsWith("/api/") ? value.slice(5) : value.replace(/^\//, "");
}

function requestMatches(request, call) {
  return Boolean(call && (
    String(call?.method || "").toUpperCase() === String(request?.method || "").toUpperCase()
    && requestPath(call?.path) === requestPath(request?.path)
  ));
}

function hasExactRequest(request, calls) {
  return Array.isArray(calls) && calls.some((call) => requestMatches(request, call));
}

function mutationCalls(calls) {
  return (calls || []).filter((call) => ["POST", "PUT", "PATCH", "DELETE"].includes(String(call?.method || "").toUpperCase()));
}

function classify(intent, outcome) {
  if (!intent) return { unclassified: true };
  if (intent.intent === "blocked") return { pass: outcome?.clicked !== true };
  if (intent.intent === "ui-only") {
    return { pass: (!mutationCalls(outcome?.calls).length) && effectMatches(intent.effect, outcome) };
  }
  const matched = hasExactRequest(intent.request, outcome?.calls);
  const unexpected = mutationCalls(outcome?.calls).some((call) => !requestMatches(intent.request, call));
  return { pass: matched && !unexpected, unrecorded_command: !matched, unexpected_command: unexpected };
}

function intentFor(state, key) {
  return INTENTS.get(`${state}:${key}`) || INTENTS_BY_KEY.get(key) || null;
}

function createIntentReport() {
  return { unclassified: [], unrecorded_commands: [], unexpected_calls: [], failed_effects: [] };
}

function summarizeUnclassified(rows) {
  const grouped = new Map();
  for (const row of rows) {
    const key = JSON.stringify(row);
    const prior = grouped.get(key);
    if (prior) prior.count += 1;
    else grouped.set(key, { ...row, count: 1 });
  }
  return [...grouped.values()];
}

function withOccurrences(items) {
  const totals = new Map();
  for (const item of items) totals.set(item.key, (totals.get(item.key) || 0) + 1);
  const seen = new Map();
  return items.map((item) => {
    const occurrence = seen.get(item.key) || 0;
    seen.set(item.key, occurrence + 1);
    return { ...item, occurrence, occurrenceTotal: totals.get(item.key) };
  });
}

function latencySummary(samples) {
  const sorted = samples
    .filter((value) => Number.isFinite(value) && value >= 0)
    .map((value) => Number(value.toFixed(3)))
    .sort((left, right) => left - right);
  if (!sorted.length) return { count: 0, p50_ms: 0, p95_ms: 0, max_ms: 0 };
  const nearestRank = (quantile) => sorted[Math.max(0, Math.ceil(sorted.length * quantile) - 1)];
  return {
    count: sorted.length,
    p50_ms: nearestRank(0.50),
    p95_ms: nearestRank(0.95),
    max_ms: sorted[sorted.length - 1],
  };
}

function assignControlLanes(controls, requestedLaneCount) {
  const laneCount = Math.max(1, Math.min(requestedLaneCount, controls.length || 1));
  const lanes = Array.from({ length: laneCount }, () => []);
  controls.forEach((control, index) => lanes[index % laneCount].push({ index, control }));
  return lanes;
}

function harnessPhaseMessage({ state, key = null, occurrence = null, lane = null, phase }) {
  return `state=${state} key=${key ?? "-"} occurrence=${occurrence ?? "-"} lane=${lane ?? "-"} phase=${phase}`;
}

function harnessPhaseError(identity) {
  const error = new Error(`Harness phase deadline exceeded: ${harnessPhaseMessage(identity)}`);
  error.harnessPhase = identity;
  return error;
}

async function withHarnessDeadline(identity, operation, timeoutMs = HARNESS_PHASE_TIMEOUT_MS) {
  let timer;
  const work = Promise.resolve().then(operation);
  // The race may settle before a browser operation. Retain a rejection handler
  // on that operation so a late failure is observed instead of becoming an
  // unhandled rejection after the lane has already stopped.
  work.catch(() => {});
  try {
    return await Promise.race([
      work,
      new Promise((_, reject) => { timer = setTimeout(() => reject(harnessPhaseError(identity)), timeoutMs); }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function closeHarnessResource(resource, identity) {
  if (resource) await withHarnessDeadline({ ...identity, phase: "close" }, () => resource.close(), HARNESS_CLOSE_TIMEOUT_MS);
}

test("harness deadline rejects a never-resolving action with its complete identity", async () => {
  const identity = { state: "rooms", key: "room:open", occurrence: 2, lane: 3, phase: "exercise" };
  await expect(withHarnessDeadline(identity, () => new Promise(() => {}), 10))
    .rejects.toThrow("state=rooms key=room:open occurrence=2 lane=3 phase=exercise");
});

test("intent classifier: ui-only effect", async () => {
  expect(classify({ intent: "ui-only", effect: { attribute: "data-section", equals: "rooms" } }, { calls: [], attributes: { "data-section": "rooms" } }).pass).toBe(true);
});

test("state filter keeps only explicitly requested technical states", async () => {
  expect(selectedStates([["overview", ""], ["kiosk", "&kiosk=1"]], "kiosk")).toEqual([["kiosk", "&kiosk=1"]]);
  expect(() => selectedStates([["overview", ""]], "missing")).toThrow(/Unknown HACS_INTENT_STATES/);
});

test("intent classifier: command without harness call", async () => {
  expect(classify({ intent: "command", request: { method: "POST", path: "/api/actions" } }, { calls: [] }).unrecorded_command).toBe(true);
});

test("intent classifier: command rejects an additional mutation route", async () => {
  const intent = { intent: "command", request: { method: "POST", path: "/api/actions" } };
  const result = classify(intent, { calls: [{ method: "POST", path: "/api/actions" }, { method: "DELETE", path: "/api/other" }] });
  expect(result.pass).toBe(false);
  expect(result.unexpected_command).toBe(true);
});

test("intent classifier: ui-only permits read refreshes but rejects mutations", async () => {
  const intent = { intent: "ui-only", effect: { attribute: "data-harness-key", equals: "overview:refresh" } };
  expect(classify(intent, { calls: [{ method: "GET", path: "/api/hausman_hub/v1/dashboard" }], attributes: { "data-harness-key": "overview:refresh" } }).pass).toBe(true);
  expect(classify(intent, { calls: [{ method: "POST", path: "/api/actions" }], attributes: { "data-harness-key": "overview:refresh" } }).pass).toBe(false);
});

test("intent classifier: local-change effect needs an observed DOM or field change", async () => {
  const intent = { intent: "ui-only", effect: { kind: "dom-or-value-change" } };
  expect(classify(intent, { calls: [], domChanged: true, valueChanged: false }).pass).toBe(true);
  expect(classify(intent, { calls: [], domChanged: false, valueChanged: true }).pass).toBe(true);
  expect(classify(intent, { calls: [], selected: true }).pass).toBe(true);
  expect(classify(intent, { calls: [], checkedChanged: true }).pass).toBe(true);
  expect(classify(intent, { calls: [], domChanged: false, valueChanged: false }).pass).toBe(false);
});

test("intent classifier: editor action verifies the editor is open", async () => {
  const intent = { intent: "ui-only", effect: { kind: "editor-open" } };
  expect(classify(intent, { calls: [], editorOpen: true }).pass).toBe(true);
  expect(classify(intent, { calls: [], editorOpen: false }).pass).toBe(false);
});

test("intent classifier: scroll action verifies the requested local scroll", async () => {
  const intent = { intent: "ui-only", effect: { kind: "scroll-into-view" } };
  expect(classify(intent, { calls: [], scrollIntoViewCalls: 1 }).pass).toBe(true);
  expect(classify(intent, { calls: [], scrollIntoViewCalls: 0 }).pass).toBe(false);
});

test("intent classifier: unknown and blocked", async () => {
  expect(classify(null, { calls: [] }).unclassified).toBe(true);
  expect(classify({ intent: "blocked" }, { clicked: false }).pass).toBe(true);
});

test("intent report starts fail-closed buckets", async () => {
  const report = createIntentReport();
  expect(report.unclassified).toEqual([]);
  expect(report.unrecorded_commands).toEqual([]);
  expect(report.unexpected_calls).toEqual([]);
  expect(report.failed_effects).toEqual([]);
});

test("unclassified report groups technical controls without display text", async () => {
  expect(summarizeUnclassified([
    { state: "rooms", key: null, intent: null, tag: "BUTTON", className: "room-card", testid: "", id: "", name: "", ariaControls: "", auditSource: "rooms.js:1" },
    { state: "rooms", key: null, intent: null, tag: "BUTTON", className: "room-card", testid: "", id: "", name: "", ariaControls: "", auditSource: "rooms.js:1" },
  ])).toEqual([{ state: "rooms", key: null, intent: null, tag: "BUTTON", className: "room-card", testid: "", id: "", name: "", ariaControls: "", auditSource: "rooms.js:1", count: 2 }]);
});

test("repeated controls keep one technical key and distinct runtime instances", async () => {
  expect(withOccurrences([{ key: "scenario:run" }, { key: "scenario:run" }, { key: "navigation:rooms" }]))
    .toEqual([
      { key: "scenario:run", occurrence: 0, occurrenceTotal: 2 },
      { key: "scenario:run", occurrence: 1, occurrenceTotal: 2 },
      { key: "navigation:rooms", occurrence: 0, occurrenceTotal: 1 },
    ]);
});

test("safe action latency summary uses nearest-rank percentiles", async () => {
  expect(latencySummary([0.4, 0.8, 2.1, 15.3, 16.1])).toEqual({
    count: 5,
    p50_ms: 2.1,
    p95_ms: 16.1,
    max_ms: 16.1,
  });
  expect(latencySummary([])).toEqual({ count: 0, p50_ms: 0, p95_ms: 0, max_ms: 0 });
});

test("isolated action lanes cover every control exactly once in stable order", async () => {
  const lanes = assignControlLanes(["a", "b", "c", "d", "e"], 3);
  expect(lanes).toEqual([
    [{ index: 0, control: "a" }, { index: 3, control: "d" }],
    [{ index: 1, control: "b" }, { index: 4, control: "e" }],
    [{ index: 2, control: "c" }],
  ]);
  expect(lanes.flat().sort((left, right) => left.index - right.index).map((item) => item.control)).toEqual(["a", "b", "c", "d", "e"]);
});

test("UI intent assignments have matching manifest entries", async () => {
  for (const [, key, intent] of HARNESS_INTENT_RULES) {
    expect(intentFor("shared", key), `missing manifest intent for ${key}`).toMatchObject({ key, intent });
  }
});

// This exhaustive audit creates a fresh document for every control.  Keep its
// artifacts off: retaining failure traces would otherwise retain hundreds of
// complete browser contexts and turn a resource check into a disk-pressure run.
test.use({ trace: "off", video: "off", screenshot: "off" });

test("сброс browser storage не ломает страницу с opaque origin", async ({ browser }) => {
  const context = await browser.newContext();
  await context.addInitScript(resetLocalStateAndFreezeClock, FIXED_NOW);
  const page = await context.newPage();
  try {
    await page.goto("data:text/html,<title>opaque</title>", { waitUntil: "domcontentloaded" });
    await expect(page).toHaveTitle("opaque");
  } finally {
    await context.close();
  }
});

test("fresh state context initializes route telemetry before the first local request", async ({ browser }) => {
  const telemetry = {};
  const context = await createStateContext(browser, telemetry);
  try {
    await freshStatePage(context, ["overview", ""]);
    expect(telemetry).toMatchObject({
      continued_requests: expect.any(Array),
      mutation_escape_requests: expect.any(Array),
      unexpected_local_requests: expect.any(Array),
      blocked_external_requests: expect.any(Array),
      unexpected_external_requests: expect.any(Array),
    });
    expect(telemetry.continued_requests.length).toBeGreaterThan(0);
  } finally {
    await context.close();
  }
});

test("настроечные маршруты открывают заявленные подвиды и оставляют исходную точку защиты света в аудите", async ({ browser }) => {
  const context = await createStateContext(browser, { blocked: [], blockedAttempts: 0, continued: [], continuedExternal: [] });
  try {
    const page = await freshStatePage(context, ["settings-light-protection", "&settings=light-protection"]);
    await expect(page.locator("hausman-hub-panel").getByTestId("settings-manual-light-protection")).toBeVisible();
    expect(await page.locator("hausman-hub-panel").evaluate(host => host._activeSettingsView)).toBe("light-protection");
    await expect.poll(() => page.evaluate(() => window.__hausmanHubInteractionAudit.listeners.some((row) =>
      row.source_id.includes("custom_components/hausman_hub/frontend/hausman-hub-light-protection.js:")
      && row.source_id.endsWith(":listener:1"))))
      .toBe(true);
    await page.goto(`${HARNESS}?section=settings&settings=rooms&theme=dark`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("hausman-hub-panel").locator("main")).toBeVisible();
    expect(await page.locator("hausman-hub-panel").evaluate(host => host._activeSettingsView)).toBe("rooms");
  } finally {
    await context.close();
  }
});

test("повторно используемая страница получает чистый документ и storage перед действием", async ({ browser }) => {
  const telemetry = { blocked: [], blockedAttempts: 0, continued: [], continuedExternal: [] };
  const context = await createStateContext(browser, telemetry);
  const page = await context.newPage();
  try {
    await open(page, ["overview", ""]);
    await page.evaluate(() => {
      localStorage.setItem("hacs-browser-reuse-probe", "stale");
      document.body.dataset.hacsBrowserReuseProbe = "stale";
      window.__hausmanHubInteractionAudit.reuseProbe = "stale";
    });
    await open(page, ["overview", ""]);
    const state = await page.evaluate(() => ({
      storage: localStorage.getItem("hacs-browser-reuse-probe"),
      documentMarker: document.body.dataset.hacsBrowserReuseProbe || null,
      auditMarker: window.__hausmanHubInteractionAudit.reuseProbe || null,
    }));
    expect(state).toEqual({ storage: null, documentMarker: null, auditMarker: null });
    expect(telemetry.continuedExternal).toEqual([]);
  } finally {
    await context.close();
  }
});

test("готовность повторяющихся Node-RED действий требует полного устойчивого набора", async ({ browser }) => {
  const telemetry = { blocked: [], blockedAttempts: 0, continued: [], continuedExternal: [] };
  const context = await createStateContext(browser, telemetry);
  const page = await context.newPage();
  const expected = { key: "scenario:more-action", occurrence: 11, occurrenceTotal: 12 };
  try {
    await open(page, ["scenarios-nodered", "&nodeRedEditor=1"]);
    await waitForStableControlInventory(page, expected);
    await page.locator("hausman-hub-panel").evaluate((host, key) => {
      const controls = [...host.shadowRoot.querySelectorAll(`[data-harness-key="${key}"]`)];
      const last = controls.at(-1);
      delete last.dataset.harnessKey;
      requestAnimationFrame(() => { last.dataset.harnessKey = key; });
    }, expected.key);
    await waitForStableControlInventory(page, expected);
    await expect(page.locator("hausman-hub-panel").locator(`[data-harness-key="${expected.key}"]`)).toHaveCount(expected.occurrenceTotal);
  } finally {
    await context.close();
  }
});

function auditInit() {
  // Four application frames are enough to reach el()'s caller and avoid
  // turning a rendering audit into an unbounded stack-formatting workload.
  Error.stackTraceLimit = 5;
  const registry = window.__hausmanHubInteractionAudit = { sources: [], listeners: [], network: [], errors: [] };
  const sourceIds = new Set();
  let stackCaptures = 0;
  const sources = new WeakMap();
  const control = tag => ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "A"].includes(String(tag).toUpperCase());
  const caller = (construct, force = false) => {
    // A fixture may repeatedly redraw the same card.  Capturing a bounded set
    // per freshly loaded state records source sites without making audit-only
    // stack collection alter the page's runtime characteristics.
    if (++stackCaptures > 80 && !force) return null;
    const frames = String(new Error().stack || "").split("\n");
    const found = [];
    for (const frame of frames) {
      const match = frame.match(/(custom_components\/hausman_hub\/frontend\/[^?:)]+\.js)(?:\?[^:)]+)?:(\d+):(\d+)/);
      if (!match || /hausman-hub-full-interaction|panel-harness/.test(frame)) continue;
      // el() in panel.js is a factory, not the call site of the displayed control.
      if (match[1].endsWith("hausman-hub-panel.js") && Number(match[2]) >= 168 && Number(match[2]) <= 174) continue;
      found.push({ path: match[1], line: Number(match[2]) });
    }
    if (!found.length) return null;
    const site = found[0];
    // The ordinal identifies a lexical construct on its source line.  Dynamic
    // list rendering can execute that construct many times, so it is never a
    // runtime invocation counter.
    const ordinal = 1;
    return { ...site, construct, ordinal, source_id: `${site.path}:${site.line}:${construct}:${ordinal}` };
  };
  const create = Document.prototype.createElement;
  Document.prototype.createElement = function(...args) {
    const node = create.apply(this, args);
    if (control(args[0])) {
      const source = caller("create");
      if (source) { sources.set(node, source); node.dataset.hmhAuditSource = source.source_id; if (!sourceIds.has(source.source_id)) { sourceIds.add(source.source_id); registry.sources.push(source); } }
    }
    return node;
  };
  const add = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function(type, ...args) {
    if (["click", "change", "input", "submit"].includes(type) && (this.nodeType === 1 || this === document)) {
      const source = caller("listener", this.dataset?.testid?.startsWith("manual-light-protection:") || this.dataset?.testid === "lighting-protection:status");
      if (source && !sourceIds.has(source.source_id)) { sourceIds.add(source.source_id); registry.listeners.push({ ...source, type, target: this.nodeType === 1 ? `${this.tagName}.${this.className || ""}` : "DOCUMENT", control_source: sources.get(this)?.source_id || null }); }
    }
    return add.call(this, type, ...args);
  };
  const fetch = window.fetch;
  window.fetch = (...args) => { registry.network.push(String(args[0])); return fetch(...args); };
}

function resetLocalStateAndFreezeClock(iso) {
  // A shared context is intentional for resource bounds, but each action still
  // needs the same pristine storage and deterministic fixture clock that a
  // formerly fresh context supplied.
  for (const storage of [localStorage, sessionStorage]) {
    try { storage.clear(); } catch (error) { if (error?.name !== "SecurityError") throw error; }
  }
  const RealDate = Date;
  const fixed = RealDate.parse(iso);
  class FrozenDate extends RealDate {
    constructor(...args) { super(...(args.length ? args : [fixed])); }
    static now() { return fixed; }
  }
  FrozenDate.parse = RealDate.parse;
  FrozenDate.UTC = RealDate.UTC;
  window.Date = FrozenDate;
}

async function open(page, state) {
  await page.goto(`${HARNESS}?section=${state[0].split("-")[0]}&theme=dark${state[1]}`, { waitUntil: "domcontentloaded" });
  // A cold harness document can parse all modules after domcontentloaded.
  // Keep the wait bounded, while preserving the same exact panel and error
  // assertions for every state and every independently opened action page.
  await expect(page.locator("hausman-hub-panel").locator("main")).toBeVisible({ timeout: PANEL_READY_TIMEOUT_MS });
  if (state[0] === "settings-light-protection") {
    await page.locator("hausman-hub-panel").evaluate(async (host) => {
      host._capabilities = await host._hass.callApi("GET", "hausman_hub/v1/capabilities");
      host._lightProtection = { state: "Active", snapshot: structuredClone(host._manualLightProtectionHarness), error: "" };
      host._render();
    });
  }
  const errors = await page.evaluate(() => window.__hausmanHubHarnessErrors || []);
  expect(errors, `${state[0]} harness errors`).toEqual([]);
}

function writeReport(report) {
  const file = path.join(output, "hacs-full-interaction-runtime.json");
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(report, null, 2), { mode: 0o600 });
  fs.renameSync(temp, file);
}

function requestRecord(request) {
  const url = new URL(request.url());
  return { method: request.method(), resource_type: request.resourceType(), url: `${url.origin}${url.pathname}` };
}

function createRouteTelemetry(telemetry = {}) {
  for (const name of [
    "continued_requests", "mutation_escape_requests", "unexpected_local_requests",
    "blocked_external_requests", "unexpected_external_requests",
  ]) {
    if (!Array.isArray(telemetry[name])) telemetry[name] = [];
  }
  return telemetry;
}

function attachRouteTelemetry(report, routeTelemetry) {
  for (const name of Object.keys(routeTelemetry)) report[name] = routeTelemetry[name];
  report.mutation_escape = report.mutation_escape_requests.length > 0;
  report.external_network = report.unexpected_external_requests.length > 0;
  report.blocked_external_attempts = report.blocked_external_requests.length + report.unexpected_external_requests.length;
}

function expectedBlockedExternal(record, originalUrl) {
  const parsed = new URL(originalUrl);
  return record.method === "GET" && record.resource_type === "image"
    && !parsed.search && !parsed.hash && EXPECTED_BLOCKED_EXTERNAL_IMAGES.has(record.url);
}

async function createStateContext(browser, routeTelemetry, identity = { state: "test", key: null, occurrence: null, lane: null }) {
  routeTelemetry = createRouteTelemetry(routeTelemetry);
  let context;
  try {
    return await withHarnessDeadline({ ...identity, phase: "create" }, async () => {
      context = await browser.newContext();
      await context.addInitScript(resetLocalStateAndFreezeClock, FIXED_NOW);
      await context.addInitScript(auditInit);
      await context.route("**/*", async route => {
        const request = route.request();
        const record = requestRecord(request);
        if (new URL(record.url).origin === HARNESS_ORIGIN) {
          if (record.method !== "GET") routeTelemetry.mutation_escape_requests.push(record);
          else if (ALLOWED_LOCAL_REQUEST_PATHS.has(new URL(record.url).pathname)) {
            routeTelemetry.continued_requests.push(record);
            return route.continue();
          } else routeTelemetry.unexpected_local_requests.push(record);
        } else if (expectedBlockedExternal(record, request.url())) {
          routeTelemetry.blocked_external_requests.push(record);
        } else {
          routeTelemetry.unexpected_external_requests.push(record);
        }
        await route.abort("blockedbyclient");
      });
      return context;
    });
  } catch (error) {
    try { await closeHarnessResource(context, identity); } catch { /* Preserve the failed create phase. */ }
    throw error;
  }
}

async function freshStatePage(context, state, identity = { state: state[0], key: null, occurrence: null, lane: null }) {
  let page;
  try {
    return await withHarnessDeadline({ ...identity, phase: "open" }, async () => {
      page = await context.newPage();
      await open(page, state);
      return page;
    });
  } catch (error) {
    try { await closeHarnessResource(page, identity); } catch { /* Preserve the failed open phase. */ }
    throw error;
  }
}

async function waitForStableControlInventory(page, expected, identity = { state: "test", key: expected.key, occurrence: expected.occurrence, lane: null }) {
  return withHarnessDeadline({ ...identity, phase: "inventory" }, async () => {
    let consecutiveReadySamples = 0;
    const requiredReadySamples = expected.occurrenceTotal > 1 ? 2 : 1;
    await expect.poll(async () => {
    const snapshot = await page.locator("hausman-hub-panel").evaluate((host, key) => {
      const visible = node => { const style = getComputedStyle(node); return !node.disabled && !node.hidden && style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0; };
      const count = [...host.shadowRoot.querySelectorAll("button,input,select,textarea,a,[role=button]")]
        .filter(visible)
        .filter(node => node.dataset?.harnessKey === key).length;
      const stylesReady = [...host.shadowRoot.querySelectorAll('link[rel="stylesheet"]')]
        .every(link => Boolean(link.sheet));
      return { count, documentReady: document.readyState === "complete", stylesReady };
    }, expected.key);
    const ready = snapshot.documentReady && snapshot.stylesReady && snapshot.count === expected.occurrenceTotal;
    consecutiveReadySamples = ready ? consecutiveReadySamples + 1 : 0;
    return consecutiveReadySamples >= requiredReadySamples ? snapshot.count : -1;
  }, {
    timeout: PANEL_READY_TIMEOUT_MS,
    message: `stable visible inventory for ${expected.key}`,
    }).toBe(expected.occurrenceTotal);
  });
}

async function exerciseControl(page, state, expected, identity) {
  return withHarnessDeadline({ ...identity, phase: "exercise" }, () => exerciseControlUnbounded(page, state, expected, identity));
}

async function exerciseControlUnbounded(page, state, expected, identity) {
  await open(page, state);
  await waitForStableControlInventory(page, expected, identity);
  const outcome = await page.locator("hausman-hub-panel").evaluate(async (host, control) => {
    const root = host.shadowRoot;
    const visible = node => { const style = getComputedStyle(node); return !node.disabled && !node.hidden && style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0; };
    const target = [...root.querySelectorAll("button,input,select,textarea,a,[role=button]")].filter(visible).filter(node => node.dataset?.harnessKey === control.key)[control.occurrence];
    if (!target) return { missing: true, reason: "control disappeared" };
    const before = (window.__hausmanHubHarnessCalls || []).length;
    const beforeDom = root.innerHTML;
    const beforeValue = "value" in target ? target.value : undefined;
    const beforeChecked = "checked" in target ? target.checked : undefined;
    if (target.dataset?.harnessIntent === "blocked") return { clicked: false, calls: [], domChanged: false, valueChanged: false, checkedChanged: false, attributes: {} };
    const actionStartedAt = performance.now();
    let scrollIntoViewCalls = 0;
    const originalScrollIntoView = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (...args) { scrollIntoViewCalls += 1; return originalScrollIntoView?.apply(this, args); };
    if (target.tagName === "SELECT") {
      if (target.options.length > 1) target.selectedIndex = (target.selectedIndex + 1) % target.options.length;
      target.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (target.tagName === "TEXTAREA") {
      target.value = target.value === "qa" ? "qa-2" : "qa";
      target.dispatchEvent(new Event("input", { bubbles: true })); target.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (target.tagName === "INPUT" && ["checkbox", "radio"].includes(target.type)) target.click();
    else if (target.tagName === "INPUT") {
      if (target.type === "number") {
        const step = Number(target.step) || 1, currentValue = Number(target.value) || 0, maximum = Number(target.max);
        target.value = String(Number.isFinite(maximum) && currentValue + step > maximum ? currentValue - step : currentValue + step);
      } else target.value = target.value === "qa" ? "qa-2" : "qa";
      target.dispatchEvent(new Event("input", { bubbles: true })); target.dispatchEvent(new Event("change", { bubbles: true }));
    }
    else target.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    Element.prototype.scrollIntoView = originalScrollIntoView;
    const current = [...root.querySelectorAll("[data-harness-key]")].filter(node => node.dataset.harnessKey === control.key)[control.occurrence];
    const attributes = {
      "data-section": current?.getAttribute("data-section") || "",
      "data-harness-key": current?.getAttribute("data-harness-key") || "",
    };
    const selected = Boolean(current && (current.matches(".is-active,.is-selected") || ["true", "page"].includes(current.getAttribute("aria-selected")) || current.getAttribute("aria-pressed") === "true" || current.getAttribute("aria-checked") === "true"));
    return { clicked: true, calls: (window.__hausmanHubHarnessCalls || []).slice(before), actionLatencyMs: performance.now() - actionStartedAt, domChanged: root.innerHTML !== beforeDom, valueChanged: beforeValue !== undefined && target.value !== beforeValue, checkedChanged: beforeChecked !== undefined && target.checked !== beforeChecked, selected, editorOpen: Boolean(root.querySelector(".scenario-editor-overlay")), scrollIntoViewCalls, attributes };
  }, expected);
  const current = await withHarnessDeadline({ ...identity, phase: "audit" }, () => page.evaluate(() => ({ audit: window.__hausmanHubInteractionAudit, errors: window.__hausmanHubHarnessErrors || [] })));
  return { outcome, current };
}

test("route policy classifies local mutation and unexpected external requests while permitting the fixture image", async ({ browser }) => {
  const routeTelemetry = createRouteTelemetry();
  const context = await createStateContext(browser, routeTelemetry);
  try {
    const page = await context.newPage();
    await open(page, allStates[0]);
    expect(routeTelemetry.unexpected_local_requests).toEqual([]);
    for (const values of Object.values(routeTelemetry)) values.length = 0;

    await page.evaluate(async () => {
      await fetch("/api/hausman_hub/v1/security-probe", { method: "POST" }).catch(() => {});
      await fetch("https://example.invalid/security-probe").catch(() => {});
      await new Promise((resolve) => {
        const image = new Image();
        image.addEventListener("error", resolve, { once: true });
        image.src = "https://www.zigbee2mqtt.io/images/devices/TS0505B_1.png";
      });
      await new Promise((resolve) => {
        const image = new Image();
        image.addEventListener("error", resolve, { once: true });
        image.src = "https://www.zigbee2mqtt.io/images/devices/TS0505B_1.png?probe";
      });
    });

    expect(routeTelemetry.mutation_escape_requests).toEqual([
      { method: "POST", resource_type: "fetch", url: "http://127.0.0.1:8765/api/hausman_hub/v1/security-probe" },
    ]);
    expect(routeTelemetry.unexpected_external_requests).toEqual([
      { method: "GET", resource_type: "fetch", url: "https://example.invalid/security-probe" },
      { method: "GET", resource_type: "image", url: "https://www.zigbee2mqtt.io/images/devices/TS0505B_1.png" },
    ]);
    expect(routeTelemetry.blocked_external_requests).toEqual([
      { method: "GET", resource_type: "image", url: "https://www.zigbee2mqtt.io/images/devices/TS0505B_1.png" },
    ]);
  } finally {
    await context.close();
  }
});

test("every visible enabled HACS control is located and safely exercised in the isolated harness", async ({ browser }) => {
  test.setTimeout(30 * 60_000);
  if (!output) throw new Error("PLAYWRIGHT_OUTPUT_DIR or QA_ARTIFACT_ROOT is required");
  fs.mkdirSync(output, { recursive: true, mode: 0o700 });
  const routeTelemetry = createRouteTelemetry();
  const safeActionLatencies = [];
  const report = { provenance: releaseProvenance(), observed_source_ids: [], signatures: [], attempted_signatures: [], clicked_signatures: [], blocked_signatures: [], unrecorded_signatures: [], unclassified: [], unrecorded_commands: [], unexpected_calls: [], failed_effects: [], missing: [], errors: [], sections: {}, blocked_external_attempts: 0, external_network: false, mutation_escape: false, harness_calls: 0, safe_action_latency_ms: latencySummary(safeActionLatencies) };
  for (const state of states) {
    const stateIdentity = { state: state[0], key: null, occurrence: null, lane: null };
    let context;
    let stateFailure;
    try {
      context = await createStateContext(browser, routeTelemetry, stateIdentity);
      const page = await freshStatePage(context, state, stateIdentity);
      let controls;
      try {
        if (state[0] === "rooms") await expect(page.locator("hausman-hub-panel").locator(".rooms-canon-search")).toHaveAttribute("aria-label", "Найти комнату");
        const inventory = await withHarnessDeadline({ ...stateIdentity, phase: "inventory" }, () => page.locator("hausman-hub-panel").evaluate((host, currentState) => {
      const root = host.shadowRoot;
      const visible = node => { const style = getComputedStyle(node); return !node.disabled && !node.hidden && style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0; };
      const technicalPath = node => {
        const segments = [];
        for (let current = node; current && current !== root; current = current.parentElement) {
          const classes = String(current.className || "").trim().split(/\s+/).filter(Boolean).slice(0, 3);
          segments.unshift(`${current.tagName.toLowerCase()}${current.id ? `#${current.id}` : ""}${classes.map((item) => `.${item}`).join("")}`);
        }
        return segments.slice(-5).join(" > ");
      };
      return [...root.querySelectorAll("button,input,select,textarea,a,[role=button]")].filter(visible).map(node => ({
        key: node.dataset?.harnessKey || "",
        intent: node.dataset?.harnessIntent || "",
        tag: node.tagName,
        role: node.getAttribute("role") || "",
        type: node.getAttribute("type") || "",
        className: String(node.className || ""),
        testid: node.dataset?.testid || "",
        id: node.id || "",
        name: node.getAttribute("name") || "",
        ariaControls: node.getAttribute("aria-controls") || "",
        auditSource: node.dataset?.hmhAuditSource || "",
        technicalPath: technicalPath(node),
      }));
        }, state[0]));
        controls = withOccurrences(inventory.filter((item) => item.key));
        for (const item of inventory.filter((candidate) => !candidate.key || !candidate.intent || !intentFor(state[0], candidate.key) || intentFor(state[0], candidate.key)?.intent !== candidate.intent)) {
          report.unclassified.push({ state: state[0], key: item.key || null, intent: item.intent || null, tag: item.tag, className: item.className, testid: item.testid, id: item.id, name: item.name, ariaControls: item.ariaControls, auditSource: item.auditSource, technicalPath: item.technicalPath });
        }
        const initialAudit = await withHarnessDeadline({ ...stateIdentity, phase: "audit" }, () => page.evaluate(() => ({ audit: window.__hausmanHubInteractionAudit, errors: window.__hausmanHubHarnessErrors || [] })));
        report.observed_source_ids.push(...initialAudit.audit.sources.map(row => row.source_id), ...initialAudit.audit.listeners.map(row => row.source_id)); report.errors.push(...initialAudit.errors);
      } finally {
        await closeHarnessResource(page, stateIdentity);
      }
      report.sections[state[0]] = { visible_enabled: controls.length, attempted: 0, clicked: 0, blocked: 0 };
      if (INVENTORY_ONLY) continue;
      // Four storage-isolated lanes retain a pristine document per action while
      // allowing Chromium to use more than one CPU core. Each lane owns one
      // context and Page, so navigation in one lane cannot reset another lane.
      const laneRuns = assignControlLanes(controls, 4).map(async (lane, laneIndex) => {
        const laneIdentity = { state: state[0], key: null, occurrence: null, lane: laneIndex };
        const laneContext = laneIndex === 0 ? context : await createStateContext(browser, routeTelemetry, laneIdentity);
        let action;
        let laneCleanupError;
        try {
          action = await withHarnessDeadline({ ...laneIdentity, phase: "create" }, () => laneContext.newPage());
          const results = [];
          for (const item of lane) {
            const actionIdentity = { state: state[0], key: item.control.key, occurrence: item.control.occurrence, lane: laneIndex };
            results.push({ ...item, ...await exerciseControl(action, state, item.control, actionIdentity) });
          }
          return results;
        } finally {
          try { await closeHarnessResource(action, laneIdentity); } catch (error) { laneCleanupError = error; }
          try { if (laneIndex !== 0) await closeHarnessResource(laneContext, laneIdentity); } catch (error) { laneCleanupError ||= error; }
          if (laneCleanupError) throw laneCleanupError;
        }
      });
      const settledLanes = await Promise.allSettled(laneRuns);
      const rejectedLane = settledLanes.find((item) => item.status === "rejected");
      if (rejectedLane) throw rejectedLane.reason;
      const actionResults = settledLanes.flatMap((item) => item.value).sort((left, right) => left.index - right.index);
      for (const { control, outcome, current } of actionResults) {
          report.signatures.push(`${state[0]}:${control.key}:${control.occurrence}`);
          report.observed_source_ids.push(...current.audit.sources.map(row => row.source_id), ...current.audit.listeners.map(row => row.source_id)); report.errors.push(...current.errors);
          report.attempted_signatures.push(`${state[0]}:${control.key}:${control.occurrence}`); report.sections[state[0]].attempted += 1;
          const intent = intentFor(state[0], control.key);
          const classified = classify(intent, outcome);
          if (classified.unclassified) report.unclassified.push({ state: state[0], key: control.key });
          if (classified.unrecorded_command) report.unrecorded_commands.push({ state: state[0], key: control.key, occurrence: control.occurrence });
          if (classified.unexpected_command) report.unexpected_calls.push({ state: state[0], key: control.key, occurrence: control.occurrence, calls: mutationCalls(outcome.calls).filter((call) => !requestMatches(intent.request, call)) });
          if (intent?.intent === "ui-only" && !classified.pass && !mutationCalls(outcome.calls).length) report.failed_effects.push({ state: state[0], key: control.key, occurrence: control.occurrence });
          if (intent?.intent === "ui-only" && mutationCalls(outcome.calls).length) report.unexpected_calls.push({ state: state[0], key: control.key, occurrence: control.occurrence, calls: mutationCalls(outcome.calls) });
          if (outcome.missing || (intent?.intent === "blocked" ? outcome.clicked : !outcome.clicked)) report.missing.push({ state: state[0], key: control.key, occurrence: control.occurrence, reason: outcome.reason || "action failed" });
          else if (intent?.intent === "blocked") { report.blocked_signatures.push(`${state[0]}:${control.key}:${control.occurrence}`); report.sections[state[0]].blocked += 1; }
          else { report.clicked_signatures.push(`${state[0]}:${control.key}:${control.occurrence}`); report.sections[state[0]].clicked += 1; report.harness_calls += outcome.calls?.length || 0; safeActionLatencies.push(outcome.actionLatencyMs); }
      }
      report.observed_source_ids = [...new Set(report.observed_source_ids)].sort(); report.signatures = [...new Set(report.signatures)]; report.attempted_signatures = [...new Set(report.attempted_signatures)]; report.clicked_signatures = [...new Set(report.clicked_signatures)]; report.blocked_signatures = [...new Set(report.blocked_signatures)]; report.unrecorded_signatures = [...new Set(report.unrecorded_signatures)]; report.errors = [...new Set(report.errors)];
      report.safe_action_latency_ms = latencySummary(safeActionLatencies);
      attachRouteTelemetry(report, routeTelemetry);
      writeReport(report);
    } catch (error) {
      stateFailure = error;
      report.errors.push(error.message || String(error));
      report.safe_action_latency_ms = latencySummary(safeActionLatencies);
      attachRouteTelemetry(report, routeTelemetry);
      writeReport(report);
      throw error;
    } finally {
      try {
        await closeHarnessResource(context, stateIdentity);
      } catch (error) {
        report.errors.push(error.message || String(error));
        attachRouteTelemetry(report, routeTelemetry);
        writeReport(report);
        if (!stateFailure) throw error;
      }
    }
  }
  report.observed_source_ids = [...new Set(report.observed_source_ids)].sort(); report.signatures = [...new Set(report.signatures)]; report.attempted_signatures = [...new Set(report.attempted_signatures)]; report.clicked_signatures = [...new Set(report.clicked_signatures)]; report.blocked_signatures = [...new Set(report.blocked_signatures)]; report.unrecorded_signatures = [...new Set(report.unrecorded_signatures)]; report.errors = [...new Set(report.errors)];
  report.safe_action_latency_ms = latencySummary(safeActionLatencies);
  report.unclassified = summarizeUnclassified(report.unclassified);
  attachRouteTelemetry(report, routeTelemetry);
  expect(report.continued_requests.every((request) => request.method === "GET" && request.url.startsWith(`${HARNESS_ORIGIN}/`))).toBe(true);
  writeReport(report);
  if (INVENTORY_ONLY) return;
  expect(report.missing).toEqual([]); expect(report.errors).toEqual([]); expect(report.unclassified).toEqual([]); expect(report.unrecorded_commands).toEqual([]); expect(report.unexpected_calls).toEqual([]); expect(report.failed_effects).toEqual([]); expect(report.mutation_escape_requests).toEqual([]); expect(report.unexpected_local_requests).toEqual([]); expect(report.unexpected_external_requests).toEqual([]); expect(report.mutation_escape).toBe(report.mutation_escape_requests.length > 0); expect(report.external_network).toBe(report.unexpected_external_requests.length > 0); expect(report.mutation_escape).toBe(false); expect(report.external_network).toBe(false); expect(report.signatures.length).toBeGreaterThan(0);
});
