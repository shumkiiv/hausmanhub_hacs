const KIOSK_RENDERER = (() => {
const CLIMATE = new Set(["climate", "humidifier", "fan"]);

const numeric = (raw) => raw !== null && raw !== undefined && raw !== "" && Number.isFinite(Number(raw)) ? Number(raw) : null;
const metric = (raw, suffix = "", digits = 0) => numeric(raw) === null ? "—" : `${numeric(raw).toLocaleString("ru-RU", { maximumFractionDigits: digits })}${suffix}`;
const weatherLabel = (condition) => ({ sunny: "Ясно", "clear-night": "Ясно", cloudy: "Облачно", partlycloudy: "Переменная облачность", rainy: "Дождь", pouring: "Ливень", snowy: "Снег", fog: "Туман", windy: "Ветрено", lightning: "Гроза", "lightning-rainy": "Гроза с дождём" })[String(condition || "").toLowerCase()] || "Погода уточняется";

function physicalCount(devices) {
  return new Set(devices.map((device) => device.physicalId || device.id).filter(Boolean)).size;
}

function activeCount(devices) {
  return devices.filter((device) => !device.unavailable && (device.active === true || !["off", "idle", "standby", "unknown", "unavailable"].includes(device.state))).length;
}

function batteryAlerts(devices) {
  return devices.flatMap((device) => {
    const detail = (Array.isArray(device.details) ? device.details : []).find((item) => item.label === "Заряд");
    const percent = detail && parseFloat(String(detail.value).replace(",", "."));
    return Number.isFinite(percent) && percent < 8 ? [{ device, percent }] : [];
  });
}

function openSection(panel, section) {
  panel._shell?.kioskButton?.click?.();
  panel._activateSection(section);
}

function sectionButton(panel, deps, className, section, title, main, detail, icon) {
  const card = deps.el("button", className);
  card.type = "button";
  card.addEventListener("click", () => openSection(panel, section));
  const head = deps.el("span", "kiosk-card-head");
  head.appendChild(deps.svgIcon(icon));
  head.appendChild(deps.el("strong", null, title));
  head.appendChild(deps.el("span", "kiosk-card-arrow", "›"));
  card.appendChild(head);
  card.appendChild(deps.el("span", "kiosk-card-value", main));
  card.appendChild(deps.el("span", "kiosk-card-detail", detail));
  return card;
}

function renderHeader(panel, root, dashboard, deps) {
  const header = deps.el("header", "kiosk-panorama-header");
  const brand = deps.el("div", "kiosk-panorama-brand");
  brand.appendChild(deps.svgIcon("home"));
  const copy = deps.el("span");
  copy.appendChild(deps.el("strong", null, "HAUSMANHUB"));
  copy.appendChild(deps.el("small", null, panel._error ? "Связь потеряна" : "Дом на связи"));
  brand.appendChild(copy);
  header.appendChild(brand);
  const low = batteryAlerts(Array.isArray(dashboard.devices) ? dashboard.devices : []);
  if (low.length) {
    const warning = deps.el("button", "kiosk-battery-warning", `${low.length} ${low.length === 1 ? "батарея требует" : "батареи требуют"} внимания`);
    warning.type = "button";
    warning.addEventListener("click", () => openSection(panel, "devices"));
    header.appendChild(warning);
  }
  const clock = deps.el("div", "kiosk-panorama-clock");
  const now = new Date();
  clock.appendChild(deps.el("strong", null, now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })));
  clock.appendChild(deps.el("small", null, now.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })));
  header.appendChild(clock);
  const exit = deps.el("button", "kiosk-panorama-exit");
  exit.type = "button";
  exit.appendChild(deps.svgIcon("close"));
  deps.setAttr(exit, "aria-label", "Выйти из режима киоска");
  exit.addEventListener("click", deps.exit);
  header.appendChild(exit);
  root.appendChild(header);
}

function renderHero(panel, root, dashboard, deps) {
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const hero = deps.el("section", "kiosk-panorama-hero");
  const copy = deps.el("div", "kiosk-panorama-hero-copy");
  copy.appendChild(deps.el("span", "kiosk-eyebrow", panel._error ? "Нужно восстановить соединение" : "Дом работает штатно"));
  copy.appendChild(deps.el("h1", null, dashboard.summary?.homeName || "Дом"));
  copy.appendChild(deps.el("p", null, `${rooms.length} ${panel._roomCountWord(rooms.length)} · ${physicalCount(devices)} физических устройств`));
  hero.appendChild(copy);
  const facts = deps.el("div", "kiosk-panorama-hero-facts");
  [["rooms", rooms.length, "комнат"], ["device", physicalCount(devices), "устройств"], ["lightbulb", dashboard.summary?.activeLights || 0, "свет включён"]].forEach(([icon, fact, label]) => {
    const item = deps.el("span");
    item.appendChild(deps.svgIcon(icon)); item.appendChild(deps.el("strong", null, fact)); item.appendChild(deps.el("small", null, label)); facts.appendChild(item);
  });
  hero.appendChild(facts);
  root.appendChild(hero);
}

function renderMetrics(panel, root, dashboard, deps) {
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const climate = devices.filter((device) => CLIMATE.has(device.domain) || ["climate", "air_quality"].includes(device.category));
  const temperatures = rooms.map((room) => numeric(room.temp)).filter((item) => item !== null);
  const humidity = rooms.map((room) => numeric(room.humidity)).filter((item) => item !== null);
  const average = (items) => items.length ? items.reduce((sum, item) => sum + item, 0) / items.length : null;
  const energy = dashboard.energy || {};
  const grid = deps.el("div", "kiosk-panorama-metrics");
  grid.appendChild(sectionButton(panel, deps, "kiosk-metric-card", "climate", "Климат", metric(average(temperatures), "°", 1), `${metric(average(humidity), "%", 0)} · ${activeCount(climate)} систем работает`, "thermometer"));
  grid.appendChild(sectionButton(panel, deps, "kiosk-metric-card is-energy", "energy", "Энергия", metric(energy.currentPowerW, " Вт", 0), `${metric(energy.currentA, " А", 2)} · ${metric(energy.voltageV, " В", 0)}`, "energy"));
  grid.appendChild(sectionButton(panel, deps, "kiosk-metric-card is-air", "climate", "Воздух", metric(average(humidity), "%", 0), dashboard.summary?.co2 ? `CO₂ ${metric(dashboard.summary.co2, " ppm", 0)}` : "Качество воздуха в комнатах", "water"));
  root.appendChild(grid);
}

function renderScenarios(panel, root, dashboard, deps) {
  const section = deps.el("section", "kiosk-panorama-scenarios kiosk-panorama-card");
  const head = deps.el("div", "kiosk-panorama-section-head");
  head.appendChild(deps.el("h2", null, "Избранные сценарии"));
  const all = deps.el("button", null, "Все"); all.type = "button"; all.addEventListener("click", () => openSection(panel, "scenarios")); head.appendChild(all); section.appendChild(head);
  const list = deps.el("div", "kiosk-panorama-scenario-list");
  const scenarios = (Array.isArray(dashboard.scenarios) ? dashboard.scenarios : []).filter((item) => item.favorite).slice(0, 3);
  if (!scenarios.length) list.appendChild(deps.el("p", "kiosk-empty", "Добавьте сценарии в избранное"));
  scenarios.forEach((scenario) => {
    const item = deps.el("button", "kiosk-panorama-scenario"); item.type = "button"; item.disabled = scenario.enabled === false || panel._busy;
    item.appendChild(deps.svgIcon("bolt"));
    const copy = deps.el("span"); copy.appendChild(deps.el("strong", null, scenario.title)); copy.appendChild(deps.el("small", null, scenario.description || scenario.group || "Готов к запуску")); item.appendChild(copy); item.appendChild(deps.svgIcon("play"));
    item.addEventListener("click", () => panel._post("hausman_hub/v1/admin/scenarios/run", { scenario_id: scenario.id }, scenario.requiresConfirmation ? `Запустить сценарий «${scenario.title}»?` : null));
    list.appendChild(item);
  });
  section.appendChild(list); root.appendChild(section);
}

function renderWeatherAndHome(panel, root, dashboard, deps) {
  const row = deps.el("div", "kiosk-panorama-secondary");
  const weather = dashboard.weather || {};
  const weatherCard = deps.el("button", "kiosk-panorama-card kiosk-panorama-weather"); weatherCard.type = "button"; weatherCard.addEventListener("click", () => openSection(panel, "climate"));
  const head = deps.el("span", "kiosk-card-head"); head.appendChild(deps.el("strong", null, "Погода")); head.appendChild(deps.el("span", "kiosk-card-arrow", "›")); weatherCard.appendChild(head);
  weatherCard.appendChild(deps.el("strong", "kiosk-weather-now", metric(weather.temperatureC, "°", 1)));
  weatherCard.appendChild(deps.el("span", "kiosk-card-detail", `${weatherLabel(weather.condition)} · ветер ${metric(weather.windSpeedMps, " м/с", 1)}`));
  const forecast = deps.el("span", "kiosk-forecast-strip");
  (Array.isArray(weather.dailyForecast) ? weather.dailyForecast : []).slice(0, 5).forEach((day) => {
    const date = new Date(day.at);
    const slot = deps.el("span"); slot.appendChild(deps.el("small", null, Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString("ru-RU", { weekday: "short" }))); slot.appendChild(deps.el("strong", null, metric(day.temperatureC, "°", 0))); forecast.appendChild(slot);
  });
  if (!forecast.children.length) forecast.appendChild(deps.el("small", null, "Прогноз появится после обновления погодного сервиса"));
  weatherCard.appendChild(forecast); row.appendChild(weatherCard);
  const alarms = (Array.isArray(dashboard.alarms) ? dashboard.alarms : []).filter((alarm) => alarm.active);
  row.appendChild(sectionButton(panel, deps, `kiosk-panorama-card kiosk-panorama-home${alarms.length ? " is-alert" : ""}`, "security", "Дом сейчас", alarms.length ? `${alarms.length} тревог` : "Спокойно", `${dashboard.summary?.activeLights || 0} светильников · ${dashboard.summary?.activeClimate || 0} климатических систем`, "shield"));
  if (deps.showIntercom) {
    const intercom = deps.el("button", "kiosk-panorama-intercom"); intercom.type = "button"; deps.setAttr(intercom, "aria-label", "Открыть домофон без подтверждения"); intercom.appendChild(deps.svgIcon("intercom")); intercom.appendChild(deps.el("strong", null, "Открыть домофон")); intercom.appendChild(deps.el("small", null, "Без подтверждения")); intercom.addEventListener("click", deps.openIntercom); row.appendChild(intercom);
  }
  root.appendChild(row);
}

function renderKiosk(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard || {};
  renderHeader(panel, container, dashboard, deps);
  renderHero(panel, container, dashboard, deps);
  renderMetrics(panel, container, dashboard, deps);
  renderScenarios(panel, container, dashboard, deps);
  renderWeatherAndHome(panel, container, dashboard, deps);
  container.appendChild(deps.el("p", "kiosk-exit-hint", "Дважды коснитесь свободного места, чтобы выйти"));
}

return renderKiosk;
})();

export const renderKiosk = KIOSK_RENDERER;
