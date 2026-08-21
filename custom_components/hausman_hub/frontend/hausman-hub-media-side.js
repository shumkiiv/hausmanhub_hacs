/* Right column of the media section: now playing, rooms, state, history. */
/* No imports on purpose: the panel test harness evaluates this module standalone. */

const MEDIA_SIDE_SVG_NAMESPACE = "http" + "://www.w3.org/2000/svg";

/* Inline icons missing from the shared panel icon set. */
const MEDIA_SIDE_ICON_PATHS = {
  check: "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z",
  history: "M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z",
  "chevron-right": "M8.59 16.59 13.17 12 8.59 7.41 10 6l6 6-6 6-6-6z",
};

export function mediaSideIcon(name) {
  const svg = document.createElementNS(MEDIA_SIDE_SVG_NAMESPACE, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS(MEDIA_SIDE_SVG_NAMESPACE, "path");
  path.setAttribute("d", MEDIA_SIDE_ICON_PATHS[name] || MEDIA_SIDE_ICON_PATHS["chevron-right"]);
  path.setAttribute("fill", "currentColor");
  svg.appendChild(path);
  return svg;
}

function mediaSideDeviceWord(count) {
  if (count % 100 >= 11 && count % 100 <= 14) return "устройств";
  if (count % 10 === 1) return "устройство";
  if (count % 10 >= 2 && count % 10 <= 4) return "устройства";
  return "устройств";
}

function mediaSideRow(deps, iconName, iconClass, title, subtitle) {
  const { el, svgIcon } = deps;
  const row = el("span", "hh-media-side-row");
  const icon = el("span", `hh-media-side-row-icon ${iconClass}`);
  icon.appendChild(iconName === "check" ? mediaSideIcon("check") : svgIcon(iconName));
  row.appendChild(icon);
  const copy = el("span", "hh-media-side-row-copy");
  copy.appendChild(el("strong", null, title));
  copy.appendChild(el("small", null, subtitle));
  row.appendChild(copy);
  return row;
}

export function renderMediaSide(panel, container, data, deps) {
  const { el, svgIcon, setAttr } = deps;
  const playing = Array.isArray(data.playing) ? data.playing : [];
  const attention = Array.isArray(data.attention) ? data.attention : [];
  const rooms = Array.isArray(data.rooms) ? data.rooms : [];
  const aside = el("aside", "hh-media-side");

  const now = el("section", "hh-media-side-card");
  now.appendChild(el("span", "hh-media-side-label", "Сейчас"));
  if (playing.length) {
    playing.slice(0, 4).forEach((item) => {
      now.appendChild(mediaSideRow(deps, "play", "is-playing", item.name, `${item.room} · ${item.track}`));
    });
  } else {
    now.appendChild(el("p", "hh-media-side-empty", "Сейчас ничего не играет."));
  }
  aside.appendChild(now);

  const roomsCard = el("section", "hh-media-side-card");
  roomsCard.appendChild(el("span", "hh-media-side-label", "По комнатам"));
  if (rooms.length) {
    rooms.slice(0, 6).forEach((group) => {
      const row = el("button", "hh-media-side-room");
      row.type = "button";
      setAttr(row, "aria-label", `Открыть медиоустройства комнаты ${group.name}`);
      const icon = el("span", "hh-media-side-row-icon is-room");
      icon.appendChild(svgIcon("rooms"));
      row.appendChild(icon);
      const copy = el("span", "hh-media-side-row-copy");
      copy.appendChild(el("strong", null, group.name));
      copy.appendChild(el("small", null, `${group.devices.length} ${mediaSideDeviceWord(group.devices.length)}`));
      row.appendChild(copy);
      row.appendChild(mediaSideIcon("chevron-right"));
      row.addEventListener("click", () => {
        if (typeof data.openRoom === "function") data.openRoom(group);
      });
      roomsCard.appendChild(row);
    });
  } else {
    roomsCard.appendChild(el("p", "hh-media-side-empty", "Комнаты с медиаустройствами пока не найдены."));
  }
  aside.appendChild(roomsCard);

  const state = el("section", "hh-media-side-card");
  state.appendChild(el("span", "hh-media-side-label", "Состояние"));
  if (attention.length) {
    attention.slice(0, 4).forEach((item) => {
      state.appendChild(mediaSideRow(
        deps,
        item.state === "нет связи" ? "warning" : "media",
        item.state === "нет связи" ? "is-warning" : "is-off",
        item.name,
        `${item.room} · ${item.state}`,
      ));
    });
  } else {
    state.appendChild(mediaSideRow(deps, "check", "is-ok", "Все устройства на связи", "Ошибок подключения нет"));
  }
  aside.appendChild(state);

  const historyCard = el("section", "hh-media-side-card");
  const history = el("button", "hh-media-side-link");
  history.type = "button";
  const historyLabel = el("span", "hh-media-side-link-label");
  historyLabel.appendChild(mediaSideIcon("history"));
  historyLabel.appendChild(el("span", null, "История медиа"));
  history.appendChild(historyLabel);
  history.appendChild(mediaSideIcon("chevron-right"));
  setAttr(history, "aria-label", "История медиа");
  history.addEventListener("click", () => {
    panel._notice = "История медиа появится после обновления сервера Hausman Hub.";
    if (typeof panel._render === "function") panel._render();
  });
  historyCard.appendChild(history);
  aside.appendChild(historyCard);
  return aside;
}
