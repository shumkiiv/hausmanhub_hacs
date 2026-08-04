import {
  canonicalRoomMdiIcon,
  ROOM_TYPE_OPTIONS,
  roomIconName,
  roomSvgIcon,
} from "./hausman-hub-room-icons.js?v=1.52.30";

/** Render room purpose selection backed by the Home Assistant Area Registry. */
export function renderSettingsRooms(panel, container, deps) {
  const {
    el,
    normalizedText,
    renderDeviceBindingCallout,
    renderDeviceInventory,
    roomSetupPanes,
    svgIcon,
  } = deps;
  const setup = panel._settings.setup || {};
  const setupRooms = Array.isArray(setup.rooms) ? setup.rooms : [];
  const dashboardRooms = Array.isArray(panel._homeDashboard?.rooms) ? panel._homeDashboard.rooms : [];
  const dashboardDevices = Array.isArray(panel._homeDashboard?.devices) ? panel._homeDashboard.devices : [];
  const rooms = dashboardRooms.length ? dashboardRooms : setupRooms;
  const intro = el("section", "card settings-room-summary");
  intro.appendChild(svgIcon("rooms", "settings-room-summary-icon"));
  const copy = el("div", "settings-room-summary-copy");
  copy.appendChild(el("h3", null, "Комнаты и устройства"));
  copy.appendChild(el("p", "muted", "Home Assistant хранит название, устройства и назначение каждой комнаты. Выбранное назначение задаёт одну и ту же иконку на планшете и в HACS."));
  intro.appendChild(copy);
  const edit = el("button", null, setup.status === "not_configured" ? "Начать настройку" : "Настроить комнаты");
  edit.disabled = panel._busy || (setup.status !== "not_configured" && setup.editing_allowed !== true);
  edit.addEventListener("click", () => panel._openWizard(setup));
  intro.appendChild(edit);
  container.appendChild(intro);
  renderDeviceBindingCallout(panel, container, { el, svgIcon });
  renderDeviceInventory(panel, container, { el, normalizedText });

  const roomGrid = el("div", "settings-room-grid");
  rooms.forEach((room) => {
    const roomCard = el("article", "settings-room-card");
    const head = el("div", "settings-room-card-head");
    const currentType = roomIconName(room);
    head.appendChild(roomSvgIcon(currentType));
    const roomCopy = el("div");
    roomCopy.appendChild(el("strong", null, room.name || room.id || "Комната"));
    const devices = Array.isArray(room.devices) && room.devices.length
      ? room.devices
      : dashboardDevices.filter((device) => device.roomId === room.id);
    roomCopy.appendChild(el("small", null, `${devices.length} ${devices.length === 1 ? "устройство" : "устройств"}`));
    head.appendChild(roomCopy);
    roomCard.appendChild(head);
    const typeField = el("label", "settings-room-type-field");
    typeField.appendChild(el("span", "assistant-field-label", "Назначение комнаты"));
    const select = el("select", "settings-room-type-select");
    ROOM_TYPE_OPTIONS.forEach((item) => {
      const option = el("option", null, item.label);
      option.value = item.id;
      option.selected = item.id === currentType;
      select.appendChild(option);
    });
    select.value = currentType;
    typeField.appendChild(select);
    typeField.appendChild(el("small", "muted", "Название комнаты не изменится. В Area Registry Home Assistant сохранится только каноническая иконка."));
    roomCard.appendChild(typeField);
    const save = el("button", "secondary settings-room-type-save", "Сохранить назначение");
    const alreadyCanonical = String(room.icon || "").toLowerCase() === canonicalRoomMdiIcon(currentType);
    save.disabled = panel._roomTypeSaving.has(room.id) || alreadyCanonical;
    select.disabled = panel._roomTypeSaving.has(room.id);
    select.addEventListener("change", () => {
      save.disabled = panel._roomTypeSaving.has(room.id)
        || (select.value === currentType && alreadyCanonical);
    });
    save.addEventListener("click", () => panel._saveRoomType(room, select.value));
    roomCard.appendChild(save);
    roomGrid.appendChild(roomCard);
  });
  if (!rooms.length) {
    const empty = el("div", "card settings-empty-state");
    empty.appendChild(el("strong", null, "Комнаты ещё не настроены"));
    empty.appendChild(el("p", "muted", "Мастер сначала покажет комнаты Home Assistant и устройства без комнаты, чтобы их можно было быстро разобрать."));
    roomGrid.appendChild(empty);
  }
  container.appendChild(roomGrid);

  const guide = el("section", "card settings-guide-card");
  guide.appendChild(el("h3", null, "Как устроена настройка комнаты"));
  const steps = el("div", "settings-guide-steps");
  roomSetupPanes.forEach((pane, index) => {
    const step = el("div", "settings-guide-step");
    step.appendChild(el("span", null, String(index + 1)));
    const stepCopy = el("div");
    stepCopy.appendChild(el("strong", null, pane.label));
    stepCopy.appendChild(el("small", null, pane.description));
    step.appendChild(stepCopy);
    steps.appendChild(step);
  });
  guide.appendChild(steps);
  container.appendChild(guide);
}

/** Persist one canonical room purpose without changing the user-visible name. */
export async function saveRoomType(panel, room, roomType, dashboardApi) {
  const areaId = String(room?.id || "");
  const sendMessage = panel._hass?.connection?.sendMessagePromise;
  if (!areaId || typeof sendMessage !== "function" || panel._roomTypeSaving.has(areaId)) return;
  const icon = canonicalRoomMdiIcon(roomType);
  panel._roomTypeSaving.add(areaId);
  panel._notice = "";
  panel._error = false;
  panel._render();
  try {
    await sendMessage.call(panel._hass.connection, {
      type: "config/area_registry/update",
      area_id: areaId,
      icon,
    });
    room.icon = icon;
    const current = panel._homeDashboard?.rooms?.find((candidate) => candidate.id === areaId);
    if (current) current.icon = icon;
    const fresh = await panel._hass.callApi("GET", dashboardApi).catch(() => null);
    if (fresh) panel._homeDashboard = fresh;
    panel._sectionRenderKeys = {};
    panel._notice = `Назначение комнаты «${room.name || areaId}» сохранено в Home Assistant.`;
  } catch (error) {
    panel._notice = `Назначение комнаты «${room.name || areaId}» сохранить не удалось. Проверьте права администратора.`;
    panel._error = true;
  } finally {
    panel._roomTypeSaving.delete(areaId);
    panel._render();
  }
}
