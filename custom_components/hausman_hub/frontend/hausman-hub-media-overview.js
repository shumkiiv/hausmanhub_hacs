import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.89";
import { enhanceAppendedModal } from "./hausman-hub-modal.js?v=1.52.89";
import { roomIconName, roomSvgIcon } from "./hausman-hub-room-icons.js?v=1.52.89";

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
  const tv = devices.filter(mediaOverviewIsTv).length;
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
      { label: "Телевизоры", value: tv },
    ],
  }, deps));
}

function renderMediaZones(panel, container, rooms, devices, deps) {
  const { el, svgIcon, setAttr } = deps;
  const groups = new Map();
  devices.forEach((device) => {
    const room = mediaOverviewRoom(device, rooms);
    const id = room ? room.id : "unassigned";
    if (!groups.has(id)) groups.set(id, { name: room ? room.name : "Не распределено", room, devices: [] });
    groups.get(id).devices.push(device);
  });
  const section = el("section", "media-canon-section");
  const heading = el("div", "media-canon-heading");
  heading.appendChild(el("h3", null, "По комнатам"));
  const zoneMod100 = groups.size % 100;
  const zoneMod10 = groups.size % 10;
  const zoneNoun = zoneMod100 >= 11 && zoneMod100 <= 14
    ? "зон"
    : zoneMod10 === 1
      ? "зона"
      : zoneMod10 >= 2 && zoneMod10 <= 4
        ? "зоны"
        : "зон";
  heading.appendChild(el("span", null, `${groups.size} ${zoneNoun}`));
  section.appendChild(heading);
  const grid = el("div", "media-zone-grid");
  [...groups.values()].forEach((group) => {
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

function renderMediaDeviceGrid(panel, container, devices, deps) {
  const { el } = deps;
  const section = el("details", "media-canon-section media-canon-all");
  const summary = el("summary", "media-canon-heading media-canon-all-summary");
  summary.appendChild(el("h3", null, "Медиаустройства"));
  summary.appendChild(el("span", null, `${devices.length} ${mediaOverviewDeviceWord(devices.length)}`));
  section.appendChild(summary);
  const grid = el("div", "inventory-device-grid media-canon-device-grid");
  devices.forEach((device) => grid.appendChild(panel._deviceInventoryCard(device)));
  if (!devices.length) grid.appendChild(el("div", "empty-state", "Физические медиоустройства пока не найдены."));
  section.appendChild(grid);
  container.appendChild(section);
}

export function renderMediaOverview(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard;
  if (!dashboard) {
    const empty = deps.el("section", "card empty-state");
    empty.appendChild(deps.el("h2", null, "Медиа"));
    empty.appendChild(deps.el("p", null, "Данные медиа пока недоступны. Проверьте подключение HausmanHub."));
    container.appendChild(empty); return;
  }
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const unique = new Map();
  (Array.isArray(dashboard.devices) ? dashboard.devices : [])
    .filter(mediaOverviewIsDevice).forEach((device) => unique.set(mediaOverviewKey(device), device));
  const devices = [...unique.values()];
  const page = deps.el("div", "media-canon-page");
  renderMediaOverviewHero(panel, page, devices, deps);
  renderMediaZones(panel, page, rooms, devices, deps);
  renderMediaDeviceGrid(panel, page, devices, deps);
  if (panel._mediaOverlay) {
    const keys = new Set(panel._mediaOverlay.deviceKeys || []);
    const matches = devices.filter((device) => keys.has(mediaOverviewKey(device)));
    renderMediaZoneSheet(panel, page, panel._mediaOverlay.title, matches, deps);
  }
  container.appendChild(page);
}
