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

function range(values, suffix, fraction = 1) {
  const valid = [...new Set(validNumbers(values).map((value) => value.toFixed(fraction)))];
  if (!valid.length) return "Нет данных";
  if (valid.length === 1) return `${valid[0].replace(".", ",")}${suffix}`;
  const sorted = valid.map(Number).sort((left, right) => left - right);
  return `${sorted[0].toFixed(fraction).replace(".", ",")}–${sorted.at(-1).toFixed(fraction).replace(".", ",")}${suffix}`;
}

function physicalDeviceCount(devices) {
  return new Set(devices.map((device) => device.physicalId || device.id).filter(Boolean)).size;
}

function activeCount(devices, predicate = () => true) {
  return devices.filter((device) => predicate(device) && !device.unavailable && (
    device.active === true || !["off", "idle", "standby", "unknown", "unavailable"].includes(device.state)
  )).length;
}

function weatherLabel(condition) {
  return ({
    "clear-night": "Ясно", cloudy: "Облачно", fog: "Туман", hail: "Град",
    lightning: "Гроза", "lightning-rainy": "Гроза с дождём", partlycloudy: "Переменная облачность",
    pouring: "Ливень", rainy: "Дождь", snowy: "Снег", "snowy-rainy": "Снег с дождём",
    sunny: "Ясно", windy: "Ветрено", "windy-variant": "Ветрено",
  })[String(condition || "").toLowerCase()] || "Погода уточняется";
}

function timeLabel(timestamp) {
  const value = Number(timestamp);
  if (!Number.isFinite(value)) return "";
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function cardButton(deps, className, target, panel) {
  const card = deps.el("button", className);
  card.type = "button";
  card.addEventListener("click", () => panel._activateSection(target));
  return card;
}

function appendMetric(deps, card, label, value, supporting) {
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
  const hero = el("section", "overview-canon-hero");
  const overlay = el("div", "overview-canon-hero-overlay");
  const copy = el("div", "overview-canon-hero-copy");
  const homeName = dashboard.summary && dashboard.summary.homeName || "Дом";
  copy.appendChild(el("span", "overview-canon-eyebrow", readinessStatus === "ready" ? "Дом работает штатно" : "Требуется внимание"));
  copy.appendChild(el("h1", null, homeName));
  const facts = el("div", "overview-canon-hero-facts");
  [
    ["rooms", String(rooms.length), "комнат"],
    ["device", String(physicalDeviceCount(devices)), "устройств"],
    ["energy", String(activeCount(devices)), "активно"],
    ["play", String(scenarios.length), "сценариев"],
  ].forEach(([iconName, value, label]) => {
    const fact = el("span", "overview-canon-hero-fact");
    fact.appendChild(svgIcon(iconName));
    fact.appendChild(el("strong", null, value));
    fact.appendChild(el("small", null, label));
    facts.appendChild(fact);
  });
  copy.appendChild(facts);
  const details = el("button", "overview-canon-hero-action", "Подробнее о доме");
  details.type = "button";
  details.addEventListener("click", () => panel._activateSection("rooms"));
  copy.appendChild(details);
  overlay.appendChild(copy);
  const status = el("span", `overview-canon-state${readinessStatus === "ready" ? " is-ready" : " is-attention"}`,
    readinessStatus === "ready" ? "Всё в порядке" : "Проверьте настройки");
  overlay.appendChild(status);
  hero.appendChild(overlay);
  const roomStrip = el("div", "overview-canon-room-strip");
  const home = el("button", "is-active");
  home.type = "button";
  home.disabled = true;
  setAttr(home, "aria-current", "page");
  home.appendChild(svgIcon("home"));
  home.appendChild(el("span", null, "Дом"));
  roomStrip.appendChild(home);
  rooms.slice(0, 6).forEach((room) => {
    const button = el("button");
    button.type = "button";
    button.appendChild(svgIcon("rooms"));
    button.appendChild(el("span", null, room.name));
    setAttr(button, "aria-label", `Открыть комнату ${room.name}`);
    button.addEventListener("click", () => deps.openRoom(room));
    roomStrip.appendChild(button);
  });
  hero.appendChild(roomStrip);
  container.appendChild(hero);
}

function renderPrimaryCards(panel, container, dashboard, deps) {
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const climate = devices.filter((device) => CLIMATE_DOMAINS.has(device.domain) || device.category === "climate" || device.category === "air_quality");
  const row = deps.el("div", "overview-canon-primary-grid");
  const climateCard = cardButton(deps, "overview-canon-primary-card", "climate", panel);
  appendMetric(deps, climateCard, "Климат", panel._temp(average(rooms.map((room) => room.temp))),
    `${panel._humidity(average(rooms.map((room) => room.humidity)))} · ${activeCount(climate)} систем работает`);
  row.appendChild(climateCard);
  const targetCard = cardButton(deps, "overview-canon-primary-card is-target", "climate", panel);
  const humidityTarget = range(rooms.map((room) => room.targetHumidity), "%", 0);
  appendMetric(deps, targetCard, "Цель климата", range(rooms.map((room) => room.targetTemp), "°"),
    `${humidityTarget === "Нет данных" ? "Влажность — в деталях" : humidityTarget} · ${rooms.length} ${panel._roomCountWord(rooms.length)}`);
  row.appendChild(targetCard);
  const comfortCard = cardButton(deps, "overview-canon-primary-card is-comfort", "climate", panel);
  const deviations = rooms.map((room) => validNumber(room.temp) && validNumber(room.targetTemp)
    ? Math.abs(Number(room.temp) - Number(room.targetTemp)) : null).filter(Number.isFinite);
  const stable = deviations.length && Math.max(...deviations) <= 1;
  appendMetric(deps, comfortCard, "Комфорт в доме", stable ? "В норме" : (deviations.length ? "Выравнивается" : "Нет данных"),
    stable ? "Температура близка к целям комнат" : "Откройте климат для подробностей");
  row.appendChild(comfortCard);
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

function renderLowerGrid(panel, container, dashboard, deps) {
  const grid = deps.el("div", "overview-canon-lower-grid");
  const main = deps.el("div", "overview-canon-lower-main");
  deps.renderEnergyOverviewCard(panel, main);
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const lights = devices.filter((device) => device.domain === "light" || device.category === "lighting");
  const lightCard = cardButton(deps, "overview-canon-light-card", "lighting", panel);
  appendMetric(deps, lightCard, "Освещение", String(activeCount(lights)), `${physicalDeviceCount(lights)} физических устройств`);
  main.appendChild(lightCard); grid.appendChild(main);
  const sidebar = deps.el("aside", "overview-canon-side");
  const weather = dashboard.weather || {};
  const weatherCard = deps.el("button", "overview-canon-side-card overview-canon-weather");
  weatherCard.type = "button"; weatherCard.addEventListener("click", () => panel._activateSection("climate"));
  appendMetric(deps, weatherCard, "Погода", Number.isFinite(Number(weather.temperatureC)) ? panel._temp(weather.temperatureC) : "Нет данных",
    `${weatherLabel(weather.condition)} · ветер ${Number.isFinite(Number(weather.windSpeedMps)) ? `${Number(weather.windSpeedMps).toLocaleString("ru-RU")} м/с` : "—"}`);
  sidebar.appendChild(weatherCard);
  const alarms = Array.isArray(dashboard.alarms) ? dashboard.alarms.filter((alarm) => alarm.active) : [];
  const stateCard = cardButton(deps, `overview-canon-side-card${alarms.length ? " is-alert" : ""}`, "security", panel);
  appendMetric(deps, stateCard, "Дом сейчас", alarms.length ? `${alarms.length} тревог` : "Спокойно",
    alarms.length ? "Откройте безопасность" : "Активных тревог нет");
  sidebar.appendChild(stateCard);
  const eventsCard = deps.el("section", "overview-canon-side-card overview-canon-events");
  const eventHead = deps.el("div", "overview-canon-section-head"); eventHead.appendChild(deps.el("h2", null, "Последняя активность")); eventsCard.appendChild(eventHead);
  const events = Array.isArray(dashboard.events) ? dashboard.events.slice(0, 4) : [];
  if (!events.length) eventsCard.appendChild(deps.el("p", "overview-canon-supporting", "Новых событий нет"));
  events.forEach((event) => {
    const row = deps.el("div", "overview-canon-event");
    const copy = deps.el("span"); copy.appendChild(deps.el("strong", null, event.title));
    copy.appendChild(deps.el("small", null, event.message || panel._roomName(event.roomId) || "Дом")); row.appendChild(copy);
    row.appendChild(deps.el("time", null, timeLabel(event.ts))); eventsCard.appendChild(row);
  });
  sidebar.appendChild(eventsCard); grid.appendChild(sidebar); container.appendChild(grid);
}

export function renderOverviewContent(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard || {};
  renderPrimaryCards(panel, container, dashboard, deps);
  renderFavorites(panel, container, dashboard, deps);
  renderLowerGrid(panel, container, dashboard, deps);
}
