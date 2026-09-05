import { canonicalRoomMdiIcon } from "./hausman-hub-room-icons.js?v=1.52.217";

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
  const rooms = dashboardRooms.length ? dashboardRooms : setupRooms;
  const intro = el("section", "card settings-room-summary");
  intro.appendChild(svgIcon("rooms", "settings-room-summary-icon"));
  const copy = el("div", "settings-room-summary-copy");
  copy.appendChild(el("h3", null, "Комнаты и устройства"));
  copy.appendChild(el("p", "muted", "Home Assistant хранит название, устройства и назначение каждой комнаты. Назначение задаётся в разделе «Комнаты»: откройте карточку комнаты и выберите назначение — одна и та же иконка появится на планшете и в HACS."));
  intro.appendChild(copy);
  const edit = el("button", null, setup.status === "not_configured" ? "Начать настройку" : "Настроить комнаты");
  edit.disabled = panel._busy || (setup.status !== "not_configured" && setup.editing_allowed !== true);
  edit.addEventListener("click", () => panel._openWizard(setup));
  intro.appendChild(edit);
  container.appendChild(intro);
  renderDeviceBindingCallout(panel, container, { el, svgIcon });
  renderDeviceInventory(panel, container, { el, normalizedText });

  if (!rooms.length) {
    const empty = el("div", "card settings-empty-state");
    empty.appendChild(el("strong", null, "Комнаты ещё не настроены"));
    empty.appendChild(el("p", "muted", "Мастер сначала покажет комнаты Home Assistant и устройства без комнаты, чтобы их можно было быстро разобрать."));
    container.appendChild(empty);
  }

  const guide = el("details", "card settings-guide-card");
  const guideSummary = el("summary", "settings-guide-summary");
  guideSummary.appendChild(el("h3", null, "Как устроена настройка комнаты"));
  guide.appendChild(guideSummary);
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
