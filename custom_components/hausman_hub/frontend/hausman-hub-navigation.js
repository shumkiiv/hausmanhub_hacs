const NAV_SECTION_PARAM = "hh_section";
const NAV_VIEW_PARAM = "hh_view";

export const PANEL_SECTIONS = [
  { id: "overview", label: "Главная", icon: "dashboard" },
  { id: "lighting", label: "Освещение", icon: "lightbulb" },
  { id: "climate", label: "Климат", icon: "thermometer" },
  { id: "rooms", label: "Комнаты", icon: "rooms" },
  { id: "media", label: "Медиа", icon: "media" },
  { id: "security", label: "Безопасность", icon: "shield" },
  { id: "devices", label: "Устройства", icon: "device" },
  { id: "energy", label: "Энергия", icon: "energy" },
  { id: "scenarios", label: "Сценарии", icon: "bolt" },
  { id: "settings", label: "Настройки", icon: "settings" },
];

export const SECTION_SUBTITLES = {
  overview: "Состояние и управление домом",
  lighting: "Свет по комнатам и отдельным клавишам",
  climate: "Климатический контур и комфорт",
  rooms: "Все комнаты и устройства внутри",
  media: "Телевизоры, колонки и медиаплееры",
  security: "Датчики, доступ и тревоги",
  devices: "Все физические устройства дома",
  energy: "Потребление, статистика и управление нагрузкой",
  scenarios: "Управление сценариями дома",
  settings: "Подключение и параметры системы",
};

export function restoreNavigationFromLocation(
  panel,
  useOverviewFallback,
  sections,
  climateViews,
  settingsViews
) {
  if (typeof window === "undefined" || !window.location) return;
  let params;
  try {
    params = new URLSearchParams(window.location.search || "");
  } catch (error) {
    return;
  }
  const section = params.get(NAV_SECTION_PARAM);
  if (sections.some((item) => item.id === section)) {
    panel._activeSection = section;
  } else if (useOverviewFallback) {
    panel._activeSection = "overview";
  }
  const view = params.get(NAV_VIEW_PARAM);
  if (panel._activeSection === "climate" && climateViews.some((item) => item.id === view)) {
    panel._activeClimateView = view;
  }
  if (panel._activeSection === "settings" && settingsViews.some((item) => item.id === view)) {
    panel._activeSettingsView = view;
  }
}

export function writeNavigationRoute(panel) {
  if (
    typeof window === "undefined"
    || !window.location
    || !window.history
    || typeof window.history.pushState !== "function"
    || !panel._activeSection
  ) return;
  try {
    const url = new URL(window.location.href);
    url.searchParams.set(NAV_SECTION_PARAM, panel._activeSection);
    if (panel._activeSection === "climate") {
      url.searchParams.set(NAV_VIEW_PARAM, panel._activeClimateView);
    } else if (panel._activeSection === "settings") {
      url.searchParams.set(NAV_VIEW_PARAM, panel._activeSettingsView);
    } else {
      url.searchParams.delete(NAV_VIEW_PARAM);
    }
    if (url.href !== window.location.href) {
      window.history.pushState({ hausmanHub: true }, "", url.href);
    }
  } catch (error) {
  }
}

export function createKioskButton(panel, className, deps) {
  const { el, svgIcon } = deps;
  const button = el("button", `kiosk-toggle ${className}`);
  button.type = "button";
  button.appendChild(svgIcon("dashboard"));
  button.appendChild(el("span", "kiosk-label", "Режим киоска"));
  button.addEventListener("click", () => toggleKioskMode(panel));
  return button;
}

export function createKioskDock(panel, deps) {
  const { el, svgIcon, setAttr } = deps;
  const dock = el("div", "kiosk-dock");
  dock.hidden = true;
  setAttr(dock, "aria-label", "Быстрые действия режима киоска");
  const intercom = el("button", "kiosk-intercom");
  intercom.type = "button";
  setAttr(intercom, "aria-label", "Открыть домофон");
  intercom.appendChild(svgIcon("intercom"));
  intercom.appendChild(el("span", null, "Домофон"));
  intercom.addEventListener("click", () => openIntercomFromRail(panel));
  dock.appendChild(intercom);
  const exit = el("button", "kiosk-exit");
  exit.type = "button";
  setAttr(exit, "aria-label", "Выйти из режима киоска");
  exit.appendChild(svgIcon("close"));
  exit.appendChild(el("span", null, "Выйти"));
  exit.addEventListener("click", () => toggleKioskMode(panel));
  dock.appendChild(exit);
  return dock;
}

export function setKioskState(panel, active) {
  panel._kioskMode = Boolean(active);
  panel.classList?.toggle?.("kiosk-mode", panel._kioskMode);
  [panel._shell?.kioskButton, panel._shell?.sidebarKiosk].filter(Boolean).forEach((button) => {
    button.setAttribute?.("aria-pressed", panel._kioskMode ? "true" : "false");
    button.setAttribute?.("aria-label", panel._kioskMode ? "Выйти из режима киоска" : "Открыть режим киоска");
    const label = button.querySelector?.(".kiosk-label");
    if (label) label.textContent = panel._kioskMode ? "Выйти из киоска" : "Режим киоска";
  });
  if (panel._shell?.kioskDock) panel._shell.kioskDock.hidden = !panel._kioskMode;
  panel._syncSectionVisibility?.();
}

export function handleKioskPointerUp(panel, event) {
  if (!panel._kioskMode) return;
  const target = event && event.target;
  const interactiveSelector = "button, input, select, textarea, a";
  const path = event && typeof event.composedPath === "function" ? event.composedPath() : [];
  const interactive = path.some((node) => (
    node && typeof node.matches === "function" && node.matches(interactiveSelector)
  )) || (target && typeof target.closest === "function" && target.closest(interactiveSelector));
  if (interactive) return;
  const now = Date.now();
  if (now - panel._kioskTapAt <= 420) {
    panel._kioskTapAt = 0;
    toggleKioskMode(panel);
    return;
  }
  panel._kioskTapAt = now;
}

export async function toggleKioskMode(panel) {
  if (panel._kioskMode) {
    if (document.fullscreenElement && typeof document.exitFullscreen === "function") {
      await document.exitFullscreen().catch(() => {});
    }
    setKioskState(panel, false);
    return;
  }
  if (panel._activeSection !== "overview" && typeof panel._activateSection === "function") {
    panel._activateSection("overview");
  }
  setKioskState(panel, true);
  if (typeof panel.requestFullscreen === "function") {
    await panel.requestFullscreen().catch(() => setKioskState(panel, true));
  }
}

export function renderOverviewNavigationSummary(panel, container, metrics, deps) {
  const { el, setAttr, svgIcon, sections } = deps;
  const heading = el("div", "overview-section-heading");
  heading.appendChild(el("h2", null, "Сводка"));
  heading.appendChild(el("p", "section-intro", "Основные показатели дома"));
  container.appendChild(heading);
  const summary = el("div", "overview-summary");
  const deviceLabel = metrics.activeDevices == null ? "Устройства настроены" : "Активные устройства";
  const deviceValue = metrics.activeDevices == null
    ? `${metrics.deviceCount}`
    : `${metrics.activeDevices} из ${metrics.deviceCount}`;
  [
    ["thermometer", "Температура", metrics.temperature, "climate"],
    ["water", "Влажность", metrics.humidity, "climate"],
    ["device", deviceLabel, deviceValue, "devices"],
  ].forEach(([iconName, label, value, targetSection]) => {
    const item = el("button", "summary-item summary-link");
    item.type = "button";
    const targetLabel = sections.find((section) => section.id === targetSection).label;
    setAttr(item, "aria-label", `${label}: ${value}. Открыть раздел ${targetLabel}`);
    item.addEventListener("click", () => panel._activateSection(targetSection));
    const icon = el("span", "summary-icon");
    icon.appendChild(svgIcon(iconName));
    item.appendChild(icon);
    const copy = el("div", "summary-copy");
    copy.appendChild(el("span", "assistant-field-label", label));
    copy.appendChild(el("strong", "summary-value", value));
    item.appendChild(copy);
    summary.appendChild(item);
  });
  container.appendChild(summary);
}

export function openRoomFromOverview(panel, room) {
  const dashboardRooms = panel._homeDashboard && Array.isArray(panel._homeDashboard.rooms)
    ? panel._homeDashboard.rooms : [];
  const matched = dashboardRooms.find((candidate) => (
    candidate.id === room.id || candidate.name === room.name
  ));
  const roomId = (matched && matched.id) || room.id;
  if (roomId) panel._openHomeCards.add(`room:${roomId}`);
  if (panel._sectionRenderKeys) panel._sectionRenderKeys.rooms = null;
  panel._activateSection("rooms");
}

function intercomDeviceId(device) {
  return String(device?.id || device?.deviceId || device?.physicalId || device?.entityId || "");
}

export function resolveIntercomQuickAction(devices, catalog, configuredDeviceId = null) {
  const normalized = (value) => String(value || "").trim().toLocaleLowerCase("ru");
  const device = (Array.isArray(devices) ? devices : []).find((candidate) => {
    if (configuredDeviceId) return intercomDeviceId(candidate) === String(configuredDeviceId);
    const details = Array.isArray(candidate.details) ? candidate.details : [];
    const identity = normalized([
      candidate.name, candidate.entityId, candidate.physicalId, candidate.model,
      ...details.flatMap((detail) => [detail.label, detail.entityId]),
    ].filter(Boolean).join(" "));
    return /(домофон|intercom|doorbell|глазок)/.test(identity);
  });
  if (!device) return null;
  const entityIds = new Set([device.entityId].concat(
    Array.isArray(device.details) ? device.details.map((item) => item.entityId) : []
  ).filter(Boolean));
  const targets = (Array.isArray(catalog) ? catalog : []).filter((target) => entityIds.has(target.entity_id));
  const preferences = ["open", "unlock", "turn_on", "press", "activate"];
  for (const actionId of preferences) {
    for (const target of targets) {
      const action = (target.actions || []).find((candidate) => candidate.action_id === actionId);
      if (action && !(action.allowed_fields || []).includes("value")) {
        return { device, targetId: target.target_id, actionId };
      }
    }
  }
  return { device, targetId: null, actionId: null };
}

export function openIntercomFromRail(panel) {
  const configuredDeviceId = panel._tabletProfile?.settings?.intercom?.deviceId;
  if (!configuredDeviceId) {
    panel._activeSettingsView = "intercom";
    panel._activateSection("settings");
    panel._notice = "Домофон ещё не настроен. Выберите устройство в «Настройки → Домофон».";
    panel._render();
    return;
  }
  const catalog = panel._scenarios.catalog && Array.isArray(panel._scenarios.catalog.devices)
    ? panel._scenarios.catalog.devices : [];
  const command = resolveIntercomQuickAction(panel._homeDevices("devices"), catalog, configuredDeviceId);
  if (!command) {
    panel._activeSettingsView = "intercom";
    panel._activateSection("settings");
    panel._notice = "Настроенный домофон больше не найден. Выберите доступное устройство заново.";
    panel._render();
    return;
  }
  if (!command.targetId || !command.actionId) {
    panel._activeSettingsView = "intercom";
    panel._activateSection("settings");
    panel._notice = `Для «${command.device.name || "Домофон"}» команда открытия недоступна. Проверьте настройку устройства.`;
    panel._render();
    return;
  }
  panel._executeDeviceAction(command.targetId, command.actionId, null);
}
