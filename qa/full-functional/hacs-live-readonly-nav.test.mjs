import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import tls from "node:tls";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { SECTIONS, buildHassTokens, certificatePin, classifyWebSocketPayload, countMutationTypes, isCliMain, isExecutionContextDestroyed, isLoopbackHost, loadAdminAccess, loadCaFile, navigateSectionWithRetry, normalizeHost, preflightTls, redactText, reportPassesReadonlyGuard, runCli, runLiveReadonlyNavigation, stableEvaluate, summarizeTelemetry, tlsConnectOptions, verifyPinnedPeer } from "./hacs-live-readonly-nav.mjs";

const scriptPath = fileURLToPath(new URL("./hacs-live-readonly-nav.mjs", import.meta.url));

test("access parser accepts the current JSON schema without leaking token", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hacs-live-nav-test-"));
  const file = path.join(dir, "access.json");
  fs.writeFileSync(file, JSON.stringify({ base_url: "https://ha.example:8123/", token: "secret-value" }));
  const access = loadAdminAccess(file);
  assert.equal(access.baseOrigin, "https://ha.example:8123");
  assert.equal(buildHassTokens(access.baseOrigin, access.token, 0).hassUrl, access.baseOrigin);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("non-loopback HTTP is rejected while exact loopback remains allowed", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hacs-live-nav-http-"));
  const bad = path.join(dir, "bad.json");
  const local = path.join(dir, "local.json");
  fs.writeFileSync(bad, JSON.stringify({ base_url: "http://192.168.1.20:8123", token: "secret" }));
  fs.writeFileSync(local, JSON.stringify({ base_url: "http://127.0.0.1:8123", token: "secret" }));
  assert.throws(() => loadAdminAccess(bad), /HTTPS/);
  assert.equal(loadAdminAccess(local).baseOrigin, "http://127.0.0.1:8123");
  assert.equal(isLoopbackHost("::1"), true);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("CA input is explicit, private enough, regular and valid PEM", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hacs-live-nav-ca-"));
  const valid = path.join(dir, "ca.pem");
  fs.writeFileSync(valid, tls.rootCertificates[0], { mode: 0o600 });
  assert.equal(typeof loadCaFile(valid), "string");
  assert.throws(() => loadCaFile(path.join(dir, "missing.pem")), /unavailable/);
  const unsafe = path.join(dir, "unsafe.pem");
  fs.writeFileSync(unsafe, tls.rootCertificates[0], { mode: 0o666 });
  fs.chmodSync(unsafe, 0o666);
  assert.throws(() => loadCaFile(unsafe), /unsafe/);
  const link = path.join(dir, "link.pem");
  fs.symlinkSync(valid, link);
  assert.throws(() => loadCaFile(link), /unsafe/);
  assert.deepEqual(await preflightTls("http://127.0.0.1:8123", null), { launchArgs: [] });
  fs.rmSync(dir, { recursive: true, force: true });
});

test("pinned peer verification requires raw certificate, host match and leaf or SPKI match", () => {
  const pinned = certificatePin(tls.rootCertificates[0]);
  assert.equal(verifyPinnedPeer(undefined, pinned, "127.0.0.1"), false);
  assert.equal(verifyPinnedPeer(pinned.certificate.raw, pinned, "not-a-listed-host.invalid"), false);
  const wrong = certificatePin(tls.rootCertificates[1]);
  assert.equal(verifyPinnedPeer(wrong.certificate.raw, pinned, "not-a-listed-host.invalid"), false);
});

test("all IP literals use checkIP path and omit SNI while DNS keeps SNI", () => {
  for (const origin of ["https://192.168.1.20:8123", "https://[fd00::20]:8123"]) {
    const options = tlsConnectOptions(new URL(origin), { rejectUnauthorized: true });
    assert.equal("servername" in options, false, origin);
  }
  const dns = tlsConnectOptions(new URL("https://homeassistant.local:8123"), { rejectUnauthorized: true });
  assert.equal(dns.servername, "homeassistant.local");
  assert.equal(normalizeHost("[fd00::20]"), "fd00::20");
});

test("report redaction removes credential-shaped values", () => {
  assert.equal(redactText("token=secret-value authorization: bearer-secret cookie=abc"), "token=[redacted] authorization=[redacted] cookie=[redacted]");
});

test("all ten HACS sections are fixed and unique", () => {
  assert.equal(SECTIONS.length, 10);
  assert.equal(new Set(SECTIONS).size, 10);
  assert.deepEqual(SECTIONS, ["overview", "lighting", "climate", "rooms", "media", "security", "devices", "energy", "scenarios", "settings"]);
});

test("WebSocket guard permits reads and classifies only the exact preference type as expected", () => {
  assert.deepEqual(classifyWebSocketPayload(JSON.stringify({ type: "auth" })), { type: "auth", mutation: false, expectedPreference: false, unexpectedMutation: false });
  assert.deepEqual(classifyWebSocketPayload(JSON.stringify({ type: "subscribe_entities" })), { type: "subscribe_entities", mutation: false, expectedPreference: false, unexpectedMutation: false });
  assert.deepEqual(classifyWebSocketPayload(JSON.stringify({ type: "frontend/set_user_data" })), { type: "frontend/set_user_data", mutation: true, expectedPreference: true, unexpectedMutation: false });
  for (const type of ["call_service", "config/entity_registry/update", "save_dashboard", "execute_script"]) {
    assert.equal(classifyWebSocketPayload(JSON.stringify({ type })).unexpectedMutation, true, type);
  }
  assert.equal(classifyWebSocketPayload("not json").unexpectedMutation, true);
});

function safeReport(overrides = {}) {
  return {
    sections: SECTIONS.map(section => ({ section, status: "captured", telemetry_complete: true })),
    mutation_sent: 0, unexpected_mutation_attempts: 0,
    expected_preference_attempts: 0, blocked_expected_preference_attempts: 0,
    blocked_same_origin_mutations: [], external_requests_sent: 0,
    token_leaks: 0, ...overrides,
  };
}

test("expected frontend preference is blocked and passes the read-only guard", () => {
  const telemetry = summarizeTelemetry({
    attempted: ["frontend/set_user_data"], blocked: ["frontend/set_user_data"], sent: [],
    expectedPreferenceAttempts: ["frontend/set_user_data"], blockedExpectedPreferenceAttempts: ["frontend/set_user_data"], unexpectedMutationAttempts: [], hostMutationAttempts: [], attemptDetails: [],
  });
  assert.deepEqual(telemetry.type_counts, { "frontend/set_user_data": 1 });
  assert.equal(reportPassesReadonlyGuard(safeReport(telemetry)), true);
});

test("call_service and other set types fail, while type telemetry stays redacted and count-only", () => {
  for (const type of ["call_service", "frontend/set_theme"]) {
    const telemetry = summarizeTelemetry({
      attempted: [type], blocked: [type], sent: [], expectedPreferenceAttempts: [],
      blockedExpectedPreferenceAttempts: [], unexpectedMutationAttempts: [type], hostMutationAttempts: [], attemptDetails: [{ section: "overview", type, scope: "hausman_panel", domain: null, service: null, active_control: null }],
    });
    assert.equal(reportPassesReadonlyGuard(safeReport(telemetry)), false, type);
  }
  assert.deepEqual(countMutationTypes(["call_service", "call_service"]), { call_service: 2 });
});

test("blocked host-shell service frame is reported without a target and is not attributed to the panel", () => {
  const telemetry = summarizeTelemetry({
    attempted: ["call_service"], blocked: ["call_service"], sent: [], expectedPreferenceAttempts: [],
    blockedExpectedPreferenceAttempts: [], unexpectedMutationAttempts: [], hostMutationAttempts: ["call_service"],
    attemptDetails: [{ section: "energy", type: "call_service", scope: "host_shell", domain: "scene", service: "turn_on", active_control: null }],
  });
  assert.equal(telemetry.blocked_host_mutation_attempts, 1);
  assert.equal(reportPassesReadonlyGuard(safeReport(telemetry)), true);
  assert.deepEqual(telemetry.attempts, [{ section: "energy", type: "call_service", scope: "host_shell", domain: "scene", service: "turn_on", active_control: null }]);
  assert.equal(JSON.stringify(telemetry.attempts).includes("entity"), false);
});


test("auth redirect context destruction retries once before probing the panel", async () => {
  let gotos = 0;
  let probes = 0;
  const page = {
    async goto() {
      gotos += 1;
      if (gotos === 1) throw new Error("Execution context was destroyed, most likely because of a navigation");
    },
    async waitForLoadState() {},
    locator() { return { async waitFor() { probes += 1; } }; },
    async waitForFunction() { probes += 1; },
    async evaluate() { return []; },
  };
  const result = await navigateSectionWithRetry(page, "https://ha.example/hausman-hub?hh_section=overview", "overview");
  assert.equal(result.attempt, 2);
  assert.equal(gotos, 2);
  assert.equal(probes, 2, "panel probing happens only after the successful navigation");
});

test("non-navigation failure is not retried and credential-shaped errors stay detectable", async () => {
  const page = { async goto() { throw new Error("403 forbidden"); } };
  await assert.rejects(() => navigateSectionWithRetry(page, "https://ha.example/", "overview"), /403 forbidden/);
  assert.equal(isExecutionContextDestroyed(new Error("Execution context destroyed")), true);
  assert.equal(isExecutionContextDestroyed(new Error("403 forbidden")), false);
});

function redirectingTelemetryPage({ redirectOnEvaluate }) {
  let evaluations = 0;
  let shellWaits = 0;
  return {
    get evaluations() { return evaluations; },
    get shellWaits() { return shellWaits; },
    async waitForLoadState() {},
    locator() { return { async waitFor() { shellWaits += 1; } }; },
    async waitForFunction() {},
    async evaluate() {
      evaluations += 1;
      if (evaluations === redirectOnEvaluate) {
        throw new Error("Execution context was destroyed, most likely because of a navigation");
      }
      return { attempted: [], blocked: [], sent: [], expectedPreferenceAttempts: [], blockedExpectedPreferenceAttempts: [], unexpectedMutationAttempts: [], hostMutationAttempts: [], attemptDetails: [] };
    },
  };
}

test("per-section telemetry read retries one redirect after the HA shell is stable", async () => {
  const page = redirectingTelemetryPage({ redirectOnEvaluate: 1 });
  const telemetry = await stableEvaluate(page, () => window.__hausmanLiveReadonly, "climate");
  assert.deepEqual(telemetry, { attempted: [], blocked: [], sent: [], expectedPreferenceAttempts: [], blockedExpectedPreferenceAttempts: [], unexpectedMutationAttempts: [], hostMutationAttempts: [], attemptDetails: [] });
  assert.equal(page.evaluations, 2);
  assert.equal(page.shellWaits, 2, "the shell is revalidated before the retry");
});

test("final telemetry read retries a target-close redirect and remains fail-closed", async () => {
  const page = redirectingTelemetryPage({ redirectOnEvaluate: 1 });
  const telemetry = await stableEvaluate(page, () => window.__hausmanLiveReadonly, SECTIONS.at(-1));
  assert.deepEqual(telemetry, { attempted: [], blocked: [], sent: [], expectedPreferenceAttempts: [], blockedExpectedPreferenceAttempts: [], unexpectedMutationAttempts: [], hostMutationAttempts: [], attemptDetails: [] });
  assert.equal(isExecutionContextDestroyed(new Error("Target page, context or browser has been closed")), true);
});

test("CLI main guard accepts relative, absolute, and symlink script paths", () => {
  assert.equal(isCliMain(scriptPath), true);
  assert.equal(isCliMain(path.relative(process.cwd(), scriptPath)), true);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hacs-live-nav-link-"));
  const link = path.join(dir, "nav-link.mjs");
  fs.symlinkSync(scriptPath, link);
  assert.equal(isCliMain(link), true);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("actual CLI help invokes main once and has no live side effect", () => {
  const result = spawnSync(process.execPath, [scriptPath, "--help"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.equal((result.stdout.match(/Usage:/g) || []).length, 1, result.stdout);
  assert.equal(result.stderr, "");
});

test("mock CLI dispatches main exactly once", async () => {
  let calls = 0;
  const exitCode = await runCli({
    argv: ["--help"],
    runMain: async argv => { calls += 1; assert.deepEqual(argv, ["--help"]); return 0; },
  });
  assert.equal(exitCode, 0);
  assert.equal(calls, 1);
});

function fakeBrowser({ failSection = null, externalRequestHeaders = null, sameOriginMethod = null } = {}) {
  const screenshots = [];
  let currentSection = null;
  let evaluations = 0;
  let routeHandler = null;
  let launchOptions = null;
  let contextOptions = null;
  const routeActions = [];
  const page = {
    async goto(url) {
      currentSection = new URL(url).searchParams.get("hh_section");
      if (currentSection === failSection) throw new Error(`section ${currentSection} failed`);
    },
    async waitForLoadState() {},
    locator() { return { async waitFor() {} }; },
    async waitForFunction() {},
    async evaluate(evaluator) {
      evaluations += 1;
      if (evaluations > SECTIONS.length * 2) throw new Error("Target page, context or browser has been closed");
      return String(evaluator).includes("__hausmanLiveReadonly")
        ? { attempted: [], blocked: [], sent: [], expectedPreferenceAttempts: [], blockedExpectedPreferenceAttempts: [], unexpectedMutationAttempts: [], hostMutationAttempts: [], attemptDetails: [] }
        : true;
    },
    async screenshot({ path: screenshotPath }) {
      screenshots.push(path.basename(screenshotPath));
      fs.writeFileSync(screenshotPath, "png", { mode: 0o600 });
    },
  };
  const context = {
    async addInitScript() {},
    async route(_pattern, handler) { routeHandler = handler; },
    async newPage() {
      if (sameOriginMethod) {
        await routeHandler({
          request: () => ({
            url: () => "https://ha.example/api/hausman_hub/v1/climate/actions?token=private-token",
            method: () => sameOriginMethod,
            headers: () => ({}),
          }),
          abort: async reason => { routeActions.push(`abort:${reason}`); },
          continue: async () => { routeActions.push("continue"); },
        });
      }
      if (externalRequestHeaders) {
        await routeHandler({
          request: () => ({
            url: () => "https://external.example/asset.js", method: () => "GET",
            headers: () => externalRequestHeaders,
          }),
          abort: async reason => { routeActions.push(`abort:${reason}`); },
          continue: async () => { routeActions.push("continue"); },
        });
      }
      return page;
    },
    async clearCookies() {},
    async close() {},
  };
  return {
    screenshots,
    routeActions,
    get evaluations() { return evaluations; },
    get launchOptions() { return launchOptions; },
    get contextOptions() { return contextOptions; },
    tlsPreflight: async () => ({ launchArgs: ["--ignore-certificate-errors-spki-list=test-pin"] }),
    driver: { async launch(options) { launchOptions = options; return { async newContext(options) { contextOptions = options; return context; }, async close() {} }; } },
  };
}

function testAccessFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "hacs-live-nav-access-"));
  const accessPath = path.join(dir, "access.json");
  fs.writeFileSync(accessPath, JSON.stringify({ base_url: "https://ha.example", token: "private-token" }), { mode: 0o600 });
  return { dir, accessPath };
}

test("ten section screenshots and final report come from per-section aggregate after final page destruction", async () => {
  const { dir, accessPath } = testAccessFile();
  const outputDir = path.join(dir, "artifacts");
  const fake = fakeBrowser();
  const result = await runLiveReadonlyNavigation({ accessPath, outputDir, browserDriver: fake.driver, tlsPreflight: fake.tlsPreflight });
  assert.equal(result.sections, 10);
  assert.equal(fake.evaluations, 20, "no final shell wait or evaluation is allowed");
  assert.deepEqual(fake.screenshots, SECTIONS.map(section => `${section}.png`));
  const report = JSON.parse(fs.readFileSync(path.join(outputDir, "report.json"), "utf8"));
  assert.deepEqual(report.completed_sections, SECTIONS);
  assert.equal(report.sections.length, 10);
  assert.deepEqual(report.type_counts, {});
  assert.equal(report.blocked_external_requests, 0);
  assert.equal(report.external_requests_sent, 0);
  assert.deepEqual(fake.launchOptions.args, ["--ignore-certificate-errors-spki-list=test-pin"]);
  assert.equal(fake.contextOptions.ignoreHTTPSErrors, false);
  assert.equal(fake.contextOptions.serviceWorkers, "block");
  assert.equal(fs.statSync(path.join(outputDir, "report.json")).mode & 0o777, 0o600);
  for (const section of SECTIONS) assert.equal(fs.statSync(path.join(outputDir, `${section}.png`)).mode & 0o777, 0o600);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("blocked external request without credentials passes and does not count as sent", async () => {
  const { dir, accessPath } = testAccessFile();
  const outputDir = path.join(dir, "artifacts");
  const fake = fakeBrowser({ externalRequestHeaders: {} });
  await runLiveReadonlyNavigation({ accessPath, outputDir, browserDriver: fake.driver, tlsPreflight: fake.tlsPreflight });
  const report = JSON.parse(fs.readFileSync(path.join(outputDir, "report.json"), "utf8"));
  assert.equal(report.blocked_external_requests, 1);
  assert.equal(report.external_requests_sent, 0);
  assert.equal(report.token_leaks, 0);
  assert.deepEqual(fake.routeActions, ["abort:blockedbyclient"]);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("external authorization header increments the token-leak count and fails", async () => {
  const { dir, accessPath } = testAccessFile();
  const outputDir = path.join(dir, "artifacts");
  const fake = fakeBrowser({ externalRequestHeaders: { authorization: "Bearer forbidden" } });
  await assert.rejects(
    () => runLiveReadonlyNavigation({ accessPath, outputDir, browserDriver: fake.driver, tlsPreflight: fake.tlsPreflight }),
    /did not complete safely/,
  );
  const report = JSON.parse(fs.readFileSync(path.join(outputDir, "report.json"), "utf8"));
  assert.equal(report.blocked_external_requests, 1);
  assert.equal(report.external_requests_sent, 0);
  assert.equal(report.token_leaks, 1);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("same-origin POST and PUT are blocked with redacted request telemetry", async () => {
  for (const method of ["POST", "PUT"]) {
    const { dir, accessPath } = testAccessFile();
    const outputDir = path.join(dir, "artifacts");
    const fake = fakeBrowser({ sameOriginMethod: method });
    await assert.rejects(
      () => runLiveReadonlyNavigation({ accessPath, outputDir, browserDriver: fake.driver, tlsPreflight: fake.tlsPreflight }),
      /did not complete safely/,
    );
    const report = JSON.parse(fs.readFileSync(path.join(outputDir, "report.json"), "utf8"));
    assert.deepEqual(report.blocked_same_origin_mutations, [{
      method, pathname: "/api/hausman_hub/v1/climate/actions",
    }]);
    assert.equal(JSON.stringify(report).includes("private-token"), false);
    assert.equal(report.external_requests_sent, 0);
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("TLS failure stops before browser launch or token injection", async () => {
  const { dir, accessPath } = testAccessFile();
  let launched = false;
  const driver = { async launch() { launched = true; throw new Error("must not launch"); } };
  await assert.rejects(
    () => runLiveReadonlyNavigation({
      accessPath, outputDir: path.join(dir, "artifacts"), browserDriver: driver,
      tlsPreflight: async () => { throw new Error("TLS authentication failed"); },
    }),
    /TLS authentication failed/,
  );
  assert.equal(launched, false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("writes a redacted partial report when a section fails", async () => {
  const { dir, accessPath } = testAccessFile();
  const outputDir = path.join(dir, "artifacts");
  const fake = fakeBrowser({ failSection: "climate" });
  await assert.rejects(
    () => runLiveReadonlyNavigation({ accessPath, outputDir, browserDriver: fake.driver, tlsPreflight: fake.tlsPreflight }),
    /did not complete safely/,
  );
  const report = JSON.parse(fs.readFileSync(path.join(outputDir, "report.json"), "utf8"));
  assert.deepEqual(report.completed_sections.slice(0, 2), ["overview", "lighting"]);
  assert.equal(report.sections.find(item => item.section === "climate").status, "failed");
  assert.equal(JSON.stringify(report).includes("private-token"), false);
  assert.equal(fs.statSync(path.join(outputDir, "report.json")).mode & 0o777, 0o600);
  fs.rmSync(dir, { recursive: true, force: true });
});
