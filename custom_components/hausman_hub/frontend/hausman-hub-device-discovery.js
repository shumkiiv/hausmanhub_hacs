/* New-device discovery notifications: pending list, tab badge and actions. */

import { dedupeCorrelationNotifications } from "./hausman-hub-correlation.js?v=1.52.196";

export const DEVICE_DISCOVERY_API = "hausman_hub/v1/device-discovery";

const DISCOVERY_ACTION_KINDS = ["assign_area", "add_to_energy", "show_on_dashboard"];

const DISCOVERY_STATUS_LABELS = {
  available: "На связи",
  unavailable: "Нет связи",
  empty: "Нет данных",
};

const DISCOVERY_SUCCESS_TEXT = {
  acknowledge: "Уведомление скрыто.",
  assign_area: "Комната назначена.",
  add_to_energy: "Устройство добавлено в энергию.",
  show_on_dashboard: "Устройство закреплено на главной.",
};

export function deviceDiscoveryNotifications(panel) {
  const data = panel._deviceDiscovery;
  const list = data && Array.isArray(data.notifications) ? data.notifications : [];
  return dedupeCorrelationNotifications(list);
}

export function deviceDiscoveryPendingCount(panel) {
  const data = panel._deviceDiscovery;
  if (data && Number.isFinite(Number(data.pendingCount))) return Number(data.pendingCount);
  return deviceDiscoveryNotifications(panel).length;
}

function deviceDiscoveryRevision(panel) {
  const data = panel._deviceDiscovery;
  return data && Number.isFinite(Number(data.revision)) ? Number(data.revision) : 0;
}

function discoveryStatusLabel(status) {
  return DISCOVERY_STATUS_LABELS[status] || "Состояние уточняется";
}

function discoveryMessage(panel, notificationId) {
  const messages = panel._deviceDiscoveryMessages;
  return messages && notificationId ? messages[notificationId] : null;
}

function setDiscoveryMessage(panel, notificationId, tone, text) {
  if (!panel._deviceDiscoveryMessages) panel._deviceDiscoveryMessages = {};
  if (!text) delete panel._deviceDiscoveryMessages[notificationId];
  else panel._deviceDiscoveryMessages[notificationId] = { tone, text };
}

function pruneDiscoveryMessages(panel) {
  const messages = panel._deviceDiscoveryMessages;
  if (!messages) return;
  const alive = new Set(deviceDiscoveryNotifications(panel).map((item) => item.id));
  Object.keys(messages).forEach((id) => { if (!alive.has(id)) delete messages[id]; });
}

export async function loadDeviceDiscovery(panel) {
  if (!panel._hass || panel._deviceDiscoveryLoading || typeof panel._hass.callApi !== "function") return;
  panel._deviceDiscoveryLoading = true;
  try {
    panel._deviceDiscovery = await panel._hass.callApi("GET", DEVICE_DISCOVERY_API);
    panel._deviceDiscoveryError = null;
    pruneDiscoveryMessages(panel);
  } catch (error) {
    panel._deviceDiscoveryError = error && error.message || "discovery_unavailable";
  } finally {
    panel._deviceDiscoveryLoading = false;
    panel._render();
  }
}

export async function postDeviceDiscoveryAction(panel, notification, action, extra = {}) {
  if (!panel._hass || typeof panel._hass.callApi !== "function") return;
  if (!notification || !notification.id || panel._deviceDiscoveryPending) return;
  const notificationId = notification.id;
  if (action === "assign_area" && !extra.areaId) {
    setDiscoveryMessage(panel, notificationId, "error", "Сначала выберите комнату из списка.");
    panel._render();
    return;
  }
  panel._deviceDiscoveryPending = { id: notificationId, action };
  setDiscoveryMessage(panel, notificationId, "", "");
  panel._deviceDiscoveryNotice = "";
  panel._render();
  try {
    const response = await panel._hass.callApi("POST", DEVICE_DISCOVERY_API, {
      expectedRevision: deviceDiscoveryRevision(panel),
      action,
      notificationId,
      ...extra,
    });
    panel._deviceDiscovery = response || panel._deviceDiscovery;
    panel._deviceDiscoveryError = null;
    pruneDiscoveryMessages(panel);
    panel._deviceDiscoveryNotice = DISCOVERY_SUCCESS_TEXT[action] || "Действие выполнено.";
  } catch (error) {
    const status = error && error.status;
    if (status === 403) {
      setDiscoveryMessage(panel, notificationId, "error",
        "Действие доступно только локальному администратору. Войдите администратором и повторите.");
    } else if (status === 404) {
      setDiscoveryMessage(panel, notificationId, "error",
        "Уведомление уже снято. Список обновится при следующем обновлении панели.");
    } else if (status === 409) {
      setDiscoveryMessage(panel, notificationId, "error",
        "Данные изменились в другом окне. Список обновлён, повторите действие.");
      await loadDeviceDiscovery(panel);
    } else {
      setDiscoveryMessage(panel, notificationId, "error",
        "Действие не выполнено. Проверьте соединение и попробуйте ещё раз.");
    }
  } finally {
    panel._deviceDiscoveryPending = null;
    panel._render();
  }
}

function renderDiscoveryAreaAction(panel, notification, placement, box, busy, thisPending, deps) {
  const { el, setAttr } = deps;
  const options = Array.isArray(notification.areaOptions) ? notification.areaOptions : [];
  if (!options.length) {
    box.appendChild(el("small", "device-discovery-action-hint", "Комнаты Home Assistant не найдены."));
    return;
  }
  const drafts = panel._deviceDiscoveryAreaDrafts || (panel._deviceDiscoveryAreaDrafts = {});
  const fallback = (options.find((option) => option.recommended) || options[0]).id;
  if (!drafts[notification.id]) drafts[notification.id] = fallback;
  const select = el("select", "device-discovery-area-select");
  setAttr(select, "aria-label", "Комната для нового устройства");
  options.forEach((option) => {
    const suffix = option.current ? " (текущая)" : option.recommended ? " (рекомендуем)" : "";
    const node = el("option", null, `${option.name}${suffix}`);
    node.value = option.id;
    if (option.id === drafts[notification.id]) node.selected = true;
    select.appendChild(node);
  });
  select.disabled = busy;
  select.addEventListener("change", () => { drafts[notification.id] = select.value; });
  box.appendChild(select);
  const button = el("button", "device-discovery-run", thisPending ? "Выполняется…" : placement.title || "Назначить комнату");
  button.type = "button";
  button.disabled = busy;
  if (thisPending) setAttr(button, "aria-busy", "true");
  button.addEventListener("click", () => postDeviceDiscoveryAction(panel, notification, "assign_area", {
    areaId: drafts[notification.id],
  }));
  box.appendChild(button);
}

function renderDiscoveryPlacement(panel, notification, placement, deps) {
  const { el, setAttr } = deps;
  const row = el("div", `device-discovery-placement${placement.recommended ? " is-recommended" : ""}`);
  const copy = el("div", "device-discovery-placement-copy");
  copy.appendChild(el("strong", null, placement.title || "Рекомендация"));
  copy.appendChild(el("small", null, placement.reason || ""));
  row.appendChild(copy);
  if (placement.actionable !== true || !DISCOVERY_ACTION_KINDS.includes(placement.kind)) return row;
  const pending = panel._deviceDiscoveryPending;
  const busy = !!pending;
  const thisPending = !!(pending && pending.id === notification.id && pending.action === placement.kind);
  const box = el("div", "device-discovery-action");
  if (placement.kind === "assign_area") {
    renderDiscoveryAreaAction(panel, notification, placement, box, busy, thisPending, deps);
  } else {
    const button = el("button", "device-discovery-run", thisPending ? "Выполняется…" : placement.title);
    button.type = "button";
    button.disabled = busy;
    if (thisPending) setAttr(button, "aria-busy", "true");
    button.addEventListener("click", () => postDeviceDiscoveryAction(panel, notification, placement.kind));
    box.appendChild(button);
  }
  row.appendChild(box);
  return row;
}

function renderDiscoveryCard(panel, notification, deps) {
  const { el } = deps;
  const card = el("article", "device-discovery-card");
  const head = el("div", "device-discovery-card-head");
  const title = el("div", "device-discovery-card-title");
  title.appendChild(el("h4", null, notification.title || "Новое устройство"));
  const hardware = [notification.manufacturer, notification.model].filter(Boolean).join(" ");
  if (hardware) title.appendChild(el("small", null, hardware));
  head.appendChild(title);
  head.appendChild(el("span",
    `device-discovery-status${notification.status === "available" ? " is-ok" : " is-warn"}`,
    discoveryStatusLabel(notification.status)));
  card.appendChild(head);
  card.appendChild(el("p", "device-discovery-room",
    notification.roomName ? `Комната: ${notification.roomName}` : "Комната пока не назначена"));
  const placements = Array.isArray(notification.suggestedPlacements) ? notification.suggestedPlacements : [];
  if (placements.length) {
    const list = el("div", "device-discovery-placements");
    placements.forEach((placement) => {
      list.appendChild(renderDiscoveryPlacement(panel, notification, placement, deps));
    });
    card.appendChild(list);
  }
  const message = discoveryMessage(panel, notification.id);
  if (message && message.text) {
    card.appendChild(el("p",
      `device-discovery-message ${message.tone === "error" ? "is-error" : "is-success"}`, message.text));
  }
  const footer = el("div", "device-discovery-footer");
  const acknowledge = el("button", "secondary", "Скрыть");
  acknowledge.type = "button";
  acknowledge.disabled = !!panel._deviceDiscoveryPending;
  acknowledge.addEventListener("click", () => postDeviceDiscoveryAction(panel, notification, "acknowledge"));
  footer.appendChild(acknowledge);
  card.appendChild(footer);
  return card;
}

export function renderDeviceDiscovery(panel, container, deps) {
  const { el } = deps;
  const notifications = deviceDiscoveryNotifications(panel);
  const showEmpty = panel._deviceDiscoveryLoading && !panel._deviceDiscovery;
  if (!notifications.length && !panel._deviceDiscoveryNotice && !panel._deviceDiscoveryError && !showEmpty) return;
  const section = el("section", "devices-canon-section device-discovery");
  const heading = el("div", "devices-canon-heading");
  heading.appendChild(el("h3", null, "Новые устройства"));
  const count = deviceDiscoveryPendingCount(panel);
  if (count) heading.appendChild(el("span", "device-discovery-count", `${count}`));
  section.appendChild(heading);
  if (panel._deviceDiscoveryError && !notifications.length) {
    const error = el("div", "device-discovery-error");
    error.appendChild(el("span", null, "Не удалось получить список новых устройств."));
    const retry = el("button", "secondary", "Повторить");
    retry.type = "button";
    retry.disabled = panel._deviceDiscoveryLoading;
    retry.addEventListener("click", () => loadDeviceDiscovery(panel));
    error.appendChild(retry);
    section.appendChild(error);
  }
  if (panel._deviceDiscoveryNotice) {
    section.appendChild(el("p", "device-discovery-notice", panel._deviceDiscoveryNotice));
  }
  if (showEmpty) {
    section.appendChild(el("p", "device-discovery-loading", "Проверяем новые устройства…"));
  }
  notifications.forEach((notification) => {
    section.appendChild(renderDiscoveryCard(panel, notification, deps));
  });
  container.appendChild(section);
}

export function updateDeviceDiscoveryBadge(panel, deps) {
  const { el, setAttr } = deps;
  const tab = panel._shell && panel._shell.tabs && panel._shell.tabs.devices;
  if (!tab) return;
  const count = panel._deviceDiscovery ? deviceDiscoveryPendingCount(panel) : 0;
  let badge = panel._deviceDiscoveryBadgeNode;
  if (!count) {
    if (badge) badge.hidden = true;
    return;
  }
  if (!badge) {
    badge = el("span", "tab-badge device-discovery-badge");
    panel._deviceDiscoveryBadgeNode = badge;
    tab.appendChild(badge);
  }
  badge.hidden = false;
  badge.textContent = String(count);
  setAttr(badge, "aria-label", `Новых устройств: ${count}`);
}
