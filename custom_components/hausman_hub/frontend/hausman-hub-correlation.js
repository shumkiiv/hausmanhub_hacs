/* Pinned snapshot of contracts inventory/correlation-surfaces.json */
/* (hausman-hub-correlation-surfaces v1, contracts 0.35.0). Fail-closed: an */
/* invalid client ID is rejected before the API call, an unknown command path */
/* gets no injected field, and a legacy payload without the optional ID keeps */
/* rendering. The ID is never an idempotency key, authorization or read-back. */
/* Internal symbols carry the corr prefix: panel tests load every module into */
/* one shared vm scope. */

const SURFACES_CONTRACT = { name: "hausman-hub-correlation-surfaces", version: 1 };

const SURFACES_SNAPSHOT = {
  "contract": {
    "name": "hausman-hub-correlation-surfaces",
    "version": 1
  },
  "idPattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
  "generation": {
    "clientMayProvide": true,
    "serverGeneratesWhenMissing": true,
    "serverMustPreserveValidClientValue": true,
    "fallbackToRequestIdAllowed": true
  },
  "commands": [
    {
      "operationId": "applyHausmanHubContour",
      "requestSchema": "schemas/v1/contour-apply-request.schema.json",
      "requestField": "correlation_id",
      "receiptSchema": "schemas/v1/climate-control-receipt.schema.json",
      "receiptField": "correlation_id",
      "journalSource": "climate"
    },
    {
      "operationId": "setHausmanHubTemporaryTemperature",
      "requestSchema": "schemas/v1/temporary-temperature-request.schema.json",
      "requestField": "correlation_id",
      "receiptSchema": "schemas/v1/climate-control-receipt.schema.json",
      "receiptField": "correlation_id",
      "journalSource": "climate"
    },
    {
      "operationId": "setHausmanHubHomeClimateTargets",
      "requestSchema": "schemas/v1/home-climate-targets-request.schema.json",
      "requestField": "correlation_id",
      "receiptSchema": "schemas/v1/climate-control-receipt.schema.json",
      "receiptField": "correlation_id",
      "journalSource": "climate"
    },
    {
      "operationId": "executeHausmanHubClimateAction",
      "requestSchema": "schemas/v1/climate-action-request.schema.json",
      "requestField": "correlation_id",
      "receiptSchema": "schemas/v1/climate-operation-receipt.schema.json",
      "receiptField": "correlation_id",
      "journalSource": "climate"
    },
    {
      "operationId": "executeHausmanHubDeviceAction",
      "requestSchema": "schemas/v1/device-action-request.schema.json",
      "requestField": "correlationId",
      "receiptSchema": "schemas/v1/device-action-receipt.schema.json",
      "receiptField": "correlationId",
      "journalSource": "device"
    },
    {
      "operationId": "maintainHausmanHubDevice",
      "requestSchema": "schemas/v1/device-maintenance-request.schema.json",
      "requestField": "correlationId",
      "receiptSchema": "schemas/v1/device-maintenance-receipt.schema.json",
      "receiptField": "correlationId",
      "journalSource": "device"
    },
    {
      "operationId": "runHausmanHubScenario",
      "requestField": "correlationId",
      "receiptField": "correlationId",
      "journalSource": "scenario"
    },
    {
      "operationId": "dispatchHausmanHubScenarioAction",
      "requestField": "correlationId",
      "receiptField": "correlationId",
      "journalSource": "scenario"
    },
    {
      "operationId": "cancelHausmanHubScenarioUpcoming",
      "requestSchema": "schemas/v1/scenario-upcoming-cancel-request.schema.json",
      "requestField": "correlationId",
      "receiptSchema": "schemas/v1/scenario-upcoming-cancel-receipt.schema.json",
      "receiptField": "correlationId",
      "journalSource": "scenario"
    },
    {
      "operationId": "testHausmanHubVoiceGreeting",
      "requestSchema": "schemas/v1/voice-greeting-test-request.schema.json",
      "requestField": "correlationId",
      "receiptSchema": "schemas/v1/voice-command-receipt.schema.json",
      "receiptField": "correlationId",
      "journalSource": "voice"
    }
  ],
  "events": {
    "schema": "schemas/v1/event-stream-message.schema.json",
    "messageField": "correlation_id",
    "commandReceiptField": "data.correlation_id",
    "allTypes": [
      "hello",
      "snapshot_invalidated",
      "scenario_changed",
      "critical_alert",
      "attention_alert",
      "command_receipt",
      "heartbeat"
    ]
  },
  "operationJournal": {
    "schema": "schemas/v1/operation-journal.schema.json",
    "recordField": "correlation_id",
    "mustMatchCommandReceipt": true
  },
  "notifications": [
    {
      "surface": "device_discovery",
      "schema": "schemas/v1/device-discovery.schema.json",
      "field": "notifications[].correlationId"
    },
    {
      "surface": "dashboard_alarm",
      "schema": "schemas/v1/dashboard-snapshot.schema.json",
      "field": "alarms[].correlationId"
    },
    {
      "surface": "dashboard_event",
      "schema": "schemas/v1/dashboard-snapshot.schema.json",
      "field": "events[].correlationId"
    },
    {
      "surface": "sse_alert",
      "schema": "schemas/v1/event-stream-message.schema.json",
      "field": "correlation_id"
    },
    {
      "surface": "scenario_notify_service",
      "field": "serviceData.data.correlation_id"
    }
  ]
};

const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const GENERATED_PREFIX = "corr.panel.";
const GENERATED_BODY_LENGTH = 32;
const TRACKER_DEFAULT_LIMIT = 256;
const CORR_COMMAND_FIELDS = new Set(["correlation_id", "correlationId"]);
const CORR_JOURNAL_SOURCES = new Set(["climate", "device", "scenario", "voice"]);
const CORR_EVENT_TYPES = [
  "hello", "snapshot_invalidated", "scenario_changed", "critical_alert",
  "attention_alert", "command_receipt", "heartbeat",
];
const CORR_NOTIFICATION_SURFACES = new Set([
  "device_discovery", "dashboard_alarm", "dashboard_event",
  "sse_alert", "scenario_notify_service",
]);

/* Command endpoints the panel calls, bound to matrix operation IDs. The field */
/* name always comes from the pinned matrix, never from this table. */
const COMMAND_PATH_OPERATIONS = [
  ["hausman_hub/v1/admin/panel/apply", "applyHausmanHubContour"],
  ["hausman_hub/v1/admin/panel/temporary-temperature", "setHausmanHubTemporaryTemperature"],
  ["hausman_hub/v1/climate/actions", "executeHausmanHubClimateAction"],
  ["hausman_hub/v1/device-actions", "executeHausmanHubDeviceAction"],
  ["hausman_hub/v1/admin/device-maintenance", "maintainHausmanHubDevice"],
  ["hausman_hub/v1/admin/scenarios/run", "runHausmanHubScenario"],
  ["hausman_hub/v1/scenarios/upcoming/cancel", "cancelHausmanHubScenarioUpcoming"],
];

const SURFACES_KEYS = ["contract", "idPattern", "generation", "commands", "events", "operationJournal", "notifications"];
const CORR_GENERATION_KEYS = [
  "clientMayProvide", "serverGeneratesWhenMissing",
  "serverMustPreserveValidClientValue", "fallbackToRequestIdAllowed",
];
const CORR_COMMAND_KEYS = [
  "operationId", "requestSchema", "requestField",
  "receiptSchema", "receiptField", "journalSource",
];
const CORR_NOTIFICATION_KEYS = ["surface", "schema", "field"];

function corrIsObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function corrExactKeys(value, allowed) {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function corrSchemaPath(value) {
  return value === undefined || (typeof value === "string" && value.startsWith("schemas/v1/"));
}

function corrValidCommand(raw) {
  if (!corrIsObject(raw) || !corrExactKeys(raw, CORR_COMMAND_KEYS)) return false;
  if (typeof raw.operationId !== "string" || !raw.operationId.length) return false;
  if (!CORR_COMMAND_FIELDS.has(raw.requestField) || raw.receiptField !== raw.requestField) return false;
  if (typeof raw.journalSource !== "string" || !CORR_JOURNAL_SOURCES.has(raw.journalSource)) return false;
  return corrSchemaPath(raw.requestSchema) && corrSchemaPath(raw.receiptSchema);
}

function corrValidNotification(raw) {
  if (!corrIsObject(raw) || !corrExactKeys(raw, CORR_NOTIFICATION_KEYS)) return false;
  if (typeof raw.surface !== "string" || !CORR_NOTIFICATION_SURFACES.has(raw.surface)) return false;
  if (typeof raw.field !== "string" || !raw.field.length) return false;
  return corrSchemaPath(raw.schema);
}

/* Accepts only the exact v1 matrix; anything else fails closed to the pinned snapshot. */
export function validateCorrelationSurfaces(raw) {
  if (!corrIsObject(raw) || !corrExactKeys(raw, SURFACES_KEYS)) return false;
  if (!corrIsObject(raw.contract) || !corrExactKeys(raw.contract, ["name", "version"])) return false;
  if (raw.contract.name !== SURFACES_CONTRACT.name || raw.contract.version !== SURFACES_CONTRACT.version) return false;
  if (raw.idPattern !== ID_PATTERN.source) return false;
  if (!corrIsObject(raw.generation) || !corrExactKeys(raw.generation, CORR_GENERATION_KEYS)) return false;
  if (!CORR_GENERATION_KEYS.every((key) => raw.generation[key] === true)) return false;
  if (!Array.isArray(raw.commands) || raw.commands.length !== 10) return false;
  if (!raw.commands.every(corrValidCommand)) return false;
  const operationIds = raw.commands.map((command) => command.operationId);
  if (new Set(operationIds).size !== operationIds.length) return false;
  const events = raw.events;
  if (!corrIsObject(events) || !corrExactKeys(events, ["schema", "messageField", "commandReceiptField", "allTypes"])) return false;
  if (events.messageField !== "correlation_id" || events.commandReceiptField !== "data.correlation_id") return false;
  if (!corrSchemaPath(events.schema)) return false;
  if (!Array.isArray(events.allTypes) || JSON.stringify(events.allTypes) !== JSON.stringify(CORR_EVENT_TYPES)) return false;
  const journal = raw.operationJournal;
  if (!corrIsObject(journal) || !corrExactKeys(journal, ["schema", "recordField", "mustMatchCommandReceipt"])) return false;
  if (journal.recordField !== "correlation_id" || journal.mustMatchCommandReceipt !== true) return false;
  if (!corrSchemaPath(journal.schema)) return false;
  if (!Array.isArray(raw.notifications) || raw.notifications.length !== 5) return false;
  return raw.notifications.every(corrValidNotification);
}

function corrSurfacesOrSnapshot(matrix) {
  return validateCorrelationSurfaces(matrix) ? matrix : SURFACES_SNAPSHOT;
}

export function normalizeCorrelationSurfaces(raw) {
  return JSON.parse(JSON.stringify(corrSurfacesOrSnapshot(raw)));
}

export function isValidCorrelationId(value) {
  return typeof value === "string" && ID_PATTERN.test(value);
}

/* Hard gate before any API call: an invalid caller-supplied ID never leaves the panel. */
export function requireValidCorrelationId(value) {
  if (!isValidCorrelationId(value)) throw new Error("correlation ID is invalid");
  return value;
}

function corrRandomHex(length) {
  const alphabet = "0123456789abcdef";
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = crypto.getRandomValues(new Uint8Array(length));
    return Array.from(bytes, (byte) => alphabet[byte & 15]).join("");
  }
  let out = "";
  for (let index = 0; index < length; index += 1) {
    out += alphabet[Math.floor(Math.random() * 16)];
  }
  return out;
}

/* One opaque non-private ID per user command: no tokens, URLs, entity IDs or names. */
export function newCorrelationId() {
  return `${GENERATED_PREFIX}${corrRandomHex(GENERATED_BODY_LENGTH)}`;
}

export function commandSurface(operationId, matrix) {
  const source = corrSurfacesOrSnapshot(matrix);
  if (typeof operationId !== "string" || !operationId) return null;
  return source.commands.find((command) => command.operationId === operationId) || null;
}

/* Field name for a panel command endpoint, resolved through the pinned matrix. */
/* Unknown paths return null: no field is injected and the legacy call is kept. */
export function correlationFieldForPath(path, matrix) {
  if (typeof path !== "string") return null;
  const entry = COMMAND_PATH_OPERATIONS.find(([prefix]) => path === prefix || path.startsWith(`${prefix}/`));
  if (!entry) return null;
  const surface = commandSurface(entry[1], matrix);
  return surface ? surface.requestField : null;
}

/* Adds a fresh correlation ID to a command payload. A caller-supplied ID is */
/* preserved but must validate; an invalid one throws before the API call. */
export function withCorrelationId(path, payload, matrix) {
  const field = correlationFieldForPath(path, matrix);
  if (!field || !corrIsObject(payload)) return payload;
  if (payload[field] !== undefined) {
    requireValidCorrelationId(payload[field]);
    return payload;
  }
  return { ...payload, [field]: newCorrelationId() };
}

export function fullDeviceActionRequest(payload, requestId) {
  requireValidCorrelationId(requestId);
  return {
    payload: {
      ...payload,
      contract: { name: "hausman-hub-device-action-request", version: 1 },
      requestId,
      idempotencyKey: `confirmed.${requestId}`,
    },
    headers: {
      "Content-Type": "application/vnd.hausmanhub.device-action-request.full+json",
      Accept: "application/vnd.hausmanhub.device-action-receipt.full+json",
    },
  };
}

function corrCollectPath(value, path, out) {
  const segments = path.split(".");
  const walk = (node, index) => {
    if (node === null || node === undefined) return;
    if (index === segments.length) {
      if (isValidCorrelationId(node)) out.push(node);
      return;
    }
    const segment = segments[index];
    if (segment.endsWith("[]")) {
      const list = node[segment.slice(0, -2)];
      if (Array.isArray(list)) list.forEach((item) => walk(item, index + 1));
      return;
    }
    walk(corrIsObject(node) ? node[segment] : undefined, index + 1);
  };
  walk(value, 0);
  return out;
}

/* All valid correlation IDs visible in a payload across the matrix paths: */
/* commands, receipts, SSE messages, journal records and notification lists. */
export function correlationIdsOf(payload) {
  const out = [];
  corrCollectPath(payload, "correlation_id", out);
  corrCollectPath(payload, "correlationId", out);
  corrCollectPath(payload, "data.correlation_id", out);
  corrCollectPath(payload, "notifications[].correlationId", out);
  corrCollectPath(payload, "alarms[].correlationId", out);
  corrCollectPath(payload, "events[].correlationId", out);
  corrCollectPath(payload, "serviceData.data.correlation_id", out);
  corrCollectPath(payload, "records[].correlation_id", out);
  return out;
}

export function extractCorrelationId(payload, field) {
  if (typeof field !== "string" || !field) return null;
  const found = corrCollectPath(payload, field, []);
  return found.length ? found[0] : null;
}

/* Bounded dedup tracker: a repeated event or journal re-read with the same ID */
/* never creates a second user notification card. Entries without a valid ID */
/* always pass through so legacy payloads keep rendering. */
export function createCorrelationTracker(limit = TRACKER_DEFAULT_LIMIT) {
  const capacity = Number.isFinite(limit) && limit >= 1 ? Math.floor(limit) : TRACKER_DEFAULT_LIMIT;
  const seen = new Map();
  return {
    track(id) {
      if (!isValidCorrelationId(id)) return true;
      if (seen.has(id)) return false;
      seen.set(id, true);
      if (seen.size > capacity) seen.delete(seen.keys().next().value);
      return true;
    },
    has(id) {
      return seen.has(id);
    },
    get size() {
      return seen.size;
    },
  };
}

function corrNotificationId(item) {
  if (!corrIsObject(item)) return null;
  const value = item.correlationId !== undefined ? item.correlationId : item.correlation_id;
  return isValidCorrelationId(value) ? value : null;
}

/* Keeps the first card per correlation ID inside one rendered list. */
export function dedupeCorrelationNotifications(list) {
  if (!Array.isArray(list)) return [];
  const tracker = createCorrelationTracker();
  return list.filter((item) => tracker.track(corrNotificationId(item)));
}

export const CORRELATION_SURFACES_SNAPSHOT = SURFACES_SNAPSHOT;
export const CORRELATION_ID_PATTERN = ID_PATTERN;
export const CORRELATION_TRACKER_LIMIT = TRACKER_DEFAULT_LIMIT;
