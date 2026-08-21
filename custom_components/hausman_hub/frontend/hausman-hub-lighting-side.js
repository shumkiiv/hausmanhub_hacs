/* Right column of the lighting section: quick actions, active lights, attention, history. */

import { enhanceAppendedModal } from "./hausman-hub-modal.js?v=1.52.134";

const LIGHTING_SIDE_SVG_NAMESPACE = "http" + "://www.w3.org/2000/svg";

/* Inline icons missing from the shared panel icon set. */
const SIDE_ICON_PATHS = {
  power: "M13 3h-2v10h2V3zm4.83 2.17-1.42 1.42C17.99 7.86 19 9.81 19 12c0 3.87-3.13 7-7 7s-7-3.13-7-7c0-2.19 1.01-4.14 2.58-5.42L6.17 5.17C4.23 6.82 3 9.26 3 12c0 4.97 4.03 9 9 9s9-4.03 9-9c0-2.74-1.23-5.18-3.17-6.83z",
  search: "M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z",
  check: "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z",
  history: "M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z",
  "chevron-right": "M8.59 16.59 13.17 12 8.59 7.41 10 6l6 6-6 6z",
};

export function lightingSideIcon(name) {
  const svg = document.createElementNS(LIGHTING_SIDE_SVG_NAMESPACE, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS(LIGHTING_SIDE_SVG_NAMESPACE, "path");
  path.setAttribute("d", SIDE_ICON_PATHS[name] || SIDE_ICON_PATHS["chevron-right"]);
  path.setAttribute("fill", "currentColor");
  svg.appendChild(path);
  return svg;
}

/* Only actions present in the runtime scenario catalog may run: nothing is synthesized. */
function turnOffEntries(panel, devices) {
  const seen = new Set();
  const entries = [];
  if (typeof panel._catalogTargets !== "function") return entries;
  devices.forEach((device) => {
    panel._catalogTargets(device).forEach((target) => {
      const off = (target.actions || []).find((action) => action.action_id === "turn_off");
      if (!off || seen.has(target.target_id)) return;
      seen.add(target.target_id);
      entries.push({ device, targetId: target.target_id });
    });
  });
  return entries;
}

/* Commands go one by one through the shared device-actions flow with receipts and reload. */
async function executeTurnOff(panel, entries) {
  for (const entry of entries) {
    if (typeof panel._executeDeviceAction !== "function") return;
    // eslint-disable-next-line no-await-in-loop
    await panel._executeDeviceAction(entry.targetId, "turn_off", null);
  }
}

/* Honest confirmation: the list shows exactly the devices that will receive turn_off. */
export function openLightingTurnOffConfirm(panel, container, title, devices, deps) {
  const { el, setAttr } = deps;
  const entries = turnOffEntries(panel, devices);
  if (!entries.length) {
    panel._notice = "Нет включённых устройств, которые можно выключить.";
    if (typeof panel._render === "function") panel._render();
    return;
  }
  const backdrop = el("div", "lighting-room-sheet-backdrop");
  setAttr(backdrop, "role", "presentation");
  const sheet = el("section", "lighting-confirm-sheet");
  setAttr(sheet, "role", "dialog");
  setAttr(sheet, "aria-modal", "true");
  setAttr(sheet, "aria-label", title);
  sheet.appendChild(el("h3", null, title));
  sheet.appendChild(el("p", "muted", "Будут выключены:"));
  const list = el("div", "lighting-confirm-list");
  entries.forEach((entry) => list.appendChild(el("span", null, entry.device.name || "Устройство")));
  sheet.appendChild(list);
  const actions = el("div", "lighting-confirm-actions");
  const cancel = el("button", "secondary", "Отмена");
  cancel.type = "button";
  const confirm = el("button", "danger-outline", "Выключить");
  confirm.type = "button";
  actions.appendChild(cancel);
  actions.appendChild(confirm);
  sheet.appendChild(actions);
  backdrop.appendChild(sheet);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) finish();
  });
  container.appendChild(backdrop);
  const finish = enhanceAppendedModal(backdrop, sheet, () => {
    if (typeof backdrop.remove === "function") backdrop.remove();
  }, { initialFocus: cancel });
  cancel.addEventListener("click", () => finish());
  confirm.addEventListener("click", () => {
    finish();
    void executeTurnOff(panel, entries);
  });
}

function openActiveLightsSheet(panel, container, devices, deps) {
  const { el, setAttr } = deps;
  const backdrop = el("div", "lighting-room-sheet-backdrop");
  setAttr(backdrop, "role", "presentation");
  const sheet = el("section", "lighting-room-sheet");
  setAttr(sheet, "role", "dialog");
  setAttr(sheet, "aria-modal", "true");
  setAttr(sheet, "aria-label", "Сейчас включено");
  const close = el("button", "lighting-room-sheet-close", "×");
  close.type = "button";
  setAttr(close, "aria-label", "Закрыть список включённого света");
  close.addEventListener("click", () => finish());
  sheet.appendChild(close);
  const body = el("div", "lighting-room-sheet-body");
  body.appendChild(el("span", "lighting-room-sheet-eyebrow", "ОСВЕЩЕНИЕ ДОМА"));
  body.appendChild(el("h2", null, "Сейчас включено"));
  body.appendChild(el("p", "muted", "Все включённые устройства освещения. Карточка открывает подробности и управление."));
  const grid = el("div", "lighting-room-sheet-grid");
  devices.forEach((device) => grid.appendChild(panel._deviceInventoryCard(device)));
  body.appendChild(grid);
  sheet.appendChild(body);
  backdrop.appendChild(sheet);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) finish();
  });
  container.appendChild(backdrop);
  const finish = enhanceAppendedModal(backdrop, sheet, () => {
    if (typeof backdrop.remove === "function") backdrop.remove();
  }, { initialFocus: close });
}

function sideRow(deps, iconName, iconClass, title, subtitle) {
  const { el, svgIcon } = deps;
  const row = el("span", "lighting-side-row");
  const icon = el("span", `lighting-side-row-icon ${iconClass}`);
  icon.appendChild(iconName === "check" ? lightingSideIcon("check") : svgIcon(iconName));
  row.appendChild(icon);
  const copy = el("span", "lighting-side-row-copy");
  copy.appendChild(el("strong", null, title));
  copy.appendChild(el("small", null, subtitle));
  row.appendChild(copy);
  return row;
}

export function renderLightingSide(panel, container, data, deps) {
  const { el, svgIcon, setAttr } = deps;
  const { devices, isActive, roomName } = data;
  const aside = el("aside", "lighting-side");
  const active = devices.filter(isActive);
  const offline = devices.filter((device) => device.unavailable || device.state === "unavailable");

  const quick = el("section", "lighting-side-card");
  quick.appendChild(el("span", "lighting-side-label", "Быстрые сценарии"));
  const allOff = el("button", "lighting-side-action");
  allOff.type = "button";
  allOff.disabled = !active.length || panel._busy === true;
  const actionIcon = el("span", "lighting-side-action-icon");
  actionIcon.appendChild(svgIcon("lightbulb"));
  allOff.appendChild(actionIcon);
  const actionCopy = el("span", "lighting-side-action-copy");
  actionCopy.appendChild(el("strong", null, "Весь свет выключить"));
  actionCopy.appendChild(el("small", null, active.length
    ? "Выключает всё освещение в доме"
    : "Сейчас весь свет выключен"));
  allOff.appendChild(actionCopy);
  allOff.addEventListener("click", () => {
    openLightingTurnOffConfirm(panel, container, "Весь свет выключить", active, deps);
  });
  quick.appendChild(allOff);
  aside.appendChild(quick);

  const now = el("section", "lighting-side-card");
  now.appendChild(el("span", "lighting-side-label", "Сейчас включено"));
  if (active.length) {
    active.slice(0, 4).forEach((device) => {
      now.appendChild(sideRow(deps, "lightbulb", "is-on", device.name || "Устройство", roomName(device)));
    });
    const all = el("button", "lighting-side-link");
    all.type = "button";
    all.appendChild(el("span", null, "Все включённые"));
    all.appendChild(lightingSideIcon("chevron-right"));
    all.addEventListener("click", () => openActiveLightsSheet(panel, container, active, deps));
    now.appendChild(all);
  } else {
    now.appendChild(el("p", "lighting-side-empty", "Сейчас весь свет выключен."));
  }
  aside.appendChild(now);

  const attention = el("section", "lighting-side-card");
  attention.appendChild(el("span", "lighting-side-label", "Требует внимания"));
  if (offline.length) {
    offline.slice(0, 4).forEach((device) => {
      attention.appendChild(sideRow(
        deps, "warning", "is-warning", device.name || "Устройство", `${roomName(device)} · нет связи`,
      ));
    });
  } else {
    attention.appendChild(sideRow(deps, "check", "is-ok", "Все устройства на связи", "Ошибок подключения нет"));
  }
  aside.appendChild(attention);

  const historyCard = el("section", "lighting-side-card");
  const history = el("button", "lighting-side-link");
  history.type = "button";
  const historyLabel = el("span", "lighting-side-link-label");
  historyLabel.appendChild(lightingSideIcon("history"));
  historyLabel.appendChild(el("span", null, "История"));
  history.appendChild(historyLabel);
  history.appendChild(lightingSideIcon("chevron-right"));
  setAttr(history, "aria-label", "История освещения");
  history.addEventListener("click", () => {
    panel._notice = "История освещения появится после обновления сервера Hausman Hub.";
    if (typeof panel._render === "function") panel._render();
  });
  historyCard.appendChild(history);
  aside.appendChild(historyCard);
  return aside;
}
