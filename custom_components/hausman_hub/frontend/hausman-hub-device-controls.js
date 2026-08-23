/* Capability-first controls for the shared physical-device sheet. */

const ON_STATES = new Set(["on", "open", "opening", "playing", "heat", "heating", "cool", "cooling"]);
const OFF_STATES = new Set(["off", "closed", "closing", "idle", "paused", "standby"]);

function normalized(value) {
  return String(value == null ? "" : value).trim().toLocaleLowerCase("ru");
}

function targetDetail(device, target) {
  return (Array.isArray(device && device.details) ? device.details : [])
    .find((detail) => detail && detail.entityId === target.entity_id) || null;
}

function targetState(device, target) {
  const detail = targetDetail(device, target);
  const state = normalized(detail && detail.state
    || (device && device.entityId === target.entity_id ? device.state : ""));
  const supplied = String(detail && detail.value || "").trim();
  if (device && device.unavailable) return { state: "unavailable", label: "Нет связи" };
  const labels = {
    on: "Включено", off: "Выключено", open: "Открыто", closed: "Закрыто",
    locked: "Закрыт", unlocked: "Открыт", playing: "Воспроизведение", paused: "Пауза",
    idle: "Ожидание", unavailable: "Нет связи", unknown: "Состояние неизвестно",
  };
  return { state, label: labels[state] || labels[normalized(supplied)] || supplied || "Состояние неизвестно" };
}

export function conciseDeviceTargetName(target, device) {
  const deviceName = String(device && device.name || "").trim();
  const raw = String(target && target.name || "").trim();
  if (!raw) return "Основное управление";
  let name = raw.split(/\s*[·•|]\s*/).filter(Boolean).pop().trim();
  if (deviceName && normalized(name).startsWith(normalized(deviceName))) {
    name = name.slice(deviceName.length).replace(/^[\s:_·-]+/, "").trim();
  }
  if (!name || normalized(name) === normalized(deviceName)) return "Основное управление";
  if (/^\d+$/.test(name)) return `Клавиша ${name}`;
  return name;
}

export function deviceActionInitialValue(device, target, action) {
  const detail = targetDetail(device, target);
  const attributes = device && device.entityId === target.entity_id && device.attributes
    ? device.attributes : {};
  const numeric = (...values) => {
    const value = values.find((candidate) => candidate !== null && candidate !== undefined
      && candidate !== "" && Number.isFinite(Number(candidate)));
    return value === undefined ? null : Number(value);
  };
  if (action.action_id === "set_temperature") {
    return numeric(attributes.temperature, device && device.primaryValue, detail && detail.state);
  }
  if (action.action_id === "set_brightness") return numeric(attributes.brightness, detail && detail.state);
  if (action.action_id === "set_position") return numeric(attributes.current_position, detail && detail.state);
  if (action.action_id === "set_hvac_mode") {
    return String((detail && detail.state) || (device && device.state) || "").trim() || null;
  }
  if (action.action_id === "set_fan_mode") return String(attributes.fan_mode || "").trim() || null;
  if (action.action_id === "set_humidity") {
    return numeric(attributes.humidity, device && device.primaryValue, detail && detail.state);
  }
  return null;
}

function conciseActions(target, state) {
  const actions = Array.isArray(target && target.actions) ? target.actions : [];
  const simple = actions.filter((action) => !(Array.isArray(action.allowed_fields)
    ? action.allowed_fields : []).includes("value"));
  const values = actions.filter((action) => !simple.includes(action));
  const byId = new Map(simple.map((action) => [action.action_id, action]));
  const pairs = [
    ["turn_on", "turn_off", ON_STATES, OFF_STATES],
    ["open", "close", new Set(["open", "opening"]), new Set(["closed", "closing"])],
    ["open_cover", "close_cover", new Set(["open", "opening"]), new Set(["closed", "closing"])],
    ["unlock", "lock", new Set(["unlocked"]), new Set(["locked"])],
  ];
  const hidden = new Set();
  const preferred = [];
  pairs.forEach(([positive, negative, positiveStates, negativeStates]) => {
    if (!byId.has(positive) || !byId.has(negative)) return;
    hidden.add(positive); hidden.add(negative);
    if (positive === "turn_on") hidden.add("toggle");
    if (positiveStates.has(state)) preferred.push(byId.get(negative));
    else if (negativeStates.has(state)) preferred.push(byId.get(positive));
    else preferred.push(byId.get(positive), byId.get(negative));
  });
  return preferred.concat(simple.filter((action) => !hidden.has(action.action_id)), values);
}

function canRename(owner, target) {
  return Boolean(target && target.entity_id && owner && owner._hass
    && owner._hass.user && owner._hass.user.is_admin === true
    && (typeof owner._hass.callWS === "function"
      || typeof owner._hass.connection?.sendMessagePromise === "function"));
}

async function renameEntity(owner, entityId, name) {
  const value = String(name || "").trim();
  if (!value) throw new Error("Введите название.");
  if (value.length > 255) throw new Error("Название не должно быть длиннее 255 символов.");
  if (!canRename(owner, { entity_id: entityId })) {
    throw new Error("Переименование доступно администратору Home Assistant.");
  }
  const message = { type: "config/entity_registry/update", entity_id: entityId, name: value };
  if (typeof owner._hass.callWS === "function") await owner._hass.callWS(message);
  else await owner._hass.connection.sendMessagePromise(message);
  return value;
}

function appendRenameEditor(owner, target, device, title, head, deps) {
  if (!canRename(owner, target)) return;
  const { el, setAttr } = deps;
  const rename = el("button", "secondary device-target-rename", "Переименовать");
  rename.type = "button";
  setAttr(rename, "aria-label", `Переименовать: ${title.textContent}`);
  head.appendChild(rename);
  const editor = el("div", "device-target-editor");
  editor.hidden = true;
  let currentName = String(target.name || title.textContent).trim() || title.textContent;
  const input = el("input", "device-target-name-input");
  input.type = "text";
  input.maxLength = 255;
  input.value = currentName;
  setAttr(input, "aria-label", "Название в Home Assistant");
  const cancel = el("button", "secondary", "Отмена");
  cancel.type = "button";
  const save = el("button", null, "Сохранить");
  save.type = "button";
  const message = el("span", "device-target-editor-message");
  setAttr(message, "role", "status");
  const sync = () => { save.disabled = !String(input.value || "").trim() || input.value.trim() === currentName; };
  input.addEventListener("input", sync);
  rename.addEventListener("click", (event) => {
    event.preventDefault();
    editor.hidden = false;
    rename.hidden = true;
    input.value = currentName;
    message.textContent = "";
    editor.classList.remove("is-saved", "has-error");
    sync();
    input.focus && input.focus();
  });
  cancel.addEventListener("click", (event) => {
    event.preventDefault();
    editor.hidden = true;
    rename.hidden = false;
    message.textContent = "";
  });
  save.addEventListener("click", async (event) => {
    event.preventDefault();
    if (save.disabled) return;
    const draft = input.value.trim();
    input.disabled = true; cancel.disabled = true; save.disabled = true;
    save.textContent = "Сохраняю...";
    message.textContent = "Название меняется в Home Assistant";
    editor.classList.remove("is-saved", "has-error");
    try {
      const saved = await renameEntity(owner, target.entity_id, draft);
      target.name = saved;
      currentName = saved;
      const detail = targetDetail(device, target);
      if (detail) detail.label = saved;
      title.textContent = saved;
      message.textContent = "Сохранено в Home Assistant";
      editor.classList.add("is-saved");
      if (typeof owner._load === "function") owner._load();
    } catch (error) {
      message.textContent = error && error.message ? error.message : "Не удалось сохранить название.";
      editor.classList.add("has-error");
    } finally {
      input.disabled = false; cancel.disabled = false; save.textContent = "Сохранить"; sync();
    }
  });
  editor.appendChild(input);
  editor.appendChild(cancel);
  editor.appendChild(save);
  editor.appendChild(message);
  head.parentElement.appendChild(editor);
}

export function renderDeviceTargetControls(owner, target, device, deps) {
  const { el, setAttr, actionLabel } = deps;
  const state = targetState(device, target);
  const row = el("section", "device-target-controls");
  const head = el("div", "device-target-head");
  const copy = el("div", "device-target-copy");
  const title = el("strong", "device-target-name", conciseDeviceTargetName(target, device));
  copy.appendChild(title);
  copy.appendChild(el("span", `device-target-state is-${state.state || "unknown"}`, state.label));
  head.appendChild(copy);
  row.appendChild(head);
  appendRenameEditor(owner, target, device, title, head, deps);
  const actions = el("div", "device-action-list");
  conciseActions(target, state.state).forEach((action) => {
    const fields = Array.isArray(action.allowed_fields) ? action.allowed_fields : [];
    const label = actionLabel(action, target, device);
    if (!fields.includes("value")) {
      const button = el("button", `device-action${actions.children.length ? " secondary" : ""}`, label);
      button.type = "button";
      setAttr(button, "aria-label", `${label}, ${title.textContent}`);
      button.disabled = owner._busy || Boolean(device && device.unavailable);
      button.addEventListener("click", (event) => {
        event.preventDefault();
        owner._executeDeviceAction(target.target_id, action.action_id, null);
      });
      actions.appendChild(button);
      return;
    }
    const valueRow = el("label", "device-value-action");
    valueRow.appendChild(el("span", null, label));
    const input = el("input");
    const numeric = ["set_temperature", "set_brightness", "set_position"].includes(action.action_id);
    input.type = numeric ? "number" : "text";
    if (numeric) {
      input.min = action.action_id === "set_temperature" ? "10" : "0";
      input.max = action.action_id === "set_brightness" ? "255" : (action.action_id === "set_temperature" ? "35" : "100");
      input.step = action.action_id === "set_temperature" ? "0.5" : "1";
    }
    const initial = deviceActionInitialValue(device, target, action);
    input.value = initial === null ? "" : String(initial);
    input.placeholder = numeric ? "Значение" : "Режим";
    valueRow.appendChild(input);
    const apply = el("button", "secondary", "Применить");
    apply.type = "button";
    const sync = () => {
      apply.disabled = owner._busy || (numeric
        ? !Number.isFinite(Number(input.value)) || input.value === "" : !String(input.value || "").trim());
    };
    sync(); input.addEventListener("input", sync);
    apply.addEventListener("click", (event) => {
      event.preventDefault();
      owner._executeDeviceAction(target.target_id, action.action_id,
        numeric ? Number(input.value) : String(input.value).trim());
    });
    valueRow.appendChild(apply);
    actions.appendChild(valueRow);
  });
  if (actions.children.length) row.appendChild(actions);
  return row;
}
