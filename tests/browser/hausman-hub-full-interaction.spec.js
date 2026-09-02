import { expect, test } from "@playwright/test";
import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";

const HARNESS = "/tests/visual/hausman-hub-panel-harness.html";
const FIXED_NOW = "2026-08-23T02:15:00.000Z";
const PANEL_READY_TIMEOUT_MS = 45_000;
const states = [
  ["overview", ""], ["lighting", ""], ["climate", ""], ["rooms", ""], ["media", ""],
  ["security", ""], ["devices", ""], ["energy", ""], ["scenarios", ""], ["settings", ""],
  ["climate-profiles", "&screen=profiles"], ["settings-rooms", "&screen=rooms"],
  ["scenarios-editor", "&openScenario=Доброе"], ["scenarios-nodered", "&nodeRedEditor=1"], ["kiosk", "&kiosk=1"],
];
const output = process.env.PLAYWRIGHT_OUTPUT_DIR || process.env.QA_ARTIFACT_ROOT;
const ROOT = path.resolve(process.cwd());
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
  const interactions = JSON.parse(fs.readFileSync(path.join(ROOT, "qa/full-functional/hacs-interactions.json"), "utf8")).interactions;
  digest.update(`version\0${manifest.version}\0`, "utf8");
  digest.update("interactions\0", "utf8");
  digest.update(canonicalJson(interactions), "utf8");
  digest.update("\0", "utf8");
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

// This exhaustive audit creates a fresh document for every control.  Keep its
// artifacts off: retaining failure traces would otherwise retain hundreds of
// complete browser contexts and turn a resource check into a disk-pressure run.
test.use({ trace: "off", video: "off", screenshot: "off" });

function auditInit() {
  // Four application frames are enough to reach el()'s caller and avoid
  // turning a rendering audit into an unbounded stack-formatting workload.
  Error.stackTraceLimit = 5;
  const registry = window.__hausmanHubInteractionAudit = { sources: [], listeners: [], network: [], errors: [] };
  const sourceIds = new Set();
  let stackCaptures = 0;
  const sources = new WeakMap();
  const control = tag => ["BUTTON", "INPUT", "SELECT", "TEXTAREA", "A"].includes(String(tag).toUpperCase());
  const caller = (construct) => {
    // A fixture may repeatedly redraw the same card.  Capturing a bounded set
    // per freshly loaded state records source sites without making audit-only
    // stack collection alter the page's runtime characteristics.
    if (++stackCaptures > 80) return null;
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
      const source = caller("listener");
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
  localStorage.clear();
  sessionStorage.clear();
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
  const errors = await page.evaluate(() => window.__hausmanHubHarnessErrors || []);
  expect(errors, `${state[0]} harness errors`).toEqual([]);
}

function writeReport(report) {
  const file = path.join(output, "hacs-full-interaction-runtime.json");
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(report, null, 2), { mode: 0o600 });
  fs.renameSync(temp, file);
}

async function createStateContext(browser, routeTelemetry) {
  const context = await browser.newContext();
  await context.addInitScript(resetLocalStateAndFreezeClock, FIXED_NOW);
  await context.addInitScript(auditInit);
  await context.route("**/*", async route => {
    const url = new URL(route.request().url());
    const local = ["", "localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (["file:", "http:", "https:"].includes(url.protocol) && local) return route.continue();
    // Never put query parameters into QA artifacts, and retain only a small
    // sample. The aggregate count below is the authoritative telemetry.
    routeTelemetry.blockedAttempts += 1;
    if (routeTelemetry.blocked.length < 32) routeTelemetry.blocked.push(`${url.protocol}//${url.host}${url.pathname}`);
    await route.abort("blockedbyclient");
  });
  return context;
}

async function freshStatePage(context, state) {
  const page = await context.newPage();
  await open(page, state);
  return page;
}

test("every visible enabled HACS control is located and safely exercised in the isolated harness", async ({ browser }) => {
  test.setTimeout(30 * 60_000);
  if (!output) throw new Error("PLAYWRIGHT_OUTPUT_DIR or QA_ARTIFACT_ROOT is required");
  fs.mkdirSync(output, { recursive: true, mode: 0o700 });
  const routeTelemetry = { blocked: [], blockedAttempts: 0, continuedExternal: [] };
  const report = { provenance: releaseProvenance(), observed_source_ids: [], signatures: [], attempted_signatures: [], clicked_signatures: [], missing: [], errors: [], sections: {}, blocked_external_attempts: 0, external_network: false, mutation_escape: false, harness_calls: 0 };
  for (const state of states) {
    const context = await createStateContext(browser, routeTelemetry);
    try {
      const page = await freshStatePage(context, state);
      let controls;
      try {
        if (state[0] === "rooms") await expect(page.locator("hausman-hub-panel").locator(".rooms-canon-search")).toHaveAttribute("aria-label", "Найти комнату");
        controls = await page.locator("hausman-hub-panel").evaluate((host, currentState) => {
      const root = host.shadowRoot;
      const text = node => (node.textContent || "").trim().replace(/\s+/g, " ").slice(0, 160);
      const name = node => node.getAttribute("aria-label") || (node.id && root.querySelector(`label[for="${CSS.escape(node.id)}"]`)?.textContent.trim()) || node.getAttribute("title") || text(node);
      const visible = node => { const style = getComputedStyle(node); return !node.disabled && !node.hidden && style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0; };
      const seen = new Map();
      return [...root.querySelectorAll("button,input,select,textarea,a,[role=button]")].filter(visible).map(node => {
        const signature = [currentState, node.tagName, node.getAttribute("role") || "", node.getAttribute("type") || "", name(node)].join("|");
        const occurrence = seen.get(signature) || 0; seen.set(signature, occurrence + 1);
        return { signature: `${signature}|${occurrence}`, tag: node.tagName, role: node.getAttribute("role") || "", type: node.getAttribute("type") || "", name: name(node), occurrence };
      });
        }, state[0]);
        const initialAudit = await page.evaluate(() => ({ audit: window.__hausmanHubInteractionAudit, errors: window.__hausmanHubHarnessErrors || [] }));
        report.observed_source_ids.push(...initialAudit.audit.sources.map(row => row.source_id), ...initialAudit.audit.listeners.map(row => row.source_id)); report.errors.push(...initialAudit.errors);
      } finally {
        await page.close();
      }
      report.sections[state[0]] = { visible_enabled: controls.length, attempted: 0, clicked: 0 };
      for (const control of controls) {
        report.signatures.push(control.signature);
        const action = await freshStatePage(context, state);
        let outcome;
        try {
          outcome = await action.locator("hausman-hub-panel").evaluate((host, expected) => {
        const root = host.shadowRoot, text = node => (node.textContent || "").trim().replace(/\s+/g, " ").slice(0, 160);
        const name = node => node.getAttribute("aria-label") || (node.id && root.querySelector(`label[for="${CSS.escape(node.id)}"]`)?.textContent.trim()) || node.getAttribute("title") || text(node);
        const visible = node => { const style = getComputedStyle(node); return !node.disabled && !node.hidden && style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0; };
        const matches = [...root.querySelectorAll("button,input,select,textarea,a,[role=button]")].filter(visible).filter(node => node.tagName === expected.tag && (node.getAttribute("role") || "") === expected.role && (node.getAttribute("type") || "") === expected.type && name(node) === expected.name);
        const target = matches[expected.occurrence]; if (!target) return { missing: true, reason: "control disappeared" };
        const before = (window.__hausmanHubHarnessCalls || []).length;
        if (target.tagName === "SELECT") target.value = target.options[Math.min(1, target.options.length - 1)]?.value || target.value, target.dispatchEvent(new Event("change", { bubbles: true }));
        else if (target.tagName === "INPUT" && ["text", "search", ""].includes(target.type)) target.value = "qa", target.dispatchEvent(new Event("input", { bubbles: true })), target.dispatchEvent(new Event("change", { bubbles: true }));
        else target.click();
        const after = (window.__hausmanHubHarnessCalls || []).length;
        return { clicked: true, harnessRecorded: after >= before, calls: after - before };
          }, control);
          const current = await action.evaluate(() => ({ audit: window.__hausmanHubInteractionAudit, errors: window.__hausmanHubHarnessErrors || [] }));
          report.observed_source_ids.push(...current.audit.sources.map(row => row.source_id), ...current.audit.listeners.map(row => row.source_id)); report.errors.push(...current.errors);
        } finally {
          await action.close();
        }
        report.attempted_signatures.push(control.signature); report.sections[state[0]].attempted += 1;
        if (outcome.missing || !outcome.clicked) report.missing.push({ state: state[0], signature: control.signature, reason: outcome.reason || "action failed" });
        else { report.clicked_signatures.push(control.signature); report.sections[state[0]].clicked += 1; report.harness_calls += outcome.calls; if (!outcome.harnessRecorded) report.mutation_escape = true; }
      }
      report.observed_source_ids = [...new Set(report.observed_source_ids)].sort(); report.signatures = [...new Set(report.signatures)]; report.attempted_signatures = [...new Set(report.attempted_signatures)]; report.clicked_signatures = [...new Set(report.clicked_signatures)]; report.errors = [...new Set(report.errors)];
      report.blocked_external_attempts = routeTelemetry.blockedAttempts;
      writeReport(report);
    } finally {
      await context.close();
    }
  }
  report.observed_source_ids = [...new Set(report.observed_source_ids)].sort(); report.signatures = [...new Set(report.signatures)]; report.attempted_signatures = [...new Set(report.attempted_signatures)]; report.clicked_signatures = [...new Set(report.clicked_signatures)]; report.errors = [...new Set(report.errors)];
  report.external_network = routeTelemetry.continuedExternal.length > 0; report.blocked_external_attempts = routeTelemetry.blockedAttempts;
  writeReport(report);
  expect(report.missing).toEqual([]); expect(report.errors).toEqual([]); expect(report.external_network).toBe(false); expect(report.mutation_escape).toBe(false); expect(report.signatures.length).toBeGreaterThan(0);
});
