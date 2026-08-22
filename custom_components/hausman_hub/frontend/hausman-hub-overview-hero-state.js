import { roomHeroImage } from "./hausman-hub-room-icons.js?v=1.52.139";

function heroSummary(dashboard) {
  const summary = dashboard?.summary && typeof dashboard.summary === "object"
    ? dashboard.summary : {};
  const weatherCondition = summary.weatherCondition || dashboard?.weather?.condition || "";
  return { ...summary, weatherCondition };
}

export function overviewHomeName(dashboard) {
  const homeName = String(dashboard?.summary?.homeName || "").trim();
  return /^(home\s*assistant|homeassistant)$/i.test(homeName) ? "Дом" : (homeName || "Дом");
}

export function stableOverviewHeroImage(panel, room, dashboard) {
  if (!(panel._overviewHeroImages instanceof Map)) panel._overviewHeroImages = new Map();
  const key = room?.id || "home";
  const summary = heroSummary(dashboard);
  const localIso = String(dashboard?.localIso || "");
  const hasContext = /T\d{2}:/.test(localIso) || !!String(summary.weatherCondition || "").trim();
  if (!hasContext && panel._overviewHeroImages.has(key)) {
    return panel._overviewHeroImages.get(key);
  }
  const image = roomHeroImage(room, summary, localIso);
  panel._overviewHeroImages.set(key, image);
  return image;
}

function weatherKeyPart(panel, weather) {
  // Partial dashboard responses omit weather or carry an empty object; keep
  // the last known part so the stable hero image survives a refresh.
  const hasData = weather && typeof weather === "object"
    && (weather.condition || weather.temperatureC != null
      || weather.humidityPercent != null || weather.windSpeedMps != null
      || weather.outdoorSensorAvailable === true
      || weather.outdoorSensorTemperatureC != null
      || weather.outdoorSensorUpdatedAt != null);
  if (hasData) {
    const part = {
      condition: weather.condition || "",
      temperatureC: weather.temperatureC ?? null,
      humidityPercent: weather.humidityPercent ?? null,
      windSpeedMps: weather.windSpeedMps ?? null,
      sensorAvailable: weather.outdoorSensorAvailable === true,
      sensorTemperatureC: weather.outdoorSensorTemperatureC ?? null,
      sensorUpdatedAt: weather.outdoorSensorUpdatedAt ?? null,
    };
    panel._heroWeatherKeyPart = part;
    return part;
  }
  return panel._heroWeatherKeyPart || null;
}

function activeAlarmsKeyPart(panel, alarms) {
  if (Array.isArray(alarms)) {
    const count = alarms.filter((alarm) => alarm.active).length;
    panel._heroActiveAlarmsKeyPart = count;
    return count;
  }
  return panel._heroActiveAlarmsKeyPart ?? 0;
}

export function overviewHeroRenderKey(panel, readiness) {
  const dashboard = panel._homeDashboard || {};
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const selectedRoom = rooms.find((room) => room.id === panel._overviewHeroRoomId) || null;
  return JSON.stringify({
    readiness: readiness?.status || "not_ready",
    homeName: overviewHomeName(dashboard),
    image: stableOverviewHeroImage(panel, selectedRoom, dashboard),
    selectedRoomId: selectedRoom?.id || null,
    rooms: rooms.map((room) => ({
      id: room.id, name: room.name, icon: room.icon, temp: room.temp, humidity: room.humidity,
      targetTemp: room.targetTemp, targetHumidity: room.targetHumidity,
      climateRunning: room.climateRunning, status: room.status,
    })),
    weather: weatherKeyPart(panel, dashboard.weather),
    activeAlarms: activeAlarmsKeyPart(panel, dashboard.alarms),
    homeRows: devices
      .filter((device) => ["presence", "occupancy", "window", "door", "opening"].includes(device.category)
        || ["lock", "alarm_control_panel"].includes(device.domain))
      .map((device) => `${device.id}:${device.state}:${device.unavailable === true}`),
    activity: (Array.isArray(panel._activityFeed) ? panel._activityFeed : []).slice(0, 5)
      .map((entry) => `${entry.at}:${entry.title}:${entry.text || ""}`),
  });
}
