/* Climate control surface shared with the tablet information architecture. */

import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.84";
import { enhanceAppendedModal } from "./hausman-hub-modal.js?v=1.52.84";
import { roomIconName, roomSvgIcon } from "./hausman-hub-room-icons.js?v=1.52.84";

const CLIMATE_ACTION_API = "hausman_hub/v1/climate/actions";

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
    const receipt = await panel._hass.callApi("POST", CLIMATE_ACTION_API, {
      contract: { name: "hausman-hub-climate-action-request", version: 1 },
      request_id: `hacs.climate.sync.${Date.now().toString(36)}`,
      expected_state_revision: panel._climateRuntime.state_revision,
      action: "synchronize_home",
      room_id: null,
      parameters: {},
    });
    if (!receipt || receipt.confirmed !== true) throw new Error("climate synchronization was not confirmed");
    panel._notice = "Климат синхронизирован.";
    panel._error = false;
    await panel._load();
    return true;
  } catch (error) {
    panel._notice = "Синхронизация не подтверждена. Обновите состояние перед повтором.";
    panel._error = true;
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
    const receipt = await panel._hass.callApi("POST", CLIMATE_ACTION_API, {
      contract: { name: "hausman-hub-climate-action-request", version: 1 },
      request_id: `hacs.climate.${Date.now().toString(36)}`,
      expected_state_revision: panel._climateRuntime.state_revision,
      action: deviceId ? "set_device_mode" : "set_room_mode",
      room_id: roomId,
      parameters: deviceId
        ? { device_id: deviceId, mode: manual ? "manual" : "automatic" }
        : { mode: manual ? "manual" : "automatic" },
    });
    if (!receipt || receipt.confirmed !== true) throw new Error("climate mode was not confirmed");
    panel._notice = manual ? "Ручной режим включён." : "Автоматическое управление восстановлено.";
    panel._error = false;
    await panel._load();
    return true;
  } catch (error) {
    panel._notice = "Режим не изменён. Обновите состояние и повторите действие.";
    panel._error = true;
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
    const receipt = await panel._hass.callApi("POST", CLIMATE_ACTION_API, {
      contract: { name: "hausman-hub-climate-action-request", version: 1 },
      request_id: `hacs.climate.${Date.now().toString(36)}`,
      expected_state_revision: panel._climateRuntime.state_revision,
      action,
      room_id: roomId,
      parameters: { [parameter]: value },
    });
    if (!receipt || receipt.confirmed !== true) throw new Error("climate target was not confirmed");
    panel._notice = action === "set_room_target" ? "Целевая температура подтверждена." : "Целевая влажность подтверждена.";
    panel._error = false;
    await panel._load();
    return true;
  } catch (error) {
    panel._notice = "Цель не изменена. Обновите состояние и повторите действие.";
    panel._error = true;
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
    const receipt = await panel._hass.callApi("POST", CLIMATE_ACTION_API, {
      contract: { name: "hausman-hub-climate-action-request", version: 1 },
      request_id: `hacs.climate.home.${Date.now().toString(36)}`,
      expected_state_revision: panel._climateRuntime.state_revision,
      action: "set_home_targets",
      room_id: null,
      parameters: { target_temperature: targetTemperature },
    });
    if (!receipt || receipt.confirmed !== true) throw new Error("home climate target was not confirmed");
    panel._notice = "Общая климатическая цель подтверждена.";
    await panel._load();
    return true;
  } catch (error) {
    panel._notice = "Общая цель не изменена. Обновите состояние и повторите действие.";
    panel._error = true;
    return false;
  } finally {
    panel._busy = false;
    panel._climateModePendingKey = null;
    panel._render();
  }
}

export function renderHomeTargetCard(panel, dashboard, deps) {
  const control = panel._climateRuntime?.home_control || {};
  const allowed = Array.isArray(control.allowed_actions) ? control.allowed_actions : [];
  const raw = dashboard.summary && dashboard.summary.targetTemp;
  const target = raw !== null && raw !== undefined && raw !== "" && Number.isFinite(Number(raw))
    ? Number(raw) : null;
  const card = deps.el("section", "overview-canon-primary-card is-target");
  card.appendChild(deps.el("span", "overview-canon-label", "Цель климата"));
  card.appendChild(deps.el("strong", "overview-canon-value", target === null
    ? "Нет данных" : `${target.toFixed(1).replace(".0", "").replace(".", ",")} °C`));
  const controls = deps.el("div", "overview-canon-target-controls");
  [["−", -0.5, "Понизить общую цель на 0,5 °C"], ["+", 0.5, "Повысить общую цель на 0,5 °C"]].forEach(([label, delta, aria]) => {
    const button = deps.el("button", "overview-canon-target-step", label);
    button.type = "button";
    button.disabled = panel._busy || !allowed.includes("set_home_targets") || target === null;
    deps.setAttr(button, "aria-label", aria);
    button.addEventListener("click", () => target !== null && setClimateHomeTarget(panel, Math.round((target + delta) * 2) / 2));
    controls.appendChild(button);
  });
  card.appendChild(controls);
  const footer = deps.el("div", "overview-canon-target-footer");
  const details = deps.el("button", "overview-canon-link", "Настроить");
  details.type = "button";
  details.addEventListener("click", () => panel._activateSection("climate"));
  footer.appendChild(details);
  if (allowed.includes("synchronize_home")) {
    const sync = deps.el("button", "overview-canon-link", panel._climateSyncPending ? "Синхронизация..." : "Синхронизировать");
    sync.type = "button";
    sync.disabled = panel._busy;
    sync.addEventListener("click", () => synchronizeClimate(panel));
    footer.appendChild(sync);
  }
  card.appendChild(footer);
  return card;
}

const CATEGORY_DEFINITIONS = [
  { id: "conditioner", title: "Кондиционеры", icon: "snow", pattern: /кондиционер|air.?condition|smartir|\bac\b/ },
  { id: "trv", title: "Термоголовки", icon: "thermometer", pattern: /термоголов|радиатор|radiator|thermostatic|\btrv\b/ },
  { id: "floor", title: "Тёплый пол", icon: "thermometer", pattern: /т[её]пл.*пол|floor.?heat/ },
  { id: "humidifier", title: "Увлажнители", icon: "water", pattern: /увлажн|humidifier/ },
  { id: "purifier", title: "Очистители", icon: "air", pattern: /очистител|purifier|air.?clean/ },
  { id: "ventilation", title: "Вытяжки", icon: "air", pattern: /вытяж|вентил|ventilat|exhaust/ },
];

const CATEGORY_ICON_PATHS = {
  snow: "M11 2h2v3.17l2.83-1.63 1 1.73L14 6.9l2.75 1.58 2.75-1.58 1 1.73-2.75 1.59v3.56l2.75 1.59-1 1.73-2.75-1.58L14 17.1l2.83 1.63-1 1.73L13 18.83V22h-2v-3.17l-2.83 1.63-1-1.73L10 17.1l-2.75-1.58-2.75 1.58-1-1.73 2.75-1.59v-3.56L3.5 8.63l1-1.73 2.75 1.58L10 6.9 7.17 5.27l1-1.73L11 5.17z",
  air: "M4 10h10.5a2.5 2.5 0 1 0-2.45-3H10a4.5 4.5 0 1 1 4.5 5H4zm0 4h13.5a4.5 4.5 0 1 1-4.5 4.5h2a2.5 2.5 0 1 0 2.5-2.5H4zm0-8h4v2H4z",
  sync: "M12 4V1L8 5l4 4V6a6 6 0 0 1 5.65 4h2.1A8 8 0 0 0 12 4zm-5.65 6H4.25A8 8 0 0 0 12 20v3l4-4-4-4v3a6 6 0 0 1-5.65-8z",
};

function climateIcon(name, deps) {
  if (!CATEGORY_ICON_PATHS[name]) return deps.svgIcon(name);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  deps.setAttr(svg, "viewBox", "0 0 24 24");
  deps.setAttr(svg, "aria-hidden", "true");
  deps.setAttr(svg, "class", "icon");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  deps.setAttr(path, "d", CATEGORY_ICON_PATHS[name]);
  deps.setAttr(path, "fill", "currentColor");
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

function deviceIsActive(device) {
  if (!device || device.unavailable) return false;
  if (typeof device.active === "boolean") return device.active;
  const raw = normalized(device.state);
  return Boolean(raw) && !["off", "idle", "unknown", "unavailable"].includes(raw);
}

function createHero(panel, rooms, devices, deps) {
  const unavailable = devices.filter((device) => device.unavailable).length;
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

function requestClimateSheet(panel, title, devices) {
  panel._climateOverlay = {
    title,
    deviceKeys: devices.map(climateDeviceKey).filter(Boolean),
    selectedKey: null,
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

function openClimateSheet(panel, container, title, devices, deps) {
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

function renderCategories(panel, container, devices, deps) {
  const { el, svgIcon, setAttr } = deps;
  const section = el("section", "climate-category-section");
  const heading = el("div", "climate-section-heading");
  const copy = el("div");
  copy.appendChild(el("h3", null, "Обзор климата"));
  copy.appendChild(el("p", null, "Откройте группу, затем выберите конкретное устройство"));
  heading.appendChild(copy);
  section.appendChild(heading);
  const grid = el("div", "climate-category-grid");
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
      ? `${matches.length} ${panel._deviceCountWord(matches.length)}${matches.some((device) => device.unavailable) ? " · есть без связи" : ""}`
      : "Нет устройств"));
    card.appendChild(cardCopy);
    card.appendChild(el("span", "climate-category-chevron", "›"));
    card.addEventListener("click", () => requestClimateSheet(panel, category.title, matches));
    grid.appendChild(card);
  });
  section.appendChild(grid);
  container.appendChild(section);
}

function roomDevices(room, devices) {
  return devices.filter((device) => device.roomId === room.id || device.roomName === room.name);
}

function targetSummary(room) {
  const values = [];
  if (typeof room.targetTemp === "number" && Number.isFinite(room.targetTemp)) {
    values.push(temperature(room.targetTemp));
  }
  if (typeof room.targetHumidity === "number" && Number.isFinite(room.targetHumidity)) {
    values.push(humidity(room.targetHumidity));
  }
  return values.length ? values.join(" · ") : "Цель не задана";
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

function renderRooms(panel, container, rooms, devices, deps) {
  const { el, svgIcon, setAttr } = deps;
  const section = el("section", "climate-rooms-section");
  const head = el("div", "climate-section-heading");
  const copy = el("div");
  copy.appendChild(el("h3", null, "Комнаты и цели"));
  copy.appendChild(el("p", null, "Выберите комнату — показатели и цель видны сразу"));
  head.appendChild(copy);
  section.appendChild(head);
  if (!rooms.length) {
    section.appendChild(el("div", "climate-sheet-empty", "Комнаты пока не переданы Home Assistant."));
    container.appendChild(section);
    return;
  }
  if (!panel._climateRoomUi) panel._climateRoomUi = { roomId: null, filter: "all" };
  const filter = panel._climateRoomUi.filter || "all";
  const visibleRooms = rooms.filter((room) => {
    const manual = roomHasManualControl(panel, room);
    return filter === "manual" ? manual : filter === "automatic" ? !manual : true;
  });
  const selectedRoom = visibleRooms.find((room) => room.id === panel._climateRoomUi.roomId) || visibleRooms[0];
  const modeTabs = el("div", "climate-mode-tabs");
  setAttr(modeTabs, "role", "tablist");
  setAttr(modeTabs, "aria-label", "Режим климатического контура");
  [["all", "Все"], ["automatic", "Автоматически"], ["manual", "Ручной режим"]].forEach(([id, label]) => {
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
  section.appendChild(modeTabs);
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
  if (!selectedRoom) {
    section.appendChild(el("div", "climate-sheet-empty", "В этой вкладке пока нет комнат."));
    container.appendChild(section);
    return;
  }
  panel._climateRoomUi.roomId = selectedRoom.id;
  const tabs = el("div", "climate-room-tabs");
  setAttr(tabs, "role", "tablist");
  setAttr(tabs, "aria-label", "Комнаты климата");
  const focus = el("div", "climate-room-focus");
  const renderFocus = (room) => {
    focus.innerHTML = "";
    const matches = roomDevices(room, devices);
    const managedRoom = runtimeRoom(panel, room.id);
    const manual = managedRoom && managedRoom.mode === "manual";
    const contourDevices = managedRoom && Array.isArray(managedRoom.devices) ? managedRoom.devices : [];
    const hasManualDevice = contourDevices.some((device) => device.mode === "manual");
    const manualState = manual || hasManualDevice;
    const criticalSensorExcluded = contourDevices.some((device) =>
      device.mode === "manual" && ["temperature_sensor", "humidity_sensor"].includes(device.kind));
    const card = el("div", `climate-room-card is-focus${manual ? " is-manual" : ""}`);
    const title = el("span", "climate-room-title");
    const roomIcon = el("span", "climate-room-icon");
    roomIcon.appendChild(roomSvgIcon(roomIconName(room)));
    title.appendChild(roomIcon);
    const roomCopy = el("span");
    roomCopy.appendChild(el("strong", null, room.name || "Комната"));
    roomCopy.appendChild(el("small", null, manual ? "Полный ручной режим" : (matches.some(deviceIsActive) ? "Климат работает" : "Поддержание комфорта")));
    title.appendChild(roomCopy);
    if (manualState) {
      const modeBadge = el("span", "climate-manual-indicator");
      modeBadge.appendChild(svgIcon("manual"));
      modeBadge.appendChild(el("span", null, manual ? "Ручной режим" : "Есть ручное устройство"));
      setAttr(modeBadge, "aria-label", manual ? "Комната в ручном режиме" : "В комнате есть исключённое из автоматики устройство");
      title.appendChild(modeBadge);
    }
    title.appendChild(el("b", null, temperature(room.temp)));
    card.appendChild(title);
    const facts = el("span", "climate-room-facts");
    [["Влажность", humidity(room.humidity)], ["Цель", targetSummary(room)], ["Устройства", String(matches.length)]].forEach(([label, value]) => {
      const fact = el("span");
      fact.appendChild(el("small", null, label));
      fact.appendChild(el("strong", null, value));
      facts.appendChild(fact);
    });
    card.appendChild(facts);
    const targetControls = managedRoom && managedRoom.control;
    const allowedTargets = targetControls && Array.isArray(targetControls.allowed_actions)
      ? targetControls.allowed_actions : [];
    const inputs = targetControls && targetControls.action_inputs || {};
    const addTargetControl = (action, parameter, label, current, fallback) => {
      if (!allowedTargets.includes(action)) return;
      const spec = inputs[action] && inputs[action][parameter];
      if (!spec || typeof current !== "number") return;
      const field = el("label", "climate-target-field", label);
      const input = el("input");
      input.type = "number";
      input.value = String(current);
      input.min = String(spec.minimum);
      input.max = String(spec.maximum);
      input.step = String(spec.step);
      input.disabled = Boolean(panel._climateModePendingKey);
      field.appendChild(input);
      const save = el("button", "secondary", panel._climateModePendingKey === `${room.id}:${action}` ? "Сохраняем..." : "Установить");
      save.type = "button";
      save.disabled = Boolean(panel._climateModePendingKey);
      save.addEventListener("click", (event) => {
        event.stopPropagation?.();
        const next = Number(input.value);
        if (!Number.isFinite(next) || next < spec.minimum || next > spec.maximum || ((next - spec.minimum) / spec.step) % 1 !== 0) {
          panel._notice = `Введите ${label.toLocaleLowerCase("ru")} от ${spec.minimum} до ${spec.maximum}${fallback}.`;
          panel._error = true;
          panel._render();
          return;
        }
        setClimateRoomTarget(panel, room.id, action, parameter, next);
      });
      field.appendChild(save);
      card.appendChild(field);
    };
    addTargetControl("set_room_target", "target_temperature", "Целевая температура, °C", managedRoom?.target_temperature, " °C");
    addTargetControl("set_room_humidity_target", "target_humidity", "Целевая влажность, %", managedRoom?.target_humidity, " %");
    const actions = el("div", "climate-room-actions");
    const open = el("button", "climate-room-open", "Открыть устройства ›");
    open.type = "button";
    open.addEventListener("click", (event) => {
      event.stopPropagation?.();
      requestClimateSheet(panel, room.name || "Комната", matches);
    });
    actions.appendChild(open);
    if (managedRoom && managedRoom.control && Array.isArray(managedRoom.control.allowed_actions)
      && managedRoom.control.allowed_actions.includes("set_room_mode")) {
      const roomMode = el("button", `climate-manual-toggle${manual ? " is-manual" : ""}`,
        panel._climateModePendingKey === `${room.id}:room` ? "Сохраняем..."
          : criticalSensorExcluded ? "Сначала верните датчик"
            : (manual ? "Вернуть автоматику" : "Вся комната вручную"));
      roomMode.type = "button";
      roomMode.disabled = Boolean(panel._climateModePendingKey) || criticalSensorExcluded;
      roomMode.addEventListener("click", (event) => {
        event.stopPropagation?.();
        panel._setClimateManual(room.id, null, !manual);
      });
      actions.appendChild(roomMode);
    }
    card.appendChild(actions);
    if (criticalSensorExcluded) {
      card.appendChild(el("p", "climate-manual-warning",
        "Комната исключена из автоматики: критический датчик работает в ручном режиме."));
    }
    if (contourDevices.length) {
      const list = el("div", "climate-contour-devices");
      contourDevices.forEach((device) => {
        const deviceManual = device.mode === "manual";
        const row = el("div", `climate-contour-device${deviceManual ? " is-manual" : ""}`);
        const copy = el("span");
        copy.appendChild(el("strong", null, device.name || climateDeviceKindLabel(device.kind)));
        copy.appendChild(el("small", null, `${climateDeviceKindLabel(device.kind)} · ${deviceManual ? "ручной режим" : "автоматика"}`));
        row.appendChild(copy);
        const control = device.control;
        if (control && Array.isArray(control.allowed_actions) && control.allowed_actions.includes("set_device_mode")) {
          const toggle = el("button", `climate-device-mode${deviceManual ? " is-manual" : ""}`,
            panel._climateModePendingKey === `${room.id}:${device.id}` ? "Сохраняем..." : (deviceManual ? "Вернуть" : "Исключить"));
          toggle.type = "button";
          toggle.disabled = Boolean(panel._climateModePendingKey);
          toggle.addEventListener("click", (event) => {
            event.stopPropagation?.();
            const critical = ["temperature_sensor", "humidity_sensor"].includes(device.kind);
            if (critical && !deviceManual && !window.confirm(
              `Исключить «${device.name}»? Комната «${room.name}» полностью перейдёт в ручной режим до возврата датчика.`
            )) return;
            panel._setClimateManual(room.id, device.id, !deviceManual);
          });
          row.appendChild(toggle);
        }
        list.appendChild(row);
      });
      card.appendChild(list);
    }
    card.addEventListener("click", () => requestClimateSheet(panel, room.name || "Комната", matches));
    focus.appendChild(card);
  };
  const chips = [];
  visibleRooms.forEach((room) => {
    const active = room.id === selectedRoom.id;
    const chip = el("button", `climate-room-tab${active ? " is-active" : ""}`);
    chip.type = "button";
    setAttr(chip, "role", "tab");
    setAttr(chip, "aria-selected", active ? "true" : "false");
    chip.appendChild(roomSvgIcon(roomIconName(room)));
    chip.appendChild(el("span", null, room.name || "Комната"));
    chip.addEventListener("click", () => {
      panel._climateRoomUi.roomId = room.id;
      chips.forEach((candidate) => {
        const on = candidate === chip;
        candidate.classList.toggle("is-active", on);
        setAttr(candidate, "aria-selected", on ? "true" : "false");
      });
      renderFocus(room);
    });
    chips.push(chip);
    tabs.appendChild(chip);
  });
  section.appendChild(tabs);
  section.appendChild(focus);
  renderFocus(selectedRoom);
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

export function renderClimateOverview(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard;
  if (!dashboard) {
    container.appendChild(deps.el("div", "card empty-state", "Данные климата пока недоступны. Проверьте подключение HausmanHub."));
    return;
  }
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices.filter((device) => {
    const domain = String(device.domain || "");
    return ["climate", "humidifier", "fan"].includes(domain)
      || ["climate", "air_quality"].includes(String(device.category || ""));
  }) : [];
  const page = deps.el("div", "climate-dashboard");
  page.appendChild(createHero(panel, rooms, devices, deps));
  renderClimateSynchronization(panel, page, deps);
  renderCategories(panel, page, devices, deps);
  renderRooms(panel, page, rooms, devices, deps);
  if (panel._climateOverlay) {
    const keys = new Set(panel._climateOverlay.deviceKeys || []);
    const matches = devices.filter((device) => keys.has(climateDeviceKey(device)));
    openClimateSheet(panel, page, panel._climateOverlay.title, matches, deps);
  }
  container.appendChild(page);
}

export const CLIMATE_CATEGORY_DEFINITIONS = CATEGORY_DEFINITIONS;
