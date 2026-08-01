/* Tablet catalog surfaces shared by the HACS panel and the Android visual contract. */

const SECTION_META = {
  lighting: {
    title: "Освещение", icon: "lightbulb", lead: "Свет по комнатам и отдельным клавишам",
    facts: [["Включено", "active"], ["Светильников", "total"], ["Комнат", "rooms"], ["Нет связи", "offline"]],
  },
  climate: {
    title: "Климат", icon: "thermometer", lead: "Температура, влажность и все климатические устройства",
    facts: [["Средняя температура", "temperature"], ["Средняя влажность", "humidity"], ["Систем работает", "active"], ["Нет связи", "offline"]],
  },
  rooms: {
    title: "Комнаты", icon: "rooms", lead: "Комфорт и устройства в каждой комнате",
    facts: [["Комнат", "rooms"], ["Устройств", "total"], ["Активно", "active"], ["Нет связи", "offline"]],
  },
  media: {
    title: "Медиа", icon: "media", lead: "Телевизоры, колонки и медиаплееры",
    facts: [["Воспроизводят", "active"], ["Медиаустройств", "total"], ["Комнат", "rooms"], ["Нет связи", "offline"]],
  },
  security: {
    title: "Безопасность", icon: "shield", lead: "Датчики, камеры, доступ и тревоги",
    facts: [["Тревог", "alarms"], ["Устройств", "total"], ["Активно", "active"], ["Нет связи", "offline"]],
  },
  devices: {
    title: "Устройства", icon: "device", lead: "Все физические устройства дома без дублирования функций",
    facts: [["Устройств", "total"], ["Активно", "active"], ["Комнат", "rooms"], ["Нет связи", "offline"]],
  },
};

function homeDevices(panel, sectionId, normalizedText) {
  const devices = panel._homeDashboard && Array.isArray(panel._homeDashboard.devices)
    ? panel._homeDashboard.devices : [];
  if (sectionId === "devices" || sectionId === "rooms") return devices;
  return devices.filter((device) => {
    const domain = String(device.domain || "");
    const category = String(device.category || "");
    const identity = normalizedText(`${device.name} ${device.stateLabel}`);
    if (sectionId === "lighting") return domain === "light" || category === "lighting"
      || (domain === "switch" && /(свет|ламп|люстр|подсвет)/.test(identity));
    if (sectionId === "media") return domain === "media_player" || category === "media";
    if (sectionId === "climate") return ["climate", "humidifier", "fan"].includes(domain)
      || ["climate", "air_quality"].includes(category);
    if (sectionId === "security") return category === "security"
      || ["lock", "camera", "alarm_control_panel"].includes(domain);
    return false;
  });
}

function deviceCountWord(count) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return "устройств";
  if (count % 10 === 1) return "устройство";
  if (count % 10 >= 2 && count % 10 <= 4) return "устройства";
  return "устройств";
}

function average(values) {
  const valid = values.filter((value) => Number.isFinite(Number(value))).map(Number);
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null;
}

function metrics(panel, devices, rooms) {
  const alarms = panel._homeDashboard && Array.isArray(panel._homeDashboard.alarms)
    ? panel._homeDashboard.alarms.filter((alarm) => alarm.active !== false) : [];
  const offline = devices.filter((device) => device.unavailable || device.state === "unavailable").length;
  const active = devices.filter((device) => !device.unavailable && (
    device.active === true || !["off", "idle", "standby", "unknown", "unavailable"].includes(device.state)
  )).length;
  return {
    total: devices.length,
    active,
    offline,
    rooms: new Set(devices.map((device) => device.roomId).filter(Boolean)).size || rooms.length,
    alarms: alarms.length,
    temperature: panel._temp(average(rooms.map((room) => room.temp))),
    humidity: panel._humidity(average(rooms.map((room) => room.humidity))),
  };
}

function renderHero(panel, container, meta, values, deps) {
  const { el, svgIcon } = deps;
  const hero = el("section", "catalog-hero");
  const head = el("div", "catalog-hero-head");
  const icon = el("span", "catalog-hero-icon");
  icon.appendChild(svgIcon(meta.icon));
  head.appendChild(icon);
  const copy = el("div", "catalog-hero-copy");
  copy.appendChild(el("h2", null, meta.title));
  copy.appendChild(el("p", "section-intro", meta.lead));
  head.appendChild(copy);
  hero.appendChild(head);
  const facts = el("div", "catalog-hero-facts");
  meta.facts.forEach(([label, key]) => {
    const fact = el("div", "catalog-hero-fact");
    fact.appendChild(el("strong", null, String(values[key] ?? "Нет данных")));
    fact.appendChild(el("span", null, label));
    facts.appendChild(fact);
  });
  hero.appendChild(facts);
  container.appendChild(hero);
}

function renderToolbar(container, count, deps) {
  const { el, setAttr } = deps;
  const toolbar = el("div", "catalog-toolbar");
  const title = el("div", "catalog-toolbar-title");
  title.appendChild(el("h3", null, "Каталог"));
  title.appendChild(el("span", "status-badge catalog-count", `${count} устройств`));
  toolbar.appendChild(title);
  const controls = el("div", "catalog-controls");
  const filters = el("div", "catalog-filters");
  const search = el("input", "catalog-search");
  search.type = "search";
  search.placeholder = "Найти устройство или комнату";
  setAttr(search, "aria-label", search.placeholder);
  let activeFilter = "all";
  const apply = () => {
    if (typeof container.querySelectorAll !== "function") return;
    const query = String(search.value || "").trim().toLocaleLowerCase("ru");
    let visible = 0;
    container.querySelectorAll(".inventory-device-card").forEach((card) => {
      const matchesText = !query || String(card.dataset.search || "").includes(query);
      const matchesFilter = activeFilter === "all"
        || (activeFilter === "active" && card.dataset.active === "true")
        || (activeFilter === "offline" && card.dataset.offline === "true");
      card.hidden = !(matchesText && matchesFilter);
      if (!card.hidden) visible += 1;
    });
    const badge = toolbar.querySelector(".catalog-count");
    if (badge) badge.textContent = `${visible} устройств`;
    container.querySelectorAll(".inventory-room").forEach((room) => {
      room.hidden = !Array.from(room.querySelectorAll(".inventory-device-card")).some((card) => !card.hidden);
    });
  };
  [["all", "Все"], ["active", "Активные"], ["offline", "Нет связи"]].forEach(([id, label]) => {
    const button = el("button", `catalog-filter${id === "all" ? " is-active" : ""}`, label);
    button.type = "button";
    setAttr(button, "aria-pressed", id === "all" ? "true" : "false");
    button.addEventListener("click", () => {
      activeFilter = id;
      filters.querySelectorAll("button").forEach((candidate) => {
        const selected = candidate === button;
        candidate.classList.toggle("is-active", selected);
        setAttr(candidate, "aria-pressed", selected ? "true" : "false");
      });
      apply();
    });
    filters.appendChild(button);
  });
  controls.appendChild(filters);
  controls.appendChild(search);
  search.addEventListener("input", apply);
  toolbar.appendChild(controls);
  container.appendChild(toolbar);
}

function prepareCard(panel, device, setAttr) {
  const card = panel._deviceInventoryCard(device);
  setAttr(card, "data-search", [device.name, device.roomName, device.model, device.manufacturer]
    .filter(Boolean).join(" ").toLocaleLowerCase("ru"));
  setAttr(card, "data-offline", String(Boolean(device.unavailable || device.state === "unavailable")));
  setAttr(card, "data-active", String(Boolean(!device.unavailable && (
    device.active === true || !["off", "idle", "standby", "unknown", "unavailable"].includes(device.state)
  ))));
  return card;
}

export function renderHomeSection(panel, sectionId, container, deps) {
  const { el, setAttr } = deps;
  container.innerHTML = "";
  if (sectionId === "energy") {
    panel._renderEnergySection(container);
    return;
  }
  const meta = SECTION_META[sectionId] || SECTION_META.devices;
  const rooms = panel._homeDashboard && Array.isArray(panel._homeDashboard.rooms)
    ? panel._homeDashboard.rooms : [];
  const devices = panel._homeDashboard ? panel._homeDevices(sectionId) : [];
  renderHero(panel, container, meta, metrics(panel, devices, rooms), deps);
  if (sectionId === "rooms") {
    if (panel._homeDashboard) panel._renderRoomInventory(container);
    else container.appendChild(el("div", "card empty-state", "Данные комнат пока недоступны. Проверьте подключение HausmanHub."));
    return;
  }
  if (sectionId === "security" && panel._homeDashboard) panel._renderAlarmSummary(container);
  renderToolbar(container, devices.length, deps);
  if (!panel._homeDashboard) {
    container.appendChild(el("div", "card empty-state", "Данные дома пока недоступны. Проверьте подключение HausmanHub."));
    return;
  }
  if (!devices.length) {
    container.appendChild(el("div", "card empty-state", `${meta.title}: подходящие физические устройства пока не найдены.`));
    return;
  }
  const byRoom = new Map();
  devices.forEach((device) => {
    const room = device.roomName || "Без комнаты";
    if (!byRoom.has(room)) byRoom.set(room, []);
    byRoom.get(room).push(device);
  });
  [...byRoom.entries()].sort(([left], [right]) => left.localeCompare(right, "ru"))
    .forEach(([room, roomDevices]) => {
      const section = el("section", "inventory-room");
      const title = el("div", "inventory-room-heading");
      title.appendChild(el("h3", null, room));
      title.appendChild(el("span", "status-badge", `${roomDevices.length} ${panel._deviceCountWord(roomDevices.length)}`));
      section.appendChild(title);
      const grid = el("div", "inventory-device-grid");
      roomDevices.forEach((device) => grid.appendChild(prepareCard(panel, device, setAttr)));
      section.appendChild(grid);
      container.appendChild(section);
    });
}

renderHomeSection.homeDevices = homeDevices;
renderHomeSection.deviceCountWord = deviceCountWord;
