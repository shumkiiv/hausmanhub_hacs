/* Pinned snapshot of contracts fixtures/v1/device-feature-matrix.json */
/* (hausman-hub-device-feature-matrix v1, contracts 0.37.0). Fail-closed: the matrix */
/* is only an upper bound, unknown types render read-only, unknown controls stay */
/* hidden and the client never synthesizes action IDs outside the runtime catalog. */

const MATRIX_CONTRACT = { name: "hausman-hub-device-feature-matrix", version: 1 };

const MATRIX_SNAPSHOT = {
  "contract": {
    "name": "hausman-hub-device-feature-matrix",
    "version": 1
  },
  "apiMajorVersion": 1,
  "authority": {
    "semantics": "upper_bound",
    "runtimeActionSource": "scenario_catalog",
    "unknownTypePolicy": "read_only",
    "unknownControlPolicy": "hidden",
    "clientMaySynthesizeActions": false
  },
  "deviceTypes": [
    {"type": "alarm_control_panel", "title": "Охранная система", "category": "security", "readOnly": true, "controls": []},
    {"type": "binary_sensor", "title": "Датчики", "category": "devices", "readOnly": true, "controls": []},
    {"type": "button", "title": "Кнопки", "category": "devices", "readOnly": false, "controls": [
      {"id": "press", "kind": "momentary", "actionIds": ["press"], "valueType": "none", "valueSource": "none", "receiptRequired": true}
    ]},
    {"type": "camera", "title": "Камеры", "category": "security", "readOnly": true, "controls": []},
    {"type": "climate", "title": "Кондиционеры", "category": "climate", "readOnly": false, "controls": [
      {"id": "power", "kind": "binary", "actionIds": ["turn_on", "turn_off"], "valueType": "none", "valueSource": "none", "receiptRequired": true},
      {"id": "temperature", "kind": "range", "actionIds": ["set_temperature"], "valueType": "number", "valueSource": "runtime_bounds", "receiptRequired": true},
      {"id": "hvac_mode", "kind": "enum", "actionIds": ["set_hvac_mode"], "valueType": "string", "valueSource": "runtime_options", "receiptRequired": true},
      {"id": "fan_mode", "kind": "enum", "actionIds": ["set_fan_mode"], "valueType": "string", "valueSource": "runtime_options", "receiptRequired": true}
    ]},
    {"type": "cover", "title": "Шторы и ворота", "category": "devices", "readOnly": false, "controls": [
      {"id": "open_close", "kind": "binary", "actionIds": ["open_cover", "close_cover"], "valueType": "none", "valueSource": "none", "receiptRequired": true},
      {"id": "position", "kind": "range", "actionIds": ["set_position"], "valueType": "number", "valueSource": "contract_bounds", "minimum": 0, "maximum": 100, "receiptRequired": true}
    ]},
    {"type": "fan", "title": "Вентиляция", "category": "climate", "readOnly": false, "controls": [
      {"id": "power", "kind": "binary", "actionIds": ["turn_on", "turn_off", "toggle"], "valueType": "none", "valueSource": "none", "receiptRequired": true}
    ]},
    {"type": "humidifier", "title": "Увлажнители", "category": "climate", "readOnly": false, "controls": [
      {"id": "power", "kind": "binary", "actionIds": ["turn_on", "turn_off"], "valueType": "none", "valueSource": "none", "receiptRequired": true},
      {"id": "humidity", "kind": "range", "actionIds": ["set_humidity"], "valueType": "number", "valueSource": "runtime_bounds", "receiptRequired": true}
    ]},
    {"type": "light", "title": "Освещение", "category": "lighting", "readOnly": false, "controls": [
      {"id": "power", "kind": "binary", "actionIds": ["turn_on", "turn_off", "toggle"], "valueType": "none", "valueSource": "none", "receiptRequired": true},
      {"id": "brightness", "kind": "range", "actionIds": ["set_brightness"], "valueType": "number", "valueSource": "contract_bounds", "minimum": 0, "maximum": 255, "receiptRequired": true},
      {"id": "adaptive_brightness", "kind": "range", "actionIds": ["set_adaptive_brightness"], "valueType": "number", "valueSource": "contract_bounds", "minimum": 1, "maximum": 100, "receiptRequired": true},
      {"id": "brightness_percent", "kind": "range", "actionIds": ["set_brightness_percent"], "valueType": "number", "valueSource": "contract_bounds", "minimum": 0, "maximum": 100, "receiptRequired": true},
      {"id": "color_temperature", "kind": "range", "actionIds": ["set_color_temperature"], "valueType": "number", "valueSource": "contract_bounds", "minimum": 1000, "maximum": 10000, "receiptRequired": true}
    ]},
    {"type": "lock", "title": "Замки", "category": "security", "readOnly": false, "controls": [
      {"id": "lock", "kind": "binary", "actionIds": ["lock", "unlock"], "valueType": "none", "valueSource": "none", "receiptRequired": true}
    ]},
    {"type": "media_player", "title": "Медиа", "category": "media", "readOnly": false, "controls": [
      {"id": "power", "kind": "binary", "actionIds": ["turn_on", "turn_off"], "valueType": "none", "valueSource": "none", "receiptRequired": true},
      {"id": "playback", "kind": "transport", "actionIds": ["media_play", "media_pause"], "valueType": "none", "valueSource": "none", "receiptRequired": true}
    ]},
    {"type": "number", "title": "Настройки", "category": "devices", "readOnly": false, "controls": [
      {"id": "value", "kind": "range", "actionIds": ["set_value"], "valueType": "number", "valueSource": "runtime_bounds", "receiptRequired": true}
    ]},
    {"type": "select", "title": "Режимы", "category": "devices", "readOnly": true, "controls": []},
    {"type": "sensor", "title": "Датчики", "category": "devices", "readOnly": true, "controls": []},
    {"type": "sun", "title": "Солнце", "category": "devices", "readOnly": true, "controls": []},
    {"type": "switch", "title": "Выключатели", "category": "devices", "readOnly": false, "controls": [
      {"id": "power", "kind": "binary", "actionIds": ["turn_on", "turn_off", "toggle"], "valueType": "none", "valueSource": "none", "receiptRequired": true}
    ]},
    {"type": "vacuum", "title": "Пылесосы", "category": "devices", "readOnly": false, "controls": [
      {"id": "cleaning", "kind": "transport", "actionIds": ["start", "pause", "stop", "return_home"], "valueType": "none", "valueSource": "none", "receiptRequired": true}
    ]},
    {"type": "valve", "title": "Клапаны", "category": "devices", "readOnly": false, "controls": [
      {"id": "open_close", "kind": "binary", "actionIds": ["open_valve", "close_valve"], "valueType": "none", "valueSource": "none", "receiptRequired": true},
      {"id": "position", "kind": "range", "actionIds": ["set_position"], "valueType": "number", "valueSource": "contract_bounds", "minimum": 0, "maximum": 100, "receiptRequired": true}
    ]},
    {"type": "water_heater", "title": "Нагрев воды", "category": "climate", "readOnly": false, "controls": [
      {"id": "power", "kind": "binary", "actionIds": ["turn_on", "turn_off"], "valueType": "none", "valueSource": "none", "receiptRequired": true},
      {"id": "temperature", "kind": "range", "actionIds": ["set_temperature"], "valueType": "number", "valueSource": "runtime_bounds", "receiptRequired": true},
      {"id": "operation_mode", "kind": "enum", "actionIds": ["set_operation_mode"], "valueType": "string", "valueSource": "runtime_options", "receiptRequired": true}
    ]}
  ]
};

const MATRIX_KEYS = ["contract", "apiMajorVersion", "authority", "deviceTypes"];
const AUTHORITY_KEYS = [
  "semantics", "runtimeActionSource", "unknownTypePolicy",
  "unknownControlPolicy", "clientMaySynthesizeActions",
];
const AUTHORITY = {
  semantics: "upper_bound",
  runtimeActionSource: "scenario_catalog",
  unknownTypePolicy: "read_only",
  unknownControlPolicy: "hidden",
  clientMaySynthesizeActions: false,
};
const DEVICE_TYPE_KEYS = ["type", "title", "category", "readOnly", "controls"];
const CONTROL_KEYS = [
  "id", "kind", "actionIds", "valueType", "valueSource",
  "minimum", "maximum", "receiptRequired",
];
const CATEGORIES = new Set(["lighting", "climate", "security", "media", "devices"]);
const CONTROL_KINDS = new Set(["binary", "range", "enum", "momentary", "transport"]);
const VALUE_TYPES = new Set(["none", "number", "string"]);
const VALUE_SOURCES = new Set(["none", "runtime_bounds", "runtime_options", "contract_bounds"]);
const STABLE_ID = /^[a-z][a-z0-9_]{0,63}$/;
const CAPABILITIES_CONTRACT = { name: "hausman-hub-capabilities", version: 1 };
const FEATURE_MATRIX_PATH = "/api/hausman_hub/v1/device-features";

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value, allowed) {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function validStableId(value) {
  return typeof value === "string" && STABLE_ID.test(value);
}

/* Mirrors schemas/v1/device-feature-matrix.schema.json including the conditional invariants. */
function validControl(raw) {
  if (!isObject(raw) || !exactKeys(raw, CONTROL_KEYS)) return false;
  if (!validStableId(raw.id)) return false;
  if (typeof raw.kind !== "string" || !CONTROL_KINDS.has(raw.kind)) return false;
  if (!Array.isArray(raw.actionIds) || raw.actionIds.length < 1 || raw.actionIds.length > 8) return false;
  if (!raw.actionIds.every(validStableId)) return false;
  if (new Set(raw.actionIds).size !== raw.actionIds.length) return false;
  if (typeof raw.valueType !== "string" || !VALUE_TYPES.has(raw.valueType)) return false;
  if (typeof raw.valueSource !== "string" || !VALUE_SOURCES.has(raw.valueSource)) return false;
  if (raw.receiptRequired !== true) return false;
  const hasMinimum = Object.hasOwn(raw, "minimum");
  const hasMaximum = Object.hasOwn(raw, "maximum");
  if (hasMinimum && !finiteNumber(raw.minimum)) return false;
  if (hasMaximum && !finiteNumber(raw.maximum)) return false;
  if (raw.valueType === "none" && (raw.valueSource !== "none" || hasMinimum || hasMaximum)) return false;
  if (raw.valueType === "number" && raw.valueSource !== "runtime_bounds" && raw.valueSource !== "contract_bounds") return false;
  if (raw.valueType === "string" && raw.valueSource !== "runtime_options") return false;
  if (raw.valueSource === "contract_bounds") return hasMinimum && hasMaximum;
  return !hasMinimum && !hasMaximum;
}

function validDeviceType(raw) {
  if (!isObject(raw) || !exactKeys(raw, DEVICE_TYPE_KEYS)) return false;
  if (!validStableId(raw.type)) return false;
  if (typeof raw.title !== "string" || raw.title.length < 1 || raw.title.length > 120) return false;
  if (typeof raw.category !== "string" || !CATEGORIES.has(raw.category)) return false;
  if (typeof raw.readOnly !== "boolean") return false;
  if (!Array.isArray(raw.controls) || raw.controls.length > 12) return false;
  if (raw.readOnly && raw.controls.length !== 0) return false;
  if (!raw.readOnly && raw.controls.length < 1) return false;
  return raw.controls.every(validControl);
}

function validAuthority(raw) {
  if (!isObject(raw) || !exactKeys(raw, AUTHORITY_KEYS)) return false;
  return AUTHORITY_KEYS.every((key) => raw[key] === AUTHORITY[key]);
}

/* Accepts only a schema-valid v1 matrix; anything else fails closed to the pinned snapshot. */
export function validateDeviceFeatureMatrix(raw) {
  if (!isObject(raw) || !exactKeys(raw, MATRIX_KEYS)) return false;
  if (!isObject(raw.contract) || !exactKeys(raw.contract, ["name", "version"])) return false;
  if (raw.contract.name !== MATRIX_CONTRACT.name || raw.contract.version !== MATRIX_CONTRACT.version) return false;
  if (raw.apiMajorVersion !== 1) return false;
  if (!validAuthority(raw.authority)) return false;
  if (!Array.isArray(raw.deviceTypes) || raw.deviceTypes.length < 19 || raw.deviceTypes.length > 64) return false;
  if (!raw.deviceTypes.every(validDeviceType)) return false;
  const types = raw.deviceTypes.map((entry) => entry.type);
  return new Set(types).size === types.length;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function matrixOrSnapshot(matrix) {
  return validateDeviceFeatureMatrix(matrix) ? matrix : MATRIX_SNAPSHOT;
}

/* The pinned snapshot is the fail-closed fallback for a missing or invalid matrix. */
export function normalizeDeviceFeatureMatrix(raw) {
  return clone(matrixOrSnapshot(raw));
}

/* Release counters of the matrix: 19 types, 24 control groups, 41 bindings, 25 unique action IDs. */
export function matrixStats(matrix) {
  const source = matrixOrSnapshot(matrix);
  const controls = source.deviceTypes.flatMap((entry) => entry.controls);
  const actionIds = controls.flatMap((control) => control.actionIds);
  return {
    deviceTypes: source.deviceTypes.length,
    controlGroups: controls.length,
    actionBindings: actionIds.length,
    uniqueActionIds: new Set(actionIds).size,
    readOnlyTypes: source.deviceTypes.filter((entry) => entry.readOnly).length,
  };
}

export function deviceTypeEntry(matrix, deviceType) {
  const type = typeof deviceType === "string" ? deviceType.trim() : "";
  if (!type) return null;
  return matrixOrSnapshot(matrix).deviceTypes.find((entry) => entry.type === type) || null;
}

/* Unknown and read-only types never create command UI. */
export function isCommandable(matrix, deviceType) {
  const entry = deviceTypeEntry(matrix, deviceType);
  return Boolean(entry) && entry.readOnly === false;
}

/* Matrix controls narrowed by the runtime scenario catalog: the matrix is the */
/* upper bound, the runtime catalog decides which actions actually exist. A */
/* control with an empty intersection stays hidden. */
export function controlsFor(matrix, deviceType, runtimeCatalogActionIds) {
  const entry = deviceTypeEntry(matrix, deviceType);
  if (!entry || entry.readOnly) return [];
  const runtime = new Set(
    (Array.isArray(runtimeCatalogActionIds) ? runtimeCatalogActionIds : [])
      .filter((id) => typeof id === "string")
  );
  return entry.controls
    .map((control) => {
      const actionIds = control.actionIds.filter((id) => runtime.has(id));
      return actionIds.length ? { ...control, actionIds } : null;
    })
    .filter(Boolean);
}

export function allowedActionIds(matrix, deviceType, runtimeCatalogActionIds) {
  const allowed = new Set();
  controlsFor(matrix, deviceType, runtimeCatalogActionIds)
    .forEach((control) => control.actionIds.forEach((id) => allowed.add(id)));
  return allowed;
}

/* Catalog actions survive only inside the matrix upper bound; nothing is synthesized. */
export function filterCatalogActions(matrix, deviceType, actions) {
  const list = Array.isArray(actions) ? actions : [];
  const runtimeIds = list
    .map((action) => (action && typeof action.action_id === "string" ? action.action_id : null))
    .filter(Boolean);
  const allowed = allowedActionIds(matrix, deviceType, runtimeIds);
  return list.filter((action) => action && allowed.has(action.action_id));
}

/* Range bounds: contract_bounds come from the matrix, runtime_bounds from catalog */
/* properties. Missing or invalid runtime bounds fail closed to null. */
export function resolveControlBounds(control, runtimeBounds) {
  if (!isObject(control) || control.valueType !== "number") return null;
  if (control.valueSource === "contract_bounds") {
    return finiteNumber(control.minimum) && finiteNumber(control.maximum) && control.maximum > control.minimum
      ? { minimum: control.minimum, maximum: control.maximum }
      : null;
  }
  if (control.valueSource === "runtime_bounds" && isObject(runtimeBounds)) {
    const minimum = runtimeBounds.minimum;
    const maximum = runtimeBounds.maximum;
    return finiteNumber(minimum) && finiteNumber(maximum) && maximum > minimum
      ? { minimum, maximum }
      : null;
  }
  return null;
}

/* Enum options come only from runtime catalog properties; never from the client. */
export function resolveControlOptions(control, runtimeOptions) {
  if (!isObject(control) || control.valueType !== "string" || control.valueSource !== "runtime_options") {
    return [];
  }
  const list = Array.isArray(runtimeOptions) ? runtimeOptions : [];
  const seen = new Set();
  const options = [];
  list.forEach((option) => {
    const value = isObject(option) ? option.value : option;
    const key = `${typeof value}:${String(value)}`;
    if ((typeof value !== "string" && typeof value !== "number") || seen.has(key)) return;
    if (typeof value === "number" && !Number.isFinite(value)) return;
    if (typeof value === "string" && !value.trim()) return;
    seen.add(key);
    options.push(isObject(option) && typeof option.label === "string"
      ? { value, label: option.label }
      : { value, label: String(value) });
  });
  return options;
}

/* Capability metadata for the matrix endpoint. Missing metadata means the old API */
/* v1 surface: the consumer must not call an undeclared endpoint. */
export function featureMatrixCapability(capabilities) {
  if (!isObject(capabilities) || !isObject(capabilities.contract)) return null;
  if (capabilities.contract.name !== CAPABILITIES_CONTRACT.name
      || capabilities.contract.version !== CAPABILITIES_CONTRACT.version) return null;
  const sections = isObject(capabilities.capabilities) ? capabilities.capabilities : null;
  const deviceActions = sections && isObject(sections.device_actions) ? sections.device_actions : null;
  if (!deviceActions || deviceActions.available !== true) return null;
  const contract = isObject(deviceActions.feature_matrix_contract)
    ? deviceActions.feature_matrix_contract : null;
  if (!contract || contract.name !== MATRIX_CONTRACT.name || contract.version !== MATRIX_CONTRACT.version) {
    return null;
  }
  if (deviceActions.feature_matrix_method !== "GET") return null;
  const path = typeof deviceActions.feature_matrix_path === "string"
    ? deviceActions.feature_matrix_path.trim() : "";
  if (path !== FEATURE_MATRIX_PATH) return null;
  return { path, apiPath: path.replace(/^\/api\//, "") };
}

/* Loads the matrix from the declared endpoint when capabilities advertise it. */
/* Without metadata no network call is made; any failure falls back to the */
/* pinned snapshot so the panel stays fail-closed. */
export async function loadDeviceFeatureMatrix(hass, capabilities) {
  const capability = featureMatrixCapability(capabilities);
  if (!capability || !hass || typeof hass.callApi !== "function") {
    return { matrix: clone(MATRIX_SNAPSHOT), source: "snapshot", declared: false };
  }
  try {
    const raw = await hass.callApi("GET", capability.apiPath);
    if (validateDeviceFeatureMatrix(raw)) {
      return { matrix: clone(raw), source: "endpoint", declared: true };
    }
  } catch (error) {
    /* Fall through to the pinned snapshot. */
  }
  return { matrix: clone(MATRIX_SNAPSHOT), source: "snapshot", declared: true };
}

export const DEVICE_FEATURE_MATRIX_SNAPSHOT = MATRIX_SNAPSHOT;
