/* Right column of the climate section: microclimate, equipment, attention, history. */

const CLIMATE_SIDE_SVG_NAMESPACE = "http" + "://www.w3.org/2000/svg";

/* Inline icons missing from the shared panel icon set. */
const CLIMATE_SIDE_ICON_PATHS = {
  check: "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z",
  history: "M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z",
  thermometer: "M15 13V5a3 3 0 0 0-6 0v8a5 5 0 1 0 6 0zm-3-9a1 1 0 0 1 1 1v4h-2V5a1 1 0 0 1 1-1z",
  water: "M12 2s6 6.58 6 11a6 6 0 0 1-12 0c0-4.42 6-11 6-11z",
  "chevron-right": "M8.59 16.59 13.17 12 8.59 7.41 10 6l6 6-6 6z",
};

function climateSideIcon(name) {
  const svg = document.createElementNS(CLIMATE_SIDE_SVG_NAMESPACE, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS(CLIMATE_SIDE_SVG_NAMESPACE, "path");
  path.setAttribute("d", CLIMATE_SIDE_ICON_PATHS[name] || CLIMATE_SIDE_ICON_PATHS["chevron-right"]);
  path.setAttribute("fill", "currentColor");
  svg.appendChild(path);
  return svg;
}

function climateSideRow(deps, iconName, iconClass, title, subtitle) {
  const { el } = deps;
  const row = el("span", "climate-side-row");
  const icon = el("span", `climate-side-row-icon ${iconClass}`);
  icon.appendChild(climateSideIcon(iconName));
  row.appendChild(icon);
  const copy = el("span", "climate-side-row-copy");
  copy.appendChild(el("strong", null, title));
  if (subtitle) copy.appendChild(el("small", null, subtitle));
  row.appendChild(copy);
  return row;
}

/* data.microRows: [[label, value]] pre-formatted by the overview (shared helpers). */
/* data.equipment: [{ title, devices, offlineCount }] with the same category order. */
/* data.attention: [{ name, subtitle }] offline devices and manual exclusions. */
export function renderClimateSide(panel, container, data, deps) {
  const { el, setAttr } = deps;
  const aside = el("aside", "climate-side");

  const micro = el("section", "climate-side-card");
  micro.appendChild(el("span", "climate-side-label", "Микроклимат"));
  if (data.microRows && data.microRows.length) {
    const list = el("div", "climate-side-micro");
    data.microRows.forEach(([label, value], index) => {
      const row = el("span", "climate-side-micro-row");
      const icon = el("span", "climate-side-micro-icon");
      icon.appendChild(climateSideIcon(index === 0 ? "thermometer" : "water"));
      row.appendChild(icon);
      row.appendChild(el("small", null, label));
      row.appendChild(el("strong", null, value));
      list.appendChild(row);
    });
    micro.appendChild(list);
  } else {
    micro.appendChild(el("p", "climate-side-empty", "Показания датчиков пока недоступны."));
  }
  aside.appendChild(micro);

  const equipment = el("section", "climate-side-card");
  equipment.appendChild(el("span", "climate-side-label", "Оборудование"));
  if (data.equipment && data.equipment.length) {
    data.equipment.forEach((entry) => {
      const row = el("button", "climate-side-equipment");
      row.type = "button";
      const copy = el("span", "climate-side-row-copy");
      copy.appendChild(el("strong", null, entry.title));
      copy.appendChild(el("small", null, entry.offlineCount
        ? `${entry.devices.length} · нет связи: ${entry.offlineCount}`
        : `${entry.devices.length}`));
      row.appendChild(copy);
      row.appendChild(climateSideIcon("chevron-right"));
      row.addEventListener("click", () => data.openCategory(entry));
      equipment.appendChild(row);
    });
  } else {
    equipment.appendChild(el("p", "climate-side-empty", "Климатическое оборудование не найдено."));
  }
  aside.appendChild(equipment);

  const attention = el("section", "climate-side-card");
  attention.appendChild(el("span", "climate-side-label", "Требует внимания"));
  if (data.attention && data.attention.length) {
    data.attention.slice(0, 4).forEach((entry) => {
      attention.appendChild(climateSideRow(deps, "chevron-right", "is-warning", entry.name, entry.subtitle));
    });
  } else {
    attention.appendChild(climateSideRow(deps, "check", "is-ok", "Всё в норме", "Ошибок и исключений нет"));
  }
  aside.appendChild(attention);

  const historyCard = el("section", "climate-side-card");
  const history = el("button", "climate-side-link");
  history.type = "button";
  const historyLabel = el("span", "climate-side-link-label");
  historyLabel.appendChild(climateSideIcon("history"));
  historyLabel.appendChild(el("span", null, "История"));
  history.appendChild(historyLabel);
  history.appendChild(climateSideIcon("chevron-right"));
  setAttr(history, "aria-label", "История климата");
  history.addEventListener("click", () => {
    panel._notice = "История климата появится после обновления сервера Hausman Hub.";
    if (typeof panel._render === "function") panel._render();
  });
  historyCard.appendChild(history);
  aside.appendChild(historyCard);
  return aside;
}
