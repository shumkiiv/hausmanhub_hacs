/* Pinned snapshot of contracts fixtures/v1/ui-state-*.json (hausman-hub-ui-state v1). */
/* Fail-closed: the panel never fetches UI state over the network, every projection */
/* is derived locally from confirmed server facts and invalid input fails closed. */

const UI_STATE_CONTRACT = { name: "hausman-hub-ui-state", version: 1 };

const UI_STATE_FIXTURES = {
  loading: {
    contract: { name: "hausman-hub-ui-state", version: 1 },
    state: "loading",
    scope: "screen",
    commandsAllowed: false,
    message: "Загружаем данные",
    retryAction: "none",
    updatedAt: "2026-08-15T10:25:00Z",
    dataAgeSeconds: null,
    operationId: null,
    reasonCode: null,
  },
  stale: {
    contract: { name: "hausman-hub-ui-state", version: 1 },
    state: "stale",
    scope: "slice",
    commandsAllowed: false,
    message: "Данные устарели",
    retryAction: "refresh",
    updatedAt: "2026-08-15T10:25:00Z",
    dataAgeSeconds: 75,
    operationId: null,
    reasonCode: "data_stale",
  },
  offline: {
    contract: { name: "hausman-hub-ui-state", version: 1 },
    state: "offline",
    scope: "screen",
    commandsAllowed: false,
    message: "Нет связи с Home Assistant",
    retryAction: "reconnect",
    updatedAt: "2026-08-15T10:25:00Z",
    dataAgeSeconds: 180,
    operationId: null,
    reasonCode: "home_assistant_offline",
  },
  pending: {
    contract: { name: "hausman-hub-ui-state", version: 1 },
    state: "pending",
    scope: "command",
    commandsAllowed: false,
    message: "Команда принята, ждём подтверждение",
    retryAction: "none",
    updatedAt: "2026-08-15T10:25:00Z",
    dataAgeSeconds: null,
    operationId: "operation-demo-001",
    reasonCode: null,
  },
  confirmed: {
    contract: { name: "hausman-hub-ui-state", version: 1 },
    state: "confirmed",
    scope: "command",
    commandsAllowed: true,
    message: "Команда подтверждена",
    retryAction: "none",
    updatedAt: "2026-08-15T10:25:00Z",
    dataAgeSeconds: null,
    operationId: "operation-demo-001",
    reasonCode: null,
  },
  failed: {
    contract: { name: "hausman-hub-ui-state", version: 1 },
    state: "failed",
    scope: "command",
    commandsAllowed: false,
    message: "Не удалось выполнить команду",
    retryAction: "retry",
    updatedAt: "2026-08-15T10:25:00Z",
    dataAgeSeconds: null,
    operationId: "operation-demo-001",
    reasonCode: "command_failed",
  },
  disabled: {
    contract: { name: "hausman-hub-ui-state", version: 1 },
    state: "disabled",
    scope: "slice",
    commandsAllowed: false,
    message: "Раздел недоступен",
    retryAction: "none",
    updatedAt: "2026-08-15T10:25:00Z",
    dataAgeSeconds: null,
    operationId: null,
    reasonCode: "feature_disabled",
  },
};

const UI_STATE_KEYS = [
  "contract", "state", "scope", "commandsAllowed", "message",
  "retryAction", "updatedAt", "dataAgeSeconds", "operationId", "reasonCode",
];
const READ_SCOPES = new Set(["screen", "slice"]);
const SCOPES = new Set(["screen", "slice", "command"]);
const FAILED_RETRY_ACTIONS = new Set(["refresh", "reconnect", "retry", "reauthenticate", "update_client"]);
const DISABLED_RETRY_ACTIONS = new Set(["none", "reauthenticate", "update_client"]);
const OPERATION_ID_PATTERN = /^[A-Za-z0-9._:-]+$/;
const REASON_CODE_PATTERN = /^[a-z][a-z0-9_]*$/;
const MAX_DATA_AGE_SECONDS = 604800;

function validOperationId(value) {
  return typeof value === "string" && value.length >= 1 && value.length <= 128
    && OPERATION_ID_PATTERN.test(value);
}

function validReasonCode(value) {
  return typeof value === "string" && value.length >= 1 && value.length <= 128
    && REASON_CODE_PATTERN.test(value);
}

function validDataAge(value) {
  return value === null
    || (Number.isInteger(value) && value >= 0 && value <= MAX_DATA_AGE_SECONDS);
}

function validUpdatedAt(value) {
  return typeof value === "string" && value.length > 0 && !Number.isNaN(Date.parse(value));
}

/* Mirrors schemas/v1/ui-state.schema.json including the per-state invariants. */
export function validateUiState(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return false;
  if (Object.keys(raw).some((key) => !UI_STATE_KEYS.includes(key))) return false;
  const contract = raw.contract;
  if (!contract || contract.name !== UI_STATE_CONTRACT.name || contract.version !== 1) return false;
  if (typeof raw.state !== "string" || !Object.hasOwn(UI_STATE_FIXTURES, raw.state)) return false;
  if (typeof raw.scope !== "string" || !SCOPES.has(raw.scope)) return false;
  if (typeof raw.commandsAllowed !== "boolean") return false;
  if (typeof raw.message !== "string" || raw.message.length < 1 || raw.message.length > 500) return false;
  if (typeof raw.retryAction !== "string") return false;
  if (!validUpdatedAt(raw.updatedAt)) return false;
  if (!validDataAge(raw.dataAgeSeconds)) return false;
  if (!(raw.operationId === null || validOperationId(raw.operationId))) return false;
  if (!(raw.reasonCode === null || validReasonCode(raw.reasonCode))) return false;
  switch (raw.state) {
    case "loading":
      return READ_SCOPES.has(raw.scope) && raw.commandsAllowed === false
        && raw.retryAction === "none" && raw.dataAgeSeconds === null
        && raw.operationId === null && raw.reasonCode === null;
    case "stale":
      return READ_SCOPES.has(raw.scope) && raw.commandsAllowed === false
        && raw.retryAction === "refresh"
        && Number.isInteger(raw.dataAgeSeconds) && raw.dataAgeSeconds >= 1
        && raw.operationId === null && raw.reasonCode === "data_stale";
    case "offline":
      return READ_SCOPES.has(raw.scope) && raw.commandsAllowed === false
        && raw.retryAction === "reconnect" && raw.operationId === null
        && raw.reasonCode === "home_assistant_offline";
    case "pending":
      return raw.scope === "command" && raw.commandsAllowed === false
        && raw.retryAction === "none" && raw.dataAgeSeconds === null
        && validOperationId(raw.operationId) && raw.reasonCode === null;
    case "confirmed":
      return raw.scope === "command" && raw.commandsAllowed === true
        && raw.retryAction === "none" && raw.dataAgeSeconds === null
        && validOperationId(raw.operationId) && raw.reasonCode === null;
    case "failed":
      return raw.commandsAllowed === false && FAILED_RETRY_ACTIONS.has(raw.retryAction)
        && raw.dataAgeSeconds === null && validReasonCode(raw.reasonCode);
    case "disabled":
      return raw.commandsAllowed === false && DISABLED_RETRY_ACTIONS.has(raw.retryAction)
        && raw.dataAgeSeconds === null && raw.operationId === null
        && validReasonCode(raw.reasonCode);
    default:
      return false;
  }
}

function cloneFixture(state) {
  return JSON.parse(JSON.stringify(UI_STATE_FIXTURES[state]));
}

function touch(state) {
  state.updatedAt = new Date().toISOString();
  return state;
}

/* Fail-closed default: commands stay forbidden, recovery is an explicit refresh. */
export function failClosedUiState(scope) {
  return {
    contract: { ...UI_STATE_CONTRACT },
    state: "failed",
    scope: SCOPES.has(scope) ? scope : "screen",
    commandsAllowed: false,
    message: "Данные недоступны. Обновите данные.",
    retryAction: "refresh",
    updatedAt: new Date().toISOString(),
    dataAgeSeconds: null,
    operationId: null,
    reasonCode: "invalid_ui_state",
  };
}

/* Accepts only a fully valid state; anything else fails closed. */
export function normalizeUiState(raw) {
  if (validateUiState(raw)) return JSON.parse(JSON.stringify(raw));
  const scope = raw && typeof raw === "object" && SCOPES.has(raw.scope) ? raw.scope : "screen";
  return failClosedUiState(scope);
}

export function loadingUiState(scope = "screen") {
  if (!READ_SCOPES.has(scope)) return failClosedUiState(scope);
  const state = cloneFixture("loading");
  state.scope = scope;
  return touch(state);
}

export function staleUiState(scope, dataAgeSeconds) {
  if (!READ_SCOPES.has(scope) || !Number.isInteger(dataAgeSeconds) || dataAgeSeconds < 1) {
    return failClosedUiState(scope);
  }
  const state = cloneFixture("stale");
  state.scope = scope;
  state.dataAgeSeconds = Math.min(dataAgeSeconds, MAX_DATA_AGE_SECONDS);
  return touch(state);
}

export function offlineUiState(scope = "screen", dataAgeSeconds = null) {
  if (!READ_SCOPES.has(scope) || !validDataAge(dataAgeSeconds)) return failClosedUiState(scope);
  const state = cloneFixture("offline");
  state.scope = scope;
  state.dataAgeSeconds = dataAgeSeconds;
  return touch(state);
}

/* Pending requires a non-empty operation ID; without it the command fails closed. */
export function pendingUiState(operationId) {
  if (!validOperationId(operationId)) return failClosedUiState("command");
  const state = cloneFixture("pending");
  state.operationId = operationId;
  return touch(state);
}

/* Confirmed only on a server receipt with confirmed=true and read-back evidence. */
/* HTTP 2xx alone never confirms: without full evidence the command stays pending. */
export function confirmCommandUiState(receipt, operationId, readBackConfirmed) {
  if (!validOperationId(operationId)) return failClosedUiState("command");
  if (receipt && typeof receipt === "object" && receipt.confirmed === false) {
    return failedUiState("command", "command_failed", operationId);
  }
  if (!receipt || typeof receipt !== "object" || receipt.confirmed !== true
      || readBackConfirmed !== true) {
    return pendingUiState(operationId);
  }
  if (typeof receipt.operation_id === "string" && receipt.operation_id !== operationId) {
    return failedUiState("command", "command_failed", operationId);
  }
  const state = cloneFixture("confirmed");
  state.operationId = operationId;
  return touch(state);
}

export function failedUiState(scope, reasonCode, operationId = null, retryAction = "retry") {
  if (!SCOPES.has(scope) || !validReasonCode(reasonCode)
      || !FAILED_RETRY_ACTIONS.has(retryAction)) {
    return failClosedUiState(scope);
  }
  const state = cloneFixture("failed");
  state.scope = scope;
  state.reasonCode = reasonCode;
  state.retryAction = retryAction;
  state.operationId = validOperationId(operationId) ? operationId : null;
  return touch(state);
}

/* Disabled is always shown explicitly and never masked as loading or offline. */
export function disabledUiState(scope, reasonCode, retryAction = "none") {
  if (!SCOPES.has(scope) || !validReasonCode(reasonCode)
      || !DISABLED_RETRY_ACTIONS.has(retryAction)) {
    return failClosedUiState(scope);
  }
  const state = cloneFixture("disabled");
  state.scope = scope;
  state.reasonCode = reasonCode;
  state.retryAction = retryAction;
  return touch(state);
}

/* Screen projection from confirmed server facts. Returns null for the domain view: */
/* after a successful fresh read the panel leaves this contract instead of faking confirmed. */
export function projectScreenUiState({ connected, hasData, dataAgeSeconds = null, stale = false }) {
  if (connected === false) {
    return offlineUiState("screen", validDataAge(dataAgeSeconds) ? dataAgeSeconds : null);
  }
  if (hasData !== true) return loadingUiState("screen");
  if (stale === true) return staleUiState("screen", dataAgeSeconds);
  return null;
}

/* Commands are allowed only in the domain view (null) or in a confirmed command state. */
export function canExecuteCommand(uiState) {
  if (uiState === null || uiState === undefined) return true;
  return uiState.state === "confirmed" && uiState.commandsAllowed === true;
}

export function resolveRecoveryAction(uiState) {
  if (!uiState || typeof uiState.retryAction !== "string") return "none";
  return uiState.retryAction;
}

/* An optional slice failure stays inside that slice and never hides the dashboard. */
export function projectSliceUiStates(slices) {
  const projected = {};
  if (!slices || typeof slices !== "object") return projected;
  Object.keys(slices).forEach((name) => {
    const slice = slices[name];
    if (slice && typeof slice === "object" && slice.ok === false) {
      projected[name] = slice.uiState
        ? normalizeUiState(slice.uiState)
        : failedUiState("slice", "slice_unavailable", null, "refresh");
    } else {
      projected[name] = null;
    }
  });
  return projected;
}

export const UI_STATE_SNAPSHOT = UI_STATE_FIXTURES;
