const FIRST_RUN_DRAFT_KEY = "hausman_hub:first_run_draft:v1";
const FIRST_RUN_DRAFT_VERSION = 1;
const FIRST_RUN_DRAFT_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

const RESTORABLE_STEPS = new Set([
  "rooms", "room", "home", "validation", "save", "code_source", "tablet", "completion",
]);
const RESTORABLE_ROOM_PANES = new Set(["devices", "comfort", "schedule", "limits", "review"]);

function browserStorage() {
  try {
    return typeof globalThis !== "undefined" ? globalThis.localStorage : null;
  } catch (_error) {
    return null;
  }
}

function plainObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function jsonClone(value, fallback) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (_error) {
    return fallback;
  }
}

function roomDraft(state) {
  return {
    day: jsonClone(state.day, {}),
    devices: jsonClone(state.devices, {}),
    included: state.included === true,
    maxTemperature: state.maxTemperature ?? null,
    minTemperature: state.minTemperature ?? null,
    night: jsonClone(state.night, {}),
    report: jsonClone(state.report, null),
    showAllDevices: state.showAllDevices === true,
  };
}

function hasMeaningfulDraft(firstRun) {
  if (firstRun.step !== "instructions") return true;
  if (Object.keys(firstRun.areaAssignments || {}).length) return true;
  return Object.values(firstRun.rooms || {}).some((state) => (
    state.included === true || Object.values(state.devices || {}).some((device) => device.selected === true)
  ));
}

export function clearFirstRunDraft(_panel, storage = browserStorage()) {
  try {
    storage?.removeItem(FIRST_RUN_DRAFT_KEY);
  } catch (_error) {
    // Restricted WebViews may deny storage. The wizard still remains usable.
  }
}

export function resumeFirstRunDraft(panel, storage = browserStorage(), now = Date.now()) {
  if (!panel?._firstRun || panel._firstRunDraftReady || panel._firstRun.step !== "instructions" || !storage) {
    return false;
  }
  try {
    const saved = JSON.parse(storage.getItem(FIRST_RUN_DRAFT_KEY) || "null");
    if (
      !saved || saved.version !== FIRST_RUN_DRAFT_VERSION
      || !Number.isFinite(saved.savedAt) || now - saved.savedAt > FIRST_RUN_DRAFT_MAX_AGE_MS
      || !RESTORABLE_STEPS.has(saved.step)
    ) return false;
    panel._firstRun.step = "rooms";
    panel._firstRun.deferred = false;
    return true;
  } catch (_error) {
    clearFirstRunDraft(panel, storage);
    return false;
  }
}

export function persistFirstRunDraft(panel, storage = browserStorage(), now = Date.now()) {
  const firstRun = panel?._firstRun;
  if (!firstRun || !firstRun.options || !storage) return false;
  if (firstRun.completed === true || !hasMeaningfulDraft(firstRun)) {
    clearFirstRunDraft(panel, storage);
    return false;
  }
  const rooms = {};
  Object.entries(firstRun.rooms || {}).forEach(([roomId, state]) => {
    rooms[roomId] = roomDraft(state);
  });
  const payload = {
    activeRoomSetupPane: panel._activeRoomSetupPane,
    areaAssignments: jsonClone(firstRun.areaAssignments, {}),
    contourSaved: firstRun.contourSaved === true,
    draft: jsonClone(firstRun.draft, null),
    home: jsonClone(firstRun.home, null),
    issues: jsonClone(firstRun.issues, []),
    roomId: firstRun.roomId,
    rooms,
    savedAt: now,
    schedule: jsonClone(firstRun.schedule, {}),
    setupRevision: firstRun.setupRevision,
    showRoomDevices: firstRun.showRoomDevices === true,
    snapshotRevision: firstRun.options.snapshot_revision,
    step: firstRun.step,
    validRoomIds: Array.from(firstRun.validRooms || []),
    validation: jsonClone(firstRun.validation, null),
    version: FIRST_RUN_DRAFT_VERSION,
  };
  try {
    storage.setItem(FIRST_RUN_DRAFT_KEY, JSON.stringify(payload));
    return true;
  } catch (_error) {
    return false;
  }
}

function restoreRoom(panel, room, savedState, keepValidation) {
  const state = panel._firstRunRoomState(room);
  const source = plainObject(savedState);
  state.included = source.included === true;
  state.showAllDevices = source.showAllDevices === true;
  ["day", "night"].forEach((profile) => {
    const values = plainObject(source[profile]);
    if (values.temperature !== undefined) state[profile].temperature = values.temperature;
    if (values.humidity !== undefined) state[profile].humidity = values.humidity;
    if (typeof values.strategy === "string") state[profile].strategy = values.strategy;
  });
  if (source.minTemperature !== undefined) state.minTemperature = source.minTemperature;
  if (source.maxTemperature !== undefined) state.maxTemperature = source.maxTemperature;
  Object.entries(plainObject(source.devices)).forEach(([key, savedDevice]) => {
    const current = state.devices[key];
    if (!current) return;
    current.selected = savedDevice?.selected === true;
    current.channel = typeof savedDevice?.channel === "string" ? savedDevice.channel : null;
  });
  state.report = keepValidation ? jsonClone(source.report, null) : null;
}

export function restoreFirstRunDraft(panel, storage = browserStorage(), now = Date.now()) {
  const firstRun = panel?._firstRun;
  if (panel) panel._firstRunDraftReady = true;
  if (!firstRun?.options || !storage) {
    return { restored: false, validationInvalidated: false };
  }
  let saved;
  try {
    saved = JSON.parse(storage.getItem(FIRST_RUN_DRAFT_KEY) || "null");
  } catch (_error) {
    clearFirstRunDraft(panel, storage);
    return { restored: false, validationInvalidated: false };
  }
  if (
    !saved || saved.version !== FIRST_RUN_DRAFT_VERSION
    || !Number.isFinite(saved.savedAt) || now - saved.savedAt > FIRST_RUN_DRAFT_MAX_AGE_MS
  ) {
    clearFirstRunDraft(panel, storage);
    return { restored: false, validationInvalidated: false };
  }
  const sameRevision = saved.setupRevision === firstRun.setupRevision
    && saved.snapshotRevision === firstRun.options.snapshot_revision;
  const availableRooms = new Map((firstRun.options.rooms || []).map((room) => [room.id, room]));
  Object.entries(plainObject(saved.rooms)).forEach(([roomId, savedState]) => {
    const room = availableRooms.get(roomId);
    if (room) restoreRoom(panel, room, savedState, sameRevision);
  });
  const groupIds = new Set(panel._firstRunPhysicalGroups(panel._firstRunAreaCandidates())
    .map((group) => panel._firstRunPhysicalGroupId(group)));
  firstRun.areaAssignments = Object.fromEntries(
    Object.entries(plainObject(saved.areaAssignments))
      .filter(([groupId, roomId]) => groupIds.has(groupId)
        && (roomId === "" || availableRooms.has(roomId)))
  );
  firstRun.showRoomDevices = saved.showRoomDevices === true;
  const schedule = plainObject(saved.schedule);
  firstRun.schedule = {
    dayStart: typeof schedule.dayStart === "string" ? schedule.dayStart : firstRun.schedule.dayStart,
    enabled: schedule.enabled === true,
    nightStart: typeof schedule.nightStart === "string" ? schedule.nightStart : firstRun.schedule.nightStart,
  };
  if (saved.home && typeof saved.home === "object") firstRun.home = jsonClone(saved.home, null);
  firstRun.validRooms = new Set(sameRevision
    ? (Array.isArray(saved.validRoomIds) ? saved.validRoomIds.filter((id) => availableRooms.has(id)) : [])
    : []);
  firstRun.draft = sameRevision ? jsonClone(saved.draft, null) : null;
  firstRun.validation = sameRevision ? jsonClone(saved.validation, null) : null;
  firstRun.issues = sameRevision ? jsonClone(saved.issues, []) : [];
  firstRun.contourSaved = sameRevision && saved.contourSaved === true;
  const savedRoomId = typeof saved.roomId === "string" && availableRooms.has(saved.roomId)
    ? saved.roomId : null;
  firstRun.roomId = savedRoomId;
  let step = RESTORABLE_STEPS.has(saved.step) ? saved.step : "rooms";
  if (step === "room" && !savedRoomId) step = "rooms";
  if (!sameRevision && ["validation", "save", "code_source", "tablet", "completion"].includes(step)) {
    step = savedRoomId ? "room" : "rooms";
  }
  firstRun.step = step;
  if (RESTORABLE_ROOM_PANES.has(saved.activeRoomSetupPane)) {
    panel._activeRoomSetupPane = saved.activeRoomSetupPane;
  }
  panel._notice = sameRevision
    ? "Черновик настройки восстановлен."
    : "Черновик восстановлен. Инвентаризация изменилась — повторите проверку комнаты.";
  return { restored: true, validationInvalidated: !sameRevision };
}

export { FIRST_RUN_DRAFT_KEY };
