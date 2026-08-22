/* Right column of the rooms section: home overview, offline rooms, quick access, history. */

import { roomIconName, roomSvgIcon } from "./hausman-hub-room-icons.js?v=1.52.146";

const ROOMS_SIDE_SVG_NAMESPACE = "http" + "://www.w3.org/2000/svg";

/* Inline icons missing from the shared panel icon set. */
const ROOMS_SIDE_ICON_PATHS = {
  check: "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z",
  history: "M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z",
  "chevron-right": "M8.59 16.59 13.17 12 8.59 7.41 10 6l6 6-6 6z",
};

export function roomsSideIcon(name) {
  const svg = document.createElementNS(ROOMS_SIDE_SVG_NAMESPACE, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS(ROOMS_SIDE_SVG_NAMESPACE, "path");
  path.setAttribute("d", ROOMS_SIDE_ICON_PATHS[name] || ROOMS_SIDE_ICON_PATHS["chevron-right"]);
  path.setAttribute("fill", "currentColor");
  svg.appendChild(path);
  return svg;
}

function roomsSideDeviceWord(count) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return "устройств";
  if (count % 10 === 1) return "устройство";
  if (count % 10 >= 2 && count % 10 <= 4) return "устройства";
  return "устройств";
}

function sideFactRow(deps, iconNode, iconClass, label, value) {
  const { el } = deps;
  const row = el("span", "rooms-side-row rooms-side-fact");
  const icon = el("span", `rooms-side-row-icon ${iconClass}`.trim());
  icon.appendChild(iconNode);
  row.appendChild(icon);
  const copy = el("span", "rooms-side-row-copy");
  copy.appendChild(el("small", null, label));
  copy.appendChild(el("strong", null, String(value)));
  row.appendChild(copy);
  return row;
}

function sideNoteRow(deps, iconNode, iconClass, title, subtitle) {
  const { el } = deps;
  const row = el("span", "rooms-side-row");
  const icon = el("span", `rooms-side-row-icon ${iconClass}`.trim());
  icon.appendChild(iconNode);
  row.appendChild(icon);
  const copy = el("span", "rooms-side-row-copy");
  copy.appendChild(el("strong", null, title));
  copy.appendChild(el("small", null, subtitle));
  row.appendChild(copy);
  return row;
}

function sideRoomRow(deps, iconClass, room, subtitle, openRoom) {
  const { el } = deps;
  const row = el("button", "rooms-side-row rooms-side-room");
  row.type = "button";
  const icon = el("span", `rooms-side-row-icon ${iconClass}`.trim());
  icon.appendChild(roomSvgIcon(roomIconName(room)));
  row.appendChild(icon);
  const copy = el("span", "rooms-side-row-copy");
  copy.appendChild(el("strong", null, room.name));
  copy.appendChild(el("small", null, subtitle));
  row.appendChild(copy);
  row.addEventListener("click", () => openRoom(room));
  return row;
}

export function renderRoomsSide(panel, container, data, deps) {
  const { el, svgIcon, setAttr } = deps;
  const { rooms, devices, grouped, isActive, isUnavailable, openRoom } = data;
  const aside = el("aside", "rooms-side");

  const overview = el("section", "rooms-side-card");
  overview.appendChild(el("span", "rooms-side-label", "Обзор дома"));
  overview.appendChild(sideFactRow(deps, svgIcon("rooms"), "", "Комнаты", rooms.length));
  overview.appendChild(sideFactRow(deps, svgIcon("device"), "", "Физические устройства", devices.length));
  overview.appendChild(sideFactRow(
    deps, roomsSideIcon("check"), "is-ok", "Активно", devices.filter(isActive).length,
  ));
  aside.appendChild(overview);

  const offlineCard = el("section", "rooms-side-card");
  offlineCard.appendChild(el("span", "rooms-side-label", "Комнаты без связи"));
  const offlineRooms = rooms
    .map((room) => ({ room, offline: (grouped.get(room.id) || []).filter(isUnavailable) }))
    .filter((entry) => entry.offline.length);
  if (offlineRooms.length) {
    offlineRooms.slice(0, 4).forEach((entry) => {
      offlineCard.appendChild(sideRoomRow(
        deps, "is-warning", entry.room,
        `${entry.offline.length} ${roomsSideDeviceWord(entry.offline.length)} без связи`,
        openRoom,
      ));
    });
  } else {
    offlineCard.appendChild(sideNoteRow(
      deps, roomsSideIcon("check"), "is-ok", "Все комнаты на связи", "Ошибок подключения нет",
    ));
  }
  aside.appendChild(offlineCard);

  const quick = el("section", "rooms-side-card");
  quick.appendChild(el("span", "rooms-side-label", "Быстрый доступ"));
  const popular = rooms
    .map((room) => ({ room, count: (grouped.get(room.id) || []).length }))
    .filter((entry) => entry.count > 0)
    .sort((first, second) => second.count - first.count)
    .slice(0, 3);
  if (popular.length) {
    popular.forEach((entry) => {
      quick.appendChild(sideRoomRow(
        deps, "", entry.room,
        `${entry.count} ${roomsSideDeviceWord(entry.count)}`,
        openRoom,
      ));
    });
  } else {
    quick.appendChild(el("p", "rooms-side-empty", "В комнатах пока нет физических устройств."));
  }
  aside.appendChild(quick);

  const historyCard = el("section", "rooms-side-card");
  const history = el("button", "rooms-side-link");
  history.type = "button";
  const historyLabel = el("span", "rooms-side-link-label");
  historyLabel.appendChild(roomsSideIcon("history"));
  historyLabel.appendChild(el("span", null, "История дома"));
  history.appendChild(historyLabel);
  history.appendChild(roomsSideIcon("chevron-right"));
  setAttr(history, "aria-label", "История дома");
  history.addEventListener("click", () => {
    panel._notice = "История дома появится после обновления сервера Hausman Hub.";
    if (typeof panel._render === "function") panel._render();
  });
  historyCard.appendChild(history);
  aside.appendChild(historyCard);
  return aside;
}
