#!/usr/bin/env node
// Live HACS navigation evidence. This helper intentionally has no control path.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import tls from "node:tls";
import { isIP } from "node:net";
import { createHash, timingSafeEqual, X509Certificate } from "node:crypto";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

export const SECTIONS = [
  "overview", "lighting", "climate", "rooms", "media",
  "security", "devices", "energy", "scenarios", "settings",
];
const MUTATION = /(?:^|[_/.-])(call_service|create|update|delete|save|set|execute)(?:$|[_/.-])/i;
const EXPECTED_PREFERENCE_TYPE = "frontend/set_user_data";

export function loadAdminAccess(accessPath) {
  if (!accessPath) throw new Error("An explicit admin access file path is required");
  const raw = fs.readFileSync(accessPath, "utf8");
  let payload;
  try { payload = JSON.parse(raw); } catch { throw new Error("Access file must be a JSON object"); }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("Access file must be a JSON object");
  const baseUrl = payload.base_url;
  const token = payload.token;
  if (typeof baseUrl !== "string" || !/^https?:\/\//i.test(baseUrl.trim())) throw new Error("Access file has no valid base_url");
  if (typeof token !== "string" || !token.trim()) throw new Error("Access file has no token");
  const url = new URL(baseUrl.trim());
  if (url.username || url.password || url.search || url.hash) throw new Error("Access file base_url must be an origin without credentials or query");
  if (url.protocol === "http:" && !isLoopbackHost(url.hostname)) throw new Error("Non-loopback base_url must use HTTPS");
  return { baseOrigin: url.origin, token: token.trim() };
}

export function isLoopbackHost(hostname) {
  const value = normalizeHost(hostname).toLowerCase();
  return value === "localhost" || value === "127.0.0.1" || value === "::1";
}

export function normalizeHost(hostname) {
  return String(hostname).replace(/^\[|\]$/g, "");
}

export function loadCaFile(caFile) {
  if (!caFile) return undefined;
  let metadata;
  try { metadata = fs.lstatSync(caFile); } catch { throw new Error("TLS CA file is unavailable"); }
  if (!metadata.isFile() || metadata.isSymbolicLink() || (metadata.mode & 0o022)) {
    throw new Error("TLS CA file is unsafe");
  }
  let pem;
  try { pem = fs.readFileSync(caFile, "utf8"); new X509Certificate(pem); } catch { throw new Error("TLS CA file is invalid"); }
  return pem;
}

export function certificatePin(certificate) {
  const value = certificate instanceof X509Certificate ? certificate : new X509Certificate(certificate);
  const leaf = createHash("sha256").update(value.raw).digest();
  const spki = createHash("sha256").update(value.publicKey.export({ type: "spki", format: "der" })).digest();
  return { certificate: value, leaf, spki, chromiumSpki: spki.toString("base64") };
}

export function verifyPinnedPeer(peerRaw, pinned, hostname) {
  if (!peerRaw) return false;
  try {
    const peer = certificatePin(peerRaw);
    const normalizedHost = normalizeHost(hostname);
    const hostMatches = isIP(normalizedHost)
      ? peer.certificate.checkIP(normalizedHost) !== undefined
      : peer.certificate.checkHost(normalizedHost) !== undefined;
    return hostMatches && (
      timingSafeEqual(peer.leaf, pinned.leaf)
      || timingSafeEqual(peer.spki, pinned.spki)
    );
  } catch { return false; }
}

export function tlsConnectOptions(url, { ca, rejectUnauthorized } = {}) {
  const hostname = normalizeHost(url.hostname);
  return {
    host: hostname,
    port: Number(url.port || 443),
    ...(isIP(hostname) ? {} : { servername: hostname }),
    rejectUnauthorized,
    ...(ca ? { ca } : {}),
  };
}

export async function preflightTls(baseOrigin, caFile, { connect = tls.connect } = {}) {
  const url = new URL(baseOrigin);
  if (url.protocol === "http:" && isLoopbackHost(url.hostname)) return { launchArgs: [] };
  if (url.protocol !== "https:") throw new Error("TLS is required for this base_url");
  const trustPem = loadCaFile(caFile);
  const pinned = trustPem ? certificatePin(trustPem) : null;
  const caMode = pinned?.certificate.ca === true;
  return new Promise((resolve, reject) => {
    const socket = connect(tlsConnectOptions(url, {
      rejectUnauthorized: caMode || !pinned,
      ...(caMode ? { ca: trustPem } : {}),
    }));
    const fail = () => { socket.destroy(); reject(new Error("TLS authentication failed")); };
    socket.once("error", fail);
    socket.once("secureConnect", () => {
      try {
        if (caMode && !socket.authorized) return fail();
        const certificate = socket.getPeerCertificate(true);
        if (!certificate?.raw) return fail();
        if (pinned && !caMode && !verifyPinnedPeer(certificate.raw, pinned, url.hostname)) return fail();
        const pin = certificatePin(certificate.raw).chromiumSpki;
        socket.end();
        resolve({ launchArgs: [`--ignore-certificate-errors-spki-list=${pin}`] });
      } catch { fail(); }
    });
  });
}

export function buildHassTokens(baseOrigin, token, now = Date.now()) {
  return {
    access_token: token,
    token_type: "Bearer",
    expires: now + 86_400_000,
    expires_in: 86_400,
    hassUrl: baseOrigin,
  };
}

export function classifyWebSocketPayload(raw) {
  try {
    const parsed = JSON.parse(String(raw));
    const type = typeof parsed?.type === "string" ? parsed.type : "";
    const expectedPreference = type === EXPECTED_PREFERENCE_TYPE;
    return {
      type: type || "non-json",
      mutation: expectedPreference || MUTATION.test(type),
      expectedPreference,
      unexpectedMutation: !expectedPreference && MUTATION.test(type),
    };
  } catch {
    return { type: "non-json", mutation: true, expectedPreference: false, unexpectedMutation: true };
  }
}

export function redactText(value) {
  return String(value).replace(/(access_token|token|authorization|cookie)\s*[:=]\s*[^,\s}\]]+/gi, "$1=[redacted]");
}

function parseArgs(argv) {
  let accessPath = null;
  let outputDir = null;
  let caFile = null;
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--help" || value === "-h") return { help: true };
    if (value === "--access-file") accessPath = argv[++index];
    else if (value === "--output-dir") outputDir = argv[++index];
    else if (value === "--ca-file") caFile = argv[++index];
    else throw new Error(`Unknown argument: ${value}`);
  }
  if (!accessPath) throw new Error("Usage: node qa/full-functional/hacs-live-readonly-nav.mjs --access-file /protected/admin.json [--ca-file /protected/ca.pem] [--output-dir /protected/output]");
  return { accessPath, outputDir, caFile, help: false };
}

function secureDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  fs.chmodSync(directory, 0o700);
}

function writePrivateJson(file, value) {
  const temporary = path.join(path.dirname(file), `.${path.basename(file)}.${process.pid}.${Date.now()}.tmp`);
  try {
    fs.writeFileSync(temporary, JSON.stringify(value, null, 2), { mode: 0o600 });
    fs.chmodSync(temporary, 0o600);
    fs.renameSync(temporary, file);
    fs.chmodSync(file, 0o600);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function writePartialReport(outputDir, report) {
  writePrivateJson(path.join(outputDir, "report.json"), {
    ...report,
    completed_sections: report.sections.filter(item => item.status === "captured").map(item => item.section),
  });
}

const websocketGuard = `(() => {
  const telemetry = {
    attempted: [], blocked: [], sent: [], expectedPreferenceAttempts: [],
    blockedExpectedPreferenceAttempts: [], unexpectedMutationAttempts: [],
    hostMutationAttempts: [], attemptDetails: []
  };
  const mutation = /(?:^|[_/.-])(call_service|create|update|delete|save|set|execute)(?:$|[_/.-])/i;
  const expectedPreferenceType = "frontend/set_user_data";
  const sectionNames = new Set(["overview", "lighting", "climate", "rooms", "media", "security", "devices", "energy", "scenarios", "settings"]);
  const safeServicePart = (value) => typeof value === "string" && /^[a-z0-9_]{1,64}$/i.test(value) ? value : null;
  const activeSection = () => {
    const findPanel = (root) => {
      for (const node of root.querySelectorAll("*")) {
        if (node.tagName && node.tagName.toLowerCase() === "hausman-hub-panel") return node;
        if (node.shadowRoot) { const nested = findPanel(node.shadowRoot); if (nested) return nested; }
      }
      return null;
    };
    const panel = findPanel(document);
    const section = panel && panel.shadowRoot && [...panel.shadowRoot.querySelectorAll("[id^=hausman-]")]
      .find((node) => !node.hidden && node.offsetParent !== null);
    const name = section ? section.id.replace(/^hausman-/, "") : "unknown";
    return sectionNames.has(name) ? name : "unknown";
  };
  const callerScope = () => {
    const stack = String(new Error().stack || "");
    return /\\/api\\/hausman_hub\\/panel\\/|hausman-hub-[a-z0-9-]+\\.js/i.test(stack)
      ? "hausman_panel" : "host_shell";
  };
  const activeControl = () => {
    const node = document.activeElement;
    return node && /^(button|input|select|textarea)$/i.test(node.tagName || "")
      ? String(node.tagName).toLowerCase() : null;
  };
  const original = WebSocket.prototype.send;
  WebSocket.prototype.send = function(payload) {
    let type = "non-json";
    let isJson = false;
    let parsed = null;
    try {
      parsed = JSON.parse(String(payload));
      type = typeof parsed?.type === "string" && parsed.type ? parsed.type : "non-json";
      isJson = true;
    } catch (_) {}
    const expectedPreference = isJson && type === expectedPreferenceType;
    const isMutation = !isJson || expectedPreference || mutation.test(type);
    if (isMutation) {
      const scope = callerScope();
      telemetry.attempted.push(type);
      if (expectedPreference) telemetry.expectedPreferenceAttempts.push(type);
      else if (scope === "hausman_panel") telemetry.unexpectedMutationAttempts.push(type);
      else telemetry.hostMutationAttempts.push(type);
      telemetry.blocked.push(type);
      if (expectedPreference) telemetry.blockedExpectedPreferenceAttempts.push(type);
      telemetry.attemptDetails.push({
        section: activeSection(), type, scope,
        domain: safeServicePart(parsed?.domain), service: safeServicePart(parsed?.service),
        active_control: activeControl(),
      });
      throw new Error("QA blocked WebSocket mutation: " + type);
    }
    return original.call(this, payload);
  };
  Object.defineProperty(window, "__hausmanLiveReadonly", { value: telemetry, configurable: false });
})()`;

const panelProbe = (section) => `(() => {
  const visit = (root) => {
    for (const el of root.querySelectorAll("*")) {
      if (el.tagName?.toLowerCase() === "hausman-hub-panel") return el;
      if (el.shadowRoot) { const nested = visit(el.shadowRoot); if (nested) return nested; }
    }
    return null;
  };
  const panel = visit(document);
  const sectionNode = panel?.shadowRoot?.getElementById("hausman-${section}");
  return Boolean(sectionNode && !sectionNode.hidden && (sectionNode.textContent || "").trim().length > 50);
})()`;

async function waitForStableSection(page, section) {
  await page.locator("home-assistant").waitFor({ state: "attached", timeout: 45_000 });
  await page.waitForFunction(panelProbe(section), undefined, { timeout: 45_000 });
}

export function isExecutionContextDestroyed(error) {
  return /Execution context (?:was )?destroyed|Cannot find context with specified id|Target page, context or browser has been closed/i.test(String(error?.message || error));
}

/**
 * Read page state only after both the Home Assistant shell and the requested
 * panel are stable. A redirect can destroy either wait or evaluation once.
 */
export async function stableEvaluate(page, evaluator, section, attempts = 2) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await page.waitForLoadState("domcontentloaded", { timeout: 45_000 });
      await waitForStableSection(page, section);
      return await page.evaluate(evaluator);
    } catch (error) {
      lastError = error;
      if (!isExecutionContextDestroyed(error) || attempt === attempts) break;
      await page.waitForLoadState("domcontentloaded", { timeout: 45_000 }).catch(() => {});
    }
  }
  throw lastError;
}

function validTelemetry(value) {
  return value && [
    "attempted", "blocked", "sent", "expectedPreferenceAttempts",
    "blockedExpectedPreferenceAttempts", "unexpectedMutationAttempts", "hostMutationAttempts",
    "attemptDetails",
  ].every(key => Array.isArray(value[key]));
}

export function countMutationTypes(types) {
  return Object.fromEntries([...types]
    .map(type => redactText(type))
    .sort()
    .reduce((counts, type) => {
      counts.set(type, (counts.get(type) || 0) + 1);
      return counts;
    }, new Map()));
}

export function summarizeTelemetry(telemetry) {
  if (!validTelemetry(telemetry)) throw new Error("Read-only WebSocket telemetry is unavailable");
  return {
    telemetry_complete: true,
    mutation_attempts: telemetry.attempted.length,
    mutation_sent: telemetry.sent.length,
    blocked_mutations: telemetry.blocked.length,
    expected_preference_attempts: telemetry.expectedPreferenceAttempts.length,
    blocked_expected_preference_attempts: telemetry.blockedExpectedPreferenceAttempts.length,
    unexpected_mutation_attempts: telemetry.unexpectedMutationAttempts.length,
    blocked_host_mutation_attempts: telemetry.hostMutationAttempts.length,
    type_counts: countMutationTypes(telemetry.attempted),
    attempts: telemetry.attemptDetails,
  };
}

export function reportPassesReadonlyGuard(report) {
  return report.sections.length === SECTIONS.length
    && report.sections.every(item => item.status === "captured" && item.telemetry_complete)
    && report.mutation_sent === 0
    && report.unexpected_mutation_attempts === 0
    && report.expected_preference_attempts === report.blocked_expected_preference_attempts
    && report.external_requests_sent === 0
    && report.blocked_same_origin_mutations.length === 0
    && report.token_leaks === 0;
}

async function readGuardTelemetry(page, section) {
  const telemetry = await stableEvaluate(page, () => window.__hausmanLiveReadonly, section);
  return summarizeTelemetry(telemetry);
}

/**
 * Go to one read-only section, recovering once from Home Assistant's initial
 * auth redirect.  The init scripts belong to the BrowserContext, so a reload
 * cannot lose the in-memory hassTokens value.
 */
export async function navigateSectionWithRetry(page, url, section, attempts = 2) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45_000 });
      await page.waitForLoadState("domcontentloaded", { timeout: 45_000 });
      await stableEvaluate(page, () => true, section);
      return { attempt };
    } catch (error) {
      lastError = error;
      if (!isExecutionContextDestroyed(error) || attempt === attempts) break;
      // Never call page.evaluate while a document is being replaced.
      await page.waitForLoadState("domcontentloaded", { timeout: 45_000 }).catch(() => {});
    }
  }
  throw lastError;
}

export async function runLiveReadonlyNavigation({ accessPath, outputDir, caFile = null, browserDriver = chromium, tlsPreflight = preflightTls }) {
  const access = loadAdminAccess(accessPath);
  const out = outputDir || fs.mkdtempSync(path.join(os.tmpdir(), "hausman-hacs-live-readonly-"));
  secureDirectory(out);
  const report = {
    sections: [], mutation_attempts: 0, mutation_sent: 0, blocked_mutations: 0,
    expected_preference_attempts: 0, blocked_expected_preference_attempts: 0,
    unexpected_mutation_attempts: 0, blocked_host_mutation_attempts: 0,
    type_counts: {}, blocked_external_requests: 0,
    blocked_same_origin_mutations: [],
    external_requests_sent: 0, token_leaks: 0,
  };
  // Complete certificate authentication before creating a browser context or
  // placing the protected token into browser storage.
  const tlsProof = await tlsPreflight(access.baseOrigin, caFile);
  const tokens = buildHassTokens(access.baseOrigin, access.token);
  const browser = await browserDriver.launch({ headless: true, args: tlsProof.launchArgs });
  // The explicitly supplied Home Assistant origin currently uses a private
  // CA.  This helper already restricts every request to that origin and never
  // exposes credentials to other origins, so allow the browser to inspect the
  // local UI without treating the private certificate as a navigation failure.
  const context = await browser.newContext({ ignoreHTTPSErrors: false, serviceWorkers: "block" });
  try {
    await context.addInitScript(websocketGuard);
    await context.addInitScript(({ origin }) => {
      const OriginalWebSocket = WebSocket;
      const allowed = new URL(origin);
      window.WebSocket = function(url, protocols) {
        const target = new URL(String(url), location.href);
        const expectedProtocol = allowed.protocol === "https:" ? "wss:" : "ws:";
        if (
          target.protocol !== expectedProtocol
          || target.hostname !== allowed.hostname
          || target.port !== allowed.port
        ) {
          throw new Error("QA blocked cross-origin WebSocket");
        }
        return protocols === undefined
          ? new OriginalWebSocket(url)
          : new OriginalWebSocket(url, protocols);
      };
      window.WebSocket.prototype = OriginalWebSocket.prototype;
    }, { origin: access.baseOrigin });
    await context.addInitScript(({ storedTokens }) => localStorage.setItem("hassTokens", storedTokens), {
      storedTokens: JSON.stringify(tokens),
    });
    await context.route("**/*", async route => {
      const request = route.request();
      const requestUrl = new URL(request.url());
      if (requestUrl.origin !== access.baseOrigin) {
        report.blocked_external_requests += 1;
        const headers = request.headers();
        const urlContainsToken = request.url().includes(access.token) || request.url().includes(encodeURIComponent(access.token));
        if (urlContainsToken || headers.authorization || headers.cookie) report.token_leaks += 1;
        return route.abort("blockedbyclient");
      }
      if (!["GET", "HEAD"].includes(request.method())) {
        report.blocked_same_origin_mutations.push({
          method: request.method(), pathname: requestUrl.pathname,
        });
        return route.abort("blockedbyclient");
      }
      return route.continue();
    });
    const page = await context.newPage();
    for (const section of SECTIONS) {
      try {
        await navigateSectionWithRetry(
          page,
          `${access.baseOrigin}/hausman-hub?hh_section=${encodeURIComponent(section)}`,
          section,
        );
        const screenshot = path.join(out, `${section}.png`);
        await page.screenshot({ path: screenshot, fullPage: false });
        fs.chmodSync(screenshot, 0o600);
        // Collect before leaving this stable section. This makes an auth
        // redirect on the next navigation incapable of losing guard evidence.
        const telemetry = await readGuardTelemetry(page, section);
        for (const key of [
          "mutation_attempts", "mutation_sent", "blocked_mutations", "expected_preference_attempts",
          "blocked_expected_preference_attempts", "unexpected_mutation_attempts",
          "blocked_host_mutation_attempts",
        ]) report[key] += telemetry[key];
        for (const [type, count] of Object.entries(telemetry.type_counts)) {
          report.type_counts[type] = (report.type_counts[type] || 0) + count;
        }
        report.sections.push({
          section, status: "captured", ...telemetry,
        });
      } catch (error) {
        report.sections.push({ section, status: "failed", telemetry_complete: false, error_code: "section_capture_failed" });
      } finally {
        // Persist evidence after every section so an interrupted run never
        // loses the already captured, redacted read-only telemetry.
        writePartialReport(out, report);
      }
    }
    // Do not probe the final page here: a redirect after the tenth screenshot
    // must not erase evidence already captured from each stable section.
    writePartialReport(out, report);
    if (!reportPassesReadonlyGuard(report)) {
      throw new Error("Read-only navigation did not complete safely");
    }
    return { outputDir: out, sections: report.sections.length };
  } finally {
    await context.clearCookies().catch(() => {});
    await context.close();
    await browser.close();
    access.token = "";
  }
}

export function isCliMain(argv1 = process.argv[1], moduleUrl = import.meta.url) {
  if (!argv1) return false;
  try {
    return fs.realpathSync(path.resolve(argv1)) === fs.realpathSync(fileURLToPath(moduleUrl));
  } catch {
    return false;
  }
}

export async function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv);
    if (args.help) {
      console.log("Usage: node qa/full-functional/hacs-live-readonly-nav.mjs --access-file /protected/admin.json [--ca-file /protected/ca.pem] [--output-dir /protected/output]");
      return 0;
    }
    const result = await runLiveReadonlyNavigation(args);
    console.log(`PASS: read-only navigation captured ${result.sections} sections. Private artifacts: ${result.outputDir}`);
    return 0;
  } catch (error) {
    console.error(`FAIL: ${redactText(error.message)}`);
    return 1;
  }
}

export async function runCli({ argv = process.argv.slice(2), runMain = main } = {}) {
  return runMain(argv);
}

if (isCliMain()) process.exitCode = await runCli();
