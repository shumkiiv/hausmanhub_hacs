/* Core manual control envelope of transferred light resources (HH-014.b S-06.a client, contract 0.72.2). */
/* Pure helper: no DOM, no business rules, no storage. The server publishes the */
/* coreControlState block only for targets transferred to the core; its absence */
/* means the legacy path stays exactly as it was. */

const CORE_CONTROL_STATE_VERSION = 1;

function isNonNegativeSafeInteger(value) {
  return typeof value === "number"
    && Number.isInteger(value)
    && value >= 0
    && value <= Number.MAX_SAFE_INTEGER;
}

/* Normalized projection of one catalog target's coreControlState:
 *   null                    — target is not transferred (legacy path);
 *   { status: "ready", controlRevision, bindingRevision }
 *   { status: "unavailable" }
 *   { invalid: true }       — the block exists but violates contract 0.72.2.
 * An explicit null block is a damaged projection, not an absence: once a
 * target was seen transferred, losing the block refuses new commands
 * (the executor keeps the per-session seen-set). */
export function coreControlStateOf(catalogTarget) {
  const state = catalogTarget && typeof catalogTarget === "object"
    ? catalogTarget.coreControlState
    : undefined;
  if (state === undefined) return null;
  if (state === null || typeof state !== "object") return { invalid: true };
  if (state.version !== CORE_CONTROL_STATE_VERSION) return { invalid: true };
  if (state.status === "ready") {
    if (!isNonNegativeSafeInteger(state.controlRevision)
        || !isNonNegativeSafeInteger(state.bindingRevision)) {
      return { invalid: true };
    }
    return {
      status: "ready",
      controlRevision: state.controlRevision,
      bindingRevision: state.bindingRevision,
    };
  }
  if (state.status === "unavailable") {
    if (state.controlRevision !== undefined
        || state.bindingRevision !== undefined) {
      return { invalid: true };
    }
    return { status: "unavailable" };
  }
  return { invalid: true };
}

/* Decision for one explicit action over a catalog target:
 *   null                      — not transferred, legacy form;
 *   { state }                 — captured revision pair, envelope may be built;
 *   { refused: "причина" }    — the transferred target must not be commanded;
 *                               the caller shows the reason and never falls
 *                               back to the legacy writer. */
export function coreControlForTarget(catalogTarget) {
  const state = coreControlStateOf(catalogTarget);
  if (state === null) return null;
  if (state.invalid) {
    return { refused: "Данные управления ресурсом повреждены: обновите панель и повторите действие." };
  }
  if (state.status !== "ready") {
    return { refused: "Ресурс передан ядру, ревизии управления сейчас недоступны." };
  }
  return { state };
}

/* Attaches the 0.72.1 envelope to an already full-form payload. The envelope's
 * requestId is the same explicit action id as the request's requestId (never
 * the server operationId). A prepared { refused } or null leaves the payload
 * unchanged — the caller handles those before building the request. */
export function withCoreControl(payload, prepared, requestId) {
  if (!prepared || !prepared.state) return payload;
  return {
    ...payload,
    coreControl: {
      version: CORE_CONTROL_STATE_VERSION,
      requestId,
      expectedControlRevision: prepared.state.controlRevision,
      expectedBindingRevision: prepared.state.bindingRevision,
    },
  };
}

/* A connection replacement is a NEW connection object. Home Assistant
 * recreates the hass object on ordinary state updates while keeping the
 * same connection, so object identity must never decide a session
 * change (NEW-HACS-01). Missing connections on both sides (test
 * doubles) are not a replacement either. */
export function isConnectionReplaced(previousHass, nextHass) {
  if (!previousHass || !nextHass) return false;
  const previous = previousHass.connection;
  const next = nextHass.connection;
  if (previous === undefined && next === undefined) return false;
  return previous !== next;
}

/* Session bookkeeping for the panel: returns true when the core session
 * actually changed — a replaced connection or a lost connection
 * (hass → null → new hass must not slip through unnoticed,
 * SEC-HACS-02R1). Clearing and bumping happen exactly once per change.
 * The catalog epoch (_coreCatalogSession) is NOT touched here: it is
 * owned by publishCoreCatalog and always names the session of the last
 * real publication, so the executor can refuse envelopes built from a
 * catalog of a previous connection (HACS-C3-01). */
export function applyCoreSession(owner, previousHass, nextHass) {
  if (!nextHass) {
    if (!previousHass || owner._coreSessionEnded === true) return false;
    owner._coreSessionId = (owner._coreSessionId || 0) + 1;
    owner._coreSessionEnded = true;
    if (owner._coreTransferredSeen) owner._coreTransferredSeen.clear();
    return true;
  }
  owner._coreSessionEnded = false;
  if (!previousHass) return false;
  if (!isConnectionReplaced(previousHass, nextHass)) return false;
  owner._coreSessionId = (owner._coreSessionId || 0) + 1;
  if (owner._coreTransferredSeen) owner._coreTransferredSeen.clear();
  return true;
}

/* Publishes one catalog answer for core purposes: only a load that
 * stayed inside its session may publish, and every recognized
 * transferred target of the answer is remembered (SEC-HACS-01/02R1).
 * Returns false when the load crossed a session change — the stale
 * answer is dropped. The publication stamps the catalog epoch, which is
 * the only place that happens (HACS-C3-01). */
export function publishCoreCatalog(owner, sessionAtStart, catalog) {
  if (!owner || typeof owner !== "object") return false;
  if ((owner._coreSessionId || 0) !== sessionAtStart) return false;
  if (owner._scenarios && typeof owner._scenarios === "object") {
    owner._scenarios.catalog = catalog;
  }
  rememberTransferredTargets(owner, catalog);
  owner._coreCatalogSession = owner._coreSessionId || 0;
  return true;
}

/* Records every recognized transferred target of a catalog document:
 * ready, unavailable and damaged blocks all count, so losing the block
 * later refuses the command instead of silently falling back to the
 * legacy writer (SEC-HACS-01). Neighbours without a block stay out. */
export function rememberTransferredTargets(owner, catalog) {
  if (!owner || typeof owner !== "object") return;
  if (!Array.isArray(catalog && catalog.devices)) return;
  if (!owner._coreTransferredSeen) owner._coreTransferredSeen = new Set();
  const seen = owner._coreTransferredSeen;
  catalog.devices.forEach((device) => {
    if (device && typeof device.target_id === "string"
        && coreControlStateOf(device) !== null) {
      seen.add(device.target_id);
    }
  });
}
