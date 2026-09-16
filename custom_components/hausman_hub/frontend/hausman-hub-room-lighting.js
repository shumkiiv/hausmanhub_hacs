/* Room lighting settings editor for the room card and the full-size screen.
 *
 * The stored configuration stays the source of truth: this module keeps a
 * local draft, never invents server rules and never sends a physical command
 * from the preview. The same component serves the room detail sheet and the
 * full-size screen opened from the "Освещение" tab, so the `.rle-*` styles
 * have exactly one owner. The full-size wrapper adds only `.rls-*` classes.
 *
 * All identifiers that a normal user must not type (config id, entity id,
 * server version) live inside collapsed "Технические данные" blocks. The
 * visible form only offers readable names and catalog devices.
 */

import { apiErrorMessage, resolveApiError } from "./hausman-hub-error-taxonomy.js?v=1.52.288";

export const ROOM_LIGHTING_SECTIONS = [
  ["overview", "Обзор"],
  ["lights", "Светильники и роли"],
  ["inputs", "Датчики и выключатели"],
  ["periods", "Периоды"],
  ["conditions", "Условия и гашение"],
  ["manual-away", "Ручное управление и уход"],
  ["review-save", "Проверка и сохранение"],
];

const TEMPLATE_ROLES = [
  ["main", "Основной"],
  ["accent", "Акцент"],
  ["night", "Ночной"],
  ["mirror", "Зеркало"],
  ["other", "Другой"],
];
const SENSOR_KINDS = [
  ["presence", "Присутствие"],
  ["motion", "Движение"],
  ["illuminance", "Освещённость"],
];
const LIGHT_KINDS = [
  ["light", "Светильник"],
  ["switch", "Нерегулируемый"],
];
const PRESS_TYPES = [
  ["single", "Одно"],
  ["double", "Двойное"],
  ["long", "Долгое"],
  ["hold", "Удержание"],
  ["up", "Верх"],
  ["down", "Низ"],
];
const BUTTONS = [
  ["left", "Левая"],
  ["right", "Правая"],
  ["up", "Верхняя"],
  ["down", "Нижняя"],
];
const SWITCH_ACTIONS = [
  ["turn_on", "Включить"],
  ["turn_off", "Выключить"],
  ["toggle", "Переключить"],
  ["set_max", "На максимум"],
  ["return_to_auto", "Вернуть в автоуправление"],
];
const SCHEDULE_MODES = [
  ["on_presence", "По присутствию"],
  ["always", "Всегда"],
  ["night_light", "Ночная подсветка"],
  ["off", "Выключить"],
];
const ANCHOR_KINDS = [
  ["fixed", "Время"],
  ["sunrise", "Рассвет"],
  ["sunset", "Закат"],
];
const RELEASE_MODES = [
  ["timer_and_absence", "Срок и отсутствие"],
  ["timer_only", "Только срок"],
  ["absence_only", "Только отсутствие"],
];

/* Keys a user may edit. Server-owned keys (version, updatedAt, contract,
   commandsEnabled, overrides) are never carried between baselines. */
const EDITABLE_KEYS = [
  "name",
  "templateId",
  "devices",
  "schedule",
  "switchBindings",
  "illumination",
  "timers",
  "behaviors",
  "dimming",
  "manualOffProtection",
  "awayBehavior",
  "autoAdopt",
];

function lightingApi(roomId, suffix = "") {
  return `hausman_hub/v1/rooms/${encodeURIComponent(roomId)}/lighting${suffix}`;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value ?? null));
}

function nextFreeId(items, prefix) {
  const used = new Set((Array.isArray(items) ? items : [])
    .map((item) => item && item.id)
    .filter(Boolean));
  let index = 1;
  while (used.has(`${prefix}_${index}`)) index += 1;
  return `${prefix}_${index}`;
}

function lightingState(panel, roomId) {
  if (!panel._roomLighting) panel._roomLighting = { byRoom: {} };
  if (!panel._roomLighting.byRoom) panel._roomLighting.byRoom = {};
  if (!panel._roomLighting.byRoom[roomId]) {
    panel._roomLighting.byRoom[roomId] = {
      loaded: false,
      loading: false,
      saving: false,
      applying: null,
      error: "",
      notice: "",
      conflict: false,
      conflictMessage: "",
      config: null,
      draft: null,
      templates: [],
      templateBaseline: null,
      catalog: [],
      status: null,
      preview: null,
      previewing: false,
      previewTimer: null,
      section: "overview",
      live: null,
      rerender: null,
    };
  }
  return panel._roomLighting.byRoom[roomId];
}

function lightingErrorMessage(error) {
  const body = error && typeof error === "object" ? error.body : null;
  if (body && typeof body.message === "string" && body.message) return body.message;
  return apiErrorMessage(error);
}

function changedEditableKeys(baseline, draft) {
  if (!draft || typeof draft !== "object") return [];
  return EDITABLE_KEYS.filter((key) => JSON.stringify(baseline ? baseline[key] : null)
    !== JSON.stringify(draft[key]));
}

function rerender(panel, roomId) {
  const store = lightingState(panel, roomId);
  if (typeof store.rerender === "function") store.rerender();
  panel._render();
}

function markDirty(panel, roomId) {
  const store = lightingState(panel, roomId);
  store.notice = "";
  store.error = "";
  schedulePreview(panel, roomId);
  rerender(panel, roomId);
}

function schedulePreview(panel, roomId) {
  const store = lightingState(panel, roomId);
  if (store.previewTimer && typeof clearTimeout === "function") {
    clearTimeout(store.previewTimer);
  }
  if (typeof setTimeout !== "function") return;
  store.previewTimer = setTimeout(() => {
    store.previewTimer = null;
    runPreview(panel, roomId).catch(() => null);
  }, 700);
}

async function runPreview(panel, roomId) {
  const store = lightingState(panel, roomId);
  // The preview route validates a stored room; a draft without a stored
  // baseline cannot be previewed yet and must not produce a fake result.
  if (!store.draft || !store.config || store.saving) return;
  store.previewing = true;
  rerender(panel, roomId);
  try {
    const result = await panel._hass.callApi("POST", lightingApi(roomId, "/preview"), store.draft);
    store.preview = result && typeof result === "object" ? result : null;
  } catch (error) {
    store.preview = null;
    const policy = resolveApiError(error);
    if (policy.code === "revision_conflict") {
      store.error = "Настройки изменились на другом клиенте. Черновик сохранён — загрузите свежую версию и повторите.";
    } else {
      store.error = lightingErrorMessage(error);
    }
  } finally {
    store.previewing = false;
    rerender(panel, roomId);
  }
}

export async function refreshRoomLighting(panel, roomId, force = false) {
  const store = lightingState(panel, roomId);
  if (store.loading || (store.loaded && !force)) return;
  store.loading = true;
  store.error = "";
  store.conflict = false;
  store.conflictMessage = "";
  store.preview = null;
  rerender(panel, roomId);
  try {
    const [config, templates, catalog, status] = await Promise.all([
      panel._hass.callApi("GET", lightingApi(roomId, "/config")).catch((error) => {
        if (error && (error.status === 404 || error.status_code === 404)) return null;
        throw error;
      }),
      panel._hass.callApi("GET", lightingApi(roomId, "/templates")).catch(() => null),
      panel._hass.callApi("GET", lightingApi(roomId, "/editor-catalog")).catch(() => null),
      panel._hass.callApi("GET", lightingApi(roomId, "/status")).catch(() => null),
    ]);
    store.config = config && typeof config === "object" ? config : null;
    store.draft = clone(store.config);
    store.templates = templates && Array.isArray(templates.templates) ? templates.templates : [];
    store.catalog = catalog && Array.isArray(catalog.devices) ? catalog.devices : [];
    store.status = status && typeof status === "object" ? status : null;
    store.loaded = true;
  } catch (error) {
    store.error = lightingErrorMessage(error);
  } finally {
    store.loading = false;
    rerender(panel, roomId);
  }
}

async function saveRoomLighting(panel, roomId) {
  const store = lightingState(panel, roomId);
  if (!store.draft || store.saving) return;
  store.saving = true;
  store.error = "";
  store.notice = "";
  store.conflict = false;
  store.conflictMessage = "";
  rerender(panel, roomId);
  try {
    const saved = await panel._hass.callApi("PUT", lightingApi(roomId, "/config"), store.draft);
    if (!saved || typeof saved !== "object" || typeof saved.version !== "number") {
      throw new Error("сервер не вернул сохранённую версию");
    }
    store.config = saved;
    store.draft = clone(saved);
    store.preview = null;
    store.notice = "Сохранено. Черновик стал текущей версией.";
  } catch (error) {
    const policy = resolveApiError(error);
    if (policy.code === "revision_conflict") {
      await carryLocalEditsOntoFreshConfig(panel, roomId);
    } else {
      store.error = lightingErrorMessage(error);
    }
  } finally {
    store.saving = false;
    rerender(panel, roomId);
  }
}

/* A 409 must never drop the local draft. Re-read the stored document, carry
   every locally changed editable key onto the fresh baseline (including the
   fresh version/updatedAt) and tell the user honestly that nothing was saved. */
async function carryLocalEditsOntoFreshConfig(panel, roomId) {
  const store = lightingState(panel, roomId);
  const localDraft = clone(store.draft);
  const changed = changedEditableKeys(store.config, localDraft);
  const fresh = await panel._hass.callApi("GET", lightingApi(roomId, "/config")).catch(() => null);
  store.conflict = true;
  if (!fresh || typeof fresh !== "object") {
    store.conflictMessage = "Настройки изменились на другом клиенте. Свежую версию получить не удалось; локальные правки сохранены. Проверьте связь и повторите.";
    store.error = "";
    return;
  }
  const carried = clone(fresh);
  changed.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(localDraft, key)) {
      carried[key] = clone(localDraft[key]);
    }
  });
  carried.roomId = typeof fresh.roomId === "string" && fresh.roomId ? fresh.roomId : roomId;
  carried.version = fresh.version;
  carried.updatedAt = fresh.updatedAt;
  store.config = fresh;
  store.draft = carried;
  store.preview = null;
  store.error = "";
  store.notice = "";
  store.conflictMessage = changed.length
    ? `Настройки изменились на другом клиенте. Ничего не сохранено: ваши правки (${changed.length}) перенесены на свежую версию ${fresh.version}. Проверьте их и сохраните снова.`
    : `Настройки изменились на другом клиенте. Ничего не сохранено: свежая версия ${fresh.version} загружена, проверьте её и сохраните снова.`;
}

async function applyTemplate(panel, roomId, templateId) {
  const store = lightingState(panel, roomId);
  store.applying = templateId;
  store.error = "";
  store.notice = "";
  store.conflict = false;
  store.conflictMessage = "";
  rerender(panel, roomId);
  try {
    // The template route builds a draft; it is not a stored version. Re-read
    // the stored document so version/updatedAt come from the server instead of
    // being invented on the client.
    const applied = await panel._hass.callApi("POST", lightingApi(roomId, "/templates/apply"), {
      templateId,
      keepDevices: true,
    });
    const stored = await panel._hass.callApi("GET", lightingApi(roomId, "/config")).catch((error) => {
      if (error && (error.status === 404 || error.status_code === 404)) return null;
      throw error;
    });
    const draft = clone(applied && typeof applied === "object" ? applied : stored);
    if (!draft) throw new Error("шаблон вернул пустой документ");
    if (stored && typeof stored === "object") {
      draft.version = stored.version;
      draft.updatedAt = stored.updatedAt;
      if (typeof stored.roomId === "string" && stored.roomId) draft.roomId = stored.roomId;
    }
    store.config = stored && typeof stored === "object" ? stored : null;
    store.draft = draft;
    store.preview = null;
    store.templateBaseline = clone(applied && typeof applied === "object" ? applied : draft);
    store.loaded = true;
    store.notice = "Шаблон применён сервером. Проверьте черновик и сохраните изменения.";
  } catch (error) {
    store.error = lightingErrorMessage(error);
  } finally {
    store.applying = null;
    rerender(panel, roomId);
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function runLiveTest(panel, roomId, mode) {
  const store = lightingState(panel, roomId);
  store.live = { status: "running", mode, cid: "", trace: null, error: "" };
  store.error = "";
  rerender(panel, roomId);
  try {
    const started = await panel._hass.callApi("POST", lightingApi(roomId, "/live-tests"), { mode });
    store.live.cid = started && started.correlationId ? started.correlationId : "";
    pollLiveTest(panel, roomId, store.live.cid).catch(() => null);
  } catch (error) {
    store.live = { status: "error", mode, cid: "", trace: null, error: lightingErrorMessage(error) };
    rerender(panel, roomId);
  }
}

async function pollLiveTest(panel, roomId, correlationId) {
  const store = lightingState(panel, roomId);
  if (!correlationId) return;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await delay(700);
    if (!store.live || store.live.cid !== correlationId) return;
    try {
      const document = await panel._hass.callApi(
        "GET",
        lightingApi(roomId, `/live-tests/${encodeURIComponent(correlationId)}`),
      );
      store.live.trace = document;
      if (document && document.kind === "result") {
        store.live.status = document.result && document.result.status ? document.result.status : "passed";
        rerender(panel, roomId);
        return;
      }
      rerender(panel, roomId);
    } catch (error) {
      store.live.status = "error";
      store.live.error = lightingErrorMessage(error);
      rerender(panel, roomId);
      return;
    }
  }
  store.live.status = "timeout";
  rerender(panel, roomId);
}

async function cancelLiveTest(panel, roomId) {
  const store = lightingState(panel, roomId);
  if (!store.live || !store.live.cid) return;
  try {
    const trace = await panel._hass.callApi(
      "POST",
      lightingApi(roomId, `/live-tests/${encodeURIComponent(store.live.cid)}`),
    );
    store.live.trace = trace;
    store.live.status = "cancelled";
  } catch (error) {
    store.live.error = lightingErrorMessage(error);
  }
  rerender(panel, roomId);
}

/* ---------------------------------------------------------------- controls */

function field(deps, label, control) {
  const wrap = deps.el("label", "rle-field");
  wrap.appendChild(deps.el("span", "rle-field-label", label));
  wrap.appendChild(control);
  return wrap;
}

function readonlyField(deps, label, value) {
  const wrap = deps.el("div", "rle-field");
  wrap.appendChild(deps.el("span", "rle-field-label", label));
  wrap.appendChild(deps.el("strong", null, value));
  return wrap;
}

function textInput(deps, value, onCommit, options = {}) {
  const input = deps.el("input", "rle-input");
  input.type = options.type || "text";
  if (options.min !== undefined) input.min = String(options.min);
  if (options.max !== undefined) input.max = String(options.max);
  input.placeholder = options.placeholder || "";
  input.value = value === null || value === undefined ? "" : String(value);
  input.addEventListener("change", () => onCommit(input.value, options));
  return input;
}

function numberCommit(value, options) {
  if (value === "") return options.nullable ? null : 0;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return options.nullable ? null : 0;
  return parsed;
}

function numberInput(deps, label, value, onCommit, options = {}) {
  return field(deps, label, textInput(deps, value, (raw) => onCommit(numberCommit(raw, options)), {
    type: "number",
    min: options.min,
    max: options.max,
    nullable: options.nullable,
  }));
}

function selectInput(deps, label, value, options, onCommit) {
  const select = deps.el("select", "rle-input");
  options.forEach(([id, text]) => {
    const option = deps.el("option", null, text);
    option.value = id;
    select.appendChild(option);
  });
  select.value = value === null || value === undefined ? "" : String(value);
  select.addEventListener("change", () => onCommit(select.value));
  return field(deps, label, select);
}

function checkInput(deps, label, checked, onCommit) {
  const wrap = deps.el("label", "rle-check");
  const input = deps.el("input");
  input.type = "checkbox";
  input.checked = Boolean(checked);
  input.addEventListener("change", () => onCommit(input.checked));
  wrap.appendChild(input);
  wrap.appendChild(deps.el("span", null, label));
  return wrap;
}

function autoControlField(deps, target, panel, roomId) {
  const wrap = deps.el("div", "rle-field");
  wrap.appendChild(checkInput(deps, "Авто", target.autoControl !== false, (checked) => {
    target.autoControl = checked;
    markDirty(panel, roomId);
  }));
  if (target.autoControl === false) {
    wrap.appendChild(deps.el("p", "rle-hint", "Система не трогает эту цель, только вручную."));
  }
  return wrap;
}

function listInput(deps, label, values, onCommit) {
  return field(deps, label, textInput(
    deps,
    Array.isArray(values) ? values.join(", ") : values || "",
    (raw) => onCommit(raw.split(",").map((item) => item.trim()).filter(Boolean)),
    { placeholder: "через запятую" },
  ));
}

function button(deps, label, onClick, className = "") {
  const node = deps.el("button", `rle-button ${className}`.trim(), label);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function detailsBlock(deps, className, summary, children) {
  const details = deps.el("details", className);
  details.appendChild(deps.el("summary", null, summary));
  const body = deps.el("div", "rle-details-body");
  children.forEach((child) => body.appendChild(child));
  details.appendChild(body);
  return details;
}

function technicalBlock(deps, children, testId) {
  const details = detailsBlock(deps, "rle-technical", "Технические данные", children);
  if (testId) setData(deps, details, "data-testid", testId);
  return details;
}

function setData(deps, node, name, value) {
  if (deps && typeof deps.setAttr === "function") deps.setAttr(node, name, value);
  else if (node && typeof node.setAttribute === "function") node.setAttribute(name, value);
  return node;
}

function itemCard(deps, title, onRemove, children, extras = {}) {
  const card = deps.el("div", "rle-item");
  const head = deps.el("div", "rle-item-head");
  head.appendChild(deps.el("strong", null, title));
  if (onRemove) head.appendChild(button(deps, "Удалить", onRemove, "is-danger"));
  card.appendChild(head);
  const grid = deps.el("div", "rle-grid");
  children.forEach((child) => grid.appendChild(child));
  card.appendChild(grid);
  if (Array.isArray(extras.advanced) && extras.advanced.length) {
    card.appendChild(detailsBlock(deps, "rle-advanced", "Дополнительно", extras.advanced));
  }
  if (Array.isArray(extras.technical) && extras.technical.length) {
    card.appendChild(technicalBlock(deps, extras.technical, extras.technicalTestId));
  }
  return card;
}

function ensureDevices(draft) {
  const devices = draft.devices || (draft.devices = {});
  devices.sensors = Array.isArray(devices.sensors) ? devices.sensors : [];
  devices.light_targets = Array.isArray(devices.light_targets) ? devices.light_targets : [];
  devices.wireless_switches = Array.isArray(devices.wireless_switches) ? devices.wireless_switches : [];
  devices.power_switch = devices.power_switch || null;
  if (typeof devices.selectAll !== "boolean") devices.selectAll = false;
  if (typeof draft.autoAdopt !== "boolean") draft.autoAdopt = true;
  return devices;
}

/* ------------------------------------------------------------- sections */

function decisionText(store) {
  if (store.preview && typeof store.preview.summary === "string" && store.preview.summary) {
    return store.preview.summary;
  }
  const base = "Событие -> Условия -> Решение -> Свет";
  const sources = store.status && Array.isArray(store.status.sources) ? store.status.sources : [];
  const source = sources[0];
  if (!source) return `${base}. Предпросмотр появится после изменения черновика.`;
  const ownership = source.ownership === "manual"
    ? "светом управляет человек, автоматика не вмешивается"
    : source.ownership === "auto"
      ? "автоматика имеет подтверждённое владение"
      : "владение автоматикой не подтверждено";
  return `${base}. Цель «${source.name || source.source_id}»: ${source.state || "неизвестно"}, ${ownership}.`;
}

function renderDecision(store, deps) {
  const block = deps.el("div", "rle-decision");
  setData(deps, block, "data-testid", "room-lighting:decision");
  block.appendChild(deps.el("strong", null, "Событие -> Условия -> Решение -> Свет"));
  block.appendChild(deps.el("p", "rle-hint", decisionText(store)));
  if (store.previewing) block.appendChild(deps.el("p", "rle-hint", "Проверяем черновик без команд…"));
  return block;
}

function renderIssues(store, deps, section) {
  const all = store.preview && Array.isArray(store.preview.sectionIssues)
    ? store.preview.sectionIssues : [];
  const issues = section ? all.filter((issue) => issue && issue.section === section) : all;
  if (!issues.length) return null;
  const list = deps.el("ul", "rle-issues");
  issues.forEach((issue) => list.appendChild(deps.el("li", null, issue.message || issue.code || "Ошибка")));
  return list;
}

function renderConflict(panel, roomId, store, deps) {
  const block = deps.el("div", "rle-conflict");
  setData(deps, block, "data-testid", "room-lighting:conflict");
  block.appendChild(deps.el("strong", null, "Конфликт версии"));
  block.appendChild(deps.el("p", null, store.conflictMessage || "Настройки изменились на другом клиенте."));
  const actions = deps.el("div", "rle-bar");
  actions.appendChild(button(deps, "Загрузить свежую версию без моих правок", () => {
    void refreshRoomLighting(panel, roomId, true);
  }));
  actions.appendChild(button(deps, "Сохранить мои правки", () => {
    void saveRoomLighting(panel, roomId);
  }, "is-primary"));
  block.appendChild(actions);
  return block;
}

function renderOverviewSection(panel, roomId, store, deps) {
  const draft = store.draft;
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Обзор"));
  const grid = deps.el("div", "rle-grid");
  const nameInput = textInput(deps, draft.name, (value) => {
    draft.name = value;
    markDirty(panel, roomId);
  });
  setData(deps, nameInput, "data-testid", "room-lighting:name");
  grid.appendChild(field(deps, "Название профиля", nameInput));
  if (store.templateBaseline) {
    const changed = changedEditableKeys(store.templateBaseline, draft);
    grid.appendChild(readonlyField(deps, "Отличие от шаблона", changed.length ? changed.join(", ") : "совпадает с шаблоном"));
  }
  block.appendChild(grid);
  block.appendChild(checkInput(deps, "Автоматически подхватывать новые устройства", draft.autoAdopt !== false, (checked) => {
    draft.autoAdopt = checked;
    markDirty(panel, roomId);
  }));
  const issues = renderIssues(store, deps, "overview");
  if (issues) block.appendChild(issues);
  if (store.templates.length) {
    block.appendChild(deps.el("h5", null, "Шаблоны"));
    block.appendChild(deps.el("p", "rle-hint", "Шаблон применяется на сервере; сохранение остаётся отдельным действием."));
    const list = deps.el("div", "rle-bar");
    store.templates.forEach((template) => {
      const apply = button(deps, `Применить: ${template.title || template.id}`, () => {
        void applyTemplate(panel, roomId, template.id);
      });
      apply.disabled = Boolean(store.applying);
      list.appendChild(apply);
    });
    block.appendChild(list);
    if (store.applying) block.appendChild(deps.el("p", "rle-hint", "Применяем шаблон на сервере…"));
  }
  const technical = deps.el("div", "rle-grid");
  const versionField = deps.el("div", "rle-field");
  versionField.appendChild(deps.el("span", "rle-field-label", "Версия на сервере"));
  const versionValue = deps.el("strong", null, String(store.config && store.config.version !== undefined ? store.config.version : "—"));
  setData(deps, versionValue, "data-testid", "room-lighting:version");
  versionField.appendChild(versionValue);
  technical.appendChild(versionField);
  technical.appendChild(readonlyField(deps, "Обновлено", String(store.config && store.config.updatedAt !== undefined ? store.config.updatedAt : "—")));
  technical.appendChild(readonlyField(deps, "Каталог устройств", `${store.catalog.length}`));
  block.appendChild(technicalBlock(deps, [technical], "room-lighting:technical-overview"));
  setData(deps, block, "data-section", "overview");
  return [block];
}

function catalogRow(deps, label, available, action) {
  const row = deps.el("div", "rle-device-row");
  row.appendChild(deps.el("span", "rle-device", available === false ? `${label} · нет связи` : label));
  if (action) row.appendChild(action);
  return row;
}

function addCatalogTarget(devices, device) {
  const prefix = String(device.id || "light").replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 40) || "light";
  devices.light_targets.push({
    id: nextFreeId(devices.light_targets, prefix),
    name: device.label || "Свет",
    kind: device.kind === "switch" ? "switch" : "light",
    role: "other",
    entityId: device.entityId || undefined,
    groupId: null,
    brightness: device.supportsBrightness === true,
    color_temperature: device.supportsColorTemperature === true,
    autoAdoptOverride: null,
    autoControl: true,
  });
}

function renderLightsSection(panel, roomId, store, deps) {
  const devices = ensureDevices(store.draft);
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Светильники и роли"));
  block.appendChild(deps.el("p", "rle-hint", "Обычные поля показывают понятные имена. Идентификаторы и сущности — в «Технических данных»."));
  devices.light_targets.forEach((target, index) => {
    block.appendChild(itemCard(deps, target.name || target.id || "Свет", () => {
      devices.light_targets.splice(index, 1);
      markDirty(panel, roomId);
    }, [
      field(deps, "Название", textInput(deps, target.name, (value) => { target.name = value; markDirty(panel, roomId); })),
      selectInput(deps, "Вид", target.kind, LIGHT_KINDS, (value) => { target.kind = value; markDirty(panel, roomId); }),
      selectInput(deps, "Роль", target.role || "", [["", "Не задана"], ...TEMPLATE_ROLES], (value) => {
        target.role = value || null;
        markDirty(panel, roomId);
      }),
      checkInput(deps, "Поддерживает яркость", target.brightness, (checked) => { target.brightness = checked; markDirty(panel, roomId); }),
      checkInput(deps, "Поддерживает оттенок", target.color_temperature, (checked) => { target.color_temperature = checked; markDirty(panel, roomId); }),
      autoControlField(deps, target, panel, roomId),
      selectInput(deps, "Авто-подхват", target.autoAdoptOverride === null || target.autoAdoptOverride === undefined
        ? "inherit" : String(target.autoAdoptOverride), [
        ["inherit", "Как у комнаты"], ["true", "Да"], ["false", "Нет"],
      ], (value) => {
        target.autoAdoptOverride = value === "inherit" ? null : value === "true";
        markDirty(panel, roomId);
      }),
    ], {
      advanced: [
        field(deps, "Группа", textInput(deps, target.groupId, (value) => { target.groupId = value || null; markDirty(panel, roomId); })),
      ],
      technical: [
        field(deps, "ID", textInput(deps, target.id, (value) => { target.id = value; markDirty(panel, roomId); })),
        field(deps, "Сущность", textInput(deps, target.entityId, (value) => {
          target.entityId = value || undefined;
          markDirty(panel, roomId);
        })),
      ],
    }));
  });
  block.appendChild(button(deps, "Добавить цель света", () => {
    devices.light_targets.push({
      id: nextFreeId(devices.light_targets, "light"), name: "Свет", kind: "light",
      role: "other", groupId: null, brightness: true, color_temperature: false,
      autoAdoptOverride: null, autoControl: true,
    });
    markDirty(panel, roomId);
  }));
  if (store.catalog.length) {
    block.appendChild(deps.el("h5", null, "Найденные устройства комнаты"));
    store.catalog.forEach((device) => {
      const already = devices.light_targets.some((target) => device.entityId && target.entityId === device.entityId);
      const add = button(deps, already ? "Уже добавлено" : "Добавить", () => {
        addCatalogTarget(devices, device);
        markDirty(panel, roomId);
      });
      add.disabled = already || !device.entityId;
      block.appendChild(catalogRow(deps, device.label || device.id || "Устройство", device.available, add));
    });
  }
  const issues = renderIssues(store, deps, "lights");
  if (issues) block.appendChild(issues);
  return [block];
}

function renderInputsSection(panel, roomId, store, deps) {
  const devices = ensureDevices(store.draft);
  const blocks = [];
  const sensors = deps.el("section", "rle-block");
  sensors.appendChild(deps.el("h4", null, "Датчики и выключатели"));
  sensors.appendChild(deps.el("h5", null, "Датчики"));
  devices.sensors.forEach((sensor, index) => {
    sensors.appendChild(itemCard(deps, sensor.name || sensor.id || "Датчик", () => {
      devices.sensors.splice(index, 1);
      markDirty(panel, roomId);
    }, [
      field(deps, "Название", textInput(deps, sensor.name, (value) => { sensor.name = value; markDirty(panel, roomId); })),
      selectInput(deps, "Тип", sensor.kind, SENSOR_KINDS, (value) => { sensor.kind = value; markDirty(panel, roomId); }),
      selectInput(deps, "Авто-подхват", sensor.autoAdoptOverride === null || sensor.autoAdoptOverride === undefined
        ? "inherit" : String(sensor.autoAdoptOverride), [
        ["inherit", "Как у комнаты"], ["true", "Да"], ["false", "Нет"],
      ], (value) => {
        sensor.autoAdoptOverride = value === "inherit" ? null : value === "true";
        markDirty(panel, roomId);
      }),
    ], {
      technical: [
        field(deps, "ID", textInput(deps, sensor.id, (value) => { sensor.id = value; markDirty(panel, roomId); })),
        field(deps, "Сущность", textInput(deps, sensor.entityId, (value) => {
          sensor.entityId = value || undefined;
          markDirty(panel, roomId);
        })),
      ],
    }));
  });
  sensors.appendChild(button(deps, "Добавить датчик", () => {
    devices.sensors.push({ id: nextFreeId(devices.sensors, "sensor"), name: "Датчик", kind: "presence", autoAdoptOverride: null });
    markDirty(panel, roomId);
  }));
  const issues = renderIssues(store, deps, "inputs");
  if (issues) sensors.appendChild(issues);
  blocks.push(sensors);

  const switches = deps.el("section", "rle-block");
  switches.appendChild(deps.el("h4", null, "Выключатели и жесты"));
  if (devices.power_switch) {
    const power = devices.power_switch;
    switches.appendChild(itemCard(deps, `Питание: ${power.name || power.id || ""}`, () => {
      devices.power_switch = null;
      markDirty(panel, roomId);
    }, [
      field(deps, "Название", textInput(deps, power.name, (value) => { power.name = value; markDirty(panel, roomId); })),
    ], {
      technical: [
        field(deps, "ID", textInput(deps, power.id, (value) => { power.id = value; markDirty(panel, roomId); })),
        field(deps, "Сущность", textInput(deps, power.entityId, (value) => {
          power.entityId = value || undefined;
          markDirty(panel, roomId);
        })),
      ],
    }));
  } else {
    switches.appendChild(button(deps, "Добавить выключатель питания", () => {
      devices.power_switch = {
        id: nextFreeId([...devices.sensors, ...devices.light_targets, ...devices.wireless_switches], "switch_power"),
        name: "Питание",
        autoAdoptOverride: null,
      };
      markDirty(panel, roomId);
    }));
  }
  devices.wireless_switches.forEach((device, index) => {
    switches.appendChild(itemCard(deps, device.name || device.id || "Беспроводной выключатель", () => {
      devices.wireless_switches.splice(index, 1);
      markDirty(panel, roomId);
    }, [
      field(deps, "Название", textInput(deps, device.name, (value) => { device.name = value; markDirty(panel, roomId); })),
      listInput(deps, "Клавиши", device.buttons, (value) => { device.buttons = value; markDirty(panel, roomId); }),
      listInput(deps, "Типы нажатий", device.pressTypes, (value) => { device.pressTypes = value; markDirty(panel, roomId); }),
    ], {
      technical: [
        field(deps, "ID", textInput(deps, device.id, (value) => { device.id = value; markDirty(panel, roomId); })),
        field(deps, "Сущность", textInput(deps, device.entityId, (value) => {
          device.entityId = value || undefined;
          markDirty(panel, roomId);
        })),
      ],
    }));
  });
  switches.appendChild(button(deps, "Добавить беспроводной выключатель", () => {
    devices.wireless_switches.push({
      id: nextFreeId(devices.wireless_switches, "sw"),
      name: "Выключатель",
      buttons: ["left"],
      pressTypes: ["single"],
    });
    markDirty(panel, roomId);
  }));
  blocks.push(switches);
  blocks.push(renderSwitchBindings(panel, roomId, store, deps));
  return blocks;
}

function renderTargets(deps, targets, onChange) {
  const wrapper = deps.el("div", "rle-grid");
  wrapper.appendChild(listInput(deps, "Цели", targets.lightTargets, (value) => onChange({ ...targets, lightTargets: value })));
  wrapper.appendChild(listInput(deps, "Группы", targets.groupIds, (value) => onChange({ ...targets, groupIds: value })));
  wrapper.appendChild(listInput(deps, "Роли", targets.roles, (value) => onChange({ ...targets, roles: value })));
  return wrapper;
}

function renderSchedule(panel, roomId, store, deps) {
  const draft = store.draft;
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Периоды"));
  if (!Array.isArray(draft.schedule)) draft.schedule = [];
  draft.schedule.forEach((entry, index) => {
    entry.when = entry.when || { daysOfWeek: "all", holiday: false, anchor: { kind: "fixed", time: "09:00", offsetMinutes: 0 } };
    entry.how = entry.how || { brightness: 50, colorTemperature: 3000, fade: true, mode: "on_presence", minOnSeconds: 0 };
    entry.targets = entry.targets || { lightTargets: [], groupIds: [], roles: [] };
    block.appendChild(itemCard(deps, entry.title || entry.id || "Запись расписания", () => {
      draft.schedule.splice(index, 1);
      markDirty(panel, roomId);
    }, [
      field(deps, "Название", textInput(deps, entry.title, (value) => { entry.title = value; markDirty(panel, roomId); })),
      field(deps, "Дни (all/weekdays/weekend или список)", textInput(
        deps,
        Array.isArray(entry.when.daysOfWeek) ? entry.when.daysOfWeek.join(", ") : entry.when.daysOfWeek,
        (value) => {
          entry.when.daysOfWeek = value.includes(",")
            ? value.split(",").map((item) => item.trim()).filter(Boolean)
            : value;
          markDirty(panel, roomId);
        },
      )),
      checkInput(deps, "Только в праздник", entry.when.holiday, (checked) => { entry.when.holiday = checked; markDirty(panel, roomId); }),
      selectInput(deps, "Якорь", entry.when.anchor.kind, ANCHOR_KINDS, (value) => {
        entry.when.anchor.kind = value;
        markDirty(panel, roomId);
      }),
      field(deps, "Время (для якоря времени)", textInput(deps, entry.when.anchor.time, (value) => {
        entry.when.anchor.time = value || undefined;
        markDirty(panel, roomId);
      })),
      selectInput(deps, "Режим", entry.how.mode, SCHEDULE_MODES, (value) => {
        entry.how.mode = value;
        markDirty(panel, roomId);
      }),
      numberInput(deps, "Яркость, %", entry.how.brightness, (value) => { entry.how.brightness = value; markDirty(panel, roomId); }, { min: 0, max: 100, nullable: true }),
      numberInput(deps, "Оттенок, K", entry.how.colorTemperature, (value) => {
        entry.how.colorTemperature = value;
        markDirty(panel, roomId);
      }, { min: 2000, max: 6500, nullable: true }),
    ], {
      advanced: [
        renderTargets(deps, entry.targets, (value) => { entry.targets = value; markDirty(panel, roomId); }),
        numberInput(deps, "Смещение, мин", entry.when.anchor.offsetMinutes, (value) => {
          entry.when.anchor.offsetMinutes = value;
          markDirty(panel, roomId);
        }, { min: -1440, max: 1440 }),
        checkInput(deps, "Плавно", entry.how.fade, (checked) => { entry.how.fade = checked; markDirty(panel, roomId); }),
        numberInput(deps, "Минимум горения, с", entry.how.minOnSeconds, (value) => {
          entry.how.minOnSeconds = value;
          markDirty(panel, roomId);
        }, { min: 0, max: 86400 }),
      ],
      technical: [
        field(deps, "ID", textInput(deps, entry.id, (value) => { entry.id = value; markDirty(panel, roomId); })),
      ],
    }));
  });
  block.appendChild(button(deps, "Добавить запись", () => {
    draft.schedule.push({
      id: nextFreeId(draft.schedule, "sch"), title: "Запись",
      when: { daysOfWeek: "all", holiday: false, anchor: { kind: "fixed", time: "09:00", offsetMinutes: 0 } },
      targets: { lightTargets: [], groupIds: [], roles: [] },
      how: { brightness: 50, colorTemperature: 3000, fade: true, mode: "on_presence", minOnSeconds: 0 },
    });
    markDirty(panel, roomId);
  }));
  const issues = renderIssues(store, deps, "periods");
  if (issues) block.appendChild(issues);
  return block;
}

function renderSwitchBindings(panel, roomId, store, deps) {
  const draft = store.draft;
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Привязки клавиш"));
  if (!Array.isArray(draft.switchBindings)) draft.switchBindings = [];
  draft.switchBindings.forEach((binding, index) => {
    binding.targets = binding.targets || { lightTargets: [], groupIds: [], roles: [] };
    block.appendChild(itemCard(deps, `${binding.button || "Клавиша"} · ${binding.pressType || ""}`, () => {
      draft.switchBindings.splice(index, 1);
      markDirty(panel, roomId);
    }, [
      selectInput(deps, "Клавиша", binding.button, BUTTONS, (value) => { binding.button = value; markDirty(panel, roomId); }),
      selectInput(deps, "Тип нажатия", binding.pressType, PRESS_TYPES, (value) => { binding.pressType = value; markDirty(panel, roomId); }),
      selectInput(deps, "Действие", binding.action, SWITCH_ACTIONS, (value) => { binding.action = value; markDirty(panel, roomId); }),
    ], {
      advanced: [
        renderTargets(deps, binding.targets, (value) => { binding.targets = value; markDirty(panel, roomId); }),
        numberInput(deps, "Яркость, %", binding.brightness, (value) => {
          binding.brightness = value;
          markDirty(panel, roomId);
        }, { min: 1, max: 100, nullable: true }),
        numberInput(deps, "Оттенок, K", binding.colorTemperature, (value) => {
          binding.colorTemperature = value;
          markDirty(panel, roomId);
        }, { min: 1000, max: 10000, nullable: true }),
        numberInput(deps, "Шаг последовательности", binding.sequenceIndex, (value) => {
          binding.sequenceIndex = value;
          if (value == null) binding.sequenceWindowSeconds = undefined;
          markDirty(panel, roomId);
        }, { min: 1, max: 16, nullable: true }),
        numberInput(deps, "Окно повторного нажатия, с", binding.sequenceWindowSeconds, (value) => {
          binding.sequenceWindowSeconds = value;
          markDirty(panel, roomId);
        }, { min: 1, max: 30, nullable: true }),
      ],
      technical: [
        field(deps, "ID выключателя", textInput(deps, binding.switchId, (value) => {
          binding.switchId = value;
          markDirty(panel, roomId);
        })),
      ],
    }));
  });
  block.appendChild(button(deps, "Добавить привязку", () => {
    draft.switchBindings.push({
      switchId: "", button: "left", pressType: "single", action: "toggle",
      targets: { lightTargets: [], groupIds: [], roles: [] },
    });
    markDirty(panel, roomId);
  }));
  return block;
}

function renderIllumination(panel, roomId, store, deps) {
  const draft = store.draft;
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Освещённость (люкс)"));
  const hasIllumination = Boolean(draft.illumination);
  block.appendChild(button(deps, hasIllumination ? "Убрать блок люкс" : "Добавить блок люкс", () => {
    draft.illumination = hasIllumination ? null : {
      sensor: "sensor.demo_lux",
      calibration: { offset: 0, multiplier: 1 },
      hysteresis: 5,
      minLux: 0,
      maxLux: 20000,
      thresholds: [],
      failClosed: true,
    };
    markDirty(panel, roomId);
  }));
  if (!draft.illumination) return block;
  const illumination = draft.illumination;
  illumination.calibration = illumination.calibration || { offset: 0, multiplier: 1 };
  illumination.thresholds = illumination.thresholds || [];
  block.appendChild(field(deps, "Датчик", textInput(deps, illumination.sensor, (value) => {
    illumination.sensor = value;
    markDirty(panel, roomId);
  })));
  block.appendChild(numberInput(deps, "Гистерезис", illumination.hysteresis, (value) => { illumination.hysteresis = value; markDirty(panel, roomId); }));
  illumination.thresholds.forEach((threshold, index) => {
    block.appendChild(itemCard(deps, `Порог ${threshold.lux} лк`, () => {
      illumination.thresholds.splice(index, 1);
      markDirty(panel, roomId);
    }, [
      numberInput(deps, "Люкс", threshold.lux, (value) => { threshold.lux = value; markDirty(panel, roomId); }),
      numberInput(deps, "Яркость, %", threshold.brightness, (value) => { threshold.brightness = value; markDirty(panel, roomId); }, { min: 0, max: 100, nullable: true }),
      numberInput(deps, "Оттенок, K", threshold.colorTemperature, (value) => {
        threshold.colorTemperature = value;
        markDirty(panel, roomId);
      }, { min: 2000, max: 6500, nullable: true }),
    ], {
      advanced: [
        numberInput(deps, "Модификатор", threshold.modifier, (value) => { threshold.modifier = value; markDirty(panel, roomId); }, { nullable: true }),
      ],
    }));
  });
  block.appendChild(button(deps, "Добавить порог", () => {
    illumination.thresholds.push({ lux: 100, brightness: 50, colorTemperature: 3000, modifier: null });
    markDirty(panel, roomId);
  }));
  block.appendChild(detailsBlock(deps, "rle-advanced", "Дополнительно", [
    numberInput(deps, "Смещение калибровки", illumination.calibration.offset, (value) => {
      illumination.calibration.offset = value;
      markDirty(panel, roomId);
    }, { nullable: true }),
    numberInput(deps, "Множитель", illumination.calibration.multiplier, (value) => {
      illumination.calibration.multiplier = value;
      markDirty(panel, roomId);
    }, { nullable: true }),
    numberInput(deps, "Минимум люкс", illumination.minLux, (value) => { illumination.minLux = value; markDirty(panel, roomId); }),
    numberInput(deps, "Максимум люкс", illumination.maxLux, (value) => { illumination.maxLux = value; markDirty(panel, roomId); }),
  ]));
  const issues = renderIssues(store, deps, "conditions");
  if (issues) block.appendChild(issues);
  return block;
}

function renderDimming(panel, roomId, store, deps) {
  const draft = store.draft;
  draft.dimming = draft.dimming || { enabled: true, onAbsence: true, fadeSeconds: 20, targetPercent: 0 };
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Плавное гашение"));
  const dimming = draft.dimming;
  block.appendChild(checkInput(deps, "Включено", dimming.enabled, (checked) => { dimming.enabled = checked; markDirty(panel, roomId); }));
  block.appendChild(checkInput(deps, "Гасить при отсутствии", dimming.onAbsence, (checked) => { dimming.onAbsence = checked; markDirty(panel, roomId); }));
  block.appendChild(numberInput(deps, "Темп, секунд", dimming.fadeSeconds, (value) => { dimming.fadeSeconds = value; markDirty(panel, roomId); }, { min: 0, max: 3600 }));
  block.appendChild(numberInput(deps, "Целевой процент", dimming.targetPercent, (value) => { dimming.targetPercent = value; markDirty(panel, roomId); }, { min: 0, max: 100 }));
  return block;
}

function renderTimers(panel, roomId, store, deps) {
  const draft = store.draft;
  draft.timers = draft.timers || { absence_seconds: 300, turn_off_seconds: 3600 };
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Таймеры отсутствия"));
  block.appendChild(numberInput(deps, "Подтверждённое отсутствие, секунд", draft.timers.absence_seconds, (value) => {
    draft.timers.absence_seconds = value;
    markDirty(panel, roomId);
  }, { min: 0, max: 86400, nullable: true }));
  block.appendChild(numberInput(deps, "Выключение после, секунд", draft.timers.turn_off_seconds, (value) => {
    draft.timers.turn_off_seconds = value;
    markDirty(panel, roomId);
  }, { min: 0, max: 86400, nullable: true }));
  return block;
}

function renderBehaviors(panel, roomId, store, deps) {
  const draft = store.draft;
  draft.behaviors = draft.behaviors || {
    onlyWhenDark: false,
    respectManualOff: true,
    restoreOwnershipAfterRestart: true,
  };
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Права автоматики"));
  block.appendChild(checkInput(deps, "Работать только в темноте", draft.behaviors.onlyWhenDark, (checked) => {
    draft.behaviors.onlyWhenDark = checked;
    markDirty(panel, roomId);
  }));
  block.appendChild(checkInput(deps, "Уважать ручное выключение", draft.behaviors.respectManualOff, (checked) => {
    draft.behaviors.respectManualOff = checked;
    markDirty(panel, roomId);
  }));
  block.appendChild(checkInput(deps, "Восстанавливать владение только после подтверждения", draft.behaviors.restoreOwnershipAfterRestart, (checked) => {
    draft.behaviors.restoreOwnershipAfterRestart = checked;
    markDirty(panel, roomId);
  }));
  return block;
}

function renderManualProtection(panel, roomId, store, deps) {
  const draft = store.draft;
  draft.manualOffProtection = draft.manualOffProtection || {
    enabled: true, minimumIntervalSeconds: 600, releaseMode: "timer_and_absence",
    stableAbsenceSeconds: 30, priority: "manual_above_auto",
  };
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Защита после ручного выключения"));
  const protection = draft.manualOffProtection;
  block.appendChild(checkInput(deps, "Включено", protection.enabled, (checked) => { protection.enabled = checked; markDirty(panel, roomId); }));
  block.appendChild(numberInput(deps, "Минимальное время, секунд", protection.minimumIntervalSeconds, (value) => {
    protection.minimumIntervalSeconds = value;
    markDirty(panel, roomId);
  }, { min: 30, max: 86400 }));
  block.appendChild(selectInput(deps, "Режим снятия", protection.releaseMode, RELEASE_MODES, (value) => {
    protection.releaseMode = value;
    markDirty(panel, roomId);
  }));
  block.appendChild(numberInput(deps, "Устойчивое отсутствие, секунд", protection.stableAbsenceSeconds, (value) => {
    protection.stableAbsenceSeconds = value;
    markDirty(panel, roomId);
  }, { min: 5, max: 600 }));
  block.appendChild(deps.el("p", "rle-hint", "Приоритет зафиксирован: ручное выше автоматического."));
  return block;
}

function renderAway(panel, roomId, store, deps) {
  const draft = store.draft;
  draft.awayBehavior = draft.awayBehavior || { mode: "none" };
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Уход и возврат"));
  const away = draft.awayBehavior;
  block.appendChild(selectInput(deps, "Уход", away.mode, [
    ["room_off", "Выключить комнату целиком"], ["none", "Не менять"],
  ], (value) => { away.mode = value; markDirty(panel, roomId); }));
  if (away.mode === "room_off") {
    away.return = away.return || { restore: "by_current_conditions" };
    block.appendChild(selectInput(deps, "Возврат", away.return.restore, [
      ["by_current_conditions", "Восстановить по условиям"], ["none", "Не восстанавливать"],
    ], (value) => { away.return.restore = value; markDirty(panel, roomId); }));
  }
  return block;
}

function statusLabel(status) {
  const labels = { pending: "Ожидает", passed: "Успешно", skipped: "Пропущено", failed: "Ошибка" };
  return labels[status] || "Неизвестно";
}

function resultLabel(status) {
  const labels = { passed: "успешно", failed: "ошибка", cancelled: "отменено" };
  return labels[status] || "неизвестно";
}

function renderLiveTest(panel, roomId, store, deps) {
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Реальная проверка (30 секунд)"));
  block.appendChild(deps.el("p", "rle-hint", "Реальная проверка доступна только после сохранения и отдельного подтверждения."));
  const clean = Boolean(store.config) && changedEditableKeys(store.config, store.draft).length === 0;
  const actions = deps.el("div", "rle-bar");
  const safeRun = button(deps, "Прогон: безопасный", () => { void runLiveTest(panel, roomId, "safe"); });
  safeRun.disabled = !store.config;
  actions.appendChild(safeRun);
  const realRun = button(deps, "Прогон: реальный", () => {
    if (typeof window !== "undefined" && typeof window.confirm === "function"
        && !window.confirm("Запустить реальную проверку? Будут отправлены команды устройствам.")) {
      return;
    }
    void runLiveTest(panel, roomId, "real");
  });
  realRun.disabled = !clean;
  actions.appendChild(realRun);
  block.appendChild(actions);
  if (!clean) {
    block.appendChild(deps.el("p", "rle-hint", "Сначала сохраните черновик: реальная проверка идёт только по сохранённой версии."));
  }
  const live = store.live;
  if (!live) return block;
  if (live.status === "running") {
    const cancel = button(deps, "Отменить прогон", () => { void cancelLiveTest(panel, roomId); }, "is-danger");
    const bar = deps.el("div", "rle-bar");
    bar.appendChild(deps.el("span", "rle-hint", "Прогон выполняется…"));
    bar.appendChild(cancel);
    block.appendChild(bar);
  }
  if (live.error) block.appendChild(deps.el("p", "rle-error", live.error));
  const trace = live.trace;
  if (trace && Array.isArray(trace.steps)) {
    const list = deps.el("ol", "rle-live-steps");
    trace.steps.forEach((step) => {
      const item = deps.el("li", `rle-live-step is-${step.status || "pending"}`);
      item.appendChild(deps.el("strong", null, `${step.index}. ${step.comment || step.stage}`));
      item.appendChild(deps.el("span", "rle-live-status", statusLabel(step.status)));
      if (step.detail) item.appendChild(deps.el("small", null, step.detail));
      list.appendChild(item);
    });
    block.appendChild(list);
    if (trace.kind === "result" && trace.result) {
      block.appendChild(deps.el("p", "rle-hint",
        `Итог: ${resultLabel(trace.result.status)}. Команд: ${trace.result.commands_sent ?? 0}.`));
    }
  }
  return block;
}

function renderPreview(store, deps) {
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Безопасный предпросмотр"));
  if (!store.preview) {
    block.appendChild(deps.el("p", "rle-hint", "Измените черновик — предпросмотр проверит его тем же доменным кодом без записи и без команд."));
    return block;
  }
  block.appendChild(deps.el("p", store.preview.safe === false ? "rle-error" : "rle-hint",
    store.preview.summary || "Предпросмотр завершён."));
  const steps = Array.isArray(store.preview.steps) ? store.preview.steps : [];
  if (steps.length) {
    const list = deps.el("ol", "rle-preview-steps");
    steps.forEach((step) => {
      const item = deps.el("li", `rle-preview-step is-${step.status || "ok"}`);
      item.appendChild(deps.el("strong", null, step.title || step.section || "Шаг"));
      item.appendChild(deps.el("small", null, step.detail || ""));
      list.appendChild(item);
    });
    block.appendChild(list);
  }
  const issues = renderIssues(store, deps, null);
  if (issues) block.appendChild(issues);
  return block;
}

function renderReviewSection(panel, roomId, store, deps) {
  const blocks = [];
  if (store.conflict) blocks.push(renderConflict(panel, roomId, store, deps));
  blocks.push(renderPreview(store, deps));
  blocks.push(renderLiveTest(panel, roomId, store, deps));
  return blocks;
}

function renderConditionsSection(panel, roomId, store, deps) {
  return [
    renderIllumination(panel, roomId, store, deps),
    renderDimming(panel, roomId, store, deps),
    renderTimers(panel, roomId, store, deps),
    renderBehaviors(panel, roomId, store, deps),
  ];
}

function renderManualAwaySection(panel, roomId, store, deps) {
  return [
    renderManualProtection(panel, roomId, store, deps),
    renderAway(panel, roomId, store, deps),
  ];
}

function renderPeriodsSection(panel, roomId, store, deps) {
  return [renderSchedule(panel, roomId, store, deps)];
}

function renderSaveBar(panel, roomId, store, deps) {
  const bar = deps.el("footer", "rle-save-bar");
  const dirty = changedEditableKeys(store.config, store.draft).length;
  const state = deps.el("span", "rle-save-state",
    store.saving ? "Сохраняем…" : dirty ? `Не сохранено изменений: ${dirty}` : "Изменений нет");
  setData(deps, state, "data-testid", "room-lighting:save-state");
  bar.appendChild(state);
  const reload = button(deps, "Отменить правки", () => { void refreshRoomLighting(panel, roomId, true); });
  reload.disabled = !dirty || store.saving;
  setData(deps, reload, "data-testid", "room-lighting:reload");
  bar.appendChild(reload);
  const save = button(deps, store.saving ? "Сохранение…" : "Сохранить", () => { void saveRoomLighting(panel, roomId); }, "is-primary");
  save.disabled = store.saving || !store.draft || !dirty;
  setData(deps, save, "data-testid", "room-lighting:save");
  bar.appendChild(save);
  if (store.notice) {
    const notice = deps.el("p", "rle-notice", store.notice);
    setData(deps, notice, "data-testid", "room-lighting:notice");
    bar.appendChild(notice);
  }
  if (store.error) {
    const error = deps.el("p", "rle-error", store.error);
    setData(deps, error, "data-testid", "room-lighting:error");
    bar.appendChild(error);
  }
  return bar;
}

function renderEmptyState(panel, roomId, store, deps) {
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Настройка ещё не создана"));
  block.appendChild(deps.el("p", "rle-hint", "Выберите готовый профиль: сервер создаст из него черновик, после чего можно править и сохранить."));
  const list = deps.el("div", "rle-bar");
  store.templates.forEach((template) => {
    const apply = button(deps, `Создать: ${template.title || template.id}`, () => {
      void applyTemplate(panel, roomId, template.id);
    }, "is-primary");
    apply.disabled = Boolean(store.applying);
    list.appendChild(apply);
  });
  if (!store.templates.length) list.appendChild(deps.el("span", "rle-hint", "Шаблоны недоступны."));
  block.appendChild(list);
  if (store.applying) block.appendChild(deps.el("p", "rle-hint", "Создаём настройку из шаблона…"));
  if (store.error) block.appendChild(deps.el("p", "rle-error", store.error));
  return block;
}

function renderUnavailable(roomId, store, deps) {
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Не удалось загрузить настройки"));
  block.appendChild(deps.el("p", "rle-error", store.error || `Конфигурация освещения для комнаты «${roomId}» ещё не создана.`));
  if (store.templates.length) {
    const list = deps.el("div", "rle-bar");
    store.templates.forEach((template) => {
      const apply = button(deps, `Создать: ${template.title || template.id}`, () => {
        void applyTemplate(panel, roomId, template.id);
      });
      list.appendChild(apply);
    });
    block.appendChild(list);
  }
  return block;
}

function renderSectionBody(panel, roomId, store, deps) {
  const known = ROOM_LIGHTING_SECTIONS.some(([key]) => key === store.section);
  const section = known ? store.section : "overview";
  const wrapper = deps.el("div", "rle-section");
  setData(deps, wrapper, "data-section", section);
  const renderers = {
    overview: renderOverviewSection,
    lights: renderLightsSection,
    inputs: renderInputsSection,
    periods: renderPeriodsSection,
    conditions: renderConditionsSection,
    "manual-away": renderManualAwaySection,
    "review-save": renderReviewSection,
  };
  renderers[section](panel, roomId, store, deps).forEach((node) => wrapper.appendChild(node));
  return wrapper;
}

function renderSectionNav(panel, roomId, store, deps) {
  const nav = deps.el("nav", "rle-nav");
  setData(deps, nav, "aria-label", "Разделы настройки света");
  ROOM_LIGHTING_SECTIONS.forEach(([key, label]) => {
    const active = store.section === key;
    const item = button(deps, label, () => {
      store.section = key;
      rerender(panel, roomId);
    }, `rle-nav-item${active ? " is-active" : ""}`);
    setData(deps, item, "aria-current", active ? "true" : "false");
    setData(deps, item, "data-testid", `room-lighting:section:${key}`);
    nav.appendChild(item);
  });
  return nav;
}

function buildEditor(panel, room, deps, options = {}) {
  const roomId = room && room.id;
  const section = deps.el("section", `room-lighting-editor${options.screen ? " is-screen" : ""}`);
  if (!roomId) {
    section.appendChild(deps.el("p", "rle-error", "У комнаты нет идентификатора: редактор света недоступен."));
    return section;
  }
  const store = lightingState(panel, roomId);
  if (!options.screen) {
    const heading = deps.el("div", "rle-heading");
    heading.appendChild(deps.el("span", "rle-eyebrow", "АВТОМАТИЧЕСКОЕ ОСВЕЩЕНИЕ"));
    heading.appendChild(deps.el("h3", null, "Автоматическое освещение"));
    heading.appendChild(deps.el("p", "rle-hint", "Единая конфигурация комнаты: устройства, расписание, люкс, защита и уход."));
    section.appendChild(heading);
  }
  if (store.loading && !store.draft) {
    section.appendChild(deps.el("p", "rle-hint", "Загружаем настройки освещения…"));
    return section;
  }
  if (!store.draft) {
    section.appendChild(store.error ? renderUnavailable(roomId, store, deps) : renderEmptyState(panel, roomId, store, deps));
    return section;
  }
  section.appendChild(renderDecision(store, deps));
  if (store.conflict && store.section !== "review-save") {
    section.appendChild(renderConflict(panel, roomId, store, deps));
  }
  const body = deps.el("div", "rle-body");
  body.appendChild(renderSectionNav(panel, roomId, store, deps));
  body.appendChild(renderSectionBody(panel, roomId, store, deps));
  section.appendChild(body);
  section.appendChild(renderSaveBar(panel, roomId, store, deps));
  return section;
}

/* Render the shared editor into `container`. The editor owns its own re-render
   loop so that the room detail sheet and the full-size screen both stay live
   without depending on panel._render rebuilding their host node. */
export function renderRoomLightingEditor(panel, container, room, deps, options = {}) {
  const roomId = room && room.id;
  if (!roomId) {
    container.appendChild(buildEditor(panel, room, deps, options));
    return;
  }
  const store = lightingState(panel, roomId);
  let mounted = null;
  const render = () => {
    const section = buildEditor(panel, room, deps, options);
    if (mounted && typeof mounted.remove === "function") mounted.remove();
    container.appendChild(section);
    mounted = section;
  };
  store.rerender = render;
  render();
  if (!store.loaded && !store.loading && !store.error) {
    refreshRoomLighting(panel, roomId).catch(() => null);
  }
}
