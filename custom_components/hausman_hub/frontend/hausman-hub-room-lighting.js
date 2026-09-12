/* Room lighting editor for the room card "Освещение" block.
 * Shows and edits the single room lighting configuration through the
 * hausman_hub/v1 rooms/{room_id}/lighting API. Server validation wins: the
 * panel only mirrors the server response and never invents its own rules. */

import { apiErrorMessage } from "./hausman-hub-error-taxonomy.js?v=1.52.259";

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
      error: "",
      notice: "",
      config: null,
      draft: null,
      templates: [],
      templateBaseline: null,
      live: null,
    };
  }
  return panel._roomLighting.byRoom[roomId];
}

function lightingErrorMessage(error) {
  const body = error && typeof error === "object" ? error.body : null;
  if (body && typeof body.message === "string" && body.message) return body.message;
  return apiErrorMessage(error);
}

function markDirty(store) {
  store.notice = "Есть несохранённые изменения.";
  store.error = "";
}

export async function refreshRoomLighting(panel, roomId, force = false) {
  const store = lightingState(panel, roomId);
  if (store.loading || (store.loaded && !force)) return;
  store.loading = true;
  store.error = "";
  try {
    const config = await panel._hass.callApi("GET", lightingApi(roomId, "/config")).catch((error) => {
      if (error && error.status === 404) return null;
      throw error;
    });
    const catalog = await panel._hass.callApi("GET", lightingApi(roomId, "/templates")).catch(() => null);
    store.config = config;
    store.draft = clone(config);
    store.templates = Array.isArray(catalog && catalog.templates) ? catalog.templates : [];
    store.loaded = true;
  } catch (error) {
    store.error = lightingErrorMessage(error);
  } finally {
    store.loading = false;
    panel._render();
  }
}

async function saveRoomLighting(panel, roomId) {
  const store = lightingState(panel, roomId);
  if (!store.draft || store.saving) return;
  store.saving = true;
  store.error = "";
  store.notice = "";
  panel._render();
  try {
    const saved = await panel._hass.callApi("PUT", lightingApi(roomId, "/config"), store.draft);
    store.config = saved;
    store.draft = clone(saved);
    store.notice = "Конфигурация освещения сохранена.";
  } catch (error) {
    store.error = lightingErrorMessage(error);
  } finally {
    store.saving = false;
    panel._render();
  }
}

async function applyTemplate(panel, roomId, templateId) {
  const store = lightingState(panel, roomId);
  store.applying = templateId;
  store.error = "";
  panel._render();
  try {
    const config = await panel._hass.callApi("POST", lightingApi(roomId, "/templates/apply"), {
      templateId,
      keepDevices: true,
    });
    const stored = await panel._hass.callApi("GET", lightingApi(roomId, "/config")).catch(() => null);
    const draft = clone(config);
    if (stored) {
      draft.version = stored.version;
      draft.updatedAt = stored.updatedAt;
    }
    store.config = stored;
    store.draft = draft;
    store.templateBaseline = clone(config);
    store.loaded = true;
    store.notice = "Шаблон применён. Проверьте и сохраните изменения.";
  } catch (error) {
    store.error = lightingErrorMessage(error);
  } finally {
    store.applying = null;
    panel._render();
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function runLiveTest(panel, roomId, mode) {
  const store = lightingState(panel, roomId);
  store.live = { status: "running", mode, cid: "", trace: null, error: "" };
  store.error = "";
  panel._render();
  try {
    const started = await panel._hass.callApi("POST", lightingApi(roomId, "/live-tests"), { mode });
    store.live.cid = started.correlationId || "";
    pollLiveTest(panel, roomId, store.live.cid).catch(() => null);
  } catch (error) {
    store.live = { status: "error", mode, cid: "", trace: null, error: lightingErrorMessage(error) };
    panel._render();
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
        panel._render();
        return;
      }
      panel._render();
    } catch (error) {
      store.live.status = "error";
      store.live.error = lightingErrorMessage(error);
      panel._render();
      return;
    }
  }
  store.live.status = "timeout";
  panel._render();
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
  panel._render();
}

function field(deps, label, control) {
  const wrap = deps.el("label", "rle-field");
  wrap.appendChild(deps.el("span", "rle-field-label", label));
  wrap.appendChild(control);
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

function bar(deps, children) {
  const node = deps.el("div", "rle-bar");
  children.forEach((child) => node.appendChild(child));
  return node;
}

function itemCard(deps, title, onRemove, children) {
  const card = deps.el("div", "rle-item");
  const head = deps.el("div", "rle-item-head");
  head.appendChild(deps.el("strong", null, title));
  if (onRemove) head.appendChild(button(deps, "Удалить", onRemove, "is-danger"));
  card.appendChild(head);
  const grid = deps.el("div", "rle-grid");
  children.forEach((child) => grid.appendChild(child));
  card.appendChild(grid);
  return card;
}

function renderTemplates(panel, roomId, store, deps) {
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Шаблоны"));
  block.appendChild(deps.el("p", "rle-hint", "Примените готовый профиль, затем свободно правьте его."));
  const list = deps.el("div", "rle-bar");
  if (!store.templates.length) list.appendChild(deps.el("span", "rle-hint", "Шаблоны недоступны."));
  store.templates.forEach((template) => {
    const apply = button(deps, `Применить: ${template.title || template.id}`, () => {
      applyTemplate(panel, roomId, template.id);
    });
    apply.disabled = Boolean(store.applying);
    list.appendChild(apply);
  });
  block.appendChild(list);
  if (store.templateBaseline && store.draft) {
    const changed = diffTopLevel(store.templateBaseline, store.draft).join(", ");
    block.appendChild(deps.el(
      "p",
      "rle-hint",
      changed ? `Изменено относительно шаблона: ${changed}.` : "Совпадает с шаблоном.",
    ));
  }
  return block;
}

function diffTopLevel(baseline, draft) {
  const keys = ["devices", "schedule", "switchBindings", "illumination", "dimming", "manualOffProtection", "awayBehavior", "autoAdopt"];
  return keys.filter((key) => JSON.stringify(baseline ? baseline[key] : null) !== JSON.stringify(draft ? draft[key] : null));
}

function renderDevices(panel, roomId, store, deps) {
  const draft = store.draft;
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Устройства"));
  const devices = draft.devices || (draft.devices = { sensors: [], light_targets: [], power_switch: null, wireless_switches: [], selectAll: false });
  devices.sensors = devices.sensors || [];
  devices.light_targets = devices.light_targets || [];
  devices.wireless_switches = devices.wireless_switches || [];
  devices.selectAll = Boolean(devices.selectAll);
  if (typeof draft.autoAdopt !== "boolean") draft.autoAdopt = true;

  block.appendChild(checkInput(deps, "Автоматически подхватывать новые устройства", draft.autoAdopt, (checked) => {
    draft.autoAdopt = checked;
    markDirty(store);
    panel._render();
  }));
  block.appendChild(checkInput(deps, "Все устройства комнаты", devices.selectAll, (checked) => {
    devices.selectAll = checked;
    markDirty(store);
    panel._render();
  }));

  block.appendChild(deps.el("h5", null, "Датчики"));
  devices.sensors.forEach((sensor, index) => {
    block.appendChild(itemCard(deps, sensor.name || sensor.id || "Датчик", () => {
      devices.sensors.splice(index, 1);
      markDirty(store);
      panel._render();
    }, [
      field(deps, "ID", textInput(deps, sensor.id, (value) => { sensor.id = value; markDirty(store); })),
      field(deps, "Название", textInput(deps, sensor.name, (value) => { sensor.name = value; markDirty(store); })),
      selectInput(deps, "Тип", sensor.kind, SENSOR_KINDS, (value) => { sensor.kind = value; markDirty(store); panel._render(); }),
      field(deps, "Сущность", textInput(deps, sensor.entityId, (value) => { sensor.entityId = value || undefined; markDirty(store); })),
      selectInput(deps, "Авто-подхват", sensor.autoAdoptOverride === null || sensor.autoAdoptOverride === undefined ? "inherit" : String(sensor.autoAdoptOverride), [
        ["inherit", "Как у комнаты"], ["true", "Да"], ["false", "Нет"],
      ], (value) => { sensor.autoAdoptOverride = value === "inherit" ? null : value === "true"; markDirty(store); }),
    ]));
  });
  block.appendChild(button(deps, "Добавить датчик", () => {
    devices.sensors.push({ id: nextFreeId(devices.sensors, "sensor"), name: "Датчик", kind: "presence", autoAdoptOverride: null });
    markDirty(store);
    panel._render();
  }));

  block.appendChild(deps.el("h5", null, "Цели света"));
  devices.light_targets.forEach((target, index) => {
    block.appendChild(itemCard(deps, target.name || target.id || "Свет", () => {
      devices.light_targets.splice(index, 1);
      markDirty(store);
      panel._render();
    }, [
      field(deps, "ID", textInput(deps, target.id, (value) => { target.id = value; markDirty(store); })),
      field(deps, "Название", textInput(deps, target.name, (value) => { target.name = value; markDirty(store); })),
      selectInput(deps, "Вид", target.kind, LIGHT_KINDS, (value) => { target.kind = value; markDirty(store); panel._render(); }),
      field(deps, "Сущность", textInput(deps, target.entityId, (value) => { target.entityId = value || undefined; markDirty(store); })),
      selectInput(deps, "Роль", target.role || "", [["", "Не задана"], ...TEMPLATE_ROLES], (value) => { target.role = value || null; markDirty(store); panel._render(); }),
      field(deps, "Группа", textInput(deps, target.groupId, (value) => { target.groupId = value || null; markDirty(store); })),
      checkInput(deps, "Поддерживает яркость", target.brightness, (checked) => { target.brightness = checked; markDirty(store); panel._render(); }),
      checkInput(deps, "Поддерживает оттенок", target.color_temperature, (checked) => { target.color_temperature = checked; markDirty(store); panel._render(); }),
      selectInput(deps, "Авто-подхват", target.autoAdoptOverride === null || target.autoAdoptOverride === undefined ? "inherit" : String(target.autoAdoptOverride), [
        ["inherit", "Как у комнаты"], ["true", "Да"], ["false", "Нет"],
      ], (value) => { target.autoAdoptOverride = value === "inherit" ? null : value === "true"; markDirty(store); }),
    ]));
  });
  block.appendChild(button(deps, "Добавить цель света", () => {
    devices.light_targets.push({
      id: nextFreeId(devices.light_targets, "light"), name: "Свет", kind: "light",
      role: "other", groupId: null, brightness: true, color_temperature: false,
      autoAdoptOverride: null,
    });
    markDirty(store);
    panel._render();
  }));

  block.appendChild(deps.el("h5", null, "Питание и беспроводные выключатели"));
  if (devices.power_switch) {
    const power = devices.power_switch;
    block.appendChild(itemCard(deps, `Питание: ${power.name || power.id || ""}`, () => {
      devices.power_switch = null;
      markDirty(store);
      panel._render();
    }, [
      field(deps, "ID", textInput(deps, power.id, (value) => { power.id = value; markDirty(store); })),
      field(deps, "Название", textInput(deps, power.name, (value) => { power.name = value; markDirty(store); })),
      field(deps, "Сущность", textInput(deps, power.entityId, (value) => { power.entityId = value || undefined; markDirty(store); })),
    ]));
  } else {
    block.appendChild(button(deps, "Добавить выключатель питания", () => {
      devices.power_switch = {
        id: nextFreeId([...devices.sensors, ...devices.light_targets, ...devices.wireless_switches], "switch_power"),
        name: "Питание",
        autoAdoptOverride: null,
      };
      markDirty(store);
      panel._render();
    }));
  }
  devices.wireless_switches.forEach((device, index) => {
    block.appendChild(itemCard(deps, device.name || device.id || "Беспроводной выключатель", () => {
      devices.wireless_switches.splice(index, 1);
      markDirty(store);
      panel._render();
    }, [
      field(deps, "ID", textInput(deps, device.id, (value) => { device.id = value; markDirty(store); })),
      field(deps, "Название", textInput(deps, device.name, (value) => { device.name = value; markDirty(store); })),
      field(deps, "Сущность", textInput(deps, device.entityId, (value) => { device.entityId = value || undefined; markDirty(store); })),
      listInput(deps, "Клавиши", device.buttons, (value) => { device.buttons = value; markDirty(store); }),
      listInput(deps, "Типы нажатий", device.pressTypes, (value) => { device.pressTypes = value; markDirty(store); }),
    ]));
  });
  block.appendChild(button(deps, "Добавить беспроводной выключатель", () => {
    devices.wireless_switches.push({ id: nextFreeId(devices.wireless_switches, "sw"), name: "Выключатель", buttons: ["left"], pressTypes: ["single"] });
    markDirty(store);
    panel._render();
  }));
  return block;
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
  block.appendChild(deps.el("h4", null, "Расписание"));
  if (!Array.isArray(draft.schedule)) draft.schedule = [];
  draft.schedule.forEach((entry, index) => {
    entry.when = entry.when || { daysOfWeek: "all", holiday: false, anchor: { kind: "fixed", time: "09:00", offsetMinutes: 0 } };
    entry.how = entry.how || { brightness: 50, colorTemperature: 3000, fade: true, mode: "on_presence", minOnSeconds: 0 };
    entry.targets = entry.targets || { lightTargets: [], groupIds: [], roles: [] };
    block.appendChild(itemCard(deps, entry.title || entry.id || "Запись расписания", () => {
      draft.schedule.splice(index, 1);
      markDirty(store);
      panel._render();
    }, [
      field(deps, "ID", textInput(deps, entry.id, (value) => { entry.id = value; markDirty(store); })),
      field(deps, "Название", textInput(deps, entry.title, (value) => { entry.title = value; markDirty(store); })),
      field(deps, "Дни (all/weekdays/weekend или список)", textInput(
        deps,
        Array.isArray(entry.when.daysOfWeek) ? entry.when.daysOfWeek.join(", ") : entry.when.daysOfWeek,
        (value) => { entry.when.daysOfWeek = value.includes(",") ? value.split(",").map((item) => item.trim()).filter(Boolean) : value; markDirty(store); },
      )),
      checkInput(deps, "Только в праздник", entry.when.holiday, (checked) => { entry.when.holiday = checked; markDirty(store); }),
      selectInput(deps, "Якорь", entry.when.anchor.kind, ANCHOR_KINDS, (value) => { entry.when.anchor.kind = value; markDirty(store); panel._render(); }),
      field(deps, "Время (для якоря времени)", textInput(deps, entry.when.anchor.time, (value) => { entry.when.anchor.time = value || undefined; markDirty(store); })),
      numberInput(deps, "Смещение, мин", entry.when.anchor.offsetMinutes, (value) => { entry.when.anchor.offsetMinutes = value; markDirty(store); }, { min: -1440, max: 1440 }),
      renderTargets(deps, entry.targets, (value) => { entry.targets = value; markDirty(store); }),
      numberInput(deps, "Яркость, %", entry.how.brightness, (value) => { entry.how.brightness = value; markDirty(store); }, { min: 0, max: 100, nullable: true }),
      numberInput(deps, "Оттенок, K", entry.how.colorTemperature, (value) => { entry.how.colorTemperature = value; markDirty(store); }, { min: 2000, max: 6500, nullable: true }),
      checkInput(deps, "Плавно", entry.how.fade, (checked) => { entry.how.fade = checked; markDirty(store); }),
      selectInput(deps, "Режим", entry.how.mode, SCHEDULE_MODES, (value) => { entry.how.mode = value; markDirty(store); panel._render(); }),
      numberInput(deps, "Минимум горения, с", entry.how.minOnSeconds, (value) => { entry.how.minOnSeconds = value; markDirty(store); }, { min: 0, max: 86400 }),
    ]));
  });
  block.appendChild(button(deps, "Добавить запись", () => {
    draft.schedule.push({
      id: nextFreeId(draft.schedule, "sch"), title: "Запись",
      when: { daysOfWeek: "all", holiday: false, anchor: { kind: "fixed", time: "09:00", offsetMinutes: 0 } },
      targets: { lightTargets: [], groupIds: [], roles: [] },
      how: { brightness: 50, colorTemperature: 3000, fade: true, mode: "on_presence", minOnSeconds: 0 },
    });
    markDirty(store);
    panel._render();
  }));
  return block;
}

function renderSwitchBindings(panel, roomId, store, deps) {
  const draft = store.draft;
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Выключатели"));
  if (!Array.isArray(draft.switchBindings)) draft.switchBindings = [];
  draft.switchBindings.forEach((binding, index) => {
    binding.targets = binding.targets || { lightTargets: [], groupIds: [], roles: [] };
    block.appendChild(itemCard(deps, `${binding.switchId || "Выключатель"} · ${binding.button || ""}`, () => {
      draft.switchBindings.splice(index, 1);
      markDirty(store);
      panel._render();
    }, [
      field(deps, "Выключатель", textInput(deps, binding.switchId, (value) => { binding.switchId = value; markDirty(store); })),
      selectInput(deps, "Клавиша", binding.button, BUTTONS, (value) => { binding.button = value; markDirty(store); }),
      selectInput(deps, "Тип нажатия", binding.pressType, PRESS_TYPES, (value) => { binding.pressType = value; markDirty(store); }),
      selectInput(deps, "Действие", binding.action, SWITCH_ACTIONS, (value) => { binding.action = value; markDirty(store); }),
      renderTargets(deps, binding.targets, (value) => { binding.targets = value; markDirty(store); }),
    ]));
  });
  block.appendChild(button(deps, "Добавить привязку", () => {
    draft.switchBindings.push({
      switchId: "", button: "left", pressType: "single", action: "toggle",
      targets: { lightTargets: [], groupIds: [], roles: [] },
    });
    markDirty(store);
    panel._render();
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
    markDirty(store);
    panel._render();
  }));
  if (!draft.illumination) return block;
  const illumination = draft.illumination;
  illumination.calibration = illumination.calibration || { offset: 0, multiplier: 1 };
  illumination.thresholds = illumination.thresholds || [];
  block.appendChild(field(deps, "Датчик", textInput(deps, illumination.sensor, (value) => { illumination.sensor = value; markDirty(store); })));
  block.appendChild(numberInput(deps, "Смещение калибровки", illumination.calibration.offset, (value) => { illumination.calibration.offset = value; markDirty(store); }, { nullable: true }));
  block.appendChild(numberInput(deps, "Множитель", illumination.calibration.multiplier, (value) => { illumination.calibration.multiplier = value; markDirty(store); }, { nullable: true }));
  block.appendChild(numberInput(deps, "Гистерезис", illumination.hysteresis, (value) => { illumination.hysteresis = value; markDirty(store); }));
  block.appendChild(numberInput(deps, "Минимум люкс", illumination.minLux, (value) => { illumination.minLux = value; markDirty(store); }));
  block.appendChild(numberInput(deps, "Максимум люкс", illumination.maxLux, (value) => { illumination.maxLux = value; markDirty(store); }));
  illumination.thresholds.forEach((threshold, index) => {
    block.appendChild(itemCard(deps, `Порог ${threshold.lux} лк`, () => {
      illumination.thresholds.splice(index, 1);
      markDirty(store);
      panel._render();
    }, [
      numberInput(deps, "Люкс", threshold.lux, (value) => { threshold.lux = value; markDirty(store); }),
      numberInput(deps, "Яркость, %", threshold.brightness, (value) => { threshold.brightness = value; markDirty(store); }, { min: 0, max: 100, nullable: true }),
      numberInput(deps, "Оттенок, K", threshold.colorTemperature, (value) => { threshold.colorTemperature = value; markDirty(store); }, { min: 2000, max: 6500, nullable: true }),
      numberInput(deps, "Модификатор", threshold.modifier, (value) => { threshold.modifier = value; markDirty(store); }, { nullable: true }),
    ]));
  });
  block.appendChild(button(deps, "Добавить порог", () => {
    illumination.thresholds.push({ lux: 100, brightness: 50, colorTemperature: 3000, modifier: null });
    markDirty(store);
    panel._render();
  }));
  return block;
}

function renderDimming(panel, roomId, store, deps) {
  const draft = store.draft;
  draft.dimming = draft.dimming || { enabled: true, onAbsence: true, fadeSeconds: 20, targetPercent: 0 };
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Плавное гашение"));
  const dimming = draft.dimming;
  block.appendChild(checkInput(deps, "Включено", dimming.enabled, (checked) => { dimming.enabled = checked; markDirty(store); panel._render(); }));
  block.appendChild(checkInput(deps, "Гасить при отсутствии", dimming.onAbsence, (checked) => { dimming.onAbsence = checked; markDirty(store); }));
  block.appendChild(numberInput(deps, "Темп, секунд", dimming.fadeSeconds, (value) => { dimming.fadeSeconds = value; markDirty(store); }, { min: 0, max: 3600 }));
  block.appendChild(numberInput(deps, "Целевой процент", dimming.targetPercent, (value) => { dimming.targetPercent = value; markDirty(store); }, { min: 0, max: 100 }));
  return block;
}

function renderManualProtection(panel, roomId, store, deps) {
  const draft = store.draft;
  draft.manualOffProtection = draft.manualOffProtection || {
    enabled: true, minimumIntervalSeconds: 600, releaseMode: "timer_and_absence",
    stableAbsenceSeconds: 30, priority: "manual_above_auto",
  };
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Защита от повторного включения"));
  const protection = draft.manualOffProtection;
  block.appendChild(checkInput(deps, "Включено", protection.enabled, (checked) => { protection.enabled = checked; markDirty(store); panel._render(); }));
  block.appendChild(numberInput(deps, "Минимальное время, секунд", protection.minimumIntervalSeconds, (value) => { protection.minimumIntervalSeconds = value; markDirty(store); }, { min: 30, max: 86400 }));
  block.appendChild(selectInput(deps, "Режим снятия", protection.releaseMode, RELEASE_MODES, (value) => { protection.releaseMode = value; markDirty(store); }));
  block.appendChild(numberInput(deps, "Устойчивое отсутствие, секунд", protection.stableAbsenceSeconds, (value) => { protection.stableAbsenceSeconds = value; markDirty(store); }, { min: 5, max: 600 }));
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
  ], (value) => { away.mode = value; markDirty(store); panel._render(); }));
  if (away.mode === "room_off") {
    away.return = away.return || { restore: "by_current_conditions" };
    block.appendChild(selectInput(deps, "Возврат", away.return.restore, [
      ["by_current_conditions", "Восстановить по условиям"], ["none", "Не восстанавливать"],
    ], (value) => { away.return.restore = value; markDirty(store); }));
  }
  return block;
}

function renderLiveTest(panel, roomId, store, deps) {
  const block = deps.el("section", "rle-block");
  block.appendChild(deps.el("h4", null, "Live-тест (30 секунд)"));
  block.appendChild(deps.el("p", "rle-hint", "Безопасный режим только считает план и не отправляет команды."));
  block.appendChild(bar(deps, [
    button(deps, "Прогон: безопасный", () => runLiveTest(panel, roomId, "safe")),
    button(deps, "Прогон: реальный", () => runLiveTest(panel, roomId, "real")),
  ]));
  const live = store.live;
  if (!live) return block;
  if (live.status === "running") {
    const cancel = button(deps, "Отменить прогон", () => cancelLiveTest(panel, roomId), "is-danger");
    block.appendChild(bar(deps, [deps.el("span", "rle-hint", "Прогон выполняется…"), cancel]));
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

function statusLabel(status) {
  const labels = { pending: "Ожидает", passed: "Успешно", skipped: "Пропущено", failed: "Ошибка" };
  return labels[status] || "Неизвестно";
}

function resultLabel(status) {
  const labels = { passed: "успешно", failed: "ошибка", cancelled: "отменено" };
  return labels[status] || "неизвестно";
}

function renderActions(panel, roomId, store, deps) {
  const block = deps.el("section", "rle-actions");
  const save = button(deps, store.saving ? "Сохранение…" : "Сохранить", () => saveRoomLighting(panel, roomId), "is-primary");
  save.disabled = Boolean(store.saving);
  block.appendChild(save);
  if (store.notice) block.appendChild(deps.el("p", "rle-notice", store.notice));
  if (store.error) block.appendChild(deps.el("p", "rle-error", store.error));
  return block;
}

export function renderRoomLightingEditor(panel, container, room, deps) {
  const roomId = room && room.id;
  const section = deps.el("section", "room-lighting-editor");
  const heading = deps.el("div", "rle-heading");
  heading.appendChild(deps.el("span", "rle-eyebrow", "АВТОМАТИЧЕСКОЕ ОСВЕЩЕНИЕ"));
  heading.appendChild(deps.el("h3", null, "Автоматическое освещение"));
  heading.appendChild(deps.el("p", "rle-hint", "Единая конфигурация комнаты: устройства, расписание, люкс, защита и уход."));
  section.appendChild(heading);
  if (!roomId) {
    section.appendChild(deps.el("p", "rle-error", "У комнаты нет идентификатора для конфигурации освещения."));
    container.appendChild(section);
    return;
  }
  const store = lightingState(panel, roomId);
  if (!store.loaded && !store.loading && !store.error) {
    refreshRoomLighting(panel, roomId).catch(() => null);
  }
  if (store.loading) {
    section.appendChild(deps.el("p", "rle-hint", "Загрузка конфигурации освещения…"));
    container.appendChild(section);
    return;
  }
  if (!store.draft) {
    section.appendChild(deps.el("p", "rle-error", store.error || "Конфигурация освещения ещё не создана."));
    const create = button(deps, "Создать из шаблона", () => applyTemplate(panel, roomId, "day_profile"), "is-primary");
    section.appendChild(create);
    container.appendChild(section);
    return;
  }
  section.appendChild(renderTemplates(panel, roomId, store, deps));
  section.appendChild(renderDevices(panel, roomId, store, deps));
  section.appendChild(renderSchedule(panel, roomId, store, deps));
  section.appendChild(renderSwitchBindings(panel, roomId, store, deps));
  section.appendChild(renderIllumination(panel, roomId, store, deps));
  section.appendChild(renderDimming(panel, roomId, store, deps));
  section.appendChild(renderManualProtection(panel, roomId, store, deps));
  section.appendChild(renderAway(panel, roomId, store, deps));
  section.appendChild(renderLiveTest(panel, roomId, store, deps));
  section.appendChild(renderActions(panel, roomId, store, deps));
  container.appendChild(section);
}
