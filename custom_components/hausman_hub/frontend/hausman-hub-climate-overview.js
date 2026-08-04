/* Climate control surface shared with the tablet information architecture. */

import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.17";

const CATEGORY_DEFINITIONS = [
  { id: "conditioner", title: "Кондиционеры", icon: "snow", pattern: /кондиционер|air.?condition|smartir|\bac\b/ },
  { id: "trv", title: "Термоголовки", icon: "radiator", pattern: /термоголов|радиатор|radiator|thermostatic|\btrv\b/ },
  { id: "floor", title: "Тёплый пол", icon: "floorHeat", pattern: /т[её]пл.*пол|floor.?heat/ },
  { id: "humidifier", title: "Увлажнители", icon: "water", pattern: /увлажн|humidifier/ },
  { id: "purifier", title: "Очистители", icon: "air", pattern: /очистител|purifier|air.?clean/ },
  { id: "ventilation", title: "Вытяжки", icon: "air", pattern: /вытяж|вентил|ventilat|exhaust/ },
];

const CATEGORY_ICON_PATHS = {
  snow: "M11 2h2v3.17l2.83-1.63 1 1.73L14 6.9l2.75 1.58 2.75-1.58 1 1.73-2.75 1.59v3.56l2.75 1.59-1 1.73-2.75-1.58L14 17.1l2.83 1.63-1 1.73L13 18.83V22h-2v-3.17l-2.83 1.63-1-1.73L10 17.1l-2.75-1.58-2.75 1.58-1-1.73 2.75-1.59v-3.56L3.5 8.63l1-1.73 2.75 1.58L10 6.9 7.17 5.27l1-1.73L11 5.17z",
  air: "M4 10h10.5a2.5 2.5 0 1 0-2.45-3H10a4.5 4.5 0 1 1 4.5 5H4zm0 4h13.5a4.5 4.5 0 1 1-4.5 4.5h2a2.5 2.5 0 1 0 2.5-2.5H4zm0-8h4v2H4z",
  radiator: "M5 4h2v16H5zm4 0h2v16H9zm4 0h2v16h-2zm4 0h2v16h-2zM3 6h2v12H3zm16 0h2v12h-2z",
  floorHeat: "M3 17h18v2H3zm1-5h3v-2H4a3 3 0 0 1 0-6h4v2H4a1 1 0 0 0 0 2h3a3 3 0 0 1 0 6H4zm8 2v-2h3a1 1 0 0 0 0-2h-3a3 3 0 0 1 0-6h4v2h-4a1 1 0 0 0 0 2h3a3 3 0 0 1 0 6z",
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

function renderRooms(panel, container, rooms, devices, deps) {
  const { el, svgIcon, setAttr } = deps;
  const section = el("section", "climate-rooms-section");
  const head = el("div", "climate-section-heading");
  const copy = el("div");
  copy.appendChild(el("h3", null, "Комнаты и цели"));
  copy.appendChild(el("p", null, "Показатели и индивидуальные цели читаются одним взглядом"));
  head.appendChild(copy);
  section.appendChild(head);
  if (!rooms.length) {
    section.appendChild(el("div", "climate-sheet-empty", "Комнаты пока не переданы Home Assistant."));
    container.appendChild(section);
    return;
  }
  const grid = el("div", "climate-room-grid");
  rooms.forEach((room) => {
    const matches = roomDevices(room, devices);
    const card = el("button", "climate-room-card");
    card.type = "button";
    setAttr(card, "aria-label", `Открыть климат комнаты ${room.name}`);
    const title = el("span", "climate-room-title");
    const roomIcon = el("span", "climate-room-icon");
    roomIcon.appendChild(svgIcon("home"));
    title.appendChild(roomIcon);
    const roomCopy = el("span");
    roomCopy.appendChild(el("strong", null, room.name || "Комната"));
    roomCopy.appendChild(el("small", null, matches.some(deviceIsActive) ? "Климат работает" : "Поддержание комфорта"));
    title.appendChild(roomCopy);
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
    card.appendChild(el("span", "climate-room-open", "Открыть устройства ›"));
    card.addEventListener("click", () => requestClimateSheet(panel, room.name || "Комната", matches));
    grid.appendChild(card);
  });
  section.appendChild(grid);
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
