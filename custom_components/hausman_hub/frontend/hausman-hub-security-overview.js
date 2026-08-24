/* Canonical tablet-style security overview with one card per physical device. */

import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.162";

const SECURITY_CATEGORIES = new Set([
  "security", "moisture", "smoke", "gas", "carbon_monoxide", "safety", "problem",
  "occupancy", "presence", "motion", "opening", "door", "window",
]);

const TYPE_META = {
  leaks: { label: "Протечки", icon: "shield", empty: "В норме" },
  access: { label: "Двери и замки", icon: "lock", empty: "В норме" },
  windows: { label: "Окна", icon: "rooms", empty: "В норме" },
  motion: { label: "Движение", icon: "device", empty: "Спокойно" },
  cameras: { label: "Камеры", icon: "camera", empty: "Под наблюдением" },
  alarms: { label: "Охрана", icon: "alarm", empty: "Без тревог" },
  fire: { label: "Дым и газ", icon: "shield", empty: "В норме" },
};

function securityDevices(panel) {
  const devices = panel._homeDashboard && Array.isArray(panel._homeDashboard.devices)
    ? panel._homeDashboard.devices : [];
  const result = new Map();
  devices.filter((device) => {
    const domain = String(device.domain || "");
    const category = String(device.category || "");
    return SECURITY_CATEGORIES.has(category)
      || ["lock", "camera", "alarm_control_panel"].includes(domain);
  }).forEach((device) => {
    const key = String(device.physicalId || device.id || device.entityId || device.name || "");
    if (key && !result.has(key)) result.set(key, device);
  });
  return [...result.values()];
}

function securityType(device) {
  const category = String(device.category || "");
  const domain = String(device.domain || "");
  const identity = `${device.name || ""} ${device.stateLabel || ""}`.toLocaleLowerCase("ru");
  if (category === "moisture" || /протеч|water leak/.test(identity)) return "leaks";
  if (["smoke", "gas", "carbon_monoxide", "safety", "problem"].includes(category)) return "fire";
  if (domain === "camera") return "cameras";
  if (domain === "alarm_control_panel") return "alarms";
  if (category === "window" || /окн/.test(identity)) return "windows";
  if (domain === "lock" || ["opening", "door"].includes(category) || /двер|замок|lock/.test(identity)) return "access";
  if (["occupancy", "presence", "motion"].includes(category)) return "motion";
  return "alarms";
}

function securityUnavailable(device) {
  return Boolean(device.unavailable || device.state === "unavailable" || device.state === "unknown");
}

function securityNeedsAttention(device) {
  if (securityUnavailable(device)) return true;
  const state = String(device.state || "").toLowerCase();
  const type = securityType(device);
  if (type === "leaks" || type === "fire") return state === "on" || state === "triggered";
  if (type === "access" || type === "windows") return ["on", "open", "opening", "unlocked", "jammed"].includes(state);
  if (type === "alarms") return state === "triggered";
  return false;
}

function securityStatus(device) {
  if (securityUnavailable(device)) return "Нет связи";
  const state = String(device.state || "").toLowerCase();
  const type = securityType(device);
  if (type === "leaks") return state === "on" ? "Обнаружена вода" : "Сухо";
  if (type === "fire") return state === "on" || state === "triggered" ? "Тревога" : "В норме";
  if (type === "motion") return state === "on" ? "Обнаружено движение" : "Движения нет";
  if (type === "windows" || type === "access") {
    if (["on", "open", "opening", "unlocked"].includes(state)) return "Открыто";
    if (state === "jammed") return "Заклинило";
    return "Закрыто";
  }
  if (state === "triggered") return "Тревога";
  if (state.startsWith("armed_")) return "Охрана включена";
  if (state === "disarmed") return "Без охраны";
  return device.stateLabel || "Состояние неизвестно";
}

const SECURITY_QUICK_FILTERS = [
  ["all", "Все"],
  ["attention", "Требует внимания"],
  ["access", "Доступ"],
  ["offline", "Без связи"],
];

function securityMatchesQuickFilter(device, filter) {
  if (filter === "attention") return securityNeedsAttention(device);
  if (filter === "access") return ["access", "windows"].includes(securityType(device));
  if (filter === "offline") return securityUnavailable(device);
  return true;
}

function renderLeakEmergency(panel, container, devices, deps) {
  const { el, setAttr } = deps;
  const leaks = devices.filter((device) => securityType(device) === "leaks" && securityNeedsAttention(device));
  if (!leaks.length || panel._securityLeakDismissed === true) return;
  const overlay = el("section", "security-leak-emergency");
  setAttr(overlay, "role", "alertdialog");
  setAttr(overlay, "aria-label", "Обнаружена протечка");
  const card = el("div", "security-leak-emergency-card");
  card.appendChild(el("span", "security-leak-emergency-eyebrow", "АВАРИЯ ВОДЫ"));
  card.appendChild(el("h2", null, "Обнаружена протечка"));
  card.appendChild(el("p", null, `${leaks.length} ${leaks.length === 1 ? "датчик сообщает" : "датчиков сообщают"} о воде. Проверьте помещение и состояние клапана.`));
  const list = el("ul", "security-leak-emergency-list");
  leaks.slice(0, 4).forEach((device) => list.appendChild(el("li", null, `${device.roomName || "Без комнаты"} · ${device.name || "Датчик протечки"}`)));
  card.appendChild(list);
  const dismiss = el("button", "security-leak-emergency-dismiss", "Понятно");
  dismiss.type = "button";
  dismiss.addEventListener("click", () => {
    panel._securityLeakDismissed = true;
    panel._notice = "Аварийное уведомление скрыто. Проверьте воду в помещении.";
    panel._renderHomeSection("security", panel._shell.homeSections.security);
  });
  card.appendChild(dismiss);
  overlay.appendChild(card);
  container.appendChild(overlay);
}

function renderSecurityHero(panel, container, devices, alarms, deps) {
  const activeAlarms = alarms.filter((alarm) => alarm.active === true);
  const attentionDevices = devices.filter(securityNeedsAttention);
  container.appendChild(createLibraryHero(panel, {
    eyebrow: "БЕЗОПАСНОСТЬ ДОМА",
    title: "Безопасность дома",
    subtitle: "Датчики, камеры, доступ и тревоги",
    warning: activeAlarms.length > 0 || attentionDevices.length > 0,
    facts: [
      { label: "Устройства", value: devices.length },
      { label: "Тревоги", value: activeAlarms.length, warning: activeAlarms.length > 0 },
      { label: "Требуют внимания", value: attentionDevices.length, warning: attentionDevices.length > 0 },
    ],
  }, deps));
}

function renderSecurityTypes(panel, container, devices, selected, choose, deps) {
  const { el, svgIcon, setAttr } = deps;
  const section = el("section", "security-canon-section");
  const heading = el("div", "security-canon-heading");
  heading.appendChild(el("h3", null, "Контуры безопасности"));
  const all = el("button", `security-canon-all${selected ? "" : " is-active"}`, "Все устройства");
  all.type = "button";
  all.addEventListener("click", () => choose(null));
  heading.appendChild(all);
  section.appendChild(heading);
  const grid = el("div", "security-canon-type-grid");
  Object.entries(TYPE_META).forEach(([id, meta]) => {
    const items = devices.filter((device) => securityType(device) === id);
    if (!items.length) return;
    const issues = items.filter(securityNeedsAttention).length;
    const card = el("button", `security-canon-type${selected === id ? " is-selected" : ""}${issues ? " has-attention" : ""}`);
    card.type = "button";
    setAttr(card, "aria-pressed", selected === id ? "true" : "false");
    card.addEventListener("click", () => choose(id));
    const title = el("span", "security-canon-type-title");
    title.appendChild(svgIcon(meta.icon));
    title.appendChild(el("strong", null, meta.label));
    title.appendChild(el("b", null, String(items.length)));
    card.appendChild(title);
    card.appendChild(el("span", issues ? "warn" : "ok", issues ? `${issues} требуют внимания` : meta.empty));
    grid.appendChild(card);
  });
  section.appendChild(grid);
  container.appendChild(section);
}

function renderSecurityQuickFilters(container, selected, choose, deps) {
  const { el, setAttr } = deps;
  const row = el("div", "security-quick-filters");
  SECURITY_QUICK_FILTERS.forEach(([id, label]) => {
    const button = el("button", `security-quick-filter${selected === id ? " is-active" : ""}`, label);
    button.type = "button";
    setAttr(button, "aria-pressed", selected === id ? "true" : "false");
    button.addEventListener("click", () => choose(id));
    row.appendChild(button);
  });
  container.appendChild(row);
}

function renderSecurityAttention(panel, container, devices, alarms, deps) {
  const { el } = deps;
  const items = devices.filter(securityNeedsAttention);
  const activeAlarms = alarms.filter((alarm) => alarm.active === true);
  const aside = el("aside", "security-canon-aside");
  const status = el("section", "security-canon-side-card");
  status.appendChild(el("h3", null, "Состояние"));
  const rows = [
    ["Охрана", devices.some((device) => String(device.state || "").startsWith("armed_")) ? "Включена" : "Без охраны"],
    ["Тревоги", String(activeAlarms.length)],
    ["Нет связи", String(devices.filter(securityUnavailable).length)],
  ];
  rows.forEach(([label, value]) => {
    const row = el("div", "security-canon-state-row");
    row.appendChild(el("span", null, label));
    row.appendChild(el("strong", null, value));
    status.appendChild(row);
  });
  aside.appendChild(status);
  const attentionCard = el("section", "security-canon-side-card");
  attentionCard.appendChild(el("h3", null, "Требуют внимания"));
  if (!items.length && !activeAlarms.length) {
    attentionCard.appendChild(el("p", "security-canon-empty", "Всё спокойно — проверять ничего не нужно."));
  } else {
    items.slice(0, 6).forEach((device) => {
      const line = el("button", "security-canon-attention");
      line.type = "button";
      line.appendChild(el("strong", null, device.name || "Устройство"));
      line.appendChild(el("span", null, `${device.roomName || "Без комнаты"} · ${securityStatus(device)}`));
      line.addEventListener("click", () => {
        const key = String(device.id || device.physicalId || device.entityId || "");
        if (key) panel._openHomeCards.add(`device:${key}`);
        panel._renderHomeSection("security", panel._shell.homeSections.security);
      });
      attentionCard.appendChild(line);
    });
  }
  aside.appendChild(attentionCard);
  return aside;
}

function renderSecurityDeviceCatalog(panel, container, devices, selectedType, quickFilter, deps) {
  const { el } = deps;
  const filtered = devices.filter((device) => (!selectedType || securityType(device) === selectedType)
    && securityMatchesQuickFilter(device, quickFilter));
  const deviceKey = (device) => String(device.id || device.physicalId || device.entityId || "");
  const anyCardOpen = filtered.some((device) => panel._openHomeCards.has(`device:${deviceKey(device)}`));
  const section = el("details", "security-canon-section security-canon-devices");
  section.open = Boolean(selectedType) || quickFilter !== "all" || anyCardOpen || panel._securityCatalogOpen === true;
  section.addEventListener("toggle", () => {
    panel._securityCatalogOpen = section.open;
  });
  const heading = el("summary", "security-canon-heading security-canon-devices-summary");
  heading.appendChild(el("h3", null, selectedType ? TYPE_META[selectedType].label : "Датчики и доступ"));
  heading.appendChild(el("span", null, `${filtered.length} физических устройств`));
  section.appendChild(heading);
  if (!filtered.length) {
    section.appendChild(el("p", "security-canon-empty", "Подходящие устройства пока не найдены."));
  } else {
    const grid = el("div", "inventory-device-grid security-canon-device-grid");
    filtered.forEach((device) => grid.appendChild(panel._deviceInventoryCard({
      ...device,
      stateLabel: securityStatus(device),
      tone: securityNeedsAttention(device) ? "warning" : "success",
    })));
    section.appendChild(grid);
  }
  container.appendChild(section);
}

export function renderSecurityOverview(panel, container, deps) {
  container.innerHTML = "";
  if (!panel._homeDashboard) {
    const empty = deps.el("section", "card empty-state security-canon-empty-state");
    empty.appendChild(deps.el("h2", null, "Безопасность"));
    empty.appendChild(deps.el("p", null, "Данные безопасности пока недоступны. Проверьте подключение Hausman Hub."));
    container.appendChild(empty);
    return;
  }
  const devices = securityDevices(panel);
  const alarms = Array.isArray(panel._homeDashboard.alarms) ? panel._homeDashboard.alarms : [];
  if (panel._securityTypeFilter === undefined) panel._securityTypeFilter = null;
  if (!panel._securityQuickFilter) panel._securityQuickFilter = "all";
  renderSecurityHero(panel, container, devices, alarms, deps);
  renderLeakEmergency(panel, container, devices, deps);
  const layout = deps.el("div", "security-canon-layout");
  const main = deps.el("div", "security-canon-main");
  const choose = (value) => {
    panel._securityTypeFilter = value;
    panel._renderHomeSection("security", panel._shell.homeSections.security);
  };
  const chooseQuick = (value) => {
    panel._securityQuickFilter = value;
    panel._renderHomeSection("security", panel._shell.homeSections.security);
  };
  renderSecurityQuickFilters(main, panel._securityQuickFilter, chooseQuick, deps);
  renderSecurityTypes(panel, main, devices, panel._securityTypeFilter, choose, deps);
  renderSecurityDeviceCatalog(panel, main, devices, panel._securityTypeFilter, panel._securityQuickFilter, deps);
  layout.appendChild(main);
  layout.appendChild(renderSecurityAttention(panel, layout, devices, alarms, deps));
  container.appendChild(layout);
}

renderSecurityOverview.securityDevices = securityDevices;
renderSecurityOverview.securityType = securityType;
renderSecurityOverview.securityStatus = securityStatus;
