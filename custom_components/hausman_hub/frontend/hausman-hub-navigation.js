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

export function renderOverviewNavigationSummary(panel, container, metrics, deps) {
  const { el, setAttr, svgIcon, sections } = deps;
  const heading = el("div", "overview-section-heading");
  heading.appendChild(el("h2", null, "Сводка"));
  heading.appendChild(el("p", "section-intro", "Основные показатели дома"));
  container.appendChild(heading);
  const summary = el("div", "overview-summary");
  [
    ["thermometer", "Температура", metrics.temperature, "climate"],
    ["water", "Влажность", metrics.humidity, "climate"],
    ["device", "Активные устройства", `${metrics.activeDevices} из ${metrics.deviceCount}`, "devices"],
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
  panel._activateSection("rooms");
}
