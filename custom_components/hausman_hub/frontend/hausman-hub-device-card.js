/* Canonical physical-device card shared by all tablet-style HACS sections. */

import { enhanceAppendedModal } from "./hausman-hub-modal.js?v=1.52.196";
import { renderDeviceTargetControls } from "./hausman-hub-device-controls.js?v=1.52.196";

const STATE_LABELS = {
  on: "Включено",
  off: "Выключено",
  open: "Открыто",
  closed: "Закрыто",
  locked: "Закрыт",
  unlocked: "Открыт",
  disarmed: "Охрана выключена",
  armed_home: "Охрана дома",
  armed_away: "Охрана включена",
  playing: "Воспроизведение",
  paused: "Пауза",
  idle: "Ожидание",
  heat: "Обогрев",
  heating: "Обогрев",
  cool: "Охлаждение",
  cooling: "Охлаждение",
  dry: "Осушение",
  auto: "Автоматически",
  unavailable: "Нет связи",
  unknown: "Состояние неизвестно",
  "сухо": "Сухо",
  "обнаружена вода": "Обнаружена вода",
  "движения нет": "Движения нет",
  "обнаружено движение": "Обнаружено движение",
  "без охраны": "Без охраны",
};

const DETAIL_LABELS = {
  state: "Состояние",
  power: "Мощность",
  current: "Ток",
  voltage: "Напряжение",
  battery: "Заряд",
  temperature: "Температура",
  humidity: "Влажность",
  brightness: "Яркость",
  position: "Положение",
  mode: "Режим",
};

const RANGE_ACTIONS = new Set([
  "set_value",
  "set_brightness_percent",
  "set_color_temperature",
]);

function normalized(value) {
  return String(value == null ? "" : value).trim().toLocaleLowerCase("ru");
}

function localizedNumericValue(value) {
  const source = String(value == null ? "" : value).trim();
  const match = source.match(/^(-?\d+(?:[.,]\d+)?)(\s*.*)$/);
  if (!match) return source;
  const numeric = Number(match[1].replace(",", "."));
  if (!Number.isFinite(numeric)) return source;
  const formatted = numeric.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
  const suffix = match[2].trim();
  return suffix ? `${formatted} ${suffix}` : formatted;
}

function primaryMeasurement(device) {
  const rawState = String(device && device.state || "").trim();
  const rawLabel = String(device && device.stateLabel || "").trim();
  if (!/^-?\d+(?:[.,]\d+)?$/.test(rawLabel || rawState)) return "";
  if (device && device.primaryValue) return localizedNumericValue(device.primaryValue);
  const primaryDetail = (Array.isArray(device && device.details) ? device.details : []).find((detail) => (
    detail && detail.entityId && device.entityId && detail.entityId === device.entityId
  ));
  if (primaryDetail) return localizedNumericValue(primaryDetail.value ?? primaryDetail.state);
  return localizedNumericValue(rawLabel || rawState);
}

export function localizedDeviceState(device) {
  if (device && device.unavailable) return "Нет связи";
  const measurement = primaryMeasurement(device);
  if (measurement) return measurement;
  const rawState = normalized(device && device.state);
  const rawLabel = normalized(device && device.stateLabel);
  return STATE_LABELS[rawLabel] || STATE_LABELS[rawState]
    || (device && device.stateLabel) || "Состояние неизвестно";
}

export function climateModePresentation(device) {
  const mode = device && device.climateMode;
  if (mode !== "automatic" && mode !== "manual") return null;
  const supplied = String(device.climateModeName || "").trim();
  const expected = mode === "manual" ? "Ручной режим" : "Автоматический режим";
  return { mode, label: supplied === expected ? supplied : expected };
}

function appendClimateModeBadge(container, device, deps, className) {
  const mode = climateModePresentation(device);
  if (!mode) return null;
  const badge = deps.el("span", `${className} is-${mode.mode}`, mode.label);
  container.appendChild(badge);
  return badge;
}

function actionObjectName(target, device) {
  return String(target && target.name || device && device.name || "устройство")
    .trim() || "устройство";
}

/**
 * Action titles from Home Assistant are intentionally generic so they can be
 * reused in automations. A person touching a device card needs the missing
 * object back in the label: "Закрыть шторы", not merely "Закрыть".
 */
export function contextualDeviceActionLabel(action, target, device) {
  const title = String(action && action.title || action && action.action_id || "Команда").trim();
  const object = actionObjectName(target, device);
  const contextual = {
    "Включить": "Включить",
    "Выключить": "Выключить",
    "Переключить": "Переключить",
    "Открыть": "Открыть",
    "Закрыть": "Закрыть",
    "Позиция": "Положение",
  }[title];
  return contextual ? `${contextual} ${object}` : title;
}

const DETAIL_ACTION_GENERIC_SEGMENTS = new Set([
  "humidifier", "увлажнитель", "switch", "выключатель", "light", "свет",
  "fan", "вентилятор", "climate", "климат", "cover", "шторы", "device", "устройство",
]);
const DETAIL_ACTION_VERBS = new Set([
  "включить", "выключить", "переключить", "открыть", "закрыть", "запустить",
  "остановить", "сбросить", "turn on", "turn off", "toggle", "open", "close", "start", "stop", "reset",
]);
const DEVICE_ACTION_LABELS = {
  turn_on: "Включить", "turn on": "Включить", turn_off: "Выключить", "turn off": "Выключить",
  toggle: "Переключить", open: "Открыть", open_cover: "Открыть", close: "Закрыть",
  close_cover: "Закрыть", lock: "Закрыть", unlock: "Открыть", press: "Нажать",
  start: "Запустить", stop: "Остановить", media_play: "Играть", media_pause: "Пауза",
};

/** Keep the device identity in the sheet header and expose only capability + command below it. */
export function conciseDeviceActionLabel(action, target, device) {
  const supplied = String(action && (action.title || action.action_id) || "Команда").trim();
  const title = DEVICE_ACTION_LABELS[normalized(supplied)] || supplied;
  const names = [device && device.name, target && target.name]
    .map((value) => String(value || "").trim()).filter(Boolean);
  const parts = title.split(/\s*[·•|]\s*/).map((part) => part.trim()).filter(Boolean)
    .map((part) => {
      let concise = part;
      names.forEach((name) => {
        if (normalized(concise) === normalized(name)) concise = "";
        else if (normalized(concise).startsWith(`${normalized(name)} `)) {
          concise = concise.slice(name.length).replace(/^[\s:_·-]+/, "");
        }
      });
      return concise;
    })
    .filter(Boolean)
    .filter((part) => !DETAIL_ACTION_GENERIC_SEGMENTS.has(normalized(part)))
    .filter((part, index, values) => values.findIndex((item) => normalized(item) === normalized(part)) === index);
  if (!parts.length) return title.split("·").pop().trim() || "Команда";
  if (parts.length === 1) return parts[0];
  const command = parts.pop();
  return `${parts.join(" · ")}: ${DETAIL_ACTION_VERBS.has(normalized(command)) ? normalized(command) : command}`;
}

function localizedDetailLabel(detail) {
  const raw = normalized(detail && detail.label);
  const entity = normalized(detail && detail.entityId).split(".").pop() || "";
  const key = Object.keys(DETAIL_LABELS).find((candidate) => raw === candidate || entity.endsWith(`_${candidate}`));
  return key ? DETAIL_LABELS[key] : (detail && detail.label || "Показатель");
}

function parseRangeNumber(value) {
  const match = String(value == null ? "" : value).trim().match(/^-?\d+(?:[.,]\d+)?/);
  if (!match) return null;
  const numeric = Number(match[0].replace(",", "."));
  return Number.isFinite(numeric) ? numeric : null;
}

function rangeStepDecimals(step) {
  const text = String(step);
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : text.length - dot - 1;
}

/** Contract fields must be finite JSON numbers; strings, booleans and NaN/Infinity fail closed. */
function controlRangeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Fail-closed gate: only snapshot-contract range actions on an opaque hub target may render. */
export function validRangeControl(detail) {
  const control = detail && detail.control;
  if (!control || control.kind !== "range") return null;
  const minimum = controlRangeNumber(control.minimum);
  const maximum = controlRangeNumber(control.maximum);
  const step = controlRangeNumber(control.step);
  if (minimum === null || maximum === null || step === null) return null;
  if (!(maximum > minimum) || !(step > 0)) return null;
  if (step > maximum - minimum) return null;
  const targetId = String(control.targetId || "").trim();
  const actionId = String(control.actionId || "").trim();
  if (!RANGE_ACTIONS.has(actionId)) return null;
  if (!/^entity_[0-9a-f]{16}$/.test(targetId)) return null;
  return { minimum, maximum, step, targetId, actionId, unit: String(control.unit || "").trim() };
}

/** Snap to the step grid by counting whole steps, so fractional steps never accumulate error. */
function quantizeRangeValue(value, range) {
  const span = Math.floor((range.maximum - range.minimum) / range.step + 1e-9);
  const steps = Math.min(Math.max(Math.round((value - range.minimum) / range.step), 0), span);
  return Number((range.minimum + steps * range.step).toFixed(rangeStepDecimals(range.step)));
}

function initialRangeDraft(detail, range) {
  const state = parseRangeNumber(detail && detail.state);
  if (state !== null) return quantizeRangeValue(state, range);
  const value = parseRangeNumber(detail && detail.value);
  if (value !== null) return quantizeRangeValue(value, range);
  return quantizeRangeValue(range.minimum, range);
}

function formatRangeValue(value, unit) {
  const formatted = Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 3 });
  return unit ? `${formatted} ${unit}` : formatted;
}

/** Range cards live in the device sheet; dragging only edits a local draft until «Применить». */
export function appendDeviceRangeControls(container, device, owner, deps, options = {}) {
  const { el, setAttr } = deps;
  const disabled = Boolean((device && device.unavailable) || (owner && owner._busy));
  const compact = options.compact === true;
  const details = Array.isArray(device && device.details) ? device.details : [];
  let rendered = 0;
  details.forEach((detail) => {
    const range = validRangeControl(detail);
    if (!range) return;
    const label = detail && detail.label
      ? conciseDeviceActionLabel({ title: detail.label }, null, device)
      : localizedDetailLabel(detail);
    const initialDraft = initialRangeDraft(detail, range);
    let draft = initialDraft;
    const card = el("section", `device-range-card${compact ? " is-compact" : ""}`);
    const head = el("div", "device-range-head");
    head.appendChild(el("span", "device-range-label", label));
    const valueEl = el("span", "device-range-value", formatRangeValue(draft, range.unit));
    head.appendChild(valueEl);
    card.appendChild(head);

    const decrease = el("button", "secondary device-range-step", "−");
    decrease.type = "button";
    setAttr(decrease, "aria-label", `Уменьшить: ${label}`);
    decrease.disabled = disabled;
    const slider = el("input", "device-range-slider");
    slider.type = "range";
    slider.min = String(range.minimum);
    slider.max = String(range.maximum);
    slider.step = String(range.step);
    slider.value = String(draft);
    setAttr(slider, "aria-label", label);
    slider.disabled = disabled;
    const increase = el("button", "secondary device-range-step", "+");
    increase.type = "button";
    setAttr(increase, "aria-label", `Увеличить: ${label}`);
    increase.disabled = disabled;
    let reset = null;
    const syncDraft = (next) => {
      draft = quantizeRangeValue(next, range);
      slider.value = String(draft);
      valueEl.textContent = formatRangeValue(draft, range.unit);
      if (reset) reset.disabled = disabled || draft === initialDraft;
    };
    slider.addEventListener("input", () => syncDraft(Number(slider.value)));
    decrease.addEventListener("click", (event) => {
      event.preventDefault();
      syncDraft(draft - range.step);
    });
    increase.addEventListener("click", (event) => {
      event.preventDefault();
      syncDraft(draft + range.step);
    });
    const sliderRow = el("div", "device-range-slider-row");
    sliderRow.appendChild(decrease);
    sliderRow.appendChild(slider);
    sliderRow.appendChild(increase);
    card.appendChild(sliderRow);

    const scale = el("div", "device-range-scale");
    scale.appendChild(el("span", null, `От ${formatRangeValue(range.minimum, range.unit)}`));
    scale.appendChild(el("span", null, `Шаг ${formatRangeValue(range.step, range.unit)}`));
    scale.appendChild(el("span", null, `До ${formatRangeValue(range.maximum, range.unit)}`));
    card.appendChild(scale);

    const apply = el("button", "device-range-apply", "Применить");
    apply.type = "button";
    setAttr(apply, "aria-label", `Применить: ${label}`);
    apply.disabled = disabled;
    apply.addEventListener("click", (event) => {
      event.preventDefault();
      if (apply.disabled || !owner || typeof owner._executeDeviceAction !== "function") return;
      owner._executeDeviceAction(range.targetId, range.actionId, draft);
    });
    if (compact) {
      const actions = el("div", "device-range-actions");
      reset = el("button", "secondary device-range-reset", "Сброс");
      reset.type = "button";
      setAttr(reset, "aria-label", `Сбросить: ${label}`);
      reset.disabled = true;
      reset.addEventListener("click", (event) => {
        event.preventDefault();
        syncDraft(initialDraft);
      });
      actions.appendChild(reset);
      actions.appendChild(apply);
      card.appendChild(actions);
    } else {
      card.appendChild(apply);
    }
    container.appendChild(card);
    rendered += 1;
  });
  return rendered;
}

function safeImageUrl(value) {
  const url = String(value || "").trim();
  return url && !/^(?:javascript|data:text\/html)/i.test(url) ? url : "";
}

export function appendDeviceVisual(container, device, iconName, deps, className = "device-product-visual") {
  const { el, svgIcon } = deps;
  const visual = el("span", className);
  const imageUrl = safeImageUrl(device && device.imageUrl);
  if (imageUrl) {
    const image = el("img");
    image.src = imageUrl;
    image.alt = "";
    image.loading = "lazy";
    image.addEventListener("error", () => {
      visual.innerHTML = "";
      visual.classList.add("is-fallback");
      visual.appendChild(svgIcon(iconName || "devices"));
    });
    visual.appendChild(image);
  } else {
    visual.classList.add("is-fallback");
    visual.appendChild(svgIcon(iconName || "devices"));
  }
  container.appendChild(visual);
  return visual;
}

function deviceKey(device) {
  return String(device && (device.id || device.physicalId || device.deviceId || device.entityId || device.name) || "");
}

export function conciseDetails(device) {
  const seen = new Set();
  return (Array.isArray(device && device.details) ? device.details : []).filter((detail) => {
    if (validRangeControl(detail)) return false;
    const label = localizedDetailLabel(detail);
    const value = String(detail && (detail.value ?? detail.state) || "").trim();
    const signature = `${normalized(label)}:${normalized(value)}`;
    if (!value || seen.has(signature)) return false;
    seen.add(signature);
    return true;
  }).slice(0, 6);
}

export function openPhysicalDeviceSheet(owner, device, deps) {
  const { el, setAttr } = deps;
  const iconName = owner._deviceIcon(device);
  const state = localizedDeviceState(device);
  if (typeof owner._activeDeviceModalClose === "function") owner._activeDeviceModalClose();
  const backdrop = el("div", "device-sheet-backdrop");
  const sheet = el("section", "device-sheet");
  setAttr(sheet, "role", "dialog");
  setAttr(sheet, "aria-modal", "true");
  setAttr(sheet, "aria-label", device.name || "Устройство");
  const close = el("button", "device-sheet-close", "×");
  close.type = "button";
  setAttr(close, "aria-label", "Закрыть");
  let finish = () => {};
  close.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation && event.stopPropagation();
    finish();
  });
  sheet.appendChild(close);

  const hero = el("div", "device-sheet-hero");
  appendDeviceVisual(hero, device, iconName, deps, "device-sheet-product");
  const identity = el("div", "device-sheet-identity");
  identity.appendChild(el("span", "device-sheet-eyebrow", owner._deviceCategoryName(device)));
  identity.appendChild(el("h3", null, device.name || "Устройство"));
  identity.appendChild(el("p", null, [device.roomName || "Без комнаты", device.manufacturer, device.model]
    .filter(Boolean).filter((value, index, values) => values.indexOf(value) === index).join(" · ")));
  const status = el("span", `device-sheet-status ${device.unavailable ? "bad" : (device.tone || "neutral")}`, state);
  const statuses = el("div", "device-sheet-statuses");
  statuses.appendChild(status);
  appendClimateModeBadge(statuses, device, deps, "device-climate-mode");
  identity.appendChild(statuses);
  hero.appendChild(identity);
  sheet.appendChild(hero);

  const targets = owner._catalogTargets(device);
  const targetEntities = new Set(targets.map((target) => target.entity_id));
  const details = conciseDetails(device).filter((detail) => !targetEntities.has(detail.entityId));
  if (details.length) {
    const detailGrid = el("dl", "device-sheet-facts");
    details.forEach((detail) => {
      const row = el("div", "device-sheet-fact");
      row.appendChild(el("dt", null, localizedDetailLabel(detail)));
      row.appendChild(el("dd", null, detail.value || detail.state || "Нет данных"));
      detailGrid.appendChild(row);
    });
    sheet.appendChild(detailGrid);
  }

  if (targets.length) {
    const controls = el("div", "device-sheet-controls");
    controls.appendChild(el("h4", "device-sheet-section-title", targets.length > 1 ? "Клавиши и действия" : "Управление"));
    const grid = el("div", "device-sheet-control-grid");
    targets.forEach((target) => grid.appendChild(renderDeviceTargetControls(owner, target, device, {
      ...deps, actionLabel: conciseDeviceActionLabel,
    })));
    controls.appendChild(grid);
    sheet.appendChild(controls);
  }
  const features = el("div", "device-sheet-features");
  const featureGrid = el("div", "device-sheet-feature-grid");
  const rangeCount = appendDeviceRangeControls(featureGrid, device, owner, deps, { compact: true });
  if (rangeCount) {
    features.appendChild(el("h4", "device-sheet-section-title", "Параметры"));
    features.appendChild(featureGrid);
    sheet.appendChild(features);
  }
  if (!targets.length && !rangeCount) {
    const controls = el("div", "device-sheet-controls");
    controls.appendChild(el("p", "device-sheet-note", device.unavailable
      ? "Устройство сейчас недоступно. Управление появится после восстановления связи."
      : "Для этого устройства доступны просмотр состояния и диагностические показатели."));
    sheet.appendChild(controls);
  }
  backdrop.appendChild(sheet);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) finish();
  });
  const modalRoot = owner.shadowRoot || owner._shell?.container;
  if (!modalRoot) return;
  modalRoot.appendChild(backdrop);
  owner._activeDeviceModalKey = `device:${deviceKey(device)}`;
  finish = enhanceAppendedModal(backdrop, sheet, () => {
    if (backdrop.remove) backdrop.remove();
    if (owner._activeDeviceModalKey === `device:${deviceKey(device)}`) {
      owner._activeDeviceModalKey = null;
      owner._activeDeviceModalClose = null;
    }
  }, { initialFocus: close });
  owner._activeDeviceModalClose = finish;
}

export function renderPhysicalDeviceCard(owner, device, deps) {
  const { el, setAttr } = deps;
  const iconName = owner._deviceIcon(device);
  const state = localizedDeviceState(device);
  const climateMode = climateModePresentation(device);
  const card = el("article", `inventory-device-card physical-device-card${device.unavailable ? " is-unavailable" : ""}`);
  const summary = el("button", "inventory-device-summary");
  summary.type = "button";
  setAttr(summary, "aria-label", `Открыть подробности устройства ${device.name || "Устройство"}. Состояние: ${state}.${climateMode ? ` ${climateMode.label}.` : ""}`);
  summary.addEventListener("click", () => openPhysicalDeviceSheet(owner, device, deps));
  appendDeviceVisual(summary, device, iconName, deps, "inventory-device-visual");
  const copy = el("span", "inventory-device-copy");
  copy.appendChild(el("strong", null, device.name || "Устройство"));
  copy.appendChild(el("small", null, `${device.roomName || "Без комнаты"} · ${owner._deviceCategoryName(device)}`));
  appendClimateModeBadge(copy, device, deps, "device-climate-mode");
  summary.appendChild(copy);
  summary.appendChild(el("span", "inventory-device-chevron", "›"));
  const footer = el("span", "inventory-device-footer");
  const measurement = el("span", "inventory-device-measurement");
  measurement.appendChild(el("small", null, "Состояние"));
  measurement.appendChild(el("strong", "inventory-device-state", state));
  footer.appendChild(measurement);
  const connection = el("span", `inventory-device-status ${device.unavailable ? "is-unavailable" : ""}`);
  connection.appendChild(el("span", `device-state-dot ${device.unavailable ? "bad" : (device.tone || "good")}`));
  connection.appendChild(el("span", null, device.unavailable ? "Нет связи" : "На связи"));
  footer.appendChild(connection);
  summary.appendChild(footer);
  card.appendChild(summary);
  return card;
}
