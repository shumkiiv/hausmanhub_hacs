import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.131";
import { lightingSideIcon, openLightingTurnOffConfirm, renderLightingSide } from "./hausman-hub-lighting-side.js?v=1.52.131";
import { enhanceAppendedModal } from "./hausman-hub-modal.js?v=1.52.131";
import { roomIconName, roomSvgIcon } from "./hausman-hub-room-icons.js?v=1.52.131";

const LIGHTING_EXCLUSIONS = [
  "ambilight", "глазок", "домофон", "пульт", "очистител", "аквариум", "aquarium",
];

function normalized(value) {
  return String(value || "").trim().toLocaleLowerCase("ru");
}

function isLightingDevice(device) {
  const domain = normalized(device && device.domain);
  const category = normalized(device && device.category);
  const identity = normalized([device && device.name, device && device.model, device && device.stateLabel].filter(Boolean).join(" "));
  if (LIGHTING_EXCLUSIONS.some((marker) => identity.includes(marker))) return false;
  if (domain === "light") return true;
  const hasLightingIdentity = /(свет|ламп|люстр|подсвет|выключател|реле|ночник|light)/.test(identity);
  return hasLightingIdentity && (
    domain === "switch" || ["light", "lights", "lighting"].includes(category)
  );
}

function deviceIsActive(device) {
  return !device.unavailable && device.state !== "unavailable" && (
    device.active === true || (
      typeof device.active !== "boolean"
      && !["off", "idle", "standby", "unknown", "unavailable"].includes(device.state)
    )
  );
}

function deviceKey(device) {
  return device.physicalId || device.id || device.entityId;
}

function roomFor(device, rooms) {
  const byId = rooms.find((room) => room.id && room.id === device.roomId);
  if (byId) return byId;
  const roomName = normalized(device.roomName);
  return rooms.find((room) => normalized(room.name) === roomName) || null;
}

function roomNameFor(device, rooms) {
  return roomFor(device, rooms)?.name || device.roomName || "Без комнаты";
}

function deviceCountWord(count) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return "устройств";
  if (count % 10 === 1) return "устройство";
  if (count % 10 >= 2 && count % 10 <= 4) return "устройства";
  return "устройств";
}

function createLightingHero(panel, rooms, devices, deps) {
  const active = devices.filter(deviceIsActive).length;
  const unavailable = devices.filter((device) => device.unavailable || device.state === "unavailable").length;
  const roomNames = new Set(devices.map((device) => roomNameFor(device, rooms)).filter((name) => name !== "Без комнаты"));
  const activeRooms = new Set(devices.filter(deviceIsActive).map((device) => roomNameFor(device, rooms)).filter((name) => name !== "Без комнаты"));
  return createLibraryHero(panel, {
    eyebrow: "ОСВЕЩЕНИЕ ДОМА",
    title: "Освещение дома",
    subtitle: "Управляйте комнатами и каждым физическим устройством отдельно",
    warning: unavailable > 0,
    facts: [
      { label: "Комнаты со светом", value: `${activeRooms.size} из ${roomNames.size}` },
      { label: "Устройства включены", value: `${active} из ${devices.length}` },
      { label: "Без связи", value: unavailable, warning: unavailable > 0 },
    ],
  }, deps);
}

function closeSheet(panel, container) {
  panel._lightingRoomOverlay = null;
  const sheet = container.querySelector && container.querySelector(".lighting-room-sheet-backdrop");
  if (sheet && typeof sheet.remove === "function") sheet.remove();
}

function openRoomSheet(panel, container, roomName, devices, deps) {
  closeSheet(panel, container);
  panel._lightingRoomOverlay = { roomName, deviceKeys: devices.map(deviceKey) };
  const { el, setAttr } = deps;
  const backdrop = el("div", "lighting-room-sheet-backdrop");
  setAttr(backdrop, "role", "presentation");
  const sheet = el("section", "lighting-room-sheet");
  setAttr(sheet, "role", "dialog");
  setAttr(sheet, "aria-modal", "true");
  setAttr(sheet, "aria-label", `Освещение комнаты ${roomName}`);
  const close = el("button", "lighting-room-sheet-close", "×");
  close.type = "button";
  setAttr(close, "aria-label", "Закрыть устройства комнаты");
  close.addEventListener("click", () => closeSheet(panel, container));
  sheet.appendChild(close);
  const body = el("div", "lighting-room-sheet-body");
  body.appendChild(el("span", "lighting-room-sheet-eyebrow", "ОСВЕЩЕНИЕ КОМНАТЫ"));
  body.appendChild(el("h2", null, roomName));
  body.appendChild(el("p", "muted", "Одно реальное устройство показано одной карточкой. Клавиши и функции доступны внутри."));
  const grid = el("div", "lighting-room-sheet-grid");
  if (!devices.length) grid.appendChild(el("div", "empty-state", "Устройства освещения в комнате не найдены."));
  devices.forEach((device) => grid.appendChild(panel._deviceInventoryCard(device)));
  body.appendChild(grid);
  sheet.appendChild(body);
  backdrop.appendChild(sheet);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) closeSheet(panel, container);
  });
  container.appendChild(backdrop);
  enhanceAppendedModal(backdrop, sheet, () => closeSheet(panel, container));
}

function lightingRoomsGrouped(rooms, devices) {
  const byRoom = new Map();
  devices.forEach((device) => {
    const name = roomNameFor(device, rooms);
    if (!byRoom.has(name)) byRoom.set(name, []);
    byRoom.get(name).push(device);
  });
  return [...byRoom.entries()].sort(([left], [right]) => left.localeCompare(right, "ru"));
}

function lightingRoomMatches(name, roomDevices, query, filter) {
  if (query && !normalized(name).includes(query)) return false;
  const active = roomDevices.filter(deviceIsActive).length;
  if (filter === "on") return active > 0;
  if (filter === "off") return active === 0;
  if (filter === "offline") {
    return roomDevices.some((device) => device.unavailable || device.state === "unavailable");
  }
  return true;
}

function renderLightingRoomCard(panel, page, rooms, name, roomDevices, deps) {
  const { el, svgIcon, setAttr } = deps;
  const activeDevices = roomDevices.filter(deviceIsActive);
  const unavailable = roomDevices.filter((device) => device.unavailable || device.state === "unavailable").length;
  const card = el("button", `lighting-room-card${activeDevices.length ? " is-active" : ""}${unavailable ? " has-warning" : ""}`);
  card.type = "button";
  setAttr(card, "aria-label", `Открыть устройства и линии комнаты ${name}`);
  const head = el("span", "lighting-room-head");
  const icon = el("span", "lighting-room-icon");
  const room = rooms.find((candidate) => candidate.name === name) || { name };
  icon.appendChild(roomSvgIcon(roomIconName(room)));
  head.appendChild(icon);
  const cardCopy = el("span", "lighting-room-copy");
  cardCopy.appendChild(el("strong", null, name));
  cardCopy.appendChild(el("span", `lighting-room-status${activeDevices.length ? " is-on" : ""}`,
    activeDevices.length ? "Свет включён" : "Свет выключен"));
  head.appendChild(cardCopy);
  const chevron = el("span", "lighting-room-chevron");
  chevron.appendChild(svgIcon("chevron-right"));
  head.appendChild(chevron);
  card.appendChild(head);
  card.appendChild(el("span", "lighting-room-pill",
    `${roomDevices.length} физ. устройств${unavailable ? ` · ${unavailable} без связи` : ""}`));
  const footer = el("span", "lighting-room-footer");
  footer.appendChild(el("span", "lighting-room-open", "Открыть устройства и линии"));
  if (activeDevices.length) {
    const power = el("span", "lighting-room-power");
    setAttr(power, "role", "button");
    setAttr(power, "tabindex", "0");
    setAttr(power, "aria-label", `Выключить весь свет в комнате ${name}`);
    power.appendChild(lightingSideIcon("power"));
    const turnOff = (event) => {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      if (event && typeof event.stopPropagation === "function") event.stopPropagation();
      openLightingTurnOffConfirm(panel, page, `Свет в комнате «${name}»`, activeDevices, deps);
    };
    power.addEventListener("click", turnOff);
    power.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") turnOff(event);
    });
    footer.appendChild(power);
  }
  card.appendChild(footer);
  card.addEventListener("click", () => openRoomSheet(panel, page, name, roomDevices, deps));
  return card;
}

const ROOM_FILTERS = [
  ["all", "Все комнаты"],
  ["on", "Свет включён"],
  ["off", "Свет выключен"],
  ["offline", "Без связи"],
];

function renderLightingRooms(panel, sectionHost, page, rooms, devices, deps) {
  const { el, svgIcon, setAttr } = deps;
  const section = el("section", "lighting-room-section");
  const byRoom = lightingRoomsGrouped(rooms, devices);
  const heading = el("div", "lighting-section-heading");
  const copy = el("div");
  copy.appendChild(el("h3", null, "Комнаты"));
  const counter = el("p", null, "");
  copy.appendChild(counter);
  heading.appendChild(copy);
  const allDevices = el("button", "overview-canon-link lighting-section-link");
  allDevices.type = "button";
  allDevices.appendChild(el("span", null, "Все устройства"));
  allDevices.appendChild(svgIcon("chevron-right"));
  allDevices.addEventListener("click", () => {
    const target = page.querySelector && page.querySelector(".lighting-device-section");
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  heading.appendChild(allDevices);
  section.appendChild(heading);
  const grid = el("div", "lighting-room-grid");
  const applyFilters = () => {
    const query = normalized(panel._lightingRoomQuery || "");
    const filter = panel._lightingRoomFilter || "all";
    grid.innerHTML = "";
    let shown = 0;
    byRoom.forEach(([name, roomDevices]) => {
      if (!lightingRoomMatches(name, roomDevices, query, filter)) return;
      shown += 1;
      grid.appendChild(renderLightingRoomCard(panel, page, rooms, name, roomDevices, deps));
    });
    if (!shown) grid.appendChild(el("div", "empty-state", "Комнаты по этому фильтру не найдены."));
    counter.textContent = `Показано ${shown} из ${byRoom.length} · карточка открывает все устройства комнаты`;
  };
  const toolbar = el("div", "lighting-room-toolbar");
  const search = el("label", "lighting-room-search");
  search.appendChild(lightingSideIcon("search"));
  const input = el("input");
  input.type = "search";
  input.value = panel._lightingRoomQuery || "";
  setAttr(input, "placeholder", "Найти комнату");
  setAttr(input, "aria-label", "Найти комнату");
  input.addEventListener("input", () => {
    panel._lightingRoomQuery = input.value;
    applyFilters();
  });
  search.appendChild(input);
  toolbar.appendChild(search);
  const chips = el("div", "lighting-room-chips");
  const chipButtons = new Map();
  ROOM_FILTERS.forEach(([id, label]) => {
    const selected = (panel._lightingRoomFilter || "all") === id;
    const chip = el("button", `lighting-room-chip${selected ? " is-active" : ""}`, label);
    chip.type = "button";
    setAttr(chip, "aria-pressed", selected ? "true" : "false");
    chip.addEventListener("click", () => {
      panel._lightingRoomFilter = id;
      chipButtons.forEach((node, key) => {
        node.classList.toggle("is-active", key === id);
        setAttr(node, "aria-pressed", key === id ? "true" : "false");
      });
      applyFilters();
    });
    chipButtons.set(id, chip);
    chips.appendChild(chip);
  });
  toolbar.appendChild(chips);
  section.appendChild(toolbar);
  applyFilters();
  section.appendChild(grid);
  sectionHost.appendChild(section);
}

function renderLightingDevices(panel, container, rooms, devices, deps) {
  const { el } = deps;
  const section = el("section", "lighting-device-section");
  const heading = el("div", "lighting-section-heading");
  const copy = el("div");
  copy.appendChild(el("h3", null, "Устройства освещения"));
  copy.appendChild(el("p", null, "Физические выключатели, реле и светильники без дублирования сущностей"));
  heading.appendChild(copy);
  section.appendChild(heading);
  const byRoom = new Map();
  devices.forEach((device) => {
    const room = roomNameFor(device, rooms);
    if (!byRoom.has(room)) byRoom.set(room, []);
    byRoom.get(room).push(device);
  });
  [...byRoom.entries()].sort(([left], [right]) => left.localeCompare(right, "ru")).forEach(([room, roomDevices]) => {
    const group = el("div", "lighting-device-room");
    const title = el("div", "lighting-device-room-title");
    title.appendChild(el("h4", null, room));
    title.appendChild(el("span", null, `${roomDevices.length} ${deviceCountWord(roomDevices.length)}`));
    group.appendChild(title);
    const grid = el("div", "inventory-device-grid");
    roomDevices.forEach((device) => grid.appendChild(panel._deviceInventoryCard(device)));
    group.appendChild(grid);
    section.appendChild(group);
  });
  container.appendChild(section);
}

export function renderLightingOverview(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard;
  if (!dashboard) {
    const empty = deps.el("section", "card empty-state lighting-empty-state");
    empty.appendChild(deps.el("h2", null, "Освещение"));
    empty.appendChild(deps.el("p", null, "Данные освещения пока недоступны. Проверьте подключение Hausman Hub."));
    container.appendChild(empty);
    return;
  }
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const source = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const unique = new Map();
  source.filter(isLightingDevice).forEach((device) => unique.set(deviceKey(device), device));
  const devices = [...unique.values()];
  const page = deps.el("div", "lighting-dashboard");
  const layout = deps.el("div", "lighting-layout");
  const main = deps.el("div", "lighting-main");
  main.appendChild(createLightingHero(panel, rooms, devices, deps));
  if (!devices.length) {
    main.appendChild(deps.el("div", "card empty-state", "Физические устройства освещения пока не найдены."));
  } else {
    renderLightingRooms(panel, main, page, rooms, devices, deps);
    renderLightingDevices(panel, main, rooms, devices, deps);
  }
  layout.appendChild(main);
  if (devices.length) {
    layout.appendChild(renderLightingSide(panel, page, {
      devices,
      isActive: deviceIsActive,
      roomName: (device) => roomNameFor(device, rooms),
    }, deps));
  }
  page.appendChild(layout);
  if (panel._lightingRoomOverlay) {
    const keys = new Set(panel._lightingRoomOverlay.deviceKeys || []);
    openRoomSheet(panel, page, panel._lightingRoomOverlay.roomName, devices.filter((device) => keys.has(deviceKey(device))), deps);
  }
  container.appendChild(page);
}
