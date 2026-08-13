/* Canonical physical-device card shared by all tablet-style HACS sections. */

import { enhanceAppendedModal } from "./hausman-hub-modal.js?v=1.52.89";

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

/** Keep the device identity in the sheet header and expose only capability + command below it. */
export function conciseDeviceActionLabel(action, target, device) {
  const title = String(action && (action.title || action.action_id) || "Команда").trim();
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

function conciseDetails(device) {
  const seen = new Set();
  return (Array.isArray(device && device.details) ? device.details : []).filter((detail) => {
    const label = localizedDetailLabel(detail);
    const value = String(detail && (detail.value ?? detail.state) || "").trim();
    const signature = `${normalized(label)}:${normalized(value)}`;
    if (!value || seen.has(signature)) return false;
    seen.add(signature);
    return true;
  }).slice(0, 6);
}

function openDeviceSheet(owner, device, deps) {
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
  identity.appendChild(status);
  hero.appendChild(identity);
  sheet.appendChild(hero);

  const details = conciseDetails(device);
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

  const controls = el("div", "device-sheet-controls");
  const targets = owner._catalogTargets(device);
  if (!targets.length) {
    controls.appendChild(el("p", "device-sheet-note", device.unavailable
      ? "Устройство сейчас недоступно. Управление появится после восстановления связи."
      : "Для этого устройства доступны просмотр состояния и диагностические показатели."));
  } else {
    targets.forEach((target) => controls.appendChild(owner._deviceTargetControls(target, device)));
  }
  sheet.appendChild(controls);
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
  const card = el("article", `inventory-device-card physical-device-card${device.unavailable ? " is-unavailable" : ""}`);
  const summary = el("button", "inventory-device-summary");
  summary.type = "button";
  setAttr(summary, "aria-label", `Открыть подробности устройства ${device.name || "Устройство"}. Состояние: ${state}.`);
  summary.addEventListener("click", () => openDeviceSheet(owner, device, deps));
  appendDeviceVisual(summary, device, iconName, deps, "inventory-device-visual");
  const copy = el("span", "inventory-device-copy");
  copy.appendChild(el("strong", null, device.name || "Устройство"));
  copy.appendChild(el("small", null, `${device.roomName || "Без комнаты"} · ${owner._deviceCategoryName(device)}`));
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
