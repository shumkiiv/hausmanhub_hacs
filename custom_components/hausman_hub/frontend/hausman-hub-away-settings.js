const AWAY_SETTINGS_API = "hausman_hub/v1/admin/away-settings";
const TRIGGER_STATES = [
  "on", "off", "locked", "unlocked", "open", "closed",
  "home", "not_home", "detected", "clear",
];
const ACTION_IDS = [
  ["turn_on", "Включить"],
  ["turn_off", "Выключить"],
  ["set_brightness_percent", "Яркость, %"],
  ["set_color_temperature", "Оттенок, K"],
];

function cloneSettings(settings) {
  return {
    triggers: (settings?.triggers || []).map((item) => ({ ...item })),
    awayActions: (settings?.awayActions || []).map((item) => ({ ...item })),
    returnActions: (settings?.returnActions || []).map((item) => ({ ...item })),
  };
}

function awaySettingsDirty(state) {
  return JSON.stringify(state.draft) !== JSON.stringify(state.data?.settings || { triggers: [], awayActions: [], returnActions: [] });
}

function awayDevices(panel) {
  const list = panel?._scenarios?.catalog?.devices;
  return Array.isArray(list) ? list : [];
}

function awayDeviceLabel(device) {
  return [device.name || device.capability_name, device.room_name, device.entity_id]
    .filter(Boolean)
    .join(" · ");
}

function entityCandidates(panel) {
  const found = new Map();
  awayDevices(panel).forEach((device) => {
    const entityId = String(device.entity_id || "");
    if (!entityId || found.has(entityId)) return;
    found.set(entityId, { entityId, name: awayDeviceLabel(device) });
  });
  return [...found.values()].sort((left, right) => left.name.localeCompare(right.name, "ru"));
}

function actionCandidates(panel) {
  const found = new Map();
  awayDevices(panel).forEach((device) => {
    const targetId = String(device.target_id || "");
    if (!targetId || found.has(targetId)) return;
    found.set(targetId, { targetId, name: awayDeviceLabel(device) });
  });
  return [...found.values()].sort((left, right) => left.name.localeCompare(right.name, "ru"));
}

function awayOptionSelect(options, value, placeholder, onChange, disabled) {
  const select = document.createElement("select");
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = placeholder;
  select.appendChild(empty);
  options.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    select.appendChild(option);
  });
  select.value = value || "";
  select.disabled = !!disabled;
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

export async function loadAwaySettings(panel, force = false) {
  const state = panel._awaySettings;
  if (!panel._hass || state.loading || (awaySettingsDirty(state) && !force)) return;
  state.loading = true;
  state.error = "";
  panel._render();
  try {
    const document = await panel._hass.callApi("GET", AWAY_SETTINGS_API);
    state.data = document;
    state.draft = cloneSettings(document.settings);
    state.status = "";
  } catch (error) {
    state.error = "Не удалось загрузить настройки режима «Вне дома».";
  } finally {
    state.loading = false;
    panel._render();
  }
}

export function validateAwaySettingsDraft(draft) {
  if (!draft.triggers.length) return "";
  const seen = new Set();
  for (const trigger of draft.triggers) {
    if (!trigger.entityId || !trigger.activeState) return "Выберите устройство и состояние для каждого триггера.";
    if (seen.has(trigger.entityId)) return "Одно устройство можно выбрать только один раз.";
    seen.add(trigger.entityId);
    const wait = Number(trigger.forSeconds || 0);
    if (!Number.isInteger(wait) || wait < 0 || wait > 3600) return "Выдержка должна быть от 0 до 3600 секунд.";
  }
  for (const [name, list] of [["ухода", draft.awayActions], ["возврата", draft.returnActions]]) {
    const targets = new Set();
    for (const action of list) {
      if (!action.targetId || !action.actionId) return `Заполните устройство и действие для списка ${name}.`;
      if (targets.has(action.targetId)) return `В списке ${name} одно устройство указано дважды.`;
      targets.add(action.targetId);
      if (action.actionId === "set_brightness_percent" && !(Number(action.value) >= 0 && Number(action.value) <= 100)) return "Яркость должна быть от 0 до 100.";
      if (action.actionId === "set_color_temperature" && !(Number(action.value) >= 1500 && Number(action.value) <= 10000)) return "Оттенок должен быть от 1500 до 10000 K.";
    }
  }
  return "";
}

async function saveAwaySettings(panel) {
  const state = panel._awaySettings;
  const error = validateAwaySettingsDraft(state.draft);
  if (error) {
    state.status = error;
    panel._render();
    return;
  }
  if (panel._busy || !state.data || !awaySettingsDirty(state)) return;
  panel._busy = true;
  state.status = "Сохраняю настройки...";
  panel._render();
  try {
    const document = await panel._hass.callApi("PUT", AWAY_SETTINGS_API, {
      expectedRevision: state.data.revision,
      settings: state.draft,
    });
    state.data = document;
    state.draft = cloneSettings(document.settings);
    state.status = "Настройки сохранены. Команды устройствам при сохранении не отправлялись.";
    panel._notice = state.status;
    await panel._load();
  } catch (saveError) {
    state.status = saveError?.status === 409
      ? "Настройки уже изменились. Отмените локальные изменения, обновите список и повторите."
      : "Сохранить настройки не удалось. Команды устройствам не отправлялись.";
  } finally {
    panel._busy = false;
    panel._render();
  }
}

function triggerRow(panel, trigger, index, candidates, el) {
  const state = panel._awaySettings;
  const row = el("article", "away-row");
  const entityField = el("label", "settings-field");
  entityField.appendChild(el("span", "assistant-field-label", "Устройство или датчик"));
  entityField.appendChild(awayOptionSelect(
    candidates.map((item) => ({ value: item.entityId, label: item.name })),
    trigger.entityId,
    "Выберите замок, датчик или режим",
    (value) => { trigger.entityId = value; state.status = ""; panel._render(); },
    panel._busy,
  ));
  row.appendChild(entityField);

  const stateField = el("label", "settings-field");
  stateField.appendChild(el("span", "assistant-field-label", "Состояние «вне дома»"));
  stateField.appendChild(awayOptionSelect(
    TRIGGER_STATES.map((value) => ({ value, label: value })),
    trigger.activeState,
    "Например, locked",
    (value) => { trigger.activeState = value; state.status = ""; },
    panel._busy,
  ));
  row.appendChild(stateField);

  const waitField = el("label", "settings-field away-wait");
  waitField.appendChild(el("span", "assistant-field-label", "Выдержка, секунд"));
  const wait = document.createElement("input");
  wait.type = "number";
  wait.min = "0";
  wait.max = "3600";
  wait.step = "1";
  wait.value = String(trigger.forSeconds || 0);
  wait.disabled = panel._busy;
  wait.addEventListener("input", () => { trigger.forSeconds = Number(wait.value); state.status = ""; });
  waitField.appendChild(wait);
  row.appendChild(waitField);

  const remove = el("button", "secondary away-remove", "Удалить");
  remove.type = "button";
  remove.disabled = panel._busy;
  remove.addEventListener("click", () => { state.draft.triggers.splice(index, 1); state.status = ""; panel._render(); });
  row.appendChild(remove);
  return row;
}

function actionRow(panel, action, listName, index, candidates, el) {
  const state = panel._awaySettings;
  const list = state.draft[listName];
  const row = el("article", "away-row");
  const targetField = el("label", "settings-field");
  targetField.appendChild(el("span", "assistant-field-label", "Устройство"));
  targetField.appendChild(awayOptionSelect(
    candidates.map((item) => ({ value: item.targetId, label: item.name })),
    action.targetId,
    "Выберите устройство",
    (value) => { action.targetId = value; state.status = ""; panel._render(); },
    panel._busy,
  ));
  row.appendChild(targetField);

  const actionField = el("label", "settings-field");
  actionField.appendChild(el("span", "assistant-field-label", "Действие"));
  actionField.appendChild(awayOptionSelect(
    ACTION_IDS.map(([value, label]) => ({ value, label })),
    action.actionId,
    "Выберите действие",
    (value) => {
      action.actionId = value;
      if (value === "set_brightness_percent") action.value = Number.isInteger(action.value) ? action.value : 50;
      else if (value === "set_color_temperature") action.value = Number.isInteger(action.value) ? action.value : 3000;
      else delete action.value;
      state.status = "";
      panel._render();
    },
    panel._busy,
  ));
  row.appendChild(actionField);

  if (action.actionId === "set_brightness_percent" || action.actionId === "set_color_temperature") {
    const valueField = el("label", "settings-field away-value");
    valueField.appendChild(el("span", "assistant-field-label", action.actionId === "set_brightness_percent" ? "Яркость, %" : "Оттенок, K"));
    const input = document.createElement("input");
    input.type = "number";
    input.min = action.actionId === "set_brightness_percent" ? "0" : "1500";
    input.max = action.actionId === "set_brightness_percent" ? "100" : "10000";
    input.step = "1";
    input.value = String(action.value ?? (action.actionId === "set_brightness_percent" ? 50 : 3000));
    input.disabled = panel._busy;
    input.addEventListener("input", () => { action.value = Number(input.value); state.status = ""; });
    valueField.appendChild(input);
    row.appendChild(valueField);
  }

  const remove = el("button", "secondary away-remove", "Удалить");
  remove.type = "button";
  remove.disabled = panel._busy;
  remove.addEventListener("click", () => { list.splice(index, 1); state.status = ""; panel._render(); });
  row.appendChild(remove);
  return row;
}

function actionSection(panel, listName, title, hint, candidates, el) {
  const state = panel._awaySettings;
  const list = state.draft[listName];
  const section = el("section", "card settings-card away-list");
  const heading = el("div", "away-list-heading");
  const copy = el("div");
  copy.appendChild(el("strong", null, `${title}: ${list.length}`));
  copy.appendChild(el("small", null, hint));
  heading.appendChild(copy);
  const add = el("button", "secondary", "Добавить устройство");
  add.type = "button";
  add.disabled = panel._busy || list.length >= 64;
  add.addEventListener("click", () => { list.push({ targetId: "", actionId: "turn_off" }); state.status = ""; panel._render(); });
  heading.appendChild(add);
  section.appendChild(heading);
  if (!list.length) section.appendChild(el("p", "muted away-empty", "Список пуст."));
  list.forEach((action, index) => section.appendChild(actionRow(panel, action, listName, index, candidates, el)));
  return section;
}

export function renderAwaySettings(panel, container, helpers) {
  const { el } = helpers;
  const state = panel._awaySettings;
  const intro = el("section", "card settings-card away-intro");
  intro.appendChild(el("h3", null, "Режим «Вне дома»"));
  intro.appendChild(el("p", "muted settings-card-intro",
    "Выберите устройства, которые подтверждают, что дома никого нет (например, умные замки), и списки действий при уходе и возвращении. Пока триггеры не выбраны, встроенное поведение не меняется."));
  intro.appendChild(el("div", "away-safety-note",
    "Свет на возвращении подстраивается под текущее время, освещённость и присутствие штатными контроллерами; здесь задаются только дополнительные действия. Недостоверное состояние датчика не считается уходом или возвратом."));
  container.appendChild(intro);

  if (state.loading && !state.data) {
    container.appendChild(el("section", "card settings-card", "Загружаю настройки..."));
    return;
  }
  if (state.error && !state.data) {
    const failed = el("section", "card settings-card");
    failed.appendChild(el("p", "settings-inline-error", state.error));
    const retry = el("button", "secondary", "Повторить");
    retry.addEventListener("click", () => loadAwaySettings(panel, true));
    failed.appendChild(retry);
    container.appendChild(failed);
    return;
  }
  if (!state.data) return;

  const entityList = entityCandidates(panel);
  const targetList = actionCandidates(panel);
  const triggers = el("section", "card settings-card away-list");
  const heading = el("div", "away-list-heading");
  const copy = el("div");
  copy.appendChild(el("strong", null, `Триггеры ухода: ${state.draft.triggers.length}`));
  copy.appendChild(el("small", null, "Режим включается, когда все выбранные устройства находятся в указанном состоянии."));
  heading.appendChild(copy);
  const addTrigger = el("button", "secondary", "Добавить триггер");
  addTrigger.type = "button";
  addTrigger.disabled = panel._busy || state.draft.triggers.length >= 8;
  addTrigger.addEventListener("click", () => { state.draft.triggers.push({ entityId: "", activeState: "on", forSeconds: 0 }); state.status = ""; panel._render(); });
  heading.appendChild(addTrigger);
  triggers.appendChild(heading);
  if (!state.draft.triggers.length) triggers.appendChild(el("p", "muted away-empty", "Триггеры не выбраны: используется прежнее поведение."));
  state.draft.triggers.forEach((trigger, index) => triggers.appendChild(triggerRow(panel, trigger, index, entityList, el)));
  container.appendChild(triggers);

  container.appendChild(actionSection(panel, "awayActions", "Действия при уходе", "Выполняются один раз при подтверждённом уходе.", targetList, el));
  container.appendChild(actionSection(panel, "returnActions", "Действия при возвращении", "Выполняются один раз при возвращении домой или открытии замка.", targetList, el));

  const status = state.data.status || {};
  const live = el("section", "card settings-card away-status");
  live.appendChild(el("strong", null, "Состояние движка"));
  live.appendChild(el("p", "muted", `Режим: ${status.awayActive ? "вне дома" : "дома"} · триггеров: ${status.triggerCount ?? 0} · причина: ${status.reason || "—"}`));
  container.appendChild(live);

  if (state.status) container.appendChild(el("p", "away-save-status", state.status));
  const actions = el("div", "settings-page-actions");
  const cancel = el("button", "secondary", "Отменить изменения");
  cancel.type = "button";
  cancel.disabled = panel._busy || !awaySettingsDirty(state);
  cancel.addEventListener("click", () => { state.draft = cloneSettings(state.data.settings); state.status = ""; panel._render(); });
  const refresh = el("button", "secondary", "Обновить");
  refresh.type = "button";
  refresh.disabled = panel._busy || awaySettingsDirty(state);
  refresh.addEventListener("click", () => loadAwaySettings(panel, true));
  const save = el("button", "primary", "Сохранить настройки");
  save.type = "button";
  save.disabled = panel._busy || !awaySettingsDirty(state);
  save.addEventListener("click", () => saveAwaySettings(panel));
  actions.appendChild(cancel);
  actions.appendChild(refresh);
  actions.appendChild(save);
  container.appendChild(actions);
}
