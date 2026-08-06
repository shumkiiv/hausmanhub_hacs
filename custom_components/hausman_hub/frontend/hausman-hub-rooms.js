import { createLibraryHero } from "./hausman-hub-library-hero.js?v=1.52.46";
import { canonicalRoomMdiIcon, ROOM_TYPE_OPTIONS, roomIconName, roomSvgIcon } from "./hausman-hub-room-icons.js?v=1.52.46";

function roomNormalized(value) {
  return String(value || "").trim().toLocaleLowerCase("ru");
}

function roomDeviceKey(device) {
  return device.physicalId || device.id || device.entityId;
}

function roomDeviceUnavailable(device) {
  return Boolean(device.unavailable || device.state === "unavailable");
}

function roomDeviceActive(device) {
  return !roomDeviceUnavailable(device) && (
    device.active === true || (
      typeof device.active !== "boolean"
      && !["off", "idle", "standby", "unknown", "unavailable"].includes(device.state)
    )
  );
}

function roomForDevice(device, rooms) {
  const byId = rooms.find((room) => room.id && room.id === device.roomId);
  if (byId) return byId;
  const name = roomNormalized(device.roomName);
  return rooms.find((room) => roomNormalized(room.name) === name) || null;
}

function roomTemperature(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)} °C` : "Нет данных";
}

function roomHumidity(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value)} %` : "Нет данных";
}

function roomsCanonCountWord(count) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return "комнат";
  if (count % 10 === 1) return "комната";
  if (count % 10 >= 2 && count % 10 <= 4) return "комнаты";
  return "комнат";
}

function roomDeviceWord(count) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return "устройств";
  if (count % 10 === 1) return "устройство";
  if (count % 10 >= 2 && count % 10 <= 4) return "устройства";
  return "устройств";
}

function closeRoomOverview(panel, container) {
  if (panel._roomOverviewOverlay && panel._openHomeCards) {
    panel._openHomeCards.delete(`room:${panel._roomOverviewOverlay}`);
  }
  panel._roomOverviewOverlay = null;
  const backdrop = container.querySelector && container.querySelector(".rooms-detail-backdrop");
  if (backdrop && typeof backdrop.remove === "function") backdrop.remove();
}

function openRoomOverview(panel, container, room, devices, deps) {
  closeRoomOverview(panel, container);
  panel._roomOverviewOverlay = room.id;
  if (panel._openHomeCards) panel._openHomeCards.add(`room:${room.id}`);
  const { el, svgIcon, setAttr } = deps;
  const backdrop = el("div", "rooms-detail-backdrop");
  const sheet = el("section", "rooms-detail-sheet");
  setAttr(sheet, "role", "dialog");
  setAttr(sheet, "aria-modal", "true");
  setAttr(sheet, "aria-label", `Комната ${room.name}`);
  const close = el("button", "rooms-detail-close", "×");
  close.type = "button";
  setAttr(close, "aria-label", "Закрыть комнату");
  close.addEventListener("click", () => closeRoomOverview(panel, container));
  sheet.appendChild(close);
  const head = el("div", "rooms-detail-head");
  const icon = el("span", "rooms-detail-icon");
  icon.appendChild(roomSvgIcon(roomIconName(room)));
  head.appendChild(icon);
  const copy = el("div");
  copy.appendChild(el("span", "rooms-detail-eyebrow", "КОМНАТА"));
  copy.appendChild(el("h2", null, room.name));
  copy.appendChild(el("p", null, "Все физические устройства и доступные возможности без дублирования сущностей"));
  head.appendChild(copy);
  sheet.appendChild(head);
  const facts = el("div", "rooms-detail-facts");
  const active = devices.filter(roomDeviceActive).length;
  const unavailable = devices.filter(roomDeviceUnavailable).length;
  [[roomTemperature(room.temp), "Температура"], [roomHumidity(room.humidity), "Влажность"],
    [`${active} из ${devices.length}`, "Активно"], [String(unavailable), "Без связи"]]
    .forEach(([value, label]) => {
      const fact = el("span");
      fact.appendChild(el("strong", null, value));
      fact.appendChild(el("small", null, label));
      facts.appendChild(fact);
    });
  sheet.appendChild(facts);
  const purpose = el("div", "rooms-detail-purpose");
  const purposeField = el("label", "rooms-detail-purpose-field");
  purposeField.appendChild(el("span", "assistant-field-label", "Назначение комнаты"));
  const select = el("select", "settings-room-type-select");
  const currentType = roomIconName(room);
  ROOM_TYPE_OPTIONS.forEach((item) => {
    const option = el("option", null, item.label);
    option.value = item.id;
    option.selected = item.id === currentType;
    select.appendChild(option);
  });
  select.value = currentType;
  purposeField.appendChild(select);
  purpose.appendChild(purposeField);
  const alreadyCanonical = String(room.icon || "").toLowerCase() === canonicalRoomMdiIcon(currentType);
  const save = el("button", "secondary settings-room-type-save", "Сохранить назначение");
  save.type = "button";
  save.disabled = panel._roomTypeSaving.has(room.id) || alreadyCanonical;
  select.disabled = panel._roomTypeSaving.has(room.id);
  select.addEventListener("change", () => {
    save.disabled = panel._roomTypeSaving.has(room.id)
      || (select.value === currentType && alreadyCanonical);
  });
  save.addEventListener("click", () => panel._saveRoomType(room, select.value));
  purpose.appendChild(save);
  purpose.appendChild(el("small", "muted", "Название комнаты не изменится. В Area Registry Home Assistant сохранится только каноническая иконка."));
  sheet.appendChild(purpose);
  const title = el("div", "rooms-detail-section-title");
  title.appendChild(el("h3", null, "Устройства комнаты"));
  title.appendChild(el("span", null, `${devices.length} ${roomDeviceWord(devices.length)}`));
  sheet.appendChild(title);
  const grid = el("div", "inventory-device-grid rooms-detail-grid");
  if (!devices.length) grid.appendChild(el("div", "empty-state", "Физические устройства в этой комнате пока не найдены."));
  devices.forEach((device) => grid.appendChild(panel._deviceInventoryCard(device)));
  sheet.appendChild(grid);
  backdrop.appendChild(sheet);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) closeRoomOverview(panel, container);
  });
  container.appendChild(backdrop);
}

function renderRoomsHero(panel, container, rooms, devices, deps) {
  const active = devices.filter(roomDeviceActive).length;
  const unavailable = devices.filter(roomDeviceUnavailable).length;
  container.appendChild(createLibraryHero(panel, {
    eyebrow: "КОМНАТЫ ДОМА",
    title: "Дом по комнатам",
    subtitle: "Каждая карточка открывает все физические устройства выбранной комнаты",
    warning: unavailable > 0,
    facts: [
      { label: "Комнаты", value: rooms.length },
      { label: "Устройства", value: devices.length },
      { label: "Активно", value: active },
      { label: "Без связи", value: unavailable, warning: unavailable > 0 },
    ],
  }, deps));
}

function roomMatchesFilter(room, devices, filter) {
  if (filter === "active") return devices.some(roomDeviceActive);
  if (filter === "climate") return typeof room.temp === "number" || typeof room.humidity === "number";
  if (filter === "offline") return devices.some(roomDeviceUnavailable);
  return true;
}

function renderRoomCards(panel, container, rooms, grouped, deps) {
  const { el, svgIcon, setAttr } = deps;
  if (!panel._roomsUi) panel._roomsUi = { query: "", filter: "all" };
  const section = el("section", "rooms-canon-section");
  const heading = el("div", "rooms-canon-heading");
  const headingCopy = el("div");
  headingCopy.appendChild(el("h3", null, "Все комнаты"));
  headingCopy.appendChild(el("p", null, `${rooms.length} ${roomsCanonCountWord(rooms.length)} · устройства не дублируются по функциям`));
  heading.appendChild(headingCopy);
  section.appendChild(heading);
  const controls = el("div", "rooms-canon-controls");
  const search = el("input", "rooms-canon-search");
  search.type = "search";
  search.placeholder = "Найти комнату";
  search.value = panel._roomsUi.query;
  controls.appendChild(search);
  const filters = el("div", "rooms-canon-filters");
  [["all", "Все"], ["active", "Активные"], ["climate", "Есть климат"], ["offline", "Без связи"]]
    .forEach(([id, label]) => {
      const button = el("button", panel._roomsUi.filter === id ? "is-active" : "", label);
      button.type = "button";
      setAttr(button, "aria-pressed", panel._roomsUi.filter === id);
      button.addEventListener("click", () => {
        panel._roomsUi.filter = id;
        if (panel._sectionRenderKeys) panel._sectionRenderKeys.rooms = null;
        panel._render();
      });
      filters.appendChild(button);
    });
  controls.appendChild(filters);
  section.appendChild(controls);
  const grid = el("div", "rooms-canon-grid");
  const renderGrid = () => {
    grid.innerHTML = "";
    panel._roomsUi.query = search.value;
    const query = roomNormalized(search.value);
    const visible = rooms.filter((room) => {
      const devices = grouped.get(room.id) || [];
      return (!query || roomNormalized(room.name).includes(query))
        && roomMatchesFilter(room, devices, panel._roomsUi.filter);
    });
    visible.forEach((room) => {
      const devices = grouped.get(room.id) || [];
      const active = devices.filter(roomDeviceActive).length;
      const unavailable = devices.filter(roomDeviceUnavailable).length;
      const card = el("button", `rooms-canon-card${active ? " is-active" : ""}${unavailable ? " has-warning" : ""}`);
      card.type = "button";
      setAttr(card, "aria-label", `Открыть комнату ${room.name}`);
      const icon = el("span", "rooms-canon-card-icon");
      icon.appendChild(roomSvgIcon(roomIconName(room)));
      card.appendChild(icon);
      const copy = el("span", "rooms-canon-card-copy");
      copy.appendChild(el("strong", null, room.name));
      copy.appendChild(el("small", null, `${devices.length} ${roomDeviceWord(devices.length)}${unavailable ? ` · ${unavailable} без связи` : ""}`));
      card.appendChild(copy);
      const climate = el("span", "rooms-canon-card-climate");
      climate.appendChild(el("strong", null, roomTemperature(room.temp)));
      climate.appendChild(el("small", null, typeof room.humidity === "number" ? `Влажность ${Math.round(room.humidity)} %` : "Климат без данных"));
      card.appendChild(climate);
      card.appendChild(el("span", "rooms-canon-card-chevron", "›"));
      card.addEventListener("click", () => openRoomOverview(panel, container, room, devices, deps));
      grid.appendChild(card);
    });
    if (!visible.length) grid.appendChild(el("div", "empty-state", "Комнаты по выбранному фильтру не найдены."));
  };
  search.addEventListener("input", renderGrid);
  renderGrid();
  section.appendChild(grid);
  container.appendChild(section);
}

export function renderRoomsOverview(panel, container, deps) {
  container.innerHTML = "";
  const dashboard = panel._homeDashboard;
  if (!dashboard) {
    const empty = deps.el("section", "card empty-state");
    empty.appendChild(deps.el("h2", null, "Комнаты"));
    empty.appendChild(deps.el("p", null, "Данные комнат пока недоступны. Проверьте подключение HausmanHub."));
    container.appendChild(empty);
    return;
  }
  const rooms = Array.isArray(dashboard.rooms) ? dashboard.rooms : [];
  const source = Array.isArray(dashboard.devices) ? dashboard.devices : [];
  const unique = new Map();
  source.forEach((device) => unique.set(roomDeviceKey(device), device));
  const devices = [...unique.values()];
  const grouped = new Map(rooms.map((room) => [room.id, []]));
  devices.forEach((device) => {
    const room = roomForDevice(device, rooms);
    if (room) grouped.get(room.id).push(device);
  });
  const page = deps.el("div", "rooms-canon-page");
  renderRoomsHero(panel, page, rooms, devices, deps);
  renderRoomCards(panel, page, rooms, grouped, deps);
  const requested = panel._roomOverviewOverlay
    || rooms.find((room) => panel._openHomeCards && panel._openHomeCards.has(`room:${room.id}`))?.id;
  const selected = rooms.find((room) => room.id === requested);
  if (selected) openRoomOverview(panel, page, selected, grouped.get(selected.id) || [], deps);
  container.appendChild(page);
}
