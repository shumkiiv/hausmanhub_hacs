import { activityTimeLabel } from "./hausman-hub-pagination.js?v=1.52.148";

function plural(count, one, few, many) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

function isOffline(device) {
  return device?.unavailable === true || device?.state === "unavailable";
}

function isOpen(device) {
  return ["on", "open", "opened", "unlocked"].includes(String(device?.state || "").toLowerCase());
}

function physicalCount(devices) {
  return new Set(devices.map((device) => device?.physicalId || device?.id).filter(Boolean)).size;
}

function formatPower(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number).toLocaleString("ru-RU")} Вт` : "нет данных";
}

function eventTimestamp(entry) {
  const raw = entry?.at ?? entry?.ts ?? entry?.timestamp;
  const numeric = Number(raw);
  return Number.isFinite(numeric) && numeric > 0 && numeric < 1e12 ? numeric * 1000 : raw;
}

function eventIcon(entry) {
  const explicit = String(entry?.icon || "").toLowerCase();
  if (["warning", "bolt", "settings", "history", "lightbulb", "shield"].includes(explicit)) return explicit;
  const identity = `${entry?.title || ""} ${entry?.message || entry?.text || ""}`.toLowerCase();
  if (/сценар|голос|настрой/.test(identity)) return "settings";
  if (/свет|ламп/.test(identity)) return "lightbulb";
  if (/тревог|охран|двер|окн/.test(identity)) return "shield";
  return "history";
}

function activityEntries(panel, dashboard) {
  const live = Array.isArray(panel._activityFeed) ? panel._activityFeed : [];
  const snapshot = Array.isArray(dashboard.events) ? dashboard.events : [];
  return (live.length ? live : snapshot).slice(0, 12).map((entry) => ({
    icon: eventIcon(entry),
    title: String(entry?.title || "Событие"),
    text: String(entry?.text || entry?.message || "").trim(),
    at: eventTimestamp(entry),
    alert: entry?.alert === true || entry?.level === "bad",
  }));
}

function appendCompactHome(card, state, deps) {
  const grid = deps.el("div", "overview-tablet-home-compact");
  [
    ["door", state.open.length, "Открыто", state.open.length ? "warning" : "neutral"],
    ["warning", state.offline.length, "Офлайн", state.offline.length ? "warning" : "neutral"],
    ["lightbulb", state.activeLights, "Свет", state.activeLights ? "warning" : "neutral"],
    ["energy", Number.isFinite(Number(state.power)) ? Math.round(Number(state.power)) : "—", "Вт", "neutral"],
  ].forEach(([iconName, value, label, tone]) => {
    const tile = deps.el("div", `overview-tablet-home-tile is-${tone}`);
    const main = deps.el("div", "overview-tablet-home-tile-main");
    main.appendChild(deps.svgIcon(iconName));
    const valueZone = deps.el("span", "overview-tablet-home-value-zone");
    valueZone.appendChild(deps.el("strong", null, String(value)));
    main.appendChild(valueZone);
    tile.appendChild(main);
    tile.appendChild(deps.el("span", null, label));
    grid.appendChild(tile);
  });
  card.appendChild(grid);
}

function appendDetailedHome(card, state, deps) {
  const list = deps.el("div", "overview-tablet-home-detailed");
  const openLabel = state.open.length
    ? `Открыто: ${state.open.map((device) => device.roomName || device.name || "проём").slice(0, 2).join(", ")}`
    : (state.openings.length ? "Все окна и двери закрыты" : "Нет данных об окнах и дверях");
  [
    ["door", openLabel, state.open.length ? "warning" : "neutral"],
    ["warning", `${state.offline.length} ${plural(state.offline.length, "устройство", "устройства", "устройств")} без связи`, state.offline.length ? "warning" : "neutral"],
    ["lightbulb", `${state.activeLights} ${plural(state.activeLights, "светильник горит", "светильника горят", "светильников горят")}`, state.activeLights ? "warning" : "neutral"],
    ["energy", `Потребление: ${formatPower(state.power)}`, "neutral"],
    ["bolt", `Активные сценарии: ${state.scenarioCount}`, "neutral"],
  ].forEach(([iconName, value, tone]) => {
    const row = deps.el("div", `overview-tablet-home-detail is-${tone}`);
    row.appendChild(deps.svgIcon(iconName));
    row.appendChild(deps.el("span", null, value));
    list.appendChild(row);
  });
  card.appendChild(list);
}

function appendActivityCards(card, entries, compact, deps) {
  const list = deps.el("div", compact ? "overview-tablet-activity-compact" : "overview-tablet-activity-detailed");
  const rows = deps.el("div", "overview-tablet-activity-rows");
  if (!entries.length) rows.appendChild(deps.el("div", "overview-tablet-activity-empty", "Событий пока нет"));
  entries.forEach((entry) => {
    const row = deps.el("div", `overview-tablet-activity-row${entry.alert ? " is-alert" : ""}`);
    const icon = deps.el("span", "overview-tablet-activity-icon");
    icon.appendChild(deps.svgIcon(entry.icon));
    row.appendChild(icon);
    const copy = deps.el("span", "overview-tablet-activity-copy");
    copy.appendChild(deps.el("strong", null, entry.title));
    if (compact) copy.appendChild(deps.el("time", null, activityTimeLabel(entry.at)));
    else if (entry.text) copy.appendChild(deps.el("small", null, entry.text));
    row.appendChild(copy);
    if (!compact) row.appendChild(deps.el("time", null, activityTimeLabel(entry.at)));
    rows.appendChild(row);
  });
  list.appendChild(rows);
  const footer = deps.el("div", "overview-tablet-activity-footer", compact ? "История" : "Вся активность");
  footer.appendChild(deps.svgIcon("chevron-right"));
  list.appendChild(footer);
  card.appendChild(list);
}

export function renderOverviewSideCards(panel, dashboard, devices, deps) {
  const openings = devices.filter((device) => ["door", "window", "opening"].includes(device?.category)
    || device?.domain === "lock");
  const open = openings.filter(isOpen);
  const offline = devices.filter(isOffline);
  const lights = devices.filter((device) => device?.domain === "light" || device?.category === "lighting");
  const activeLights = physicalCount(lights.filter((device) => !isOffline(device) && device.active === true));
  const state = {
    openings,
    open,
    offline,
    activeLights,
    power: dashboard.energy?.currentPowerW,
    scenarioCount: Array.isArray(dashboard.scenarios) ? dashboard.scenarios.length : 0,
  };
  const aside = deps.el("aside", "overview-tablet-sidebar");
  const home = deps.el("section", "overview-tablet-side-card is-home-now");
  home.appendChild(deps.el("h2", null, "Дом сейчас"));
  appendCompactHome(home, state, deps);
  appendDetailedHome(home, state, deps);
  aside.appendChild(home);
  const activity = deps.el("section", "overview-tablet-side-card is-activity");
  const activityTitle = deps.el("h2");
  activityTitle.appendChild(deps.el("span", "overview-tablet-side-title-compact", "Активность"));
  activityTitle.appendChild(deps.el("span", "overview-tablet-side-title-detailed", "Последняя активность"));
  activity.appendChild(activityTitle);
  const entries = activityEntries(panel, dashboard);
  appendActivityCards(activity, entries, true, deps);
  appendActivityCards(activity, entries, false, deps);
  aside.appendChild(activity);
  return aside;
}
