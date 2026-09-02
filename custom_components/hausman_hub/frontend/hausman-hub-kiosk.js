import { createHeroRoomNavigation } from "./hausman-hub-hero-room-navigation.js?v=1.52.203";
import { overviewHomeName, stableOverviewHeroImage } from "./hausman-hub-overview-hero-state.js?v=1.52.203";
import { scenarioIconMeta } from "./hausman-hub-scenario-icons.js?v=1.52.203";

const KIOSK_RENDERER = (() => {
const CLIMATE = new Set(["climate", "humidifier", "fan"]);

function numeric(raw) {
  return raw !== null && raw !== undefined && raw !== "" && Number.isFinite(Number(raw)) ? Number(raw) : null;
}

function average(values) {
  const valid = values.map(numeric).filter((value) => value !== null);
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null;
}

function number(value, digits = 1) {
  return numeric(value) === null ? "—" : numeric(value).toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function temperature(value) {
  return numeric(value) === null ? "—" : `${number(value)}°`;
}

function percent(value) {
  return numeric(value) === null ? "—" : `${number(value)}%`;
}

function physicalCount(devices) {
  return new Set(devices.map((device) => device.physicalId || device.id).filter(Boolean)).size;
}

function activeCount(devices) {
  return physicalCount(devices.filter((device) => !device.unavailable && device.state !== "unavailable" && device.active === true));
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
    sunny: "Ясно", "clear-night": "Ясно", cloudy: "Облачно", partlycloudy: "Переменная облачность",
    rainy: "Дождь", pouring: "Ливень", snowy: "Снег", fog: "Туман", windy: "Ветрено",
    lightning: "Гроза", "lightning-rainy": "Гроза с дождём",
  })[String(condition || "").toLowerCase()] || "Погода уточняется";
}

function openSection(panel, section) {
  panel._shell?.kioskButton?.click?.();
  panel._activateSection(section);
}

function selectedEnergySources(energy) {
  const sources = Array.isArray(energy?.sources) ? energy.sources : [];
  if (energy?.settings?.useAllDevices === true) return sources;
  const selected = new Set(Array.isArray(energy?.selectedSourceIds) ? energy.selectedSourceIds : []);
  return sources.filter((source) => selected.has(source.id) || selected.has(source.deviceId));
}

function energyValue(energy) {
  const watts = numeric(energy?.currentPowerW);
  if (watts !== null) return watts < 1000 ? `${number(watts, 0)} Вт` : `${number(watts / 1000)} кВт`;
  if (numeric(energy?.todayKwh) !== null) return `${number(energy.todayKwh)} кВт·ч`;
  return "—";
}

function iconButton(deps, className, icon, label, onClick) {
  const button = deps.el("button", className);
  button.type = "button";
  deps.setAttr(button, "aria-label", label);
  button.appendChild(deps.svgIcon(icon));
  if (onClick) button.addEventListener("click", onClick);
  return button;
}

function renderHeader(panel, root, deps) {
  const header = deps.el("header", "kiosk-panorama-header");
  const brand = deps.el("div", "kiosk-panorama-brand");
  brand.appendChild(deps.svgIcon("home"));
  const copy = deps.el("span");
  copy.appendChild(deps.el("strong", null, "HAUSMAN"));
  copy.appendChild(deps.el("small", null, panel._error ? "Связь потеряна" : "Все системы работают штатно"));
  brand.appendChild(copy);
  header.appendChild(brand);
  const pager = deps.el("span", "kiosk-panorama-page-dots");
  for (let index = 0; index < 3; index += 1) pager.appendChild(deps.el("i"));
  header.appendChild(pager);
  const system = deps.el("span", `kiosk-panorama-system${panel._error ? " is-error" : ""}`);
  system.appendChild(deps.svgIcon("wifi"));
  deps.setAttr(system, "aria-label", panel._error ? "Связь потеряна" : "Система на связи");
  header.appendChild(system);
  const clock = deps.el("div", "kiosk-panorama-clock");
  const now = new Date();
  clock.appendChild(deps.el("strong", null, now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })));
  clock.appendChild(deps.el("small", null, now.toLocaleDateString("ru-RU", {
    weekday: "long", day: "numeric", month: "long",
  })));
  header.appendChild(clock);
  header.appendChild(iconButton(deps, "kiosk-panorama-exit", "fullscreen",
    "Выйти из режима киоска", deps.exit));
  root.appendChild(header);
}

function replaceHeroFacts(row, facts, deps) {
  row.innerHTML = "";
  facts.forEach(([iconName, value, label]) => {
    const item = deps.el("span");
    item.appendChild(deps.svgIcon(iconName));
    item.appendChild(deps.el("strong", null, String(value)));
    item.appendChild(deps.el("small", null, label));
    row.appendChild(item);
  });
}

function renderHero(panel, root, dashboard, deps) {
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const selectedRoom = rooms.find((room) => room.id === panel._overviewHeroRoomId) || null;
  const hero = deps.el("section", "kiosk-panorama-hero");
  const media = deps.el("div", "kiosk-panorama-hero-media");
  let currentImage = stableOverviewHeroImage(panel, selectedRoom, dashboard);
  media.style.backgroundImage = `url("${currentImage}")`;
  hero.appendChild(media);
  const overlay = deps.el("div", "kiosk-panorama-hero-overlay");
  const copy = deps.el("div", "kiosk-panorama-hero-copy");
  const title = deps.el("h1");
  copy.appendChild(title);
  const facts = deps.el("div", "kiosk-panorama-hero-facts");
  copy.appendChild(facts);
  const details = deps.el("button", "kiosk-panorama-hero-action", "Подробнее о доме");
  details.type = "button";
  details.appendChild(deps.svgIcon("chevron-right"));
  details.addEventListener("click", () => openSection(panel, "rooms"));
  copy.appendChild(details);
  overlay.appendChild(copy);
  hero.appendChild(overlay);
  const controls = deps.el("div", "kiosk-panorama-hero-controls");
  controls.appendChild(iconButton(deps, "kiosk-panorama-hero-control", "star", "Закрепить слайд"));
  controls.appendChild(iconButton(deps, "kiosk-panorama-hero-control", "more",
    "Открыть настройки", () => openSection(panel, "settings")));
  hero.appendChild(controls);
  const navigation = createHeroRoomNavigation(panel, rooms, deps);
  hero.appendChild(navigation.element);
  const homeButton = iconButton(deps, "kiosk-panorama-hero-home", "home-filled", "Показать весь дом");
  hero.appendChild(homeButton);
  const selectRoom = (room, animate = true) => {
    panel._overviewHeroRoomId = room?.id || null;
    const image = stableOverviewHeroImage(panel, room, dashboard);
    if (image !== currentImage) {
      media.style.backgroundImage = `url("${image}")`;
      currentImage = image;
    }
    navigation.setActive(room, animate);
    title.textContent = room?.name || overviewHomeName(dashboard);
    replaceHeroFacts(facts, room ? [
      ["thermometer", temperature(room.temp), "температура"],
      ["auto", temperature(room.targetTemp), "цель"],
      ["water", percent(room.humidity), "влажность"],
    ] : [
      ["rooms", rooms.length, plural(rooms.length, "комната", "комнаты", "комнат")],
      ["device", physicalCount(devices), "устройства"],
      ["check", activeCount(devices), "активно"],
      ["bolt", Array.isArray(dashboard.scenarios) ? dashboard.scenarios.length : 0, "автоматизации"],
    ], deps);
  };
  navigation.attachCarouselChrome(hero);
  navigation.bind(selectRoom);
  homeButton.addEventListener("click", () => selectRoom(null));
  selectRoom(selectedRoom, false);
  root.appendChild(hero);
}

function metricCard(panel, deps, options) {
  const card = deps.el(options.action ? "section" : "button", `kiosk-metric-card ${options.className || ""}`.trim());
  if (!options.action) {
    card.type = "button";
    card.addEventListener("click", options.onClick);
  }
  const head = deps.el("div", "kiosk-card-head");
  head.appendChild(deps.svgIcon(options.icon));
  head.appendChild(deps.el("strong", null, options.title));
  if (options.action) {
    const action = deps.el("button", null, options.action.label);
    action.type = "button";
    action.addEventListener("click", (event) => {
      event.stopPropagation?.();
      options.action.onClick();
    });
    head.appendChild(action);
  } else {
    head.appendChild(deps.svgIcon("chevron-right"));
  }
  card.appendChild(head);
  card.appendChild(deps.el("strong", "kiosk-card-value", options.value));
  card.appendChild(deps.el("span", "kiosk-card-detail", options.detail));
  const indicators = deps.el("div", "kiosk-card-indicators");
  options.indicators.slice(0, 3).forEach(([label, value]) => {
    const indicator = deps.el("span");
    indicator.appendChild(deps.el("strong", null, value));
    indicator.appendChild(deps.el("small", null, label));
    indicators.appendChild(indicator);
  });
  card.appendChild(indicators);
  return card;
}

function renderMetrics(panel, root, dashboard, deps) {
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const climate = devices.filter((device) => CLIMATE.has(device.domain)
    || ["climate", "air_quality"].includes(device.category));
  const avgTemp = dashboard.summary?.avgTemp ?? average(rooms.map((room) => room.temp));
  const avgHumidity = average(rooms.map((room) => room.humidity));
  const energy = dashboard.energy || {};
  const sources = selectedEnergySources(energy);
  const sourceRooms = new Set(sources.map((source) => source.roomName).filter(Boolean)).size;
  const co2 = numeric(dashboard.summary?.co2);
  const grid = deps.el("div", "kiosk-panorama-metrics");
  grid.appendChild(metricCard(panel, deps, {
    title: "Климат", value: temperature(avgTemp),
    detail: [numeric(dashboard.summary?.targetTemp) === null ? null : `Цель ${temperature(dashboard.summary.targetTemp)}`,
      numeric(avgHumidity) === null ? null : `Влажность ${percent(avgHumidity)}`].filter(Boolean).join(" · ") || "Климатические данные недоступны",
    icon: "thermometer", onClick: () => openSection(panel, "climate"),
    indicators: [["Цель", temperature(dashboard.summary?.targetTemp)], ["Влажность", percent(avgHumidity)], ["Активно", String(activeCount(climate))]],
  }));
  grid.appendChild(metricCard(panel, deps, {
    title: "Показания энергии", value: energyValue(energy),
    detail: `${sources.length} ${plural(sources.length, "источник", "источника", "источников")} · ${sourceRooms} ${plural(sourceRooms, "помещение", "помещения", "помещений")}`,
    icon: "energy", className: "is-energy", onClick: () => openSection(panel, "energy"),
    action: { label: "Настройки", onClick: () => openSection(panel, "energy") },
    indicators: [["Сегодня", numeric(energy.todayKwh) === null ? "—" : `${number(energy.todayKwh)} кВт·ч`],
      ["Источников", String(sources.length)], ["Помещений", String(sourceRooms)]],
  }));
  grid.appendChild(metricCard(panel, deps, {
    title: "Воздух", value: co2 === null ? percent(avgHumidity) : `${number(co2, 0)} ppm`,
    detail: co2 === null ? "Средняя влажность" : (co2 <= 800 ? "Воздух свежий" : co2 <= 1200 ? "Стоит проветрить" : "Нужно проветрить"),
    icon: "air", className: "is-air", onClick: () => openSection(panel, "climate"),
    indicators: [["Влажность", percent(avgHumidity)], ["CO₂", co2 === null ? "—" : `${number(co2, 0)} ppm`],
      ["Комнат", String(rooms.filter((room) => numeric(room.humidity) !== null).length)]],
  }));
  root.appendChild(grid);
}

function renderScenarios(panel, root, dashboard, deps) {
  const section = deps.el("section", "kiosk-panorama-scenarios kiosk-panorama-card");
  const head = deps.el("div", "kiosk-panorama-section-head");
  const title = deps.el("h2");
  title.appendChild(deps.svgIcon("star"));
  title.appendChild(deps.el("span", null, "Избранные сценарии"));
  head.appendChild(title);
  const all = deps.el("button", null, "Все");
  all.type = "button";
  all.appendChild(deps.svgIcon("chevron-right"));
  all.addEventListener("click", () => openSection(panel, "scenarios"));
  head.appendChild(all);
  section.appendChild(head);
  const list = deps.el("div", "kiosk-panorama-scenario-list");
  const scenarios = (Array.isArray(dashboard.scenarios) ? dashboard.scenarios : [])
    .filter((scenario) => scenario.favorite).slice(0, 4);
  if (!scenarios.length) list.appendChild(deps.el("p", "kiosk-empty", "Добавьте сценарии в избранное"));
  scenarios.forEach((scenario) => {
    const item = deps.el("button", "kiosk-panorama-scenario");
    item.type = "button";
    item.disabled = scenario.enabled === false || panel._busy;
    const meta = scenarioIconMeta(scenario.icon, scenario.title);
    const icon = deps.el("span", "kiosk-panorama-scenario-icon");
    icon.appendChild(deps.svgIcon(meta.glyph));
    item.appendChild(icon);
    const copy = deps.el("span");
    copy.appendChild(deps.el("strong", null, scenario.title));
    copy.appendChild(deps.el("small", null, scenario.description || scenario.group || "Ручной запуск"));
    item.appendChild(copy);
    item.appendChild(deps.svgIcon("play"));
    item.addEventListener("click", () => panel._post("hausman_hub/v1/admin/scenarios/run",
      { scenario_id: scenario.id }, scenario.requiresConfirmation ? `Запустить сценарий «${scenario.title}»?` : null));
    list.appendChild(item);
  });
  section.appendChild(list);
  root.appendChild(section);
}

function weatherSnapshot(dashboard) {
  const weather = dashboard.weather || {};
  const summary = dashboard.summary || {};
  return {
    condition: weather.condition || summary.weatherCondition,
    temperature: weather.temperatureC ?? summary.outdoorTemp,
    sensor: weather.outdoorSensorTemperatureC,
    humidity: weather.humidityPercent ?? summary.weatherHumidity,
    wind: weather.windSpeedMps ?? summary.weatherWindSpeed,
  };
}

function weatherGlyph(weather, deps) {
  const glyph = deps.el("span", "kiosk-weather-glyph");
  glyph.appendChild(deps.svgIcon(String(weather.condition).toLowerCase() === "clear-night" ? "moon" : "cloud"));
  return glyph;
}

function renderWeather(panel, root, dashboard, deps) {
  const weather = weatherSnapshot(dashboard);
  const card = deps.el("section", "kiosk-panorama-card kiosk-panorama-weather");
  card.addEventListener("click", () => openSection(panel, "climate"));
  const head = deps.el("div", "kiosk-panorama-section-head");
  head.appendChild(deps.el("h2", null, "Погода"));
  head.appendChild(deps.el("span", null, weatherLabel(weather.condition)));
  card.appendChild(head);
  const body = deps.el("div", "kiosk-weather-body");
  body.appendChild(weatherGlyph(weather, deps));
  const outside = deps.el("span");
  outside.appendChild(deps.el("strong", null, temperature(weather.temperature)));
  outside.appendChild(deps.el("small", null, "на улице"));
  body.appendChild(outside);
  const sensor = deps.el("span");
  sensor.appendChild(deps.el("strong", null, temperature(weather.sensor)));
  sensor.appendChild(deps.el("small", null, "датчик"));
  body.appendChild(sensor);
  card.appendChild(body);
  const footer = deps.el("div", "kiosk-weather-footer");
  [["water", percent(weather.humidity), "Влажность"],
    ["air", numeric(weather.wind) === null ? "—" : `${number(weather.wind)} м/с`, "Ветер"]]
    .forEach(([iconName, value, label]) => {
      const item = deps.el("span");
      item.appendChild(deps.svgIcon(iconName));
      const copy = deps.el("span");
      copy.appendChild(deps.el("strong", null, value));
      copy.appendChild(deps.el("small", null, label));
      item.appendChild(copy);
      footer.appendChild(item);
    });
  card.appendChild(footer);
  root.appendChild(card);
}

function contactState(devices, category, label) {
  const matches = devices.filter((device) => device.category === category);
  if (!matches.length) return `${label}: нет данных`;
  const open = matches.filter((device) => ["on", "open", "unlocked"].includes(String(device.state).toLowerCase())).length;
  return open ? `${label}: открыто ${open}` : `${label}: всё закрыто`;
}

function renderHomeNow(panel, root, dashboard, deps) {
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const alarms = Array.isArray(dashboard.alarms) ? dashboard.alarms.filter((alarm) => alarm.active) : [];
  const lights = devices.filter((device) => device.domain === "light" || device.category === "lighting");
  const activeLights = activeCount(lights);
  const modeRaw = String(dashboard.summary?.mode || "").toLowerCase();
  const mode = modeRaw === "home" ? "Дома" : ["away", "not_home"].includes(modeRaw) ? "Не дома" : "нет данных";
  const rows = [
    ["home", `Режим: ${mode}`, "neutral"],
    ["window", contactState(devices, "window", "Окна"), "neutral"],
    ["door", contactState(devices, "door", "Двери"), "neutral"],
    ["shield", alarms.length ? `Тревог: ${alarms.length}` : "Охрана не настроена", alarms.length ? "danger" : "neutral"],
    ["lightbulb", `${activeLights} ${plural(activeLights, "светильник горит", "светильника горят", "светильников горят")}`, activeLights ? "warning" : "neutral"],
  ];
  const card = deps.el("section", "kiosk-panorama-card kiosk-panorama-home");
  card.addEventListener("click", () => openSection(panel, "security"));
  const head = deps.el("div", "kiosk-panorama-section-head");
  head.appendChild(deps.el("h2", null, "Дом сейчас"));
  head.appendChild(deps.svgIcon("chevron-right"));
  card.appendChild(head);
  const list = deps.el("div", "kiosk-home-list");
  rows.forEach(([iconName, text, tone]) => {
    const row = deps.el("span", `is-${tone}`);
    const icon = deps.el("i");
    icon.appendChild(deps.svgIcon(iconName));
    row.appendChild(icon);
    row.appendChild(deps.el("strong", null, text));
    list.appendChild(row);
  });
  card.appendChild(list);
  root.appendChild(card);
}

function renderIntercom(root, deps) {
  const button = deps.el("button", `kiosk-panorama-intercom${deps.showIntercom ? "" : " is-disabled"}`);
  button.type = "button";
  button.disabled = !deps.showIntercom;
  const icon = deps.el("span");
  icon.appendChild(deps.svgIcon("intercom"));
  button.appendChild(icon);
  button.appendChild(deps.el("strong", null, deps.showIntercom ? "Открыть домофон" : "Домофон не настроен"));
  button.appendChild(deps.el("small", null, deps.showIntercom ? "Без подтверждения" : "Настройте быстрый доступ"));
  if (deps.showIntercom) button.addEventListener("click", deps.openIntercom);
  root.appendChild(button);
}

function render(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard || {};
  renderHeader(panel, container, deps);
  const body = deps.el("div", "kiosk-panorama-body");
  const primary = deps.el("div", "kiosk-panorama-primary");
  renderHero(panel, primary, dashboard, deps);
  renderMetrics(panel, primary, dashboard, deps);
  renderScenarios(panel, primary, dashboard, deps);
  body.appendChild(primary);
  const side = deps.el("aside", "kiosk-panorama-side");
  renderWeather(panel, side, dashboard, deps);
  renderHomeNow(panel, side, dashboard, deps);
  renderIntercom(side, deps);
  body.appendChild(side);
  container.appendChild(body);
  container.appendChild(deps.el("p", "kiosk-exit-hint", "Дважды коснитесь свободного места, чтобы выйти"));
}

return { render };
})();

export const renderKiosk = (panel, container, deps) => KIOSK_RENDERER.render(panel, container, deps);
