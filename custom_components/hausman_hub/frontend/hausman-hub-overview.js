import { createHeroRoomNavigation } from "./hausman-hub-hero-room-navigation.js?v=1.52.141";
import { overviewHeroRenderKey, overviewHomeName, stableOverviewHeroImage } from "./hausman-hub-overview-hero-state.js?v=1.52.141";
import { renderHomeTargetCard } from "./hausman-hub-climate-overview.js?v=1.52.141";
import { scenarioIconMeta } from "./hausman-hub-scenario-icons.js?v=1.52.141";
import { openUpcomingEventsModal } from "./hausman-hub-overview-events-modal.js?v=1.52.141";
import { renderOverviewSideCards } from "./hausman-hub-overview-side.js?v=1.52.141";
import { renderOverviewUtilityCards } from "./hausman-hub-overview-utility-cards.js?v=1.52.141";

function validNumber(value) {
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
}

function average(values) {
  const valid = values.filter(validNumber).map(Number);
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null;
}

function physicalDeviceCount(devices) {
  return new Set(devices.map((device) => device.physicalId || device.id).filter(Boolean)).size;
}

function activeDeviceCount(devices) {
  return physicalDeviceCount(devices.filter((device) => device.active === true
    && device.unavailable !== true && device.state !== "unavailable"));
}

function compactNumber(value, digits = 1) {
  if (!validNumber(value)) return "—";
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function compactTemperature(value) {
  return validNumber(value) ? `${compactNumber(value)}°` : "—";
}

function compactPercent(value) {
  return validNumber(value) ? `${compactNumber(value)}%` : "—";
}

function plural(count, one, few, many) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

function weatherLabel(condition) {
  return ({
    "clear-night": "Ясно", cloudy: "Облачно", fog: "Туман", hail: "Град",
    lightning: "Гроза", "lightning-rainy": "Гроза с дождём", partlycloudy: "Переменная облачность",
    pouring: "Ливень", rainy: "Дождь", snowy: "Снег", "snowy-rainy": "Снег с дождём",
    sunny: "Ясно", windy: "Ветрено", "windy-variant": "Ветрено",
  })[String(condition || "").toLowerCase()] || "Погода уточняется";
}

function weatherSnapshot(dashboard) {
  const weather = dashboard.weather || {};
  const summary = dashboard.summary || {};
  return {
    condition: weather.condition || summary.weatherCondition,
    temperature: weather.temperatureC ?? summary.outdoorTemp,
    sensorTemperature: weather.outdoorSensorTemperatureC,
    humidity: weather.humidityPercent ?? summary.weatherHumidity,
    wind: weather.windSpeedMps ?? summary.weatherWindSpeed,
  };
}

function appendWeatherGlyph(container, condition, deps) {
  const normalized = String(condition || "").toLowerCase();
  const cloudy = !["sunny", "clear-night"].includes(normalized);
  const glyph = deps.el("span", `overview-tablet-weather-glyph${cloudy ? " is-cloudy" : ""}`);
  glyph.appendChild(deps.svgIcon(normalized === "clear-night" ? "moon" : "sun"));
  if (cloudy) glyph.appendChild(deps.el("span", "overview-tablet-weather-cloud"));
  container.appendChild(glyph);
}

export function upcomingTriggerLabel(triggerType) {
  return ({ time: "время", sunrise: "рассвет", sunset: "закат" })[String(triggerType || "")] || "время";
}

export function formatUpcomingRunTime(runAt) {
  const date = new Date(runAt);
  if (!Number.isFinite(date.getTime())) return "Время уточняется";
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(date);
}

export function formatUpcomingCountdown(runAt, now) {
  const date = new Date(runAt);
  if (!Number.isFinite(date.getTime())) return "Время уточняется";
  const nowMs = Number.isFinite(Number(now)) ? Number(now) : Date.now();
  const diffMs = date.getTime() - nowMs;
  if (diffMs <= 0) return "запуск сейчас";
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "менее чем через минуту";
  if (minutes < 60) return `через ${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  if (hours < 24) return restMinutes ? `через ${hours} ч ${restMinutes} мин` : `через ${hours} ч`;
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `через ${days} д ${restHours} ч` : `через ${days} д`;
}

export function upcomingEventsSorted(payload, limit = 5) {
  const events = payload && Array.isArray(payload.events) ? payload.events : [];
  const sorted = events
    .filter((event) => event && Number.isFinite(new Date(event.runAt).getTime()))
    .slice()
    .sort((left, right) => new Date(left.runAt) - new Date(right.runAt));
  return { visible: sorted.slice(0, limit), remaining: Math.max(0, sorted.length - limit) };
}

export function overviewGreeting(now = new Date()) {
  const hour = now.getHours();
  if (hour >= 5 && hour < 12) return "Доброе утро";
  if (hour >= 12 && hour < 18) return "Добрый день";
  if (hour >= 18 && hour < 23) return "Добрый вечер";
  return "Доброй ночи";
}

function iconButton(deps, className, iconName, label, onClick) {
  const button = deps.el("button", className);
  button.type = "button";
  deps.setAttr(button, "aria-label", label);
  button.appendChild(deps.svgIcon(iconName));
  if (onClick) button.addEventListener("click", onClick);
  return button;
}

function renderDashboardHeader(panel, readinessStatus, container, deps) {
  const now = new Date();
  const header = deps.el("header", "overview-tablet-header");
  const copy = deps.el("div", "overview-tablet-header-copy");
  copy.appendChild(deps.el("h2", null, overviewGreeting(now)));
  copy.appendChild(deps.el("span", `overview-tablet-header-status is-${readinessStatus === "ready" ? "ready" : "attention"}`,
    readinessStatus === "ready" ? "Все системы работают штатно" : "Состояние обновляется"));
  header.appendChild(copy);
  const pager = deps.el("span", "overview-tablet-page-dots");
  for (let index = 0; index < 3; index += 1) pager.appendChild(deps.el("i"));
  header.appendChild(pager);
  const clock = deps.el("div", "overview-tablet-header-clock");
  clock.appendChild(deps.el("span", null, now.toLocaleDateString("ru-RU", {
    weekday: "long", day: "numeric", month: "long",
  })));
  clock.appendChild(deps.el("strong", null, now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })));
  header.appendChild(clock);
  const actions = deps.el("div", "overview-tablet-header-actions");
  const upcoming = upcomingEventsSorted(panel._upcomingEvents, 100).visible.length;
  const events = deps.el("button", "overview-tablet-events");
  events.type = "button";
  events.appendChild(deps.svgIcon("history"));
  events.appendChild(deps.el("span", null, `События · ${upcoming}`));
  events.addEventListener("click", () => openUpcomingEventsModal(panel, container, deps,
    upcomingEventsSorted(panel._upcomingEvents, 20).visible, appendUpcomingEventRow));
  actions.appendChild(events);
  actions.appendChild(iconButton(deps, "overview-tablet-header-icon", "fullscreen",
    "Открыть режим киоска", deps.enterKiosk));
  actions.appendChild(iconButton(deps, "overview-tablet-header-icon is-refresh", "refresh",
    "Обновить главную", deps.refresh));
  const system = deps.el("span", `overview-tablet-system-state is-${readinessStatus === "ready" ? "ready" : "attention"}`);
  system.appendChild(deps.svgIcon("wifi"));
  system.appendChild(deps.svgIcon("cloud"));
  deps.setAttr(system, "aria-label", readinessStatus === "ready" ? "Система на связи" : "Связь уточняется");
  actions.appendChild(system);
  header.appendChild(actions);
  container.appendChild(header);
}

function renderHeroFacts(row, facts, deps) {
  row.innerHTML = "";
  facts.forEach(([iconName, value, label]) => {
    const fact = deps.el("span", "overview-tablet-hero-fact");
    fact.appendChild(deps.svgIcon(iconName));
    fact.appendChild(deps.el("strong", null, String(value)));
    fact.appendChild(deps.el("small", null, label));
    row.appendChild(fact);
  });
}

export function renderOverviewHero(panel, container, readiness, deps) {
  const readinessStatus = readiness?.status || "not_ready";
  const dashboard = panel._homeDashboard || {};
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  container.innerHTML = "";
  renderDashboardHeader(panel, readinessStatus, container, deps);
  const selectedRoom = rooms.find((room) => room.id === panel._overviewHeroRoomId) || null;
  if (panel._overviewHeroRoomId && !selectedRoom) panel._overviewHeroRoomId = null;
  const hero = deps.el("section", "overview-canon-hero");
  const media = deps.el("div", "overview-canon-hero-media");
  let currentImage = stableOverviewHeroImage(panel, selectedRoom, dashboard);
  media.style.backgroundImage = `url("${currentImage}")`;
  hero.appendChild(media);
  const overlay = deps.el("div", "overview-canon-hero-overlay");
  const copy = deps.el("div", "overview-canon-hero-copy");
  const title = deps.el("h1");
  copy.appendChild(title);
  const facts = deps.el("div", "overview-tablet-hero-facts");
  copy.appendChild(facts);
  const details = deps.el("button", "overview-canon-hero-action", "Подробнее о доме");
  details.type = "button";
  details.appendChild(deps.svgIcon("chevron-right"));
  details.addEventListener("click", () => {
    const room = rooms.find((candidate) => candidate.id === panel._overviewHeroRoomId);
    if (room) deps.openRoom(room);
    else panel._activateSection("rooms");
  });
  copy.appendChild(details);
  overlay.appendChild(copy);
  hero.appendChild(overlay);
  const controls = deps.el("div", "overview-tablet-hero-controls");
  const pin = iconButton(deps, `overview-tablet-hero-control${panel._overviewHeroPinned ? " is-active" : ""}`,
    "star", "Закрепить слайд");
  pin.addEventListener("click", () => {
    panel._overviewHeroPinned = !panel._overviewHeroPinned;
    pin.classList.toggle("is-active", panel._overviewHeroPinned);
  });
  controls.appendChild(pin);
  controls.appendChild(iconButton(deps, "overview-tablet-hero-control", "more",
    "Открыть настройки главной", () => panel._activateSection("settings")));
  hero.appendChild(controls);
  const roomNavigation = createHeroRoomNavigation(panel, rooms, deps);
  hero.appendChild(roomNavigation.element);
  const homeControl = iconButton(deps, "overview-tablet-hero-home", "home-filled", "Показать весь дом");
  hero.appendChild(homeControl);
  container.appendChild(hero);
  container.appendChild(renderOverviewSideCards(panel, dashboard, devices, deps));

  const selectHeroRoom = (room, animate = true) => {
    panel._overviewHeroRoomId = room?.id || null;
    const nextImage = stableOverviewHeroImage(panel, room, dashboard);
    if (nextImage !== currentImage) {
      if (animate) media.classList.add("is-changing");
      media.style.backgroundImage = `url("${nextImage}")`;
      currentImage = nextImage;
      const frame = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (callback) => callback();
      if (animate) frame(() => media.classList.remove("is-changing"));
    }
    roomNavigation.setActive(room, animate);
    if (!room) {
      title.textContent = overviewHomeName(dashboard);
      renderHeroFacts(facts, [
        ["rooms", rooms.length, plural(rooms.length, "комната", "комнаты", "комнат")],
        ["device", physicalDeviceCount(devices), "устройства"],
        ["check", activeDeviceCount(devices), "активно"],
        ["bolt", Array.isArray(dashboard.scenarios) ? dashboard.scenarios.length : 0, "автоматизации"],
      ], deps);
      details.hidden = false;
    } else {
      title.textContent = room.name;
      renderHeroFacts(facts, [
        ["thermometer", compactTemperature(room.temp), "температура"],
        ["auto", compactTemperature(room.targetTemp), "цель"],
        ["water", compactPercent(room.humidity), "влажность"],
      ], deps);
      details.hidden = false;
    }
    panel._overviewHeroRenderKey = overviewHeroRenderKey(panel, readiness);
  };
  roomNavigation.attachCarouselChrome(hero);
  roomNavigation.bind(selectHeroRoom);
  homeControl.addEventListener("click", () => selectHeroRoom(null));
  selectHeroRoom(selectedRoom, false);
}

function renderWeatherCard(panel, dashboard, deps) {
  const weather = weatherSnapshot(dashboard);
  const card = deps.el("button", "overview-canon-primary-card is-weather");
  card.type = "button";
  card.addEventListener("click", () => panel._activateSection("climate"));
  const head = deps.el("span", "overview-tablet-card-head");
  head.appendChild(deps.el("strong", null, "Погода"));
  head.appendChild(deps.el("span", null, weatherLabel(weather.condition)));
  card.appendChild(head);
  const body = deps.el("span", "overview-tablet-weather-body");
  appendWeatherGlyph(body, weather.condition, deps);
  const outside = deps.el("span", "overview-tablet-weather-main");
  outside.appendChild(deps.el("strong", null, compactTemperature(weather.temperature)));
  outside.appendChild(deps.el("small", null, "на улице"));
  body.appendChild(outside);
  const sensor = deps.el("span", "overview-tablet-weather-sensor");
  sensor.appendChild(deps.el("strong", null, compactTemperature(weather.sensorTemperature)));
  sensor.appendChild(deps.el("small", null, "датчик"));
  body.appendChild(sensor);
  card.appendChild(body);
  const footer = deps.el("span", "overview-tablet-weather-footer");
  [["water", compactPercent(weather.humidity), "Влажность"],
    ["air", validNumber(weather.wind) ? `${compactNumber(weather.wind)} м/с` : "—", "Ветер"]]
    .forEach(([iconName, value, label]) => {
      const metric = deps.el("span");
      metric.appendChild(deps.svgIcon(iconName));
      const metricCopy = deps.el("span");
      metricCopy.appendChild(deps.el("strong", null, value));
      metricCopy.appendChild(deps.el("small", null, label));
      metric.appendChild(metricCopy);
      footer.appendChild(metric);
    });
  card.appendChild(footer);
  return card;
}

function renderComfortCard(panel, dashboard, deps) {
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const comfort = dashboard.comfort || {};
  const available = comfort.available === true && validNumber(comfort.score);
  const card = deps.el("button", `overview-canon-primary-card is-comfort${available ? "" : " is-empty"}`);
  card.type = "button";
  card.addEventListener("click", () => panel._activateSection("climate"));
  const head = deps.el("span", "overview-tablet-comfort-head");
  head.appendChild(deps.svgIcon("leaf"));
  head.appendChild(deps.el("strong", null, "Комфорт в доме"));
  card.appendChild(head);
  const score = deps.el("span", "overview-tablet-comfort-score");
  score.appendChild(deps.el("strong", null, available ? String(Math.round(Number(comfort.score))) : "—"));
  score.appendChild(deps.el("small", null, "из 100"));
  const primary = deps.el("span", "overview-tablet-comfort-primary");
  primary.appendChild(score);
  primary.appendChild(deps.el("span", "overview-tablet-comfort-status",
    available ? (comfort.statusLabel || "Нет оценки") : "Нет данных"));
  card.appendChild(primary);
  const facts = deps.el("span", "overview-tablet-comfort-facts");
  [
    ["thermometer", compactTemperature(dashboard.summary?.avgTemp ?? average(rooms.map((room) => room.temp))), "Темп."],
    ["water", compactPercent(average(rooms.map((room) => room.humidity))), "Влажн."],
    ["leaf", validNumber(dashboard.summary?.co2) ? compactNumber(dashboard.summary.co2, 0) : "—", "CO₂ ppm"],
  ].forEach(([iconName, value, label]) => {
    const fact = deps.el("span");
    fact.appendChild(deps.svgIcon(iconName));
    fact.appendChild(deps.el("strong", null, value));
    fact.appendChild(deps.el("small", null, label));
    facts.appendChild(fact);
  });
  card.appendChild(facts);
  return card;
}

function renderPrimaryCards(panel, container, dashboard, deps) {
  const row = deps.el("div", "overview-canon-primary-grid");
  row.appendChild(renderWeatherCard(panel, dashboard, deps));
  const target = deps.el("section", "overview-canon-primary-card is-target");
  target.appendChild(renderHomeTargetCard(panel, dashboard, deps, { embedded: true }));
  row.appendChild(target);
  row.appendChild(renderComfortCard(panel, dashboard, deps));
  container.appendChild(row);
}

function renderFavorites(panel, container, dashboard, deps) {
  const source = Array.isArray(dashboard.scenarios) ? dashboard.scenarios
    : panel._scenarios.list && Array.isArray(panel._scenarios.list.scenarios) ? panel._scenarios.list.scenarios : [];
  const favorites = source.filter((scenario) => scenario.favorite === true).slice(0, 4);
  const section = deps.el("section", "overview-canon-favorites");
  const head = deps.el("div", "overview-canon-section-head");
  const title = deps.el("h2");
  title.appendChild(deps.svgIcon("star"));
  title.appendChild(deps.el("span", null, "Избранное"));
  head.appendChild(title);
  const all = deps.el("button", "overview-canon-link", "Все сценарии");
  all.type = "button";
  all.appendChild(deps.svgIcon("chevron-right"));
  all.addEventListener("click", () => panel._activateSection("scenarios"));
  head.appendChild(all);
  section.appendChild(head);
  const list = deps.el("div", "overview-canon-favorite-grid");
  if (!favorites.length) {
    const empty = deps.el("button", "overview-canon-favorite is-empty", "Добавьте сценарии в избранное");
    empty.type = "button";
    empty.addEventListener("click", () => panel._activateSection("scenarios"));
    list.appendChild(empty);
  } else {
    favorites.forEach((scenario) => {
      const item = deps.el("button", "overview-canon-favorite");
      item.type = "button";
      item.disabled = panel._busy || scenario.enabled === false;
      deps.setAttr(item, "aria-label", `Запустить сценарий «${scenario.title}»`);
      const meta = scenarioIconMeta(scenario.icon, scenario.title);
      const icon = deps.el("span", "overview-canon-favorite-icon");
      icon.appendChild(deps.svgIcon(meta.glyph));
      item.appendChild(icon);
      const copy = deps.el("span", "overview-canon-favorite-copy");
      copy.appendChild(deps.el("strong", null, scenario.title));
      copy.appendChild(deps.el("small", null, scenario.description || scenario.group || "Ручной запуск"));
      item.appendChild(copy);
      item.appendChild(deps.svgIcon("play"));
      item.addEventListener("click", () => panel._post(deps.runApi, { scenario_id: scenario.id },
        scenario.requiresConfirmation ? `Запустить сценарий «${scenario.title}»?` : null));
      list.appendChild(item);
    });
  }
  section.appendChild(list);
  container.appendChild(section);
}

function appendUpcomingEventRow(panel, list, event, deps) {
  const row = deps.el("div", "overview-canon-upcoming-event");
  const icon = deps.el("span", "overview-canon-favorite-icon");
  icon.appendChild(deps.svgIcon("play"));
  row.appendChild(icon);
  const copy = deps.el("span", "overview-canon-upcoming-copy");
  copy.appendChild(deps.el("strong", null, event.scenarioTitle || "Сценарий"));
  copy.appendChild(deps.el("small", null, upcomingTriggerLabel(event.triggerType)));
  row.appendChild(copy);
  const timing = deps.el("span", "overview-canon-upcoming-time");
  timing.appendChild(deps.el("strong", null, formatUpcomingRunTime(event.runAt)));
  const countdown = deps.el("small", "overview-canon-upcoming-countdown",
    formatUpcomingCountdown(event.runAt, Date.now()));
  deps.setAttr(countdown, "data-upcoming-run-at", event.runAt || "");
  timing.appendChild(countdown);
  row.appendChild(timing);
  if (event.cancellable === true && deps.upcomingCancelApi) {
    const cancel = deps.el("button", "overview-canon-upcoming-cancel", "Пропустить");
    cancel.type = "button";
    cancel.disabled = panel._busy === true;
    cancel.addEventListener("click", () => panel._post(deps.upcomingCancelApi,
      { scenarioId: event.scenarioId, triggerId: event.triggerId, runAt: event.runAt },
      `Пропустить запуск «${event.scenarioTitle || "Сценарий"}» ${formatUpcomingRunTime(event.runAt)}?`));
    row.appendChild(cancel);
  }
  list.appendChild(row);
}

export function renderUpcomingEvents(panel, container, deps) {
  const { visible, remaining } = upcomingEventsSorted(panel._upcomingEvents);
  const section = deps.el("section", "overview-canon-upcoming");
  const head = deps.el("div", "overview-canon-section-head");
  head.appendChild(deps.el("h2", null, "Ближайшие события"));
  section.appendChild(head);
  if (!visible.length) {
    section.appendChild(deps.el("div", "card empty-state muted", "Нет запланированных событий"));
    container.appendChild(section);
    return;
  }
  const list = deps.el("div", "overview-canon-upcoming-list");
  visible.forEach((event) => appendUpcomingEventRow(panel, list, event, deps));
  section.appendChild(list);
  if (remaining > 0) section.appendChild(deps.el("div", "overview-canon-upcoming-more", `и ещё ${remaining}`));
  container.appendChild(section);
}

export function renderOverviewContent(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard || {};
  renderPrimaryCards(panel, container, dashboard, deps);
  renderFavorites(panel, container, dashboard, deps);
  renderOverviewUtilityCards(panel, container, dashboard, deps);
  renderUpcomingEvents(panel, container, deps);
}
