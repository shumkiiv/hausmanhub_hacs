/* Canonical physical-device catalog shared with the tablet information hierarchy. */

import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.187";
import { renderDeviceDiscovery } from "./hausman-hub-device-discovery.js?v=1.52.187";

const DEVICE_CATEGORY_META = {
  lighting: { label: "Освещение", icon: "lightbulb" },
  climate: { label: "Климат", icon: "thermometer" },
  security: { label: "Безопасность", icon: "shield" },
  media: { label: "Медиа", icon: "media" },
  sensors: { label: "Датчики", icon: "device" },
  covers: { label: "Шторы и ворота", icon: "rooms" },
  appliances: { label: "Техника", icon: "device" },
  energy: { label: "Энергия", icon: "energy" },
  other: { label: "Другое", icon: "device" },
};

function physicalDevices(panel) {
  const devices = panel._homeDashboard && Array.isArray(panel._homeDashboard.devices)
    ? panel._homeDashboard.devices : [];
  const unique = new Map();
  devices.forEach((device) => {
    const key = String(device.physicalId || device.id || device.entityId || device.name || "");
    if (key && !unique.has(key)) unique.set(key, device);
  });
  return [...unique.values()];
}

function deviceCatalogCategory(device) {
  const domain = String(device.domain || "");
  const category = String(device.category || "");
  const name = String(device.name || "").toLocaleLowerCase("ru");
  if (domain === "light" || category === "lighting" || (domain === "switch" && /(свет|ламп|люстр|подсвет)/.test(name))) return "lighting";
  if (["climate", "humidifier", "fan"].includes(domain) || ["climate", "air", "air_quality"].includes(category)) return "climate";
  if (domain === "media_player" || category === "media") return "media";
  if (["lock", "camera", "alarm_control_panel"].includes(domain)
    || ["security", "moisture", "smoke", "gas", "carbon_monoxide", "safety", "problem", "motion", "presence", "occupancy", "opening", "door", "window"].includes(category)) return "security";
  if (domain === "cover" || category === "cover") return "covers";
  if (["power", "current", "energy", "voltage"].includes(category)) return "energy";
  if (["temperature", "humidity", "carbon_dioxide", "volatile_organic_compounds", "pm25", "distance", "battery"].includes(category)
    || ["sensor", "binary_sensor"].includes(domain)) return "sensors";
  if (["vacuum", "switch"].includes(domain) || category === "appliance") return "appliances";
  return "other";
}

function deviceCatalogUnavailable(device) {
  return Boolean(device.unavailable || ["unavailable", "unknown"].includes(String(device.state || "")));
}

function deviceCatalogActive(device) {
  return !deviceCatalogUnavailable(device) && (device.active === true || (
    typeof device.active !== "boolean"
    && !["off", "idle", "standby", "closed", "locked", "disarmed"].includes(String(device.state || ""))
  ));
}

function deviceWord(count) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return "устройств";
  const last = count % 10;
  if (last === 1) return "устройство";
  if (last >= 2 && last <= 4) return "устройства";
  return "устройств";
}

function renderDevicesHero(panel, container, devices, deps) {
  const active = devices.filter(deviceCatalogActive).length;
  const offline = devices.filter(deviceCatalogUnavailable).length;
  container.appendChild(createLibraryHero(panel, {
    eyebrow: "ФИЗИЧЕСКИЕ УСТРОЙСТВА ДОМА",
    title: "Устройства дома",
    subtitle: "Все физические устройства дома без дублирования функций",
    warning: offline > 0,
    facts: [
      { label: "Всего", value: devices.length },
      { label: "Активны", value: active },
      { label: "Без связи", value: offline, warning: offline > 0 },
    ],
  }, deps));
}

function renderDeviceCategoryFilters(panel, container, devices, selected, select, deps) {
  const { el, svgIcon, setAttr } = deps;
  const section = el("section", "devices-canon-section");
  const heading = el("div", "devices-canon-heading");
  heading.appendChild(el("h3", null, "Категории устройств"));
  const reset = el("button", `devices-canon-reset${selected ? "" : " is-active"}`, "Все категории");
  reset.type = "button";
  reset.addEventListener("click", () => select(null));
  heading.appendChild(reset);
  section.appendChild(heading);
  const grid = el("div", "devices-canon-category-grid");
  Object.entries(DEVICE_CATEGORY_META).forEach(([id, meta]) => {
    const items = devices.filter((device) => deviceCatalogCategory(device) === id);
    if (!items.length) return;
    const offline = items.filter(deviceCatalogUnavailable).length;
    const active = items.filter(deviceCatalogActive).length;
    const card = el("button", `devices-canon-category${selected === id ? " is-selected" : ""}${offline ? " has-offline" : ""}`);
    card.type = "button";
    setAttr(card, "aria-pressed", selected === id ? "true" : "false");
    card.addEventListener("click", () => select(id));
    const title = el("span", "devices-canon-category-title");
    title.appendChild(svgIcon(meta.icon));
    title.appendChild(el("strong", null, meta.label));
    title.appendChild(el("b", null, String(items.length)));
    card.appendChild(title);
    card.appendChild(el("span", offline ? "warn" : "ok", offline ? `${offline} без связи` : `${active} активны`));
    grid.appendChild(card);
  });
  section.appendChild(grid);
  container.appendChild(section);
}

function renderDevicesCatalog(panel, container, devices, selected, deps) {
  const { el, setAttr } = deps;
  const section = el("section", "devices-canon-section devices-canon-catalog");
  const heading = el("div", "devices-canon-heading devices-canon-catalog-heading");
  const label = selected ? DEVICE_CATEGORY_META[selected].label : "Все устройства";
  heading.appendChild(el("h3", null, label));
  const search = el("input", "devices-canon-search");
  search.type = "search";
  search.placeholder = "Найти устройство или комнату";
  setAttr(search, "aria-label", search.placeholder);
  heading.appendChild(search);
  section.appendChild(heading);
  const filtered = selected ? devices.filter((device) => deviceCatalogCategory(device) === selected) : devices;
  const byRoom = new Map();
  filtered.forEach((device) => {
    const room = device.roomName || "Без комнаты";
    if (!byRoom.has(room)) byRoom.set(room, []);
    byRoom.get(room).push(device);
  });
  const groups = [];
  [...byRoom.entries()].sort(([left], [right]) => left.localeCompare(right, "ru"))
    .forEach(([room, roomDevices]) => {
      const group = el("div", "devices-canon-room");
      const groupHead = el("div", "devices-canon-room-heading");
      groupHead.appendChild(el("h4", null, room));
      groupHead.appendChild(el("span", null, `${roomDevices.length} ${deviceWord(roomDevices.length)}`));
      group.appendChild(groupHead);
      const grid = el("div", "inventory-device-grid devices-canon-device-grid");
      const cards = roomDevices.map((device) => {
        const card = panel._deviceInventoryCard(device);
        setAttr(card, "data-search", [device.name, device.roomName, device.manufacturer, device.model]
          .filter(Boolean).join(" ").toLocaleLowerCase("ru"));
        grid.appendChild(card);
        return card;
      });
      group.appendChild(grid);
      section.appendChild(group);
      groups.push({ group, cards });
    });
  const empty = el("p", "devices-canon-empty", "По этому запросу устройства не найдены.");
  empty.hidden = true;
  const applySearch = () => {
    const query = String(search.value || "").trim().toLocaleLowerCase("ru");
    let visible = 0;
    groups.forEach(({ group, cards }) => {
      let groupVisible = 0;
      cards.forEach((card) => {
        card.hidden = Boolean(query && !String(card.dataset.search || "").includes(query));
        if (!card.hidden) groupVisible += 1;
      });
      group.hidden = groupVisible === 0;
      visible += groupVisible;
    });
    empty.hidden = visible !== 0;
  };
  search.addEventListener("input", applySearch);
  section.appendChild(empty);
  container.appendChild(section);
}

export function renderDevicesOverview(panel, container, deps) {
  container.innerHTML = "";
  if (!panel._homeDashboard) {
    renderDeviceDiscovery(panel, container, deps);
    const empty = deps.el("section", "card empty-state devices-canon-empty-state");
    empty.appendChild(deps.el("h2", null, "Устройства"));
    empty.appendChild(deps.el("p", null, "Каталог устройств пока недоступен. Проверьте подключение Hausman Hub."));
    container.appendChild(empty);
    return;
  }
  const devices = physicalDevices(panel);
  if (panel._deviceCategoryFilter === undefined) panel._deviceCategoryFilter = null;
  renderDevicesHero(panel, container, devices, deps);
  const workspace = deps.el("section", "devices-canon-workspace");
  renderDeviceDiscovery(panel, workspace, deps);
  const select = (value) => {
    panel._deviceCategoryFilter = value;
    panel._renderHomeSection("devices", panel._shell.homeSections.devices);
  };
  renderDeviceCategoryFilters(panel, workspace, devices, panel._deviceCategoryFilter, select, deps);
  renderDevicesCatalog(panel, workspace, devices, panel._deviceCategoryFilter, deps);
  container.appendChild(workspace);
}

renderDevicesOverview.physicalDevices = physicalDevices;
renderDevicesOverview.category = deviceCatalogCategory;
