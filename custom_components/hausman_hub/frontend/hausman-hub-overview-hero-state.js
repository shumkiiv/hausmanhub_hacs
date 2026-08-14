import { roomHeroImage } from "./hausman-hub-room-icons.js?v=1.52.97";

function heroSummary(dashboard) {
  const summary = dashboard?.summary && typeof dashboard.summary === "object"
    ? dashboard.summary : {};
  const weatherCondition = summary.weatherCondition || dashboard?.weather?.condition || "";
  return { ...summary, weatherCondition };
}

function physicalDeviceCount(devices) {
  return new Set(devices.map((device) => device.physicalId || device.id).filter(Boolean)).size;
}

function activeCount(devices) {
  return devices.filter((device) => !device.unavailable && device.active === true).length;
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

export function overviewHeroRenderKey(panel, readiness) {
  const dashboard = panel._homeDashboard || {};
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const devices = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const scenarios = Array.isArray(dashboard.scenarios) ? dashboard.scenarios : [];
  const selectedRoom = rooms.find((room) => room.id === panel._overviewHeroRoomId) || null;
  return JSON.stringify({
    readiness: readiness?.status || "not_ready",
    homeName: dashboard.summary?.homeName || "Дом",
    image: stableOverviewHeroImage(panel, selectedRoom, dashboard),
    selectedRoomId: selectedRoom?.id || null,
    rooms: rooms.map((room) => ({
      id: room.id, name: room.name, icon: room.icon, temp: room.temp, humidity: room.humidity,
      targetTemp: room.targetTemp, targetHumidity: room.targetHumidity,
      climateRunning: room.climateRunning, status: room.status,
      deviceCount: Array.isArray(room.deviceIds) ? room.deviceIds.length : 0,
    })),
    physicalDevices: physicalDeviceCount(devices),
    activeDevices: activeCount(devices),
    scenarios: scenarios.length,
  });
}
