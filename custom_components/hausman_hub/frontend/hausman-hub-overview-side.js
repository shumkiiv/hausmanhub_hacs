import { activityTimeLabel } from "./hausman-hub-pagination.js?v=1.52.132";

function weatherLabel(condition) {
  return ({
    "clear-night": "Ясно", cloudy: "Облачно", fog: "Туман", hail: "Град",
    lightning: "Гроза", "lightning-rainy": "Гроза с дождём", partlycloudy: "Переменная облачность",
    pouring: "Ливень", rainy: "Дождь", snowy: "Снег", "snowy-rainy": "Снег с дождём",
    sunny: "Ясно", windy: "Ветрено", "windy-variant": "Ветрено",
  })[String(condition || "").toLowerCase()] || "Погода уточняется";
}

function appendWeatherGlyph(card, condition, deps) {
  const normalized = String(condition || "").toLowerCase();
  const cloudy = !["sunny", "clear-night"].includes(normalized);
  const glyph = deps.el("span", `overview-canon-weather-glyph${cloudy ? " is-cloudy" : ""}`);
  glyph.appendChild(deps.svgIcon(normalized === "clear-night" ? "moon" : "sun"));
  if (cloudy) glyph.appendChild(deps.el("span", "overview-canon-weather-cloud"));
  card.appendChild(glyph);
}

/* Side column of the overview hero: weather and current home state. */
export function renderOverviewSideCards(panel, dashboard, devices, deps) {
  const { el, svgIcon } = deps;
  const side = el("aside", "overview-canon-top-side");
  const weather = dashboard.weather || {};
  const weatherCard = el("button", "overview-canon-top-card is-weather");
  weatherCard.type = "button";
  weatherCard.addEventListener("click", () => panel._activateSection("climate"));
  weatherCard.appendChild(el("span", "overview-canon-panel-label", "Погода"));
  appendWeatherGlyph(weatherCard, weather.condition, deps);
  weatherCard.appendChild(el("strong", "overview-canon-weather-value", Number.isFinite(Number(weather.temperatureC)) ? panel._temp(weather.temperatureC) : "Нет данных"));
  weatherCard.appendChild(el("span", "overview-canon-weather-condition", weatherLabel(weather.condition)));
  const weatherSupporting = [];
  if (Number.isFinite(Number(weather.humidityPercent))) weatherSupporting.push(`влажность ${Math.round(Number(weather.humidityPercent))} %`);
  weatherSupporting.push(`ветер ${Number.isFinite(Number(weather.windSpeedMps)) ? `${Number(weather.windSpeedMps).toLocaleString("ru-RU")} м/с` : "—"}`);
  weatherCard.appendChild(el("span", "overview-canon-panel-supporting", weatherSupporting.join(" · ")));
  const sensorTemp = Number(weather.outdoorSensorTemperatureC);
  if (weather.outdoorSensorAvailable === true && Number.isFinite(sensorTemp)) {
    const updatedSec = Number(weather.outdoorSensorUpdatedAt);
    const updatedLabel = Number.isFinite(updatedSec) && updatedSec > 0
      ? ` · обновлено ${activityTimeLabel(updatedSec * 1000)}` : "";
    weatherCard.appendChild(el("span", "overview-canon-panel-footnote",
      `Датчик: ${sensorTemp.toFixed(1).replace(".", ",")}°${updatedLabel}`));
  }
  side.appendChild(weatherCard);
  const alarms = Array.isArray(dashboard.alarms) ? dashboard.alarms.filter((alarm) => alarm.active) : [];
  const reachable = devices.filter((device) => device.unavailable !== true && device.state !== "unavailable");
  const byCategory = (...categories) => reachable.filter((device) => categories.includes(device.category));
  const locks = reachable.filter((device) => device.domain === "lock");
  const alarmPanels = reachable.filter((device) => device.domain === "alarm_control_panel");
  const openCount = (items) => items.filter((device) => ["on", "open", "unlocked"].includes(device.state)).length;
  const openingsLabel = (...groups) => {
    const items = groups.flat();
    if (!items.length) return "нет данных";
    const open = openCount(items);
    return open ? `открыто: ${open}` : "все закрыты";
  };
  const securityLabel = alarms.length ? `тревог: ${alarms.length}`
    : alarmPanels.some((device) => device.state === "triggered") ? "тревога"
    : alarmPanels.some((device) => String(device.state || "").startsWith("armed")) ? "включена"
    : alarmPanels.length ? "выключена" : "не настроена";
  const homeCard = el("button", `overview-canon-top-card is-home${alarms.length ? " is-alert" : ""}`);
  homeCard.type = "button";
  homeCard.addEventListener("click", () => panel._activateSection("security"));
  homeCard.appendChild(el("span", "overview-canon-panel-label", "Дом сейчас"));
  const homeRows = el("span", "overview-canon-home-rows");
  [
    ["home", "Режим", (() => { const p = byCategory("presence", "occupancy")[0]; return p ? (p.state === "on" ? "Дома" : "Не дома") : "нет данных"; })()],
    ["door", "Двери", openingsLabel(byCategory("door", "opening"), locks)],
    ["window", "Окна", openingsLabel(byCategory("window"))],
    ["shield", "Охрана", securityLabel],
  ].forEach(([icon, label, value]) => {
    const homeRow = el("span", `overview-canon-home-row${value === "нет данных" ? " is-empty" : ""}`);
    homeRow.appendChild(svgIcon(icon));
    homeRow.appendChild(el("small", null, label));
    homeRow.appendChild(el("b", null, value));
    homeRows.appendChild(homeRow);
  });
  homeCard.appendChild(homeRows);
  side.appendChild(homeCard);
  return side;
}
