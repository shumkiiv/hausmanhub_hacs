/* Pinned snapshot of contracts inventory/pagination-retention.json */
/* (hausman-hub-pagination-retention v1, contracts 0.36.0). Fail-closed: an */
/* invalid matrix is rejected in favour of the pinned snapshot, an energy */
/* window longer than 31 days is rejected before any request or render, and */
/* a journal page that repeats a sequence aborts the read. The SSE client */
/* tracks the last fully processed opaque event ID, bounds its delivery queue */
/* at 32 messages and recovers through the gap flow (one snapshot refresh, no */
/* command replay). hello/heartbeat never reach the user history. Internal */
/* symbols carry the pag prefix: panel tests load every module into one */
/* shared vm scope. */

const PAGINATION_CONTRACT = { name: "hausman-hub-pagination-retention", version: 1 };

const PAGINATION_SNAPSHOT = {
  "contract": {
    "name": "hausman-hub-pagination-retention",
    "version": 1
  },
  "apiMajorVersion": 1,
  "surfaces": [
    {
      "id": "event_stream",
      "operationId": "streamHausmanHubEvents",
      "pagination": {
        "strategy": "last_event_id",
        "requestHeader": "Last-Event-ID",
        "order": "oldest_first",
        "cursorOpaque": true,
        "gapSignal": "snapshot_invalidated.data.replay_status=gap"
      },
      "retention": {
        "mode": "memory_count",
        "maxItems": 128,
        "deliveryQueueLimit": 32,
        "survivesRestart": false,
        "retainedTypes": [
          "snapshot_invalidated",
          "critical_alert",
          "attention_alert",
          "command_receipt"
        ],
        "sessionOnlyTypes": ["hello", "heartbeat"]
      }
    },
    {
      "id": "energy_history",
      "operationId": "getHausmanHubEnergyHistory",
      "pagination": {
        "strategy": "time_window",
        "fromInclusive": true,
        "toExclusive": true,
        "order": "timestamp_asc",
        "maxWindowDays": 31,
        "maxSeries": 128,
        "maxPointsPerSeries": 8928
      },
      "retention": {
        "mode": "source_bound",
        "source": "home_assistant_recorder",
        "separateCopy": false,
        "missingPointsAllowed": true
      }
    },
    {
      "id": "operation_journal",
      "operationId": "getHausmanHubOperationJournal",
      "pagination": {
        "strategy": "keyset",
        "cursorParameter": "before_sequence",
        "cursorResponseField": "page.next_before_sequence",
        "order": "sequence_desc",
        "defaultLimit": 100,
        "maxLimit": 512,
        "cursorExclusive": true
      },
      "retention": {
        "mode": "durable_count",
        "maxItems": 512,
        "survivesRestart": true,
        "ttlSeconds": null
      }
    },
    {
      "id": "dashboard_events",
      "operationId": "getHausmanHubDashboard",
      "pagination": {
        "strategy": "head_projection",
        "order": "timestamp_desc",
        "maxItems": 100,
        "continuationOperationId": "getHausmanHubOperationJournal"
      },
      "retention": {
        "mode": "operation_journal_projection",
        "maxItems": 100
      }
    },
    {
      "id": "manual_energy_readings",
      "operationId": "getHausmanHubEnergyMeter",
      "pagination": {
        "strategy": "head_projection",
        "order": "timestamp_desc",
        "maxItems": 60,
        "continuationOperationId": null
      },
      "retention": {
        "mode": "durable_count",
        "maxItems": 60,
        "survivesRestart": true,
        "ttlSeconds": null
      }
    }
  ]
};

const SSE_REPLAY_MAX_EVENTS = 128;
const SSE_DELIVERY_QUEUE_LIMIT = 32;
const SSE_BACKOFF_INITIAL_MS = 1000;
const SSE_BACKOFF_MAX_MS = 30000;
const ENERGY_MAX_WINDOW_DAYS = 31;
const ENERGY_MAX_WINDOW_MS = ENERGY_MAX_WINDOW_DAYS * 24 * 60 * 60 * 1000;
const ENERGY_MAX_SERIES = 128;
const ENERGY_MAX_POINTS_PER_SERIES = 8928;
const JOURNAL_DEFAULT_LIMIT = 100;
const JOURNAL_MAX_LIMIT = 512;
const JOURNAL_MAX_PAGES = 16;
const DASHBOARD_EVENTS_MAX_ITEMS = 100;
const MANUAL_READINGS_MAX_ITEMS = 60;
const EVENT_STREAM_URL = "/api/hausman_hub/v1/events";
const JOURNAL_API_PATH = "hausman_hub/v1/admin/operations";
const SSE_SESSION_ONLY_TYPES = ["hello", "heartbeat"];

function pagIsObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function pagDeepEqual(left, right) {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
    return left.every((item, index) => pagDeepEqual(item, right[index]));
  }
  if (pagIsObject(left) && pagIsObject(right)) {
    const leftKeys = Object.keys(left);
    const rightKeys = Object.keys(right);
    if (leftKeys.length !== rightKeys.length) return false;
    return leftKeys.every((key) => Object.prototype.hasOwnProperty.call(right, key)
      && pagDeepEqual(left[key], right[key]));
  }
  return false;
}

/* Accepts only the exact v1 matrix; anything else fails closed to the pinned snapshot. */
export function validatePaginationRetentionMatrix(raw) {
  return pagDeepEqual(raw, PAGINATION_SNAPSHOT);
}

export function normalizePaginationRetention(raw) {
  const source = validatePaginationRetentionMatrix(raw) ? raw : PAGINATION_SNAPSHOT;
  return JSON.parse(JSON.stringify(source));
}

export function isSessionOnlyEvent(type) {
  return SSE_SESSION_ONLY_TYPES.includes(type);
}

export function isGapInvalidation(message) {
  return pagIsObject(message)
    && message.type === "snapshot_invalidated"
    && pagIsObject(message.data)
    && message.data.replay_status === "gap";
}

/* Bounded exponential backoff: 1s, 2s, 4s... capped at 30 seconds. */
export function sseBackoffDelayMs(attempt, initialMs = SSE_BACKOFF_INITIAL_MS, maxMs = SSE_BACKOFF_MAX_MS) {
  const base = Number.isFinite(initialMs) && initialMs > 0 ? initialMs : SSE_BACKOFF_INITIAL_MS;
  const cap = Number.isFinite(maxMs) && maxMs > 0 ? maxMs : SSE_BACKOFF_MAX_MS;
  const step = Number.isFinite(attempt) && attempt >= 1 ? Math.floor(attempt) : 1;
  return Math.min(cap, base * (2 ** (step - 1)));
}

/* Transport-agnostic SSE client. connect(cursor) returns an EventSource-like */
/* object ({onopen, onmessage, onerror, close()}); onmessage receives */
/* {id, data} with data as the raw JSON string. The cursor sent on reconnect */
/* is the last fully processed opaque event ID. */
export function createEventStreamClient(options) {
  const opts = pagIsObject(options) ? options : {};
  if (typeof opts.connect !== "function") throw new Error("event stream client requires connect");
  const onDomainEvent = typeof opts.onDomainEvent === "function" ? opts.onDomainEvent : () => {};
  const onGap = typeof opts.onGap === "function" ? opts.onGap : () => {};
  const onStreamId = typeof opts.onStreamId === "function" ? opts.onStreamId : () => {};
  const queueLimit = Number.isFinite(opts.queueLimit) && opts.queueLimit >= 1
    ? Math.floor(opts.queueLimit) : SSE_DELIVERY_QUEUE_LIMIT;
  const initialMs = Number.isFinite(opts.backoffInitialMs) && opts.backoffInitialMs > 0
    ? opts.backoffInitialMs : SSE_BACKOFF_INITIAL_MS;
  const maxMs = Number.isFinite(opts.backoffMaxMs) && opts.backoffMaxMs > 0
    ? opts.backoffMaxMs : SSE_BACKOFF_MAX_MS;
  const setTimeoutFn = typeof opts.setTimeout === "function"
    ? opts.setTimeout : ((fn, ms) => setTimeout(fn, ms));
  const clearTimeoutFn = typeof opts.clearTimeout === "function"
    ? opts.clearTimeout : ((handle) => clearTimeout(handle));

  let source = null;
  let stopped = true;
  let cursor = null;
  let streamId = null;
  let attempts = 0;
  let timer = null;
  let pending = [];
  let pumping = false;
  let gapBusy = false;
  const seen = new Set();

  const rememberSeen = (id) => {
    seen.add(id);
    if (seen.size > SSE_REPLAY_MAX_EVENTS) seen.delete(seen.keys().next().value);
  };

  /* Exactly one snapshot refresh per gap episode; commands are never replayed. */
  function runGapFlow() {
    if (gapBusy) return;
    gapBusy = true;
    Promise.resolve()
      .then(() => onGap({ streamId }))
      .catch(() => {})
      .finally(() => { gapBusy = false; });
  }

  async function process(message) {
    const id = pagIsObject(message) && typeof message.id === "string" && message.id
      ? message.id : null;
    if (id && seen.has(id)) return;
    let parsed = null;
    try {
      parsed = JSON.parse(typeof message.data === "string" ? message.data : "");
    } catch {
      return;
    }
    if (!pagIsObject(parsed) || typeof parsed.type !== "string") return;
    const data = pagIsObject(parsed.data) ? parsed.data : {};
    if (parsed.type === "hello") {
      if (typeof data.stream_id === "string" && data.stream_id && data.stream_id !== streamId) {
        streamId = data.stream_id;
        onStreamId(streamId);
      }
    } else if (!isSessionOnlyEvent(parsed.type)) {
      if (isGapInvalidation(parsed)) runGapFlow();
      else await onDomainEvent(parsed);
    }
    if (id) {
      cursor = id;
      rememberSeen(id);
    }
  }

  async function pump() {
    if (pumping) return;
    pumping = true;
    try {
      while (pending.length) {
        const message = pending.shift();
        await process(message);
      }
    } finally {
      pumping = false;
    }
  }

  function closeSource() {
    const current = source;
    source = null;
    if (current && typeof current.close === "function") current.close();
  }

  function scheduleReconnect() {
    if (stopped || timer !== null) return;
    attempts += 1;
    const delay = sseBackoffDelayMs(attempts, initialMs, maxMs);
    timer = setTimeoutFn(() => {
      timer = null;
      connectSource();
    }, delay);
  }

  /* Slow consumer: the queue never grows past the limit; recovery goes */
  /* through the gap flow and a fresh connection without the stale cursor. */
  function handleOverflow() {
    pending = [];
    cursor = null;
    closeSource();
    runGapFlow();
    scheduleReconnect();
  }

  function connectSource() {
    if (stopped) return;
    closeSource();
    const next = opts.connect(cursor);
    if (!next || typeof next !== "object") {
      scheduleReconnect();
      return;
    }
    source = next;
    next.onopen = () => {
      if (source === next) attempts = 0;
    };
    next.onmessage = (message) => {
      if (stopped || source !== next) return;
      if (pending.length >= queueLimit) {
        handleOverflow();
        return;
      }
      pending.push(message);
      void pump();
    };
    next.onerror = () => {
      if (stopped || source !== next) return;
      closeSource();
      scheduleReconnect();
    };
  }

  return {
    start() {
      if (!stopped) return;
      stopped = false;
      attempts = 0;
      connectSource();
    },
    stop() {
      stopped = true;
      if (timer !== null) {
        clearTimeoutFn(timer);
        timer = null;
      }
      pending = [];
      closeSource();
    },
    get lastEventId() {
      return cursor;
    },
    get streamId() {
      return streamId;
    },
    get pendingCount() {
      return pending.length;
    },
    get reconnectAttempts() {
      return attempts;
    },
  };
}

/* Minimal SSE reader over fetch: the panel cannot set Last-Event-ID on a */
/* native EventSource, so reconnect with the cursor uses the Authorization */
/* header transport instead. */
export async function fetchEventStream(url, options) {
  const opts = pagIsObject(options) ? options : {};
  if (typeof fetch !== "function") throw new Error("fetch is unavailable");
  const headers = { Accept: "text/event-stream" };
  if (typeof opts.token === "string" && opts.token) headers.Authorization = `Bearer ${opts.token}`;
  if (typeof opts.lastEventId === "string" && opts.lastEventId) {
    headers["Last-Event-ID"] = opts.lastEventId;
  }
  const response = await fetch(url, { headers, signal: opts.signal, cache: "no-store" });
  if (!response.ok || !response.body) {
    throw new Error(`event stream HTTP ${response.status}`);
  }
  if (typeof opts.onOpen === "function") opts.onOpen();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventId = "";
  let dataLines = [];
  const flush = () => {
    if (!dataLines.length) {
      eventId = "";
      return;
    }
    const data = dataLines.join("\n");
    dataLines = [];
    const message = { id: eventId || null, data };
    eventId = "";
    if (typeof opts.onMessage === "function") opts.onMessage(message);
  };
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let index = buffer.indexOf("\n");
      while (index >= 0) {
        let line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        if (line.endsWith("\r")) line = line.slice(0, -1);
        if (line === "") {
          flush();
        } else if (!line.startsWith(":")) {
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          let value = colon < 0 ? "" : line.slice(colon + 1);
          if (value.startsWith(" ")) value = value.slice(1);
          if (field === "id") eventId = value;
          else if (field === "data") dataLines.push(value);
        }
        index = buffer.indexOf("\n");
      }
    }
    flush();
  } finally {
    try {
      reader.cancel();
    } catch {
      /* reader already released */
    }
  }
}

/* EventSource-like factory backed by fetchEventStream for createEventStreamClient. */
export function createFetchEventSource(url, resolveToken) {
  return (lastEventId) => {
    const controller = new AbortController();
    const source = { onopen: null, onmessage: null, onerror: null };
    fetchEventStream(url, {
      token: typeof resolveToken === "function" ? resolveToken() : null,
      lastEventId,
      signal: controller.signal,
      onOpen: () => {
        if (!controller.signal.aborted && typeof source.onopen === "function") source.onopen();
      },
      onMessage: (message) => {
        if (!controller.signal.aborted && typeof source.onmessage === "function") source.onmessage(message);
      },
    }).catch((error) => {
      if (!controller.signal.aborted && typeof source.onerror === "function") source.onerror(error);
    }).then(() => {
      if (!controller.signal.aborted && typeof source.onerror === "function") {
        source.onerror(new Error("event stream ended"));
      }
    });
    source.close = () => controller.abort();
    return source;
  };
}

export function resolveEventStreamToken(hass) {
  if (!pagIsObject(hass)) return null;
  const direct = pagIsObject(hass.auth) ? hass.auth : null;
  const viaConnection = pagIsObject(hass.connection) && pagIsObject(hass.connection.options)
    ? hass.connection.options.auth : null;
  const auth = direct || viaConnection;
  const data = auth && pagIsObject(auth.data) ? auth.data : null;
  return data && typeof data.access_token === "string" && data.access_token
    ? data.access_token : null;
}

/* Hard gate before any request or render: one window is [from, to) and */
/* never longer than 31 days. */
export function validateEnergyWindow(fromMs, toMs) {
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || fromMs >= toMs) {
    throw new Error("energy window is invalid");
  }
  if (toMs - fromMs > ENERGY_MAX_WINDOW_MS) {
    throw new Error("energy window exceeds 31 days");
  }
  return { fromMs, toMs };
}

/* Splits an arbitrary range into adjacent legal windows: next window starts */
/* exactly where the previous ended (to-exclusive), so the boundary point */
/* is never requested twice. */
export function splitEnergyWindows(fromMs, toMs) {
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs) || fromMs >= toMs) {
    throw new Error("energy window is invalid");
  }
  const windows = [];
  let cursorMs = fromMs;
  while (toMs - cursorMs > ENERGY_MAX_WINDOW_MS) {
    windows.push({ fromMs: cursorMs, toMs: cursorMs + ENERGY_MAX_WINDOW_MS });
    cursorMs += ENERGY_MAX_WINDOW_MS;
  }
  windows.push({ fromMs: cursorMs, toMs });
  return windows;
}

function pagPointTime(point) {
  const parsed = Date.parse(point.at);
  return Number.isFinite(parsed) ? parsed : null;
}

/* Merges consecutive window responses into one history: series keyed by */
/* scope/metric/source/device, boundary points deduplicated by timestamp, */
/* ascending order preserved, missing Recorder points left missing (never */
/* fabricated as zeros). */
export function mergeEnergyHistoryResponses(responses) {
  const list = Array.isArray(responses) ? responses.filter(pagIsObject) : [];
  const byKey = new Map();
  for (const response of list) {
    const series = Array.isArray(response.series) ? response.series : [];
    for (const item of series) {
      if (!pagIsObject(item)) continue;
      const key = [item.scope || "", item.metric || "", item.sourceId || "", item.deviceId || ""].join("|");
      let target = byKey.get(key);
      if (!target) {
        if (byKey.size >= ENERGY_MAX_SERIES) continue;
        target = { ...item, points: [] };
        byKey.set(key, target);
      }
      const seenAt = new Set(target.points.map((point) => point.at));
      const points = Array.isArray(item.points) ? item.points : [];
      for (const point of points) {
        if (!pagIsObject(point) || typeof point.at !== "string" || pagPointTime(point) === null) continue;
        if (seenAt.has(point.at)) continue;
        seenAt.add(point.at);
        target.points.push(point);
      }
      target.points = target.points
        .sort((left, right) => pagPointTime(left) - pagPointTime(right))
        .slice(0, ENERGY_MAX_POINTS_PER_SERIES);
    }
  }
  const merged = { series: [...byKey.values()] };
  const first = list[0];
  if (pagIsObject(first)) {
    if (pagIsObject(first.contract)) merged.contract = first.contract;
    if (pagIsObject(first.page)) merged.page = first.page;
    if (pagIsObject(first.retention)) merged.retention = first.retention;
  }
  return merged;
}

/* Keyset query for one journal page. Cursor exclusive: the next page reads */
/* records with sequence < before_sequence. Filters apply before pagination. */
export function journalPageQuery(options) {
  const opts = pagIsObject(options) ? options : {};
  const query = {};
  let limit = JOURNAL_DEFAULT_LIMIT;
  if (opts.limit !== undefined) {
    if (!Number.isSafeInteger(opts.limit) || opts.limit < 1 || opts.limit > JOURNAL_MAX_LIMIT) {
      throw new Error("journal limit is invalid");
    }
    limit = opts.limit;
  }
  query.limit = String(limit);
  if (opts.beforeSequence !== undefined && opts.beforeSequence !== null) {
    if (!Number.isSafeInteger(opts.beforeSequence) || opts.beforeSequence < 1) {
      throw new Error("journal cursor is invalid");
    }
    query.before_sequence = String(opts.beforeSequence);
  }
  for (const key of ["source", "correlation_id"]) {
    const value = opts[key];
    if (value !== undefined && value !== null) {
      if (typeof value !== "string" || !value) throw new Error(`journal filter ${key} is invalid`);
      query[key] = value;
    }
  }
  return query;
}

/* Reads journal pages until has_more=false. A repeated sequence, a broken */
/* sequence_desc order or an invalid continuation cursor aborts the read */
/* instead of showing duplicated or lost operations. Legacy pages without */
/* page metadata are read once. */
export async function readOperationJournal(callApi, options) {
  if (typeof callApi !== "function") throw new Error("journal reader requires callApi");
  const opts = pagIsObject(options) ? options : {};
  const path = typeof opts.path === "string" && opts.path ? opts.path : JOURNAL_API_PATH;
  const maxPages = Number.isSafeInteger(opts.maxPages) && opts.maxPages > 0
    ? opts.maxPages : JOURNAL_MAX_PAGES;
  const records = [];
  const seenSequences = new Set();
  let beforeSequence = Number.isSafeInteger(opts.beforeSequence) ? opts.beforeSequence : null;
  let pages = 0;
  for (;;) {
    const query = journalPageQuery({
      limit: opts.limit,
      beforeSequence,
      source: opts.source,
      correlation_id: opts.correlation_id,
    });
    const params = new URLSearchParams(query);
    const page = await callApi("GET", `${path}?${params.toString()}`);
    pages += 1;
    const list = pagIsObject(page) && Array.isArray(page.records) ? page.records : [];
    let lastSequence = null;
    for (const record of list) {
      const sequence = pagIsObject(record) ? record.sequence : null;
      if (!Number.isSafeInteger(sequence)) continue;
      if (lastSequence !== null && sequence >= lastSequence) {
        throw new Error("journal order is not sequence_desc");
      }
      if (seenSequences.has(sequence)) {
        throw new Error("journal page repeated a sequence");
      }
      seenSequences.add(sequence);
      lastSequence = sequence;
      records.push(record);
    }
    const pageMeta = pagIsObject(page) && pagIsObject(page.page) ? page.page : null;
    if (!pageMeta || pageMeta.has_more !== true) break;
    const next = pageMeta.next_before_sequence;
    if (!Number.isSafeInteger(next) || next < 1) {
      throw new Error("journal continuation cursor is invalid");
    }
    if (lastSequence !== null && next > lastSequence) {
      throw new Error("journal continuation cursor is not exclusive");
    }
    if (pages >= maxPages) break;
    beforeSequence = next;
  }
  return { records, pages };
}

/* Head projection guard: dashboard events stay at 100 entries, manual */
/* energy readings at 60; continuation goes through the operation journal. */
export function headProjection(items, maxItems) {
  if (!Array.isArray(items)) return [];
  const limit = Number.isSafeInteger(maxItems) && maxItems >= 0 ? maxItems : 0;
  return items.slice(0, limit);
}

export const PAGINATION_RETENTION_SNAPSHOT = PAGINATION_SNAPSHOT;
export const SSE_REPLAY_RETENTION = SSE_REPLAY_MAX_EVENTS;
export const SSE_QUEUE_LIMIT = SSE_DELIVERY_QUEUE_LIMIT;
export const SSE_BACKOFF_CAP_MS = SSE_BACKOFF_MAX_MS;
export const ENERGY_WINDOW_MAX_DAYS = ENERGY_MAX_WINDOW_DAYS;
export const ENERGY_SERIES_MAX = ENERGY_MAX_SERIES;
export const ENERGY_POINTS_PER_SERIES_MAX = ENERGY_MAX_POINTS_PER_SERIES;
export const JOURNAL_LIMIT_DEFAULT = JOURNAL_DEFAULT_LIMIT;
export const JOURNAL_LIMIT_MAX = JOURNAL_MAX_LIMIT;
export const JOURNAL_PATH = JOURNAL_API_PATH;
export const EVENT_STREAM_PATH = EVENT_STREAM_URL;
export const DASHBOARD_EVENTS_LIMIT = DASHBOARD_EVENTS_MAX_ITEMS;
export const MANUAL_READINGS_LIMIT = MANUAL_READINGS_MAX_ITEMS;
