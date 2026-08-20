import { createHeroRoomNavigation } from "./hausman-hub-hero-room-navigation.js?v=1.52.118";
import { overviewHeroRenderKey, stableOverviewHeroImage } from "./hausman-hub-overview-hero-state.js?v=1.52.118";
import { renderHomeTargetCard } from "./hausman-hub-climate-overview.js?v=1.52.118";

const CLIMATE_DOMAINS = new Set(["climate", "humidifier", "fan"]);

function validNumbers(values) {
  return values
    .filter((value) => value !== null && value !== undefined && value !== "")
    .map(Number)
    .filter(Number.isFinite);
}

function validNumber(value) {
  return value !== null && value !== undefined && value !== ""
    && Number.isFinite(Number(value));
}

function average(values) {
  const valid = validNumbers(values);
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null;
}

function physicalDeviceCount(devices) {
  return new Set(devices.map((device) => device.physicalId || device.id).filter(Boolean)).size;
}

function activeCount(devices, predicate = () => true) {
  return devices.filter((device) => predicate(device) && !device.unavailable && device.active === true).length;
}

function weatherLabel(condition) {
  return ({
    "clear-night": "Ясно", cloudy: "Облачно", fog: "Туман", hail: "Град",
    lightning: "Гроза", "lightning-rainy": "Гроза с дождём", partlycloudy: "Переменная облачность",
    pouring: "Ливень", rainy: "Дождь", snowy: "Снег", "snowy-rainy": "Снег с дождём",
    sunny: "Ясно", windy: "Ветрено", "windy-variant": "Ветрено",
  })[String(condition || "").toLowerCase()] || "Погода уточняется";
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

function cardButton(deps, className, target, panel) {
  const card = deps.el("button", className);
  card.type = "button";
  card.addEventListener("click", () => panel._activateSection(target));
  return card;
}

function appendMetric(deps, card, label, value, supporting) {
  if (value === "Нет данных") card.classList.add("is-empty");
  card.appendChild(deps.el("span", "overview-canon-label", label));
  card.appendChild(deps.el("strong", "overview-canon-value", value));
  card.appendChild(deps.el("span", "overview-canon-supporting", supporting));
}

export function renderOverviewHero(panel, container, readiness, deps) {
  const { el, svgIcon, setAttr } = deps;
  const readinessStatus = readiness?.status || "not_ready";
  const dashboard = panel._homeDashboard || {};
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const scenarios = Array.isArray(dashboard.scenarios) ? dashboard.scenarios : [];
  container.innerHTML = "";
  const selectedRoom = rooms.find((room) => room.id === panel._overviewHeroRoomId) || null;
  if (panel._overviewHeroRoomId && !selectedRoom) panel._overviewHeroRoomId = null;
  const hero = el("section", "overview-canon-hero");
  const media = el("div", "overview-canon-hero-media");
  let currentImage = stableOverviewHeroImage(panel, selectedRoom, dashboard);
  media.style.backgroundImage = `url("${currentImage}")`;
  hero.appendChild(media);
  const overlay = el("div", "overview-canon-hero-overlay");
  const copy = el("div", "overview-canon-hero-copy");
  const homeName = dashboard.summary && dashboard.summary.homeName || "Дом";
  const eyebrow = el("span", "overview-canon-eyebrow");
  const title = el("h1");
  copy.appendChild(eyebrow);
  copy.appendChild(title);
  const facts = el("div", "overview-canon-hero-facts");
  copy.appendChild(facts);
  const details = el("button", "overview-canon-hero-action", "Подробнее о доме");
  details.type = "button";
  details.addEventListener("click", () => {
    const room = rooms.find((candidate) => candidate.id === panel._overviewHeroRoomId);
    if (room) deps.openRoom(room);
    else panel._activateSection("rooms");
  });
  copy.appendChild(details);
  overlay.appendChild(copy);
  const status = el("span", `overview-canon-state${readinessStatus === "ready" ? " is-ready" : " is-attention"}`,
    readinessStatus === "ready" ? "Всё в порядке" : "Проверьте настройки");
  overlay.appendChild(status);
  hero.appendChild(overlay);
  const roomNavigation = createHeroRoomNavigation(panel, rooms, { el, setAttr, svgIcon });
  hero.appendChild(roomNavigation.element);
  const top = el("div", "overview-canon-top-grid");
  top.appendChild(hero);
  const side = el("aside", "overview-canon-top-side");
  const weather = dashboard.weather || {};
  const weatherCard = el("button", "overview-canon-top-card is-weather");
  weatherCard.type = "button";
  weatherCard.addEventListener("click", () => panel._activateSection("climate"));
  weatherCard.appendChild(el("span", "overview-canon-panel-label", "Погода"));
  weatherCard.appendChild(el("strong", null, Number.isFinite(Number(weather.temperatureC)) ? panel._temp(weather.temperatureC) : "Нет данных"));
  weatherCard.appendChild(el("span", "overview-canon-panel-supporting", `${weatherLabel(weather.condition)} · ветер ${Number.isFinite(Number(weather.windSpeedMps)) ? `${Number(weather.windSpeedMps).toLocaleString("ru-RU")} м/с` : "—"}`));
  side.appendChild(weatherCard);
  const alarms = Array.isArray(dashboard.alarms) ? dashboard.alarms.filter((alarm) => alarm.active) : [];
  const offline = devices.filter((device) => device.unavailable === true || device.state === "unavailable").length;
  const lights = activeCount(devices, (device) => device.domain === "light" || device.category === "lighting");
  const homeCard = el("button", `overview-canon-top-card is-home${alarms.length ? " is-alert" : ""}`);
  homeCard.type = "button";
  homeCard.addEventListener("click", () => panel._activateSection("security"));
  homeCard.appendChild(el("span", "overview-canon-panel-label", "Дом сейчас"));
  homeCard.appendChild(el("strong", null, alarms.length ? `${alarms.length} тревог` : "Спокойно"));
  const homeFacts = el("span", "overview-canon-status-list");
  [["Связь", offline ? `${offline} недоступно` : "все устройства"], ["Свет", lights ? `${lights} включено` : "выключен"]]
    .forEach(([label, value]) => { const row = el("span"); row.appendChild(el("small", null, label)); row.appendChild(el("b", null, value)); homeFacts.appendChild(row); });
  homeCard.appendChild(homeFacts);
  side.appendChild(homeCard);
  top.appendChild(side);
  container.appendChild(top);

  const formatTemperature = (value) => validNumber(value) ? `${Number(value).toFixed(1).replace(".0", "").replace(".", ",")} °C` : "Нет данных";
  const formatHumidity = (value) => validNumber(value) ? `${Math.round(Number(value))} %` : null;
  const renderFact = (iconName, value, label) => {
    const fact = el("span", "overview-canon-hero-fact");
    fact.appendChild(svgIcon(iconName));
    fact.appendChild(el("strong", null, value));
    fact.appendChild(el("small", null, label));
    facts.appendChild(fact);
  };
  const selectHeroRoom = (room, animate = true) => {
    panel._overviewHeroRoomId = room?.id || null;
    const nextImage = stableOverviewHeroImage(panel, room, dashboard);
    if (nextImage !== currentImage) {
      if (animate) media.classList.add("is-changing");
      media.style.backgroundImage = `url("${nextImage}")`;
      currentImage = nextImage;
      if (animate) {
        const scheduleFrame = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (callback) => callback();
        scheduleFrame(() => media.classList.remove("is-changing"));
      }
    }
    roomNavigation.setActive(room, animate);
    facts.innerHTML = "";
    if (!room) {
      eyebrow.textContent = readinessStatus === "ready" ? "Дом работает штатно" : "Требуется внимание";
      title.textContent = homeName;
      renderFact("rooms", String(rooms.length), "комнат");
      renderFact("device", String(physicalDeviceCount(devices)), "устройств");
      renderFact("energy", String(activeCount(devices)), "активно");
      renderFact("play", String(scenarios.length), "сценариев");
      details.hidden = false;
      details.textContent = "Подробнее о доме";
      status.textContent = readinessStatus === "ready" ? "Всё в порядке" : "Проверьте настройки";
      panel._overviewHeroRenderKey = overviewHeroRenderKey(panel, readiness);
      return;
    }
    eyebrow.textContent = "Состояние комнаты";
    title.textContent = room.name;
    renderFact("thermometer", formatTemperature(room.temp), "температура");
    renderFact("water", formatHumidity(room.humidity) || "Нет данных", "влажность");
    renderFact("thermometer", formatTemperature(room.targetTemp), "цель");
    renderFact("device", String(Array.isArray(room.deviceIds) ? room.deviceIds.length : 0), "устройств");
    details.hidden = true;
    status.textContent = room.climateRunning ? "Климат работает" : (room.status || "Обычный режим");
    panel._overviewHeroRenderKey = overviewHeroRenderKey(panel, readiness);
  };
  roomNavigation.bind(selectHeroRoom);
  selectHeroRoom(selectedRoom, false);
}

function renderPrimaryCards(panel, container, dashboard, deps) {
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const climate = devices.filter((device) => CLIMATE_DOMAINS.has(device.domain) || device.category === "climate" || device.category === "air_quality");
  const row = deps.el("div", "overview-canon-primary-grid");
  const climateCard = cardButton(deps, "overview-canon-primary-card is-climate", "climate", panel);
  appendMetric(deps, climateCard, "Климат", panel._temp(average(rooms.map((room) => room.temp))),
    `Цель ${panel._temp(average(rooms.map((room) => room.targetTemp)))} · влажность ${panel._humidity(average(rooms.map((room) => room.humidity)))}`);
  const climateFacts = deps.el("span", "overview-canon-card-facts");
  [["Цель", panel._temp(average(rooms.map((room) => room.targetTemp)))],
    ["Влажность", panel._humidity(average(rooms.map((room) => room.humidity)))],
    ["Работает", String(activeCount(climate))]].forEach(([label, value]) => {
    const fact = deps.el("span"); fact.appendChild(deps.el("strong", null, value)); fact.appendChild(deps.el("small", null, label)); climateFacts.appendChild(fact);
  });
  climateCard.appendChild(climateFacts);
  row.appendChild(climateCard);
  row.appendChild(renderHomeTargetCard(panel, dashboard, deps));
  const lights = devices.filter((device) => device.domain === "light" || device.category === "lighting");
  const lightingCard = cardButton(deps, "overview-canon-primary-card is-lighting", "lighting", panel);
  appendMetric(deps, lightingCard, "Освещение", String(activeCount(lights)), `из ${physicalDeviceCount(lights)} устройств включено`);
  row.appendChild(lightingCard);
  const alarms = Array.isArray(dashboard.alarms) ? dashboard.alarms.filter((alarm) => alarm.active) : [];
  const securityCard = cardButton(deps, `overview-canon-primary-card is-security${alarms.length ? " is-alert" : ""}`, "security", panel);
  appendMetric(deps, securityCard, "Безопасность", alarms.length ? `${alarms.length} тревог` : "Спокойно",
    alarms.length ? "Требуется внимание" : "Активных тревог нет");
  row.appendChild(securityCard);
  container.appendChild(row);
}

function renderFavorites(panel, container, dashboard, deps) {
  const source = Array.isArray(dashboard.scenarios) ? dashboard.scenarios
    : panel._scenarios.list && Array.isArray(panel._scenarios.list.scenarios) ? panel._scenarios.list.scenarios : [];
  const favorites = source.filter((scenario) => scenario.favorite === true).slice(0, 4);
  const section = deps.el("section", "overview-canon-favorites");
  const head = deps.el("div", "overview-canon-section-head");
  head.appendChild(deps.el("h2", null, "Избранные сценарии"));
  const all = deps.el("button", "overview-canon-link", "Все сценарии");
  all.type = "button"; all.addEventListener("click", () => panel._activateSection("scenarios")); head.appendChild(all);
  section.appendChild(head);
  const list = deps.el("div", "overview-canon-favorite-grid");
  if (!favorites.length) {
    const empty = deps.el("button", "overview-canon-favorite is-empty", "Добавьте сценарии в избранное");
    empty.type = "button"; empty.addEventListener("click", () => panel._activateSection("scenarios")); list.appendChild(empty);
  } else favorites.forEach((scenario) => {
    const item = deps.el("div", "overview-canon-favorite");
    const icon = deps.el("span", "overview-canon-favorite-icon"); icon.appendChild(deps.svgIcon("play")); item.appendChild(icon);
    const copy = deps.el("span", "overview-canon-favorite-copy");
    copy.appendChild(deps.el("strong", null, scenario.title));
    copy.appendChild(deps.el("small", null, scenario.description || scenario.group || "Готов к запуску"));
    item.appendChild(copy);
    const run = deps.el("button", "overview-canon-play"); run.type = "button"; run.appendChild(deps.svgIcon("play"));
    deps.setAttr(run, "aria-label", `Запустить сценарий «${scenario.title}»`);
    run.disabled = panel._busy || scenario.enabled === false;
    run.addEventListener("click", () => panel._post(deps.runApi, { scenario_id: scenario.id },
      scenario.requiresConfirmation ? `Запустить сценарий «${scenario.title}»?` : null));
    item.appendChild(run); list.appendChild(item);
  });
  section.appendChild(list); container.appendChild(section);
}

export function renderUpcomingEvents(panel, container, deps) {
  const { el, svgIcon, setAttr } = deps;
  const { visible, remaining } = upcomingEventsSorted(panel._upcomingEvents);
  const section = el("section", "overview-canon-upcoming");
  const head = el("div", "overview-canon-section-head");
  head.appendChild(el("h2", null, "Ближайшие события"));
  const all = el("button", "overview-canon-link", "Все сценарии");
  all.type = "button"; all.addEventListener("click", () => panel._activateSection("scenarios")); head.appendChild(all);
  section.appendChild(head);
  if (!visible.length) {
    section.appendChild(el("div", "card empty-state muted", "Нет запланированных событий"));
    container.appendChild(section);
    return;
  }
  const list = el("div", "overview-canon-upcoming-list");
  visible.forEach((event) => {
    const row = el("div", "overview-canon-upcoming-event");
    const icon = el("span", "overview-canon-favorite-icon"); icon.appendChild(svgIcon("play")); row.appendChild(icon);
    const copy = el("span", "overview-canon-upcoming-copy");
    copy.appendChild(el("strong", null, event.scenarioTitle || "Сценарий"));
    copy.appendChild(el("small", null, upcomingTriggerLabel(event.triggerType)));
    row.appendChild(copy);
    const timing = el("span", "overview-canon-upcoming-time");
    timing.appendChild(el("strong", null, formatUpcomingRunTime(event.runAt)));
    const countdown = el("small", "overview-canon-upcoming-countdown", formatUpcomingCountdown(event.runAt, Date.now()));
    setAttr(countdown, "data-upcoming-run-at", event.runAt || "");
    timing.appendChild(countdown);
    row.appendChild(timing);
    if (event.cancellable === true) {
      const cancel = el("button", "overview-canon-upcoming-cancel", "Пропустить");
      cancel.type = "button";
      cancel.disabled = panel._busy === true;
      setAttr(cancel, "aria-label", `Пропустить запуск сценария «${event.scenarioTitle || "Сценарий"}»`);
      cancel.addEventListener("click", () => panel._post(deps.upcomingCancelApi,
        { scenarioId: event.scenarioId, triggerId: event.triggerId, runAt: event.runAt },
        `Пропустить запуск «${event.scenarioTitle || "Сценарий"}» ${formatUpcomingRunTime(event.runAt)}?`));
      row.appendChild(cancel);
    }
    list.appendChild(row);
  });
  section.appendChild(list);
  if (remaining > 0) section.appendChild(el("div", "overview-canon-upcoming-more", `и ещё ${remaining}`));
  container.appendChild(section);
}

function renderDashboardGrid(panel, container, dashboard, deps) {
  const layout = deps.el("div", "overview-canon-dashboard-grid");
  const main = deps.el("div", "overview-canon-dashboard-main");
  renderPrimaryCards(panel, main, dashboard, deps);
  renderFavorites(panel, main, dashboard, deps);
  renderUpcomingEvents(panel, main, deps);
  layout.appendChild(main);
  const side = deps.el("aside", "overview-canon-dashboard-side");
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const offline = devices.filter((device) => device.unavailable === true || device.state === "unavailable").length;
  const energyWrap = deps.el("div", "overview-canon-dashboard-energy");
  deps.renderEnergyOverviewCard(panel, energyWrap);
  side.appendChild(energyWrap);
  if (offline) {
    const devicesCard = deps.el("button", "overview-canon-dashboard-panel is-devices is-alert");
    devicesCard.type = "button";
    devicesCard.addEventListener("click", () => panel._activateSection("devices"));
    appendMetric(deps, devicesCard, "Требуют внимания", String(offline), "Устройства без связи");
    side.appendChild(devicesCard);
  }
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const deviations = rooms.map((room) => validNumber(room.temp) && validNumber(room.targetTemp)
    ? Math.abs(Number(room.temp) - Number(room.targetTemp)) : null).filter(Number.isFinite);
  const stable = deviations.length && Math.max(...deviations) <= 1.5;
  const comfortCard = cardButton(deps, "overview-canon-dashboard-panel is-comfort", "climate", panel);
  appendMetric(deps, comfortCard, "Комфорт в доме", stable ? "В порядке" : (deviations.length ? "Выравнивается" : "Нет данных"),
    stable ? "Температура близка к целям комнат" : "Откройте климат для подробностей");
  side.appendChild(comfortCard);
  layout.appendChild(side);
  container.appendChild(layout);
}

export function renderOverviewContent(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard || {};
  renderDashboardGrid(panel, container, dashboard, deps);
}
