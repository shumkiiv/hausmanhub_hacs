import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.192";
import { enhanceAppendedModal } from "./hausman-hub-modal.js?v=1.52.192";
import { roomIconName, roomSvgIcon } from "./hausman-hub-room-icons.js?v=1.52.192";
import { renderMediaDeviceCard } from "./hausman-hub-media-device.js?v=1.52.192";
import { renderMediaSide } from "./hausman-hub-media-side.js?v=1.52.192";

function mediaOverviewNormalized(value) { return String(value || "").trim().toLocaleLowerCase("ru"); }
function mediaOverviewKey(device) { return device.physicalId || device.id || device.entityId; }
function mediaOverviewUnavailable(device) { return Boolean(device.unavailable || device.state === "unavailable"); }
function mediaOverviewPlaying(device) { return !mediaOverviewUnavailable(device) && String(device.state || "").toLowerCase() === "playing"; }
function mediaOverviewIsDevice(device) {
  return String(device && device.domain || "") === "media_player"
    || String(device && device.category || "") === "media";
}
function mediaOverviewIsTv(device) {
  return /(?:\btv\b|телевиз|television|smart[ _-]?tv|pus\d|oled|qled)/i.test([
    device.name, device.model, device.entityId,
  ].filter(Boolean).join(" "));
}
function mediaOverviewRoom(device, rooms) {
  return rooms.find((room) => room.id && room.id === device.roomId)
    || rooms.find((room) => mediaOverviewNormalized(room.name) === mediaOverviewNormalized(device.roomName))
    || null;
}
function mediaOverviewDeviceWord(count) {
  if (count % 100 >= 11 && count % 100 <= 14) return "устройств";
  if (count % 10 === 1) return "устройство";
  if (count % 10 >= 2 && count % 10 <= 4) return "устройства";
  return "устройств";
}

const MEDIA_SEARCH_ICON_PATH = "M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z";

function mediaSearchIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", MEDIA_SEARCH_ICON_PATH);
  path.setAttribute("fill", "currentColor");
  svg.appendChild(path);
  return svg;
}

function refreshMediaOverlay(panel) {
  if (panel._sectionRenderKeys) panel._sectionRenderKeys.media = null;
  if (typeof panel._render === "function") panel._render();
}

function requestMediaZone(panel, title, devices) {
  panel._mediaOverlay = {
    title,
    deviceKeys: devices.map(mediaOverviewKey).filter(Boolean),
  };
  refreshMediaOverlay(panel);
}

function renderMediaZoneSheet(panel, container, title, devices, deps) {
  const { el, setAttr } = deps;
  const backdrop = el("div", "media-zone-sheet-backdrop");
  const sheet = el("section", "media-zone-sheet");
  setAttr(sheet, "role", "dialog");
  setAttr(sheet, "aria-modal", "true");
  setAttr(sheet, "aria-label", `Медиа в комнате ${title}`);
  const close = el("button", "media-zone-sheet-close", "×");
  close.type = "button";
  setAttr(close, "aria-label", "Закрыть");
  const dismiss = () => {
    panel._mediaOverlay = null;
    if (backdrop.remove) backdrop.remove();
  };
  close.addEventListener("click", dismiss);
  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) dismiss(); });
  enhanceAppendedModal(backdrop, sheet, dismiss);
  sheet.appendChild(close);
  const heading = el("div", "media-zone-sheet-heading");
  heading.appendChild(el("h3", null, title));
  heading.appendChild(el("p", null, `${devices.length} ${mediaOverviewDeviceWord(devices.length)} · выберите устройство для управления`));
  sheet.appendChild(heading);
  const grid = el("div", "inventory-device-grid media-zone-sheet-grid");
  devices.forEach((device) => grid.appendChild(panel._deviceInventoryCard(device)));
  if (!devices.length) grid.appendChild(el("div", "empty-state", "В этой комнате медиоустройства пока не найдены."));
  sheet.appendChild(grid);
  backdrop.appendChild(sheet);
  container.appendChild(backdrop);
}

function renderMediaOverviewHero(panel, container, devices, deps) {
  const playing = devices.filter(mediaOverviewPlaying);
  const available = devices.filter((device) => !mediaOverviewUnavailable(device));
  const unavailable = devices.length - available.length;
  container.appendChild(createLibraryHero(panel, {
    eyebrow: "МЕДИА ДОМА",
    title: "Медиа по комнатам",
    subtitle: "Телевизоры, колонки и медиаплееры по комнатам",
    warning: unavailable > 0,
    facts: [
      { label: "Устройства", value: devices.length },
      { label: "Воспроизводят", value: playing.length },
      { label: "На связи", value: available.length },
      { label: "Без связи", value: unavailable, warning: unavailable > 0 },
    ],
  }, deps));
}

/* Room grouping kept for the zone sheet and room chips. */
function mediaRoomGroups(rooms, devices) {
  const groups = new Map();
  devices.forEach((device) => {
    const room = mediaOverviewRoom(device, rooms);
    const id = room ? room.id : "unassigned";
    if (!groups.has(id)) groups.set(id, { id, name: room ? room.name : "Не распределено", room, devices: [] });
    groups.get(id).devices.push(device);
  });
  return [...groups.values()].sort((left, right) => {
    if (left.id === "unassigned") return 1;
    if (right.id === "unassigned") return -1;
    return left.name.localeCompare(right.name, "ru");
  });
}

function renderMediaZones(panel, container, rooms, devices, deps) {
  const { el, svgIcon, setAttr } = deps;
  const groups = mediaRoomGroups(rooms, devices);
  const section = el("section", "media-canon-section");
  const heading = el("div", "media-canon-heading");
  heading.appendChild(el("h3", null, "По комнатам"));
  const zoneMod100 = groups.length % 100;
  const zoneMod10 = groups.length % 10;
  const zoneNoun = zoneMod100 >= 11 && zoneMod100 <= 14
    ? "зон"
    : zoneMod10 === 1
      ? "зона"
      : zoneMod10 >= 2 && zoneMod10 <= 4
        ? "зоны"
        : "зон";
  heading.appendChild(el("span", null, `${groups.length} ${zoneNoun}`));
  section.appendChild(heading);
  const grid = el("div", "media-zone-grid");
  groups.forEach((group) => {
    const card = el("button", `media-zone-card${group.devices.some(mediaOverviewUnavailable) ? " has-warning" : ""}`);
    card.type = "button";
    setAttr(card, "aria-label", `Открыть медиоустройства комнаты ${group.name}`);
    const head = el("div", "media-zone-head");
    const icon = el("span");
    icon.appendChild(group.room ? roomSvgIcon(roomIconName(group.room)) : svgIcon("media"));
    head.appendChild(icon);
    head.appendChild(el("strong", null, group.name));
    head.appendChild(el("b", null, String(group.devices.length)));
    card.appendChild(head);
    card.appendChild(el("p", null, group.devices.slice(0, 3).map((device) => device.name).join(" · ") || "Устройств нет"));
    const playing = group.devices.filter(mediaOverviewPlaying).length;
    const unavailable = group.devices.filter(mediaOverviewUnavailable).length;
    card.appendChild(el("small", unavailable ? `${unavailable} без связи` : (playing ? `${playing} воспроизводит` : "Все устройства на связи")));
    card.appendChild(el("span", "media-zone-open", "Открыть устройства ›"));
    card.addEventListener("click", () => requestMediaZone(panel, group.name, group.devices));
    grid.appendChild(card);
  });
  section.appendChild(grid);
  container.appendChild(section);
}

const MEDIA_DEVICE_FILTERS = [
  ["all", "Все"],
  ["playing", "Играют"],
  ["tv", "ТВ"],
  ["speakers", "Колонки"],
  ["offline", "Без связи"],
];

function mediaDeviceMatchesFilter(device, filter) {
  if (filter === "playing") return mediaOverviewPlaying(device);
  if (filter === "tv") return mediaOverviewIsTv(device);
  if (filter === "speakers") return !mediaOverviewIsTv(device);
  if (filter === "offline") return mediaOverviewUnavailable(device);
  return true;
}

function renderMediaDeviceSection(panel, container, rooms, devices, deps) {
  const { el, setAttr } = deps;
  if (!panel._mediaUi) panel._mediaUi = { filter: "all", roomId: null, search: "" };
  const ui = panel._mediaUi;
  const section = el("section", "media-canon-section media-canon-all");
  const heading = el("div", "media-canon-heading");
  const headingCopy = el("div");
  headingCopy.appendChild(el("h3", null, "Медиаустройства"));
  const counter = el("p", "media-canon-counter", "");
  headingCopy.appendChild(counter);
  heading.appendChild(headingCopy);
  section.appendChild(heading);
  if (!devices.length) {
    section.appendChild(el("div", "empty-state", "Медиаустройства не найдены."));
    container.appendChild(section);
    return;
  }
  const groups = mediaRoomGroups(rooms, devices);
  const toolbar = el("div", "hh-media-toolbar");
  const search = el("label", "hh-media-search");
  search.appendChild(mediaSearchIcon());
  const input = el("input");
  input.type = "search";
  input.value = typeof ui.search === "string" ? ui.search : "";
  setAttr(input, "placeholder", "Найти медиоустройство");
  setAttr(input, "aria-label", "Найти медиоустройство");
  search.appendChild(input);
  toolbar.appendChild(search);
  const chips = el("div", "hh-media-chip-row");
  const chipButtons = new Map();
  MEDIA_DEVICE_FILTERS.forEach(([id, label]) => {
    const selected = (ui.filter || "all") === id;
    const chip = el("button", `hh-media-chip${selected ? " is-active" : ""}`, label);
    chip.type = "button";
    setAttr(chip, "aria-pressed", selected ? "true" : "false");
    chip.addEventListener("click", () => {
      ui.filter = id;
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
  const roomChipButtons = new Map();
  let roomChips = null;
  if (groups.length) {
    roomChips = el("div", "hh-media-room-row");
    groups.forEach((group) => {
      const playing = group.devices.filter(mediaOverviewPlaying).length;
      const selected = ui.roomId === group.id;
      const chip = el("button", `hh-media-room-chip${selected ? " is-active" : ""}`);
      chip.type = "button";
      setAttr(chip, "aria-pressed", selected ? "true" : "false");
      setAttr(chip, "aria-label", `Показать медиоустройства комнаты ${group.name}`);
      const icon = el("span", "hh-media-room-chip-icon");
      icon.appendChild(group.room ? roomSvgIcon(roomIconName(group.room)) : deps.svgIcon("media"));
      chip.appendChild(icon);
      const copy = el("span", "hh-media-room-chip-copy");
      copy.appendChild(el("strong", null, group.name));
      copy.appendChild(el("small", null, `${group.devices.length} · ${playing} активны`));
      chip.appendChild(copy);
      chip.addEventListener("click", () => {
        ui.roomId = ui.roomId === group.id ? null : group.id;
        roomChipButtons.forEach((node, key) => {
          node.classList.toggle("is-active", key === ui.roomId);
          setAttr(node, "aria-pressed", key === ui.roomId ? "true" : "false");
        });
        applyFilters();
      });
      roomChipButtons.set(group.id, chip);
      roomChips.appendChild(chip);
    });
    toolbar.appendChild(roomChips);
  }
  section.appendChild(toolbar);
  const grid = el("div", "inventory-device-grid media-canon-device-grid hh-media-device-grid");
  const emptyNote = el("div", "empty-state", "По этому фильтру медиаустройства не найдены.");
  const applyFilters = () => {
    const query = mediaOverviewNormalized(ui.search);
    grid.innerHTML = "";
    let shown = 0;
    devices.forEach((device) => {
      if (!mediaDeviceMatchesFilter(device, ui.filter || "all")) return;
      if (ui.roomId) {
        const room = mediaOverviewRoom(device, rooms);
        if ((room ? room.id : "unassigned") !== ui.roomId) return;
      }
      if (query && !mediaOverviewNormalized(device.name).includes(query)) return;
      const card = renderMediaDeviceCard(panel, device, deps);
      if (!card) return;
      shown += 1;
      grid.appendChild(card);
    });
    emptyNote.hidden = shown > 0;
    counter.textContent = `Показано ${shown} из ${devices.length} · карточка открывает полное управление устройством`;
  };
  input.addEventListener("input", () => {
    ui.search = input.value;
    applyFilters();
  });
  applyFilters();
  section.appendChild(grid);
  section.appendChild(emptyNote);
  container.appendChild(section);
}

function mediaSideData(rooms, devices) {
  const roomNameOf = (device) => {
    const room = mediaOverviewRoom(device, rooms);
    return device.roomName || (room && room.name) || "Без комнаты";
  };
  const playing = devices.filter(mediaOverviewPlaying).map((device) => ({
    name: device.name || "Медиаустройство",
    room: roomNameOf(device),
    track: (device.attributes && (
      device.attributes.media_title || device.attributes.app_name || device.attributes.source
    )) || "Воспроизведение",
  }));
  const attention = [];
  devices.filter(mediaOverviewUnavailable).forEach((device) => {
    attention.push({ name: device.name || "Медиаустройство", room: roomNameOf(device), state: "нет связи" });
  });
  devices.filter((device) => !mediaOverviewUnavailable(device)
    && ["off", "standby"].includes(String(device.state || "").toLowerCase())).forEach((device) => {
    attention.push({ name: device.name || "Медиаустройство", room: roomNameOf(device), state: "выключено" });
  });
  return {
    playing,
    attention,
    rooms: mediaRoomGroups(rooms, devices),
  };
}

export function renderMediaOverview(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard;
  if (!dashboard) {
    const empty = deps.el("section", "card empty-state");
    empty.appendChild(deps.el("h2", null, "Медиа"));
    empty.appendChild(deps.el("p", null, "Данные медиа пока недоступны. Проверьте подключение Hausman Hub."));
    container.appendChild(empty); return;
  }
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const unique = new Map();
  (Array.isArray(dashboard.devices) ? dashboard.devices : [])
    .filter(mediaOverviewIsDevice).forEach((device) => unique.set(mediaOverviewKey(device), device));
  const devices = [...unique.values()];
  const page = deps.el("div", "media-canon-page");
  const layout = deps.el("div", "media-layout");
  const main = deps.el("div", "media-main");
  renderMediaOverviewHero(panel, main, devices, deps);
  renderMediaDeviceSection(panel, main, rooms, devices, deps);
  layout.appendChild(main);
  layout.appendChild(renderMediaSide(panel, page, {
    ...mediaSideData(rooms, devices),
    openRoom: (group) => requestMediaZone(panel, group.name, group.devices),
  }, deps));
  page.appendChild(layout);
  if (panel._mediaOverlay) {
    const keys = new Set(panel._mediaOverlay.deviceKeys || []);
    const matches = devices.filter((device) => keys.has(mediaOverviewKey(device)));
    renderMediaZoneSheet(panel, page, panel._mediaOverlay.title, matches, deps);
  }
  container.appendChild(page);
}
