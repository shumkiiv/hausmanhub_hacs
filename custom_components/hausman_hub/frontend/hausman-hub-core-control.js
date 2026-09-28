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
