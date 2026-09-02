/* Climate control surface shared with the tablet information architecture. */

import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.203";
import { enhanceAppendedModal } from "./hausman-hub-modal.js?v=1.52.203";
import { roomIconName, roomSvgIcon } from "./hausman-hub-room-icons.js?v=1.52.203";
import { pendingOperationId, requiresSnapshotRefresh, resolveApiError, resolveClimateReceipt } from "./hausman-hub-error-taxonomy.js?v=1.52.203";
import { withCorrelationId } from "./hausman-hub-correlation.js?v=1.52.203";
import { renderClimateSide } from "./hausman-hub-climate-side.js?v=1.52.203";

const CLIMATE_ACTION_API = "hausman_hub/v1/climate/actions";
const CLIMATE_OPERATION_API = "hausman_hub/v1/climate/operations";

/* Canonical taxonomy failure flow: pending reads the stored operation, */
/* conflict and stale first refresh the snapshot, a confirmation failure */
/* requires a readback refresh and a new user action. No automatic retry */
/* of a physical command ever happens here. */
async function failClimateAction(panel, error, receipt) {
  const policy = (receipt && resolveClimateReceipt(receipt)) || resolveApiError(error);
  const operationId = pendingOperationId(policy);
  if (operationId) {
    await panel._hass.callApi("GET", `${CLIMATE_OPERATION_API}/${encodeURIComponent(operationId)}`).catch(() => null);
    await panel._load();
  } else if (requiresSnapshotRefresh(policy) || policy.code === "command_not_confirmed") {
    await panel._load();
  }
  panel._notice = policy.safeMessage;
  panel._error = true;
}

export async function synchronizeClimate(panel) {
  const homeControl = panel._climateRuntime && panel._climateRuntime.home_control;
  const allowed = homeControl && Array.isArray(homeControl.allowed_actions)
    && homeControl.allowed_actions.includes("synchronize_home");
  if (panel._busy || !allowed) return false;
  panel._busy = true;
  panel._climateSyncPending = true;
  panel._notice = "Синхронизируем климат...";
  panel._error = false;
  panel._render();
  try {
    const receipt = await panel._hass.callApi("POST", CLIMATE_ACTION_API, withCorrelationId(CLIMATE_ACTION_API, {
      contract: { name: "hausman-hub-climate-action-request", version: 1 },
      request_id: `hacs.climate.sync.${Date.now().toString(36)}`,
      expected_state_revision: panel._climateRuntime.state_revision,
      action: "synchronize_home",
      room_id: null,
      parameters: {},
    }));
    if (!receipt || receipt.confirmed !== true) {
      await failClimateAction(panel, null, receipt);
      return false;
    }
    panel._notice = "Климат синхронизирован.";
    panel._error = false;
    await panel._load();
    return true;
  } catch (error) {
    await failClimateAction(panel, error, null);
    return false;
  } finally {
    panel._busy = false;
    panel._climateSyncPending = false;
    panel._render();
  }
}

export async function setClimateManualMode(panel, roomId, deviceId, manual) {
  if (panel._busy || !panel._climateRuntime) return false;
  panel._busy = true;
  panel._climateModePendingKey = `${roomId}:${deviceId || "room"}`;
  panel._notice = manual ? "Переводим в ручной режим..." : "Возвращаем в автоматику...";
  panel._render();
  try {
    const receipt = await panel._hass.callApi("POST", CLIMATE_ACTION_API, withCorrelationId(CLIMATE_ACTION_API, {
      contract: { name: "hausman-hub-climate-action-request", version: 1 },
      request_id: `hacs.climate.${Date.now().toString(36)}`,
      expected_state_revision: panel._climateRuntime.state_revision,
      action: deviceId ? "set_device_mode" : "set_room_mode",
      room_id: roomId,
      parameters: deviceId
        ? { device_id: deviceId, mode: manual ? "manual" : "automatic" }
        : { mode: manual ? "manual" : "automatic" },
    }));
    if (!receipt || receipt.confirmed !== true) {
      await failClimateAction(panel, null, receipt);
      return false;
    }
    panel._notice = manual ? "Ручной режим включён." : "Автоматическое управление восстановлено.";
    panel._error = false;
    await panel._load();
    return true;
  } catch (error) {
    await failClimateAction(panel, error, null);
    return false;
  } finally {
    panel._busy = false;
    panel._climateModePendingKey = null;
    panel._render();
  }
}

export async function setClimateRoomTarget(panel, roomId, action, parameter, value) {
  if (panel._busy || !panel._climateRuntime || !Number.isFinite(value)) return false;
  panel._busy = true;
  panel._climateModePendingKey = `${roomId}:${action}`;
  panel._notice = "Сохраняем климатическую цель...";
  panel._render();
  try {
    const receipt = await panel._hass.callApi("POST", CLIMATE_ACTION_API, withCorrelationId(CLIMATE_ACTION_API, {
      contract: { name: "hausman-hub-climate-action-request", version: 1 },
      request_id: `hacs.climate.${Date.now().toString(36)}`,
      expected_state_revision: panel._climateRuntime.state_revision,
      action,
      room_id: roomId,
      parameters: { [parameter]: value },
    }));
    if (!receipt || receipt.confirmed !== true) {
      await failClimateAction(panel, null, receipt);
      return false;
    }
    panel._notice = action === "set_room_target" ? "Целевая температура подтверждена." : "Целевая влажность подтверждена.";
    panel._error = false;
    await panel._load();
    return true;
  } catch (error) {
    await failClimateAction(panel, error, null);
    return false;
  } finally {
    panel._busy = false;
    panel._climateModePendingKey = null;
    panel._render();
  }
}

export async function setClimateHomeTarget(panel, targetTemperature) {
  const homeControl = panel._climateRuntime && panel._climateRuntime.home_control;
  const allowed = homeControl && Array.isArray(homeControl.allowed_actions)
    && homeControl.allowed_actions.includes("set_home_targets");
  if (panel._busy || !allowed || !Number.isFinite(targetTemperature)) return false;
  panel._busy = true;
  panel._climateModePendingKey = "home:set_home_targets";
  panel._notice = "Сохраняем общую цель климата...";
  panel._error = false;
  panel._render();
  try {
    const receipt = await panel._hass.callApi("POST", CLIMATE_ACTION_API, withCorrelationId(CLIMATE_ACTION_API, {
      contract: { name: "hausman-hub-climate-action-request", version: 1 },
      request_id: `hacs.climate.home.${Date.now().toString(36)}`,
      expected_state_revision: panel._climateRuntime.state_revision,
      action: "set_home_targets",
      room_id: null,
      parameters: { target_temperature: targetTemperature },
    }));
    if (!receipt || receipt.confirmed !== true) {
      await failClimateAction(panel, null, receipt);
      return false;
    }
    panel._notice = "Общая климатическая цель подтверждена.";
    await panel._load();
    return true;
  } catch (error) {
    await failClimateAction(panel, error, null);
    return false;
  } finally {
    panel._busy = false;
    panel._climateModePendingKey = null;
    panel._render();
  }
}

export function renderHomeTargetCard(panel, dashboard, deps, options = {}) {
  const embedded = options.embedded === true;
  const control = panel._climateRuntime?.home_control || {};
  const allowed = Array.isArray(control.allowed_actions) ? control.allowed_actions : [];
  const canSetTargets = allowed.includes("set_home_targets");
  const raw = dashboard.summary && dashboard.summary.targetTemp;
  const target = raw !== null && raw !== undefined && raw !== "" && Number.isFinite(Number(raw))
    ? Number(raw) : null;
  const card = deps.el(embedded ? "div" : "section", embedded
    ? `overview-canon-climate-controls${target === null ? " is-empty" : ""}`
    : `overview-canon-primary-card is-target${target === null ? " is-empty" : ""}`);
  const head = deps.el("div", "overview-canon-target-head");
  head.appendChild(deps.el("span", "overview-canon-label", "Цель климата"));
  if (panel._climateRuntime && panel._climateRuntime.phase === "managed") {
    head.appendChild(deps.el("span", "overview-canon-target-auto", "Авто"));
  }
  card.appendChild(head);
  const formatTarget = (value) => `${value.toFixed(1).replace(".0", "").replace(".", ",")}°`;
  const dial = deps.el("div", "overview-canon-target-dial");
  const stepButton = (label, delta, aria) => {
    const button = deps.el("button", "overview-canon-target-step");
    button.type = "button";
    button.disabled = panel._busy || !canSetTargets || target === null;
    deps.setAttr(button, "aria-label", aria);
    button.appendChild(climateIcon(delta < 0 ? "minus" : "plus", deps));
    const hint = deps.el("span", "visually-hidden", label);
    button.appendChild(hint);
    button.addEventListener("click", () => {
      if (button.disabled || target === null) return;
      setClimateHomeTarget(panel, Math.round((target + delta) * 2) / 2);
    });
    return button;
  };
  dial.appendChild(stepButton("−0,5", -0.5, "Понизить общую цель на 0,5 °C"));
  const value = deps.el("strong", "overview-canon-target-value", target === null
    ? "Нет данных" : formatTarget(target));
  dial.appendChild(value);
  dial.appendChild(stepButton("+0,5", 0.5, "Повысить общую цель на 0,5 °C"));
  card.appendChild(dial);
  if (target !== null) {
    const sliderWrap = deps.el("div", "overview-canon-target-slider");
    const slider = deps.el("input");
    slider.type = "range";
    slider.min = "16";
    slider.max = "30";
    slider.step = "0.5";
    slider.value = String(target);
    slider.disabled = panel._busy || !canSetTargets;
    deps.setAttr(slider, "aria-label", "Общая целевая температура дома");
    slider.addEventListener("input", () => {
      const next = Number(slider.value);
      if (Number.isFinite(next)) value.textContent = formatTarget(next);
    });
    slider.addEventListener("change", () => {
      const next = Math.round(Number(slider.value) * 2) / 2;
      if (!slider.disabled && Number.isFinite(next) && next !== target) {
        setClimateHomeTarget(panel, next);
      }
    });
    sliderWrap.appendChild(slider);
    card.appendChild(sliderWrap);
  }
  const presets = deps.el("div", "overview-canon-target-presets");
  [["Прохладно", 24], ["Комфорт", 25], ["Тепло", 26]].forEach(([name, preset]) => {
    const chip = deps.el("button", `overview-canon-target-preset${target === preset ? " is-active" : ""}`);
    chip.type = "button";
    chip.disabled = panel._busy || !canSetTargets || target === null;
    chip.appendChild(deps.el("strong", null, name));
    chip.appendChild(deps.el("small", null, `${preset}°`));
    deps.setAttr(chip, "aria-label", `Установить общую цель ${preset} °C`);
    chip.addEventListener("click", () => {
      if (chip.disabled || target === preset) return;
      setClimateHomeTarget(panel, preset);
    });
    presets.appendChild(chip);
  });
  card.appendChild(presets);
  const footer = deps.el("div", "overview-canon-target-footer");
  const details = deps.el("button", "overview-canon-link", "Настроить");
  details.type = "button";
  details.addEventListener("click", () => panel._activateSection("climate"));
  footer.appendChild(details);
  if (allowed.includes("synchronize_home")) {
    const sync = deps.el("button", "overview-canon-link is-tertiary",
      panel._climateSyncPending ? "Синхронизация..." : "Синхронизировать");
    sync.type = "button";
    sync.disabled = Boolean(panel._busy || panel._climateSyncPending);
    deps.setAttr(sync, "aria-label", "Синхронизировать климатические цели дома");
    sync.addEventListener("click", () => {
      if (!sync.disabled) synchronizeClimate(panel);
    });
    footer.appendChild(sync);
  }
  if (!embedded) card.appendChild(footer);
  return card;
}

const CATEGORY_DEFINITIONS = [
  { id: "conditioner", title: "Кондиционеры", icon: "conditioner", pattern: /кондиционер|air.?condition|smartir|\bac\b/ },
  { id: "trv", title: "Термоголовки", icon: "thermometer", pattern: /термоголов|радиатор|radiator|thermostatic|\btrv\b/ },
  { id: "floor", title: "Тёплый пол", icon: "thermometer", pattern: /т[её]пл.*пол|floor.?heat/ },
  { id: "humidifier", title: "Увлажнители", icon: "water", pattern: /увлажн|humidifier/ },
  { id: "purifier", title: "Очистители", icon: "air", pattern: /очистител|purifier|air.?clean/ },
  { id: "ventilation", title: "Вытяжки", icon: "air", pattern: /вытяж|вентил|ventilat|exhaust/ },
];

const CATEGORY_ICON_PATHS = {
  conditioner: {
    path: "M5 4.5h14a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2ZM6.5 10.5h11M8 16v1.5M12 15.5v2M16 16v1.5",
    stroke: true,
  },
  minus: "M5 11h14v2H5z",
  plus: "M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z",
  snow: "M11 2h2v3.17l2.83-1.63 1 1.73L14 6.9l2.75 1.58 2.75-1.58 1 1.73-2.75 1.59v3.56l2.75 1.59-1 1.73-2.75-1.58L14 17.1l2.83 1.63-1 1.73L13 18.83V22h-2v-3.17l-2.83 1.63-1-1.73L10 17.1l-2.75-1.58-2.75 1.58-1-1.73 2.75-1.59v-3.56L3.5 8.63l1-1.73 2.75 1.58L10 6.9 7.17 5.27l1-1.73L11 5.17z",
  air: "M4 10h10.5a2.5 2.5 0 1 0-2.45-3H10a4.5 4.5 0 1 1 4.5 5H4zm0 4h13.5a4.5 4.5 0 1 1-4.5 4.5h2a2.5 2.5 0 1 0 2.5-2.5H4zm0-8h4v2H4z",
  sync: "M12 4V1L8 5l4 4V6a6 6 0 0 1 5.65 4h2.1A8 8 0 0 0 12 4zm-5.65 6H4.25A8 8 0 0 0 12 20v3l4-4-4-4v3a6 6 0 0 1-5.65-8z",
};

function climateIcon(name, deps) {
  const definition = CATEGORY_ICON_PATHS[name];
  if (!definition) return deps.svgIcon(name);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  deps.setAttr(svg, "viewBox", "0 0 24 24");
  deps.setAttr(svg, "aria-hidden", "true");
  deps.setAttr(svg, "class", "icon");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  deps.setAttr(path, "d", typeof definition === "string" ? definition : definition.path);
  if (typeof definition === "object" && definition.stroke) {
    deps.setAttr(path, "fill", "none");
    deps.setAttr(path, "stroke", "currentColor");
    deps.setAttr(path, "stroke-width", "1.8");
    deps.setAttr(path, "stroke-linecap", "round");
    deps.setAttr(path, "stroke-linejoin", "round");
  } else {
    deps.setAttr(path, "fill", "currentColor");
  }
  svg.appendChild(path);
  return svg;
}

function normalized(value) {
  return String(value || "").trim().toLocaleLowerCase("ru");
}

function climateIdentity(device) {
  const details = Array.isArray(device && device.details) ? device.details : [];
  return normalized([
    device && device.name, device && device.model, device && device.manufacturer,
    device && device.domain, device && device.category,
    ...details.map((detail) => `${detail.label || ""} ${detail.entityId || ""}`),
  ].join(" "));
}

export function climateCategory(device) {
  const identity = climateIdentity(device);
  const explicit = CATEGORY_DEFINITIONS.find((category) => category.id !== "conditioner"
    && category.pattern.test(identity));
  if (explicit) return explicit.id;
  if ((device && device.domain) === "humidifier") return "humidifier";
  if ((device && device.domain) === "fan") return "ventilation";
  if ((device && device.domain) === "climate" || CATEGORY_DEFINITIONS[0].pattern.test(identity)) {
    return "conditioner";
  }
  return "conditioner";
}

function average(values) {
  const valid = values.filter((value) => typeof value === "number" && Number.isFinite(value));
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null;
}

function temperature(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Нет данных";
  const number = value;
  return `${Number.isInteger(number) ? number : number.toFixed(1)} °C`;
}

function humidity(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value)} %`
    : "Нет данных";
}

function stateLabel(device) {
  if (device && device.unavailable) return "Нет связи";
  const raw = normalized(device && (device.stateLabel || device.state));
  const labels = {
    off: "Выключено", on: "Включено", idle: "Ожидание", heat: "Обогрев",
    heating: "Обогрев", cool: "Охлаждение", cooling: "Охлаждение", dry: "Осушение",
    fan_only: "Вентиляция", auto: "Автоматически", unavailable: "Нет связи", unknown: "Состояние неизвестно",
  };
  return labels[raw] || device && device.stateLabel || "Состояние неизвестно";
}

function dashboardClimateMode(device) {
  const mode = device && device.climateMode;
  if (mode !== "automatic" && mode !== "manual") return null;
  const expected = mode === "manual" ? "Ручной режим" : "Автоматический режим";
  return {
    mode,
    label: device.climateModeName === expected ? device.climateModeName : expected,
  };
}

function deviceIsActive(device) {
  if (!device || device.unavailable) return false;
  if (typeof device.active === "boolean") return device.active;
  const raw = normalized(device.state);
  return Boolean(raw) && !["off", "idle", "unknown", "unavailable"].includes(raw);
}

function createHero(panel, rooms, devices, deps) {
  const unavailable = devices.filter((device) => device.unavailable).length;
  const categories = renderCategories(panel, devices, deps);
  return createLibraryHero(panel, {
    eyebrow: "КЛИМАТ ДОМА",
    title: "Климат по комнатам",
    subtitle: "Текущие показатели, цели и оборудование в одном месте",
    warning: unavailable > 0,
    facts: [
      { label: "Сейчас", value: temperature(average(rooms.map((room) => room.temp))) },
      { label: "Влажность", value: humidity(average(rooms.map((room) => room.humidity))) },
      { label: "Работает", value: `${devices.filter(deviceIsActive).length} из ${devices.length}` },
      { label: "Без связи", value: unavailable, warning: unavailable > 0 },
    ],
    extra: categories,
  }, deps);
}

function productImage(device, deps) {
  const { el, svgIcon } = deps;
  const media = el("span", "climate-product-image");
  if (device && device.imageUrl) {
    const image = el("img");
    image.src = device.imageUrl;
    image.alt = "";
    image.loading = "lazy";
    image.addEventListener("error", () => {
      media.innerHTML = "";
      media.classList.add("is-fallback");
      const category = CATEGORY_DEFINITIONS.find((item) => item.id === climateCategory(device));
      media.appendChild(climateIcon(category ? category.icon : "thermometer", deps));
    });
    media.appendChild(image);
  } else {
    media.classList.add("is-fallback");
    const category = CATEGORY_DEFINITIONS.find((item) => item.id === climateCategory(device));
    media.appendChild(climateIcon(category ? category.icon : "thermometer", deps));
  }
  return media;
}

function climateDeviceKey(device) {
  return String(device && (
    device.physicalId || device.deviceId || device.id || device.entityId || device.name
  ) || "");
}

function refreshClimateOverlay(panel) {
  if (panel._sectionRenderKeys) panel._sectionRenderKeys.climate = null;
  if (typeof panel._render === "function") panel._render();
}

function requestClimateSheet(panel, title, devices, roomId) {
  panel._climateOverlay = {
    title,
    deviceKeys: devices.map(climateDeviceKey).filter(Boolean),
    selectedKey: null,
    roomId: roomId || null,
  };
  refreshClimateOverlay(panel);
}

function renderClimateDeviceList(panel, body, title, devices, deps, onBack) {
  const { el, setAttr, svgIcon } = deps;
  body.innerHTML = "";
  const head = el("div", "climate-sheet-heading");
  if (onBack) {
    const back = el("button", "climate-sheet-back", "Назад");
    back.type = "button";
    back.addEventListener("click", onBack);
    head.appendChild(back);
  }
  const copy = el("div");
  copy.appendChild(el("h3", null, title));
  copy.appendChild(el("p", null, `${devices.length} ${panel._deviceCountWord(devices.length)} · одно физическое устройство — одна карточка`));
  head.appendChild(copy);
  body.appendChild(head);
  if (!devices.length) {
    body.appendChild(el("div", "climate-sheet-empty", "В этой группе пока нет доступных устройств."));
    return;
  }
  const grid = el("div", "climate-sheet-device-grid");
  devices.forEach((device) => {
    const card = el("button", `climate-product-card${device.unavailable ? " is-unavailable" : ""}`);
    card.type = "button";
    setAttr(card, "aria-label", `Открыть ${device.name || "устройство"}`);
    card.appendChild(productImage(device, deps));
    const deviceCopy = el("span", "climate-product-copy");
    deviceCopy.appendChild(el("strong", null, device.name || "Климатическое устройство"));
    deviceCopy.appendChild(el("small", null, `${device.roomName || "Без комнаты"} · ${stateLabel(device)}`));
    const climateMode = dashboardClimateMode(device);
    if (climateMode) {
      deviceCopy.appendChild(el(
        "span",
        `climate-product-mode is-${climateMode.mode}`,
        climateMode.label,
      ));
    }
    card.appendChild(deviceCopy);
    const dot = el("span", `climate-product-state ${device.unavailable ? "bad" : (deviceIsActive(device) ? "good" : "neutral")}`);
    card.appendChild(dot);
    card.addEventListener("click", () => {
      if (panel._climateOverlay) {
        panel._climateOverlay.selectedKey = climateDeviceKey(device);
      }
      refreshClimateOverlay(panel);
    });
    grid.appendChild(card);
  });
  body.appendChild(grid);
}

function renderRoomContour(panel, body, roomId, roomName, deps) {
  const { el } = deps;
  const managedRoom = runtimeRoom(panel, roomId);
  if (!managedRoom) return;
  const contourDevices = Array.isArray(managedRoom.devices) ? managedRoom.devices : [];
  const manual = managedRoom.mode === "manual";
  const criticalSensorExcluded = contourDevices.some((device) =>
    device.mode === "manual" && ["temperature_sensor", "humidity_sensor"].includes(device.kind));
  if (!contourDevices.length && !managedRoom.control) return;
  const section = el("div", "climate-room-contour");
  const heading = el("div", "climate-sheet-heading");
  const headingCopy = el("div");
  headingCopy.appendChild(el("h3", null, "Климатический контур"));
  headingCopy.appendChild(el("p", null, "Режим автоматики комнаты и её устройств"));
  heading.appendChild(headingCopy);
  section.appendChild(heading);
  const roomControl = managedRoom.control;
  if (roomControl && Array.isArray(roomControl.allowed_actions)
    && roomControl.allowed_actions.includes("set_room_mode")) {
    const roomMode = el("button", `climate-manual-toggle${manual ? " is-manual" : ""}`,
      panel._climateModePendingKey === `${roomId}:room` ? "Сохраняем..."
        : criticalSensorExcluded ? "Сначала верните датчик"
          : (manual ? "Вернуть автоматику" : "Вся комната вручную"));
    roomMode.type = "button";
    roomMode.disabled = Boolean(panel._climateModePendingKey) || criticalSensorExcluded;
    roomMode.addEventListener("click", (event) => {
      event.stopPropagation?.();
      panel._setClimateManual(roomId, null, !manual);
    });
    section.appendChild(roomMode);
  }
  if (criticalSensorExcluded) {
    section.appendChild(el("p", "climate-manual-warning",
      "Комната исключена из автоматики: критический датчик работает в ручном режиме."));
  }
  if (contourDevices.length) {
    const list = el("div", "climate-contour-devices");
    contourDevices.forEach((device) => {
      const deviceManual = device.mode === "manual";
      const row = el("div", `climate-contour-device${deviceManual ? " is-manual" : ""}`);
      const deviceCopy = el("span");
      deviceCopy.appendChild(el("strong", null, device.name || climateDeviceKindLabel(device.kind)));
      const modeName = device.mode_name === "Ручной режим" || device.mode_name === "Автоматический режим"
        ? device.mode_name : (deviceManual ? "Ручной режим" : "Автоматический режим");
      deviceCopy.appendChild(el("small", null, `${climateDeviceKindLabel(device.kind)} · ${modeName}`));
      row.appendChild(deviceCopy);
      const control = device.control;
      if (control && Array.isArray(control.allowed_actions) && control.allowed_actions.includes("set_device_mode")) {
        const toggle = el("button", `climate-device-mode${deviceManual ? " is-manual" : ""}`,
          panel._climateModePendingKey === `${roomId}:${device.id}` ? "Сохраняем..." : (deviceManual ? "Вернуть" : "Исключить"));
        toggle.type = "button";
        toggle.disabled = Boolean(panel._climateModePendingKey);
        toggle.addEventListener("click", (event) => {
          event.stopPropagation?.();
          const critical = ["temperature_sensor", "humidity_sensor"].includes(device.kind);
          if (critical && !deviceManual && !window.confirm(
            `Исключить «${device.name}»? Комната «${roomName}» полностью перейдёт в ручной режим до возврата датчика.`
          )) return;
          panel._setClimateManual(roomId, device.id, !deviceManual);
        });
        row.appendChild(toggle);
      }
      list.appendChild(row);
    });
    section.appendChild(list);
  }
  body.appendChild(section);
}

function openClimateSheet(panel, container, title, devices, deps, roomId) {
  const { el, setAttr } = deps;
  const existing = container.querySelector && container.querySelector(".climate-device-sheet-backdrop");
  if (existing && existing.remove) existing.remove();
  const backdrop = el("div", "climate-device-sheet-backdrop");
  const sheet = el("section", "climate-device-sheet");
  setAttr(sheet, "role", "dialog");
  setAttr(sheet, "aria-modal", "true");
  setAttr(sheet, "aria-label", title);
  const close = el("button", "climate-sheet-close", "×");
  close.type = "button";
  setAttr(close, "aria-label", "Закрыть");
  const dismiss = () => {
    panel._climateOverlay = null;
    if (backdrop.remove) backdrop.remove();
  };
  close.addEventListener("click", dismiss);
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) dismiss(); });
  enhanceAppendedModal(backdrop, sheet, dismiss);
  sheet.appendChild(close);
  const body = el("div", "climate-sheet-body");
  sheet.appendChild(body);
  backdrop.appendChild(sheet);
  container.appendChild(backdrop);
  const selectedKey = panel._climateOverlay && panel._climateOverlay.selectedKey;
  const selected = selectedKey && devices.find((device) => climateDeviceKey(device) === selectedKey);
  if (!selected) {
    renderClimateDeviceList(panel, body, title, devices, deps, null);
    if (roomId) renderRoomContour(panel, body, roomId, title, deps);
    return;
  }
  renderClimateDeviceList(panel, body, selected.name || "Устройство", [selected], deps, () => {
    if (panel._climateOverlay) panel._climateOverlay.selectedKey = null;
    refreshClimateOverlay(panel);
  });
  const selectedGrid = body.querySelector ? body.querySelector(".climate-sheet-device-grid") : null;
  if (selectedGrid) {
    selectedGrid.innerHTML = "";
    const selectedCard = panel._deviceInventoryCard(selected);
    selectedCard.open = true;
    selectedGrid.appendChild(selectedCard);
  }
}

function renderCategories(panel, devices, deps) {
  const { el, svgIcon, setAttr } = deps;
  const grid = el("div", "climate-category-grid");
  setAttr(grid, "aria-label", "Оборудование климата");
  CATEGORY_DEFINITIONS.forEach((category) => {
    const matches = devices.filter((device) => climateCategory(device) === category.id);
    const card = el("button", "climate-category-card");
    card.type = "button";
    setAttr(card, "aria-label", `Открыть ${category.title.toLocaleLowerCase("ru")}`);
    const icon = el("span", "climate-category-icon");
    icon.appendChild(climateIcon(category.icon, deps));
    card.appendChild(icon);
    const cardCopy = el("span", "climate-category-copy");
    cardCopy.appendChild(el("strong", null, category.title));
    cardCopy.appendChild(el("small", null, matches.length
      ? `${matches.length}${matches.some((device) => device.unavailable) ? " · нет связи" : ""}`
      : "0"));
    card.appendChild(cardCopy);
    card.addEventListener("click", () => requestClimateSheet(panel, category.title, matches));
    grid.appendChild(card);
  });
  return grid;
}

function roomDevices(room, devices) {
  return devices.filter((device) => device.roomId === room.id || device.roomName === room.name);
}

function runtimeRoom(panel, roomId) {
  const rooms = panel._climateRuntime && Array.isArray(panel._climateRuntime.rooms)
    ? panel._climateRuntime.rooms : [];
  return rooms.find((room) => room.id === roomId) || null;
}

function roomHasManualControl(panel, room) {
  const runtime = runtimeRoom(panel, room.id);
  return Boolean(runtime && (
    runtime.mode === "manual"
    || (Array.isArray(runtime.devices) && runtime.devices.some((device) => device.mode === "manual"))
  ));
}

function climateDeviceKindLabel(kind) {
  return ({
    air_conditioner: "Кондиционер", radiator_thermostat: "Термоголовка",
    humidifier: "Увлажнитель", floor_heating: "Тёплый пол",
    temperature_sensor: "Датчик температуры", humidity_sensor: "Датчик влажности",
  })[kind] || "Устройство климата";
}

function climateRoomTemperatureText(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}°`;
}

function roomIsOffline(room, devices) {
  const matches = roomDevices(room, devices);
  return matches.length > 0 && matches.every((device) => device.unavailable);
}

function roomCurrentSummary(room) {
  if (typeof room.humidity === "number" && Number.isFinite(room.humidity)) {
    return `${Math.round(room.humidity)}%`;
  }
  return "Нет данных";
}

function roomTargetSummary(room) {
  const parts = [];
  const temp = climateRoomTemperatureText(room.targetTemp);
  if (temp !== null) parts.push(temp);
  if (typeof room.targetHumidity === "number" && Number.isFinite(room.targetHumidity)) {
    parts.push(`${Math.round(room.targetHumidity)}%`);
  }
  return parts.length ? parts.join(" · ") : "Не заданы";
}

function roomTargetSpec(panel, room, action, parameter) {
  const managedRoom = runtimeRoom(panel, room.id);
  const control = managedRoom && managedRoom.control;
  const allowed = control && Array.isArray(control.allowed_actions) ? control.allowed_actions : [];
  if (!allowed.includes(action)) return null;
  const inputs = control.action_inputs || {};
  const spec = inputs[action] && inputs[action][parameter];
  if (!spec || !Number.isFinite(Number(spec.minimum)) || !Number.isFinite(Number(spec.maximum))) return null;
  return { managedRoom, spec };
}

function renderRoomStepper(panel, room, deps, options) {
  const { el, setAttr } = deps;
  const { action, parameter, field, label, icon, current, formatValue } = options;
  const wrap = el("div", "hh-climate-room-stepper");
  const found = roomTargetSpec(panel, room, action, parameter);
  const controllable = Boolean(found) && typeof current === "number" && Number.isFinite(current);
  const busy = Boolean(panel._busy || panel._climateModePendingKey);
  const pending = panel._climateModePendingKey === `${room.id}:${action}`;
  if (!controllable) wrap.classList.add("is-readonly");
  setAttr(wrap, "role", "group");
  const displayedValue = typeof current === "number" && Number.isFinite(current)
    ? formatValue(current) : "Не задана";
  setAttr(wrap, "aria-label", `${label}: ${displayedValue}${controllable ? "" : ", только просмотр"}`);
  const stepButton = (direction, aria) => {
    const button = el("button", "hh-climate-room-step");
    button.type = "button";
    button.disabled = !controllable || busy;
    setAttr(button, "aria-label", aria);
    button.appendChild(climateIcon(direction < 0 ? "minus" : "plus", deps));
    button.addEventListener("click", (event) => {
      event.stopPropagation?.();
      if (button.disabled || !found) return;
      const { managedRoom, spec } = found;
      const step = Number(spec.step) > 0 ? Number(spec.step) : 1;
      const next = Number(Math.min(Number(spec.maximum), Math.max(Number(spec.minimum),
        Math.round((current + direction * step) / step) * step)).toFixed(2));
      if (!Number.isFinite(next) || next === current) return;
      managedRoom[field] = next;
      if (field === "target_temperature") room.targetTemp = next;
      if (field === "target_humidity") room.targetHumidity = next;
      setClimateRoomTarget(panel, room.id, action, parameter, next);
    });
    return button;
  };
  const caption = el("span", "hh-climate-room-stepper-label");
  const captionIcon = el("span", "hh-climate-room-stepper-icon");
  captionIcon.appendChild(climateIcon(icon, deps));
  caption.appendChild(captionIcon);
  caption.appendChild(el("span", null, label));
  wrap.appendChild(caption);
  const actions = el("span", `hh-climate-room-stepper-actions${controllable ? "" : " is-readonly"}`);
  if (controllable) actions.appendChild(stepButton(-1, `Понизить цель «${label}» в комнате ${room.name || "Комната"}`));
  actions.appendChild(el("strong", `hh-climate-room-stepper-value${pending ? " is-pending" : ""}`,
    pending ? "Сохраняем..." : displayedValue));
  if (controllable) actions.appendChild(stepButton(1, `Повысить цель «${label}» в комнате ${room.name || "Комната"}`));
  wrap.appendChild(actions);
  return wrap;
}

function appendRoomFact(container, label, value, el) {
  const fact = el("p", "hh-climate-room-fact");
  fact.appendChild(el("small", null, label));
  fact.appendChild(el("strong", null, value));
  container.appendChild(fact);
}

function renderRoomCard(panel, room, devices, deps) {
  const { el, setAttr } = deps;
  const matches = roomDevices(room, devices);
  const offline = roomIsOffline(room, devices);
  const manual = roomHasManualControl(panel, room);
  const hasReadings = typeof room.temp === "number" && Number.isFinite(room.temp)
    || typeof room.humidity === "number" && Number.isFinite(room.humidity);
  const card = el("div", `climate-room-card hh-climate-room-card${manual ? " is-manual" : ""}${offline ? " is-offline" : ""}`);
  const head = el("div", "hh-climate-room-head");
  const roomIcon = el("span", "climate-room-icon");
  roomIcon.appendChild(roomSvgIcon(roomIconName(room)));
  head.appendChild(roomIcon);
  const roomCopy = el("span", "hh-climate-room-copy");
  roomCopy.appendChild(el("strong", null, room.name || "Комната"));
  const roomManual = runtimeRoom(panel, room.id)?.mode === "manual";
  roomCopy.appendChild(el("small", null, manual
    ? (roomManual ? "Полный ручной режим" : "Есть ручное устройство")
    : (!hasReadings ? "Нет данных"
      : (matches.some(deviceIsActive) ? "Климат работает" : "Поддержание комфорта"))));
  head.appendChild(roomCopy);
  const status = el("span", `hh-climate-room-status ${offline ? "is-offline" : (manual ? "is-manual" : "is-auto")}`,
    offline ? "Без связи" : (manual ? "Ручной режим" : "Авто"));
  head.appendChild(status);
  head.appendChild(el("b", "hh-climate-room-temperature", climateRoomTemperatureText(room.temp) || "—"));
  card.appendChild(head);
  const lines = el("div", "hh-climate-room-lines");
  appendRoomFact(lines, "Влажность", roomCurrentSummary(room), el);
  appendRoomFact(lines, "Цели", roomTargetSummary(room), el);
  card.appendChild(lines);
  const managedRoom = runtimeRoom(panel, room.id);
  const steppers = el("div", "hh-climate-room-steppers");
  steppers.appendChild(renderRoomStepper(panel, room, deps, {
    action: "set_room_target", parameter: "target_temperature", field: "target_temperature",
    label: "Температура", icon: "thermometer",
    formatValue: (value) => climateRoomTemperatureText(value) || "Не задана",
    current: typeof managedRoom?.target_temperature === "number" ? managedRoom.target_temperature : room.targetTemp,
  }));
  steppers.appendChild(renderRoomStepper(panel, room, deps, {
    action: "set_room_humidity_target", parameter: "target_humidity", field: "target_humidity",
    label: "Влажность", icon: "water", formatValue: (value) => `${Math.round(value)}%`,
    current: typeof managedRoom?.target_humidity === "number" ? managedRoom.target_humidity : room.targetHumidity,
  }));
  card.appendChild(steppers);
  card.addEventListener("click", () => requestClimateSheet(panel, room.name || "Комната", matches, room.id));
  return card;
}

function renderRooms(panel, container, rooms, devices, deps) {
  const { el, svgIcon, setAttr } = deps;
  const section = el("section", "climate-rooms-section");
  const head = el("div", "climate-section-heading");
  const copy = el("div");
  copy.appendChild(el("h3", null, "Комнаты и цели"));
  copy.appendChild(el("p", null, "Показатели и цели каждой комнаты видны сразу"));
  head.appendChild(copy);
  section.appendChild(head);
  if (!rooms.length) {
    section.appendChild(el("div", "climate-sheet-empty", "Комнаты пока не переданы Home Assistant."));
    container.appendChild(section);
    return;
  }
  if (!panel._climateRoomUi) panel._climateRoomUi = { roomId: null, filter: "all", search: "" };
  const filter = panel._climateRoomUi.filter || "all";
  const search = typeof panel._climateRoomUi.search === "string" ? panel._climateRoomUi.search : "";
  const matchesFilter = (room) => {
    if (filter === "manual") return roomHasManualControl(panel, room);
    if (filter === "automatic") return !roomHasManualControl(panel, room);
    if (filter === "unavailable") return roomIsOffline(room, devices);
    return true;
  };
  const matchesSearch = (room) => {
    const query = normalized(panel._climateRoomUi.search);
    return !query || normalized(room.name).includes(query);
  };
  const toolbar = el("div", "hh-climate-room-toolbar");
  const searchField = el("input", "hh-climate-room-search");
  searchField.type = "search";
  searchField.value = search;
  setAttr(searchField, "placeholder", "Найти комнату");
  setAttr(searchField, "aria-label", "Найти комнату");
  toolbar.appendChild(searchField);
  const modeTabs = el("div", "climate-mode-tabs");
  setAttr(modeTabs, "role", "tablist");
  setAttr(modeTabs, "aria-label", "Режим климатического контура");
  [["all", "Все"], ["automatic", "Авто"], ["manual", "Ручной режим"], ["unavailable", "Без связи"]].forEach(([id, label]) => {
    const button = el("button", `climate-mode-tab${filter === id ? " is-active" : ""}`, label);
    button.type = "button";
    setAttr(button, "role", "tab");
    setAttr(button, "aria-selected", filter === id ? "true" : "false");
    button.addEventListener("click", () => {
      panel._climateRoomUi.filter = id;
      panel._climateRoomUi.roomId = null;
      refreshClimateOverlay(panel);
    });
    modeTabs.appendChild(button);
  });
  toolbar.appendChild(modeTabs);
  section.appendChild(toolbar);
  const manualDevices = (panel._climateRuntime?.rooms || []).flatMap((runtimeRoom) =>
    Array.isArray(runtimeRoom?.devices)
      ? runtimeRoom.devices.filter((device) => device?.mode === "manual").map((device) => ({
        room: runtimeRoom,
        device,
      }))
      : []
  );
  if (manualDevices.length) {
    const manualList = el("section", "climate-manual-list");
    const manualHead = el("div", "climate-manual-list-heading");
    const manualTitle = el("div");
    const manualIcon = el("span", "climate-manual-list-icon");
    manualIcon.appendChild(svgIcon("manual"));
    manualTitle.appendChild(manualIcon);
    manualTitle.appendChild(el("strong", null, "Исключено из климатического контура"));
    manualHead.appendChild(manualTitle);
    manualHead.appendChild(el("span", "climate-manual-count", String(manualDevices.length)));
    manualList.appendChild(manualHead);
    manualDevices.forEach(({ room, device }) => {
      const row = el("div", "climate-manual-list-item");
      const itemCopy = el("span");
      itemCopy.appendChild(el("strong", null, device.name || climateDeviceKindLabel(device.kind)));
      itemCopy.appendChild(el("small", null, `${room.name || "Комната"} · ${climateDeviceKindLabel(device.kind)}`));
      row.appendChild(itemCopy);
      const allowed = device.control && Array.isArray(device.control.allowed_actions)
        && device.control.allowed_actions.includes("set_device_mode");
      const restore = el("button", "climate-manual-restore", "Вернуть в контур");
      restore.type = "button";
      restore.disabled = !allowed || Boolean(panel._climateModePendingKey);
      restore.addEventListener("click", (event) => {
        event.stopPropagation?.();
        panel._setClimateManual(room.id, device.id, false);
      });
      row.appendChild(restore);
      manualList.appendChild(row);
    });
    section.appendChild(manualList);
  }
  const gridRooms = rooms.filter(matchesFilter);
  if (!gridRooms.length) {
    section.appendChild(el("div", "climate-sheet-empty", "В этой вкладке пока нет комнат."));
    container.appendChild(section);
    return;
  }
  const grid = el("div", "hh-climate-room-grid");
  const entries = gridRooms.map((room) => {
    const card = renderRoomCard(panel, room, devices, deps);
    grid.appendChild(card);
    return { room, card };
  });
  const emptyNote = el("div", "climate-sheet-empty", "Комната не найдена.");
  const applySearch = () => {
    let shown = 0;
    entries.forEach(({ room, card }) => {
      const visible = matchesSearch(room);
      card.hidden = !visible;
      if (visible) shown += 1;
    });
    emptyNote.hidden = shown > 0;
  };
  searchField.addEventListener("input", () => {
    panel._climateRoomUi.search = searchField.value;
    applySearch();
  });
  section.appendChild(grid);
  section.appendChild(emptyNote);
  applySearch();
  container.appendChild(section);
}

function renderClimateSynchronization(panel, container, deps) {
  const control = panel._climateRuntime && panel._climateRuntime.home_control;
  const allowed = control && Array.isArray(control.allowed_actions)
    && control.allowed_actions.includes("synchronize_home");
  if (!allowed) return;
  const { el, setAttr } = deps;
  const section = el("section", "climate-sync-action");
  const icon = el("span", "climate-sync-icon");
  icon.appendChild(climateIcon("sync", deps));
  section.appendChild(icon);
  const copy = el("span", "climate-sync-copy");
  copy.appendChild(el("strong", null, panel._climateSyncPending
    ? "Синхронизируем климат..." : "Синхронизировать климат"));
  copy.appendChild(el("small", null,
    "Отправить сохранённые цели всем устройствам в автоматическом контуре"));
  section.appendChild(copy);
  const button = el("button", "climate-sync-button", panel._climateSyncPending
    ? "Выполняется..." : "Синхронизировать");
  button.type = "button";
  button.disabled = Boolean(panel._busy || panel._climateSyncPending);
  setAttr(button, "aria-label", "Синхронизировать климат со стандартными целями");
  button.addEventListener("click", () => synchronizeClimate(panel));
  section.appendChild(button);
  container.appendChild(section);
}

function climateSideData(panel, rooms, devices) {
  const microRows = [];
  const avgTemp = average(rooms.map((room) => room.temp));
  if (typeof avgTemp === "number" && Number.isFinite(avgTemp)) {
    microRows.push(["Температура", temperature(avgTemp)]);
  }
  const avgHumidity = average(rooms.map((room) => room.humidity));
  if (typeof avgHumidity === "number" && Number.isFinite(avgHumidity)) {
    microRows.push(["Влажность", humidity(avgHumidity)]);
  }
  const equipment = CATEGORY_DEFINITIONS.map((category) => {
    const matches = devices.filter((device) => climateCategory(device) === category.id);
    return {
      title: category.title,
      devices: matches,
      offlineCount: matches.filter((device) => device.unavailable).length,
    };
  }).filter((entry) => entry.devices.length);
  const attention = [];
  devices.filter((device) => device.unavailable).forEach((device) => {
    attention.push({
      name: device.name || "Устройство",
      subtitle: `${device.roomName || "Без комнаты"} · нет связи`,
    });
  });
  const runtimeRooms = panel._climateRuntime && Array.isArray(panel._climateRuntime.rooms)
    ? panel._climateRuntime.rooms : [];
  runtimeRooms.forEach((runtimeRoom) => {
    (Array.isArray(runtimeRoom?.devices) ? runtimeRoom.devices : [])
      .filter((device) => device?.mode === "manual")
      .forEach((device) => attention.push({
        name: device.name || climateDeviceKindLabel(device.kind),
        subtitle: `${runtimeRoom.name || "Комната"} · исключено из контура`,
      }));
  });
  return { microRows, equipment, attention };
}

export function renderClimateOverview(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard;
  if (!dashboard) {
    container.appendChild(deps.el("div", "card empty-state", "Данные климата пока недоступны. Проверьте подключение Hausman Hub."));
    return;
  }
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices.filter((device) => {
    const domain = String(device.domain || "");
    return ["climate", "humidifier", "fan"].includes(domain)
      || ["climate", "air_quality"].includes(String(device.category || ""));
  }) : [];
  const page = deps.el("div", "climate-dashboard");
  const layout = deps.el("div", "climate-layout");
  const main = deps.el("div", "climate-main");
  main.appendChild(createHero(panel, rooms, devices, deps));
  renderClimateSynchronization(panel, main, deps);
  renderRooms(panel, main, rooms, devices, deps);
  layout.appendChild(main);
  layout.appendChild(renderClimateSide(panel, page, {
    ...climateSideData(panel, rooms, devices),
    openCategory: (entry) => requestClimateSheet(panel, entry.title, entry.devices),
  }, deps));
  page.appendChild(layout);
  if (panel._climateOverlay) {
    const keys = new Set(panel._climateOverlay.deviceKeys || []);
    const matches = devices.filter((device) => keys.has(climateDeviceKey(device)));
    openClimateSheet(panel, page, panel._climateOverlay.title, matches, deps, panel._climateOverlay.roomId);
  }
  container.appendChild(page);
}

export const CLIMATE_CATEGORY_DEFINITIONS = CATEGORY_DEFINITIONS;
